"""
Telling somebody something is waiting on them.

One place every outgoing message goes through, for three reasons.

A notification is never the point of the action that raised it. Somebody
submitting an assessment on a hilltop has done their job the moment it is
recorded; if the mail server is down, or slow, or the address is wrong,
that must not undo the work or show them an error about something they
neither asked for nor can fix. So nothing here raises — a failure is
logged and the action it followed stands.

Nothing here sends a password or a sign-in link either. Staff here sign in
with work addresses that are not all read, and a link sitting in an unread
inbox helps nobody on site. Somebody locked out asks an administrator, who
hands them a temporary password in person. That is a deliberate decision
and this module does not reopen it.

And a message says where to go. "A requisition needs approving" is worth
little without the address of the requisition.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger("a1360.notifications")


def link(url_name, *args):
    """An absolute address for a screen, or '' when nobody has said where
    this deployment lives."""
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    return f"{base}{reverse(url_name, args=args)}"


def recipients_holding(code, exclude=None):
    """
    Everybody who holds a permission, as addresses.

    Who hears about a thing is who may act on it — the same rule the screens
    use, so a role edited in Settings changes the mailing list too, with
    nothing to keep in step by hand.
    """
    from django.contrib.auth import get_user_model

    people = (
        get_user_model()
        .objects.filter(
            is_active=True,
            user_roles__role__permissions__code=code,
        )
        .exclude(email="")
        .distinct()
    )
    if exclude is not None:
        people = people.exclude(pk=exclude.pk)
    return [person.email for person in people]


def _deliver(subject, body, addresses, template):
    """One attempt at the wire. Never raises."""
    try:
        EmailMessage(subject=subject, body=body, to=addresses).send(fail_silently=False)
        return True
    except Exception:
        # Deliberately broad: a bad address, a refused login, a server that
        # is simply down — none of them is the caller's problem, and none of
        # them should undo work that has already been recorded.
        logger.exception("Could not send '%s' to %s", template, ", ".join(addresses))
        return False


def _deliver_in_background(subject, body, addresses, template):
    """
    Hand the attempt to a daemon thread, so a mail server that accepts the
    connection and then says nothing cannot hold a web worker.

    A thread killed by a restart loses that message. That is the right trade:
    the alternative, demonstrated in production, is losing the leave request
    the message was about.
    """
    threading.Thread(
        target=_deliver,
        args=(subject, body, addresses, template),
        name="a1-mail",
        daemon=True,
    ).start()


def send(*, to, subject, template, context=None, defer=True):
    """
    Send one message, or do not, and never raise either way.

    `template` names a file under templates/email/ without its extension.

    **Why this does not send on the request thread.** The first version
    caught every exception and claimed that a notification could never break
    the action that raised it. That was true of a refusal and false of a
    hang. Railway drops outbound SMTP rather than refusing it, so the socket
    connect blocked, gunicorn's own timeout killed the worker sixty seconds
    later, and the `transaction.atomic` the caller was inside rolled back —
    taking with it the leave request the email was about. The person saw
    "Internal Server Error" and their request was gone.

    So two things changed. Delivery happens **after the transaction commits**,
    so the work is durable before anything touches the network; and it
    happens **off the request thread**, so a silent server costs nobody a
    page. `EMAIL_TIMEOUT` is the backstop under both.

    `defer=False` sends inline and is for callers that have no transaction to
    wait for, or that are about to roll one back on purpose — `mail_preview`
    builds its samples inside a transaction it discards, and an on-commit
    hook there would never fire.

    Returns True when the message was sent, or accepted for sending. Callers
    are not expected to care; the tests are what read it.
    """
    addresses = [address for address in (to if isinstance(to, (list, tuple)) else [to]) if address]
    if not addresses:
        return False

    # Rendered here, on the caller's thread, and deliberately not inside the
    # try below: a template that references a field nobody set is a code
    # fault and should be loud, not swallowed as a delivery failure.
    body = render_to_string(
        f"email/{template}.txt",
        {**(context or {}), "site_url": (getattr(settings, "SITE_URL", "") or "").rstrip("/")},
    )
    full_subject = f"A1 360 — {subject}"

    # Under test, send inline: locmem touches no network, and every test that
    # reads mail.outbox straight after the call depends on it being there.
    if not defer or getattr(settings, "TESTING", False):
        return _deliver(full_subject, body, addresses, template)

    # Outside an atomic block this runs immediately, which is what we want.
    transaction.on_commit(
        lambda: _deliver_in_background(full_subject, body, addresses, template)
    )
    return True
