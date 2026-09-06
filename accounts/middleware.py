from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordResetMiddleware:
    """
    A new account is created with `must_reset_password` set, so the first
    thing anyone does with a password an admin typed for them is replace
    it. Enforced here rather than per-view so no screen can be reached by
    URL before the reset is done.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_reset_password:
            allowed = {
                reverse("password-reset-required"),
                reverse("logout"),
            }
            if request.path not in allowed and not request.path.startswith("/static/"):
                return redirect("password-reset-required")
        return self.get_response(request)
