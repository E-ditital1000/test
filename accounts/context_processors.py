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
    return {
        "nav_items": visible_items(user),
        "nav_tabs": visible_tabs(user),
        # The module the current screen belongs to, so a detail page or a
        # form still lights up its module in the sidebar.
        "nav_active": active_module(getattr(match, "url_name", "")),
        "tab_active": active_tab(getattr(match, "url_name", "")),
        "perms_held": PermissionLookup(user),
    }
