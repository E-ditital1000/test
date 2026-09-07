"""
The permission map for one user, loaded once.

Every screen asks "does this user hold this permission", and most screens ask
several times — the sidebar alone asks eight. Each of those was a separate
round trip to the database, so a dashboard cost sixty queries against ten
tickets. The answers cannot change midway through a request, so they are
fetched once and read from memory after that.

The cache lives on the user instance. Django builds a fresh one per request,
so it expires exactly when it should without anything having to clear it. The
one case that needs care is code which grants a permission and then checks it
against the same in-memory object — `accounts.services` calls
`forget_permissions` after every change for that reason.
"""
from .models import SCOPE_ALL, RolePermission
from .permission_registry import PERMISSION_CODES

CACHE_ATTR = "_a1_permission_map"


def permission_map(user):
    """
    `{code: {scope, ...}}` for this user.

    A code can appear at more than one scope when two of the user's roles
    grant it differently — holding "correct attendance" for their own team
    through one role and for everyone through another.
    """
    cached = getattr(user, CACHE_ATTR, None)
    if cached is not None:
        return cached

    if not getattr(user, "is_authenticated", False):
        mapping = {}
    elif user.is_superuser:
        # A superuser bypasses the model entirely, and always has.
        mapping = {code: {SCOPE_ALL} for code in PERMISSION_CODES}
    else:
        mapping = {}
        rows = RolePermission.objects.filter(
            role__user_roles__user=user
        ).values_list("permission__code", "scope")
        for code, scope in rows:
            mapping.setdefault(code, set()).add(scope)

    setattr(user, CACHE_ATTR, mapping)
    return mapping


def forget_permissions(user):
    """
    Drop the cached map after a grant changes.

    Only matters within one request — the next one loads a fresh user — but
    without it, code that grants a role and then checks it on the same object
    reads the answer from before the change.
    """
    if hasattr(user, CACHE_ATTR):
        delattr(user, CACHE_ATTR)
