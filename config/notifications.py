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

from django.conf import settings
from django.core.mail import EmailMessage
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


def send(*, to, subject, template, context=None):
    """
    Send one message, or do not, and never raise either way.

    `template` names a file under templates/email/ without its extension.
    Returns True when it went, which is what the tests assert on — callers
    are not expected to care.
    """
    addresses = [address for address in (to if isinstance(to, (list, tuple)) else [to]) if address]
    if not addresses:
        return False

    body = render_to_string(
        f"email/{template}.txt",
        {**(context or {}), "site_url": (getattr(settings, "SITE_URL", "") or "").rstrip("/")},
    )
    try:
        EmailMessage(
            subject=f"A1 360 — {subject}",
            body=body,
            to=addresses,
        ).send(fail_silently=False)
        return True
    except Exception:
        # Deliberately broad: a bad address, a refused login, a server that
        # is simply down — none of them is the caller's problem, and none of
        # them should undo work that has already been recorded.
        logger.exception("Could not send '%s' to %s", template, ", ".join(addresses))
        return False
