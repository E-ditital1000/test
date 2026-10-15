"""
Coming back to where you pressed the button.

Several screens list work that lives somewhere else — "My work" holds tasks
and tickets from every project, the roll-call links into one person's
record — so the action carries a `next` saying where to return. Without it
somebody who ticks a task off their own dashboard lands on a project page
they may not even be allowed to open.

`next` arrives in the request body, which is to say from outside. Handing
it straight to `redirect()` lets a form posted from anywhere send a signed-in
person to another site wearing our address bar, which is how a convincing
sign-in page gets in front of somebody. Django ships the check; this is the
one place that calls it, so no screen has to remember to.
"""
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def back_to(request, fallback, *args):
    """
    Redirect to the request's `next`, or to `fallback` when there is none
    and whenever `next` points off this site.

    `fallback` is a URL or a view name, with any arguments after it — the
    same shapes `redirect()` already takes.
    """
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect(fallback, *args)
