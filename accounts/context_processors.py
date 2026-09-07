from .navigation import active_module, active_tab, visible_items, visible_tabs
from .permission_registry import PERMISSION_CODES


class PermissionLookup:
    """
    Template helper: `{% if perms_held.issue_invoice %}`. Only codes in the
    frozen registry resolve, so a template typo hides the control rather
    than silently showing it to everyone.
    """

    def __init__(self, user):
        self.user = user
        self._cache = {}

    def __getitem__(self, code):
        if code not in PERMISSION_CODES:
            return False
        if code not in self._cache:
            from .decorators import user_has_permission

            self._cache[code] = user_has_permission(self.user, code)
        return self._cache[code]

    def __contains__(self, code):
        return bool(self[code])


def nav(request):
    user = getattr(request, "user", None)
    if user is None:
        return {}
    match = getattr(request, "resolver_match", None)

    # The bell in the header. Counted rather than built, because this runs on
    # every page load.
    attention = 0
    if user.is_authenticated:
        from dashboard.services import attention_count

        attention = attention_count(user)

    # What to call this person under their name in the header. Their roles,
    # because the system has no job titles of its own — a role IS what they
    # can do here.
    role_label = ""
    if user.is_authenticated:
        names = list(user.user_roles.values_list("role__name", flat=True))
        role_label = ", ".join(names) if names else "No role assigned"
    return {
        "nav_items": visible_items(user),
        "nav_tabs": visible_tabs(user),
        # The module the current screen belongs to, so a detail page or a
        # form still lights up its module in the sidebar.
        "nav_active": active_module(getattr(match, "url_name", "")),
        "tab_active": active_tab(getattr(match, "url_name", "")),
        "attention_count": attention,
        "user_role_label": role_label,
        "perms_held": PermissionLookup(user),
    }
