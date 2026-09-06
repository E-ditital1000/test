"""
Startup guards on the frozen contracts. These turn "a permission code was
invented in a decorator" and "a pre-built role grants a code that does not
exist" into a failure at `manage.py check`, not a silent hole in access
control discovered in production.
"""
from django.core.checks import Error, register

from .permission_registry import PERMISSION_CODES
from .seed_data import ROLE_GRANTS


@register()
def role_grants_use_known_permissions(app_configs, **kwargs):
    errors = []
    for role_name, grants in ROLE_GRANTS.items():
        for code, _scope in grants:
            if code not in PERMISSION_CODES:
                errors.append(
                    Error(
                        f"Pre-built role '{role_name}' grants unknown permission '{code}'.",
                        hint="Add the code to accounts.permission_registry.PERMISSIONS, "
                        "or correct the grant in accounts.seed_data.",
                        id="accounts.E001",
                    )
                )
    return errors


@register()
def navigation_uses_known_permissions(app_configs, **kwargs):
    from .navigation import MAX_TABS, MOBILE_TABS, NAV_ITEMS

    errors = [
        Error(
            f"Navigation item '{item.label}' requires unknown permission '{code}'.",
            hint="Navigation is built from the frozen permission list.",
            id="accounts.E002",
        )
        for item in NAV_ITEMS + MOBILE_TABS
        for code in item.permissions
        if code not in PERMISSION_CODES
    ]
    if len(MOBILE_TABS) > MAX_TABS:
        errors.append(
            Error(
                f"{len(MOBILE_TABS)} mobile tabs are defined but the design "
                f"specification caps the bar at {MAX_TABS}.",
                hint="A fifth tab pushes every target out of comfortable "
                "one-handed reach. Move the surface inside a tab instead.",
                id="accounts.E003",
            )
        )
    return errors
