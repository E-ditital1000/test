from rest_framework.permissions import BasePermission

from .decorators import user_has_permission
from .models import SCOPE_ALL
from .scoping import user_scopes_for


def HasScopedPermission(code, object_scope_check=None):
    """
    DRF permission class factory for the two mobile-sync surfaces.

    `object_scope_check(user, obj, scope) -> bool` is supplied by the view
    for object-level scope enforcement (e.g. "is this attendance event's
    employee on my team"). Without it, only route-level access is checked.
    """

    class _HasScopedPermission(BasePermission):
        def has_permission(self, request, view):
            return bool(request.user and user_has_permission(request.user, code))

        def has_object_permission(self, request, view, obj):
            if request.user.is_superuser:
                return True
            scopes = user_scopes_for(request.user, code)
            if not scopes:
                return False
            if SCOPE_ALL in scopes:
                return True
            if object_scope_check is None:
                return False
            return any(object_scope_check(request.user, obj, scope) for scope in scopes)

    return _HasScopedPermission
