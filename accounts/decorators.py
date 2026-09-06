from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .permission_registry import PERMISSION_CODES


def user_has_permission(user, code):
    """Does this user hold this permission code at all (no scope check)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.user_roles.filter(role__permissions__code=code).exists()


def require_permission(code):
    """
    Route-gate decorator: refuses the request server-side if the user does
    not hold `code`, regardless of what the client sent or what nav shows.
    Registering a code here that isn't in the frozen registry fails the
    `accounts.checks` system check at startup rather than failing silently.
    """
    assert code in PERMISSION_CODES, (
        f"'{code}' is not in accounts.permission_registry.PERMISSIONS — "
        "add it there first; the permission list is the frozen contract."
    )

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not user_has_permission(request.user, code):
                raise PermissionDenied(f"missing permission: {code}")
            return view_func(request, *args, **kwargs)

        # Stamped so the acceptance suite can walk the URLconf and prove every
        # gated screen is covered by the role matrix, rather than trusting a
        # hand-maintained list to stay in step.
        wrapper._a1_permission = code
        return wrapper

    return decorator


class PermissionRequiredMixin:
    """Class-based-view equivalent of require_permission."""

    permission_code = None

    def dispatch(self, request, *args, **kwargs):
        assert self.permission_code in PERMISSION_CODES, (
            f"'{self.permission_code}' is not in the frozen permission registry."
        )
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not user_has_permission(request.user, self.permission_code):
            raise PermissionDenied(f"missing permission: {self.permission_code}")
        return super().dispatch(request, *args, **kwargs)
