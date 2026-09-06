from django.db.models import Q

from .models import SCOPE_ALL, SCOPE_OWN_PROJECTS, SCOPE_OWN_TEAM


def user_scopes_for(user, code):
    """
    The set of scopes the user's roles grant for this permission code, or
    an empty set if they don't hold it at all. A user can in principle hold
    the same code at more than one scope via different roles.
    """
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {SCOPE_ALL}
    return set(
        user.user_roles.filter(role__permissions__code=code)
        .values_list("role__rolepermission__scope", flat=True)
        .distinct()
    )


def apply_scope(queryset, user, code, *, own_team_filter=None, own_projects_filter=None):
    """
    Filter `queryset` per the scope(s) the user holds for `code`. Scoping
    has no universal FK path across Customer/Project/Employee, so each call
    site supplies how "own team" / "own projects" translates to a Q for
    *its* model (e.g. own_team_filter=Q(employee__supervisor=user)).

    This is never implicit — every scoped list/detail view must call it
    explicitly, so scope can't be silently skipped by a view that forgets.
    """
    if user.is_superuser:
        return queryset
    scopes = user_scopes_for(user, code)
    if not scopes:
        return queryset.none()
    if SCOPE_ALL in scopes:
        return queryset

    q = Q()
    matched = False
    if SCOPE_OWN_TEAM in scopes and own_team_filter is not None:
        q |= own_team_filter
        matched = True
    if SCOPE_OWN_PROJECTS in scopes and own_projects_filter is not None:
        q |= own_projects_filter
        matched = True
    if not matched:
        return queryset.none()
    return queryset.filter(q).distinct()
