"""
Every mutation of the access-control tables goes through here. The guard
rails in section 5 of the brief are enforced in these functions rather than
in the views, so a second entry point (admin, API, management command)
cannot bypass them.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from . import audit
from .decorators import user_has_permission
from .models import Permission, Role, RolePermission, SCOPE_ALL, UserRole
from .permissions import forget_permissions
from .permission_registry import PERMISSION_CODES

# Holding this permission is what makes an account an administrator: it is
# the power to grant power. "Last admin" is defined against it rather than
# against a role named "Admin", which an admin is free to rename.
ADMIN_PERMISSION = "manage_roles"


def _role_snapshot(role):
    return {
        "name": role.name,
        "is_system": role.is_system,
        "grants": sorted(
            f"{rp.permission.code}:{rp.scope}"
            for rp in RolePermission.objects.filter(role=role).select_related("permission")
        ),
    }


def _check_grantable(actor, grants):
    """
    Only a permission that already exists can be granted, and no role may
    grant a permission its creator does not itself hold. An admin composes
    roles; they do not invent new powers.
    """
    for code, _scope in grants:
        if code not in PERMISSION_CODES:
            raise ValidationError(f"'{code}' is not a permission in this system.")
        if not user_has_permission(actor, code):
            raise PermissionDenied(
                f"You cannot grant '{code}' because you do not hold it yourself."
            )


def admin_user_ids():
    return set(
        UserRole.objects.filter(role__permissions__code=ADMIN_PERMISSION)
        .values_list("user_id", flat=True)
        .distinct()
    )


def _assert_not_last_admin(user):
    admins = admin_user_ids()
    if user.pk in admins and len(admins) <= 1:
        raise ValidationError(
            "This is the last remaining administrator — it cannot be removed or demoted."
        )


@transaction.atomic
def create_role(*, actor, name, grants):
    """`grants` is an iterable of (permission_code, scope)."""
    if not user_has_permission(actor, "manage_roles"):
        raise PermissionDenied("missing permission: manage_roles")
    grants = list(grants)
    _check_grantable(actor, grants)
    if Role.objects.filter(name__iexact=name).exists():
        raise ValidationError(f"A role named '{name}' already exists.")

    role = Role.objects.create(name=name, is_system=False)
    _write_grants(role, grants)
    audit.record_change(
        actor=actor,
        action="role.create",
        target=role,
        before=None,
        after=_role_snapshot(role),
        reason="Role created in Settings",
    )
    return role


@transaction.atomic
def set_role_permissions(*, actor, role, grants, reason=""):
    if not user_has_permission(actor, "manage_roles"):
        raise PermissionDenied("missing permission: manage_roles")
    grants = list(grants)
    _check_grantable(actor, grants)

    before = _role_snapshot(role)
    granted_codes = {code for code, _ in grants}
    if role.is_system and role.name == "Admin" and ADMIN_PERMISSION not in granted_codes:
        raise ValidationError("The Admin role cannot have its role-management permission removed.")

    RolePermission.objects.filter(role=role).delete()
    _write_grants(role, grants)
    # Whoever is making the change may hold this role themselves.
    forget_permissions(actor)
    audit.record_change(
        actor=actor,
        action="role.permissions_changed",
        target=role,
        before=before,
        after=_role_snapshot(role),
        reason=reason,
    )
    return role


def _write_grants(role, grants):
    lookup = {p.code: p for p in Permission.objects.filter(code__in=[c for c, _ in grants])}
    RolePermission.objects.bulk_create(
        [
            RolePermission(role=role, permission=lookup[code], scope=scope or SCOPE_ALL)
            for code, scope in grants
            if code in lookup
        ]
    )


def assignable_roles(actor):
    """
    The roles this actor may put on somebody else: the ones whose every
    permission the actor already holds.

    Creating a role has always been guarded this way. Assigning one was not,
    which left the guard easy to walk around -- anyone who could manage users
    could hand out the Admin role and inherit it back. Composing power and
    conferring it are the same act, so they answer to the same rule.
    """
    return [
        role
        for role in Role.objects.prefetch_related("permissions").order_by("-is_system", "name")
        if can_grant_role(actor, role)
    ]


def can_grant_role(actor, role):
    return all(
        user_has_permission(actor, permission.code)
        for permission in role.permissions.all()
    )


@transaction.atomic
def assign_role(*, actor, user, role, reason=""):
    if not user_has_permission(actor, "manage_users"):
        raise PermissionDenied("missing permission: manage_users")
    if not can_grant_role(actor, role):
        raise PermissionDenied(
            f"You cannot assign '{role.name}' because it holds permissions you "
            "do not hold yourself."
        )
    before = {"roles": sorted(r.role.name for r in user.user_roles.all())}
    UserRole.objects.get_or_create(user=user, role=role)
    # The map is stale the moment a grant changes.
    forget_permissions(user)
    audit.record_change(
        actor=actor,
        action="user.role_assigned",
        target=user,
        before=before,
        after={"roles": sorted(r.role.name for r in user.user_roles.all())},
        reason=reason,
    )


@transaction.atomic
def revoke_role(*, actor, user, role, reason=""):
    if not user_has_permission(actor, "manage_users"):
        raise PermissionDenied("missing permission: manage_users")
    before = {"roles": sorted(r.role.name for r in user.user_roles.all())}
    # Check the demotion against the state *after* removal.
    would_lose_admin = role.permissions.filter(code=ADMIN_PERMISSION).exists()
    if would_lose_admin:
        remaining = UserRole.objects.filter(
            user=user, role__permissions__code=ADMIN_PERMISSION
        ).exclude(role=role)
        if not remaining.exists():
            _assert_not_last_admin(user)

    UserRole.objects.filter(user=user, role=role).delete()
    forget_permissions(user)
    audit.record_change(
        actor=actor,
        action="user.role_revoked",
        target=user,
        before=before,
        after={"roles": sorted(r.role.name for r in user.user_roles.all())},
        reason=reason,
    )


# No 0/O, 1/l/I: a temporary password is read aloud or copied off a screen
# by hand, and a character that could be two characters locks somebody out.
TEMPORARY_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def temporary_password():
    from django.utils.crypto import get_random_string

    return get_random_string(12, TEMPORARY_PASSWORD_ALPHABET)


@transaction.atomic
def issue_temporary_password(*, actor, user, reason=""):
    """
    For somebody who has forgotten their password or locked themselves out.

    Returns the new temporary password, which the caller shows once. The
    person must replace it at their next sign-in, and every session they
    had open ends, since each was signed with the old password.

    Setting somebody's password is signing in as them, so the same rule as
    granting a role applies: you may only do it to an account whose every
    permission you already hold. Otherwise anyone who manages users could
    reset an Admin's password and walk in as that Admin.
    """
    if not user_has_permission(actor, "manage_users"):
        raise PermissionDenied("missing permission: manage_users")
    if user.pk == actor.pk:
        raise ValidationError(
            "Change your own password from My profile, where it asks for your current one."
        )
    if not user.is_active:
        raise ValidationError("This account is deactivated. Reactivate it first.")

    held_by_target = set(
        Permission.objects.filter(role__user_roles__user=user).values_list("code", flat=True)
    )
    beyond_actor = sorted(c for c in held_by_target if not user_has_permission(actor, c))
    if beyond_actor:
        raise PermissionDenied(
            "You cannot reset this password: the account holds permissions you "
            "do not hold yourself ({}).".format(", ".join(beyond_actor))
        )

    before = {
        "must_reset_password": user.must_reset_password,
        "locked": user.is_locked_out(),
    }
    temporary = temporary_password()
    user.set_password(temporary)
    user.must_reset_password = True
    user.failed_login_attempts = 0
    user.locked_until = None
    user.save(update_fields=["password", "must_reset_password", "failed_login_attempts", "locked_until"])
    # The password itself is never recorded, only that one was issued.
    audit.record_change(
        actor=actor,
        action="user.temporary_password_issued",
        target=user,
        before=before,
        after={"must_reset_password": True, "locked": False},
        reason=reason,
    )
    return temporary


@transaction.atomic
def deactivate_user(*, actor, user, reason=""):
    """Accounts are deactivated, never deleted — history stays intact."""
    if not user_has_permission(actor, "manage_users"):
        raise PermissionDenied("missing permission: manage_users")
    _assert_not_last_admin(user)
    before = {"is_active": user.is_active}
    user.is_active = False
    user.save(update_fields=["is_active"])
    audit.record_change(
        actor=actor,
        action="user.deactivated",
        target=user,
        before=before,
        after={"is_active": False},
        reason=reason,
    )
