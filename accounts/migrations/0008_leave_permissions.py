"""
Leave management, and who holds what.

`seed_permissions` deliberately leaves an existing role's grants alone, so a
deploy that only reseeds the registry would create the six leave permissions
and give them to nobody. The screens would be live and unreachable. This
migration adds the grants instead, additively: nothing already ticked on a
role in Settings is touched, which matters because the live system has had
its system roles amended by hand.

Who decides is A-1's decision, taken explicitly: **HR approves leave, not
supervisors.** One desk that can see the balance, rather than a supervisor
approving days the entitlement does not cover. A supervisor still reads
their own crew's leave, because a supervisor who cannot see that a
technician is away next week will schedule them into it.

Asking for leave is a floor, like clocking in: every pre-built role gets it,
including the ones whose holders are office staff rather than crew.
"""
from django.db import migrations

SCOPE_ALL = "all"
SCOPE_OWN_TEAM = "own_team"

PERMISSIONS = [
    ("request_leave", "hr", "Put in for leave, and view own leave history and balance"),
    ("view_leave", "hr", "View leave requests and who is away"),
    ("approve_leave", "hr", "Approve or decline a leave request"),
    ("manage_leave", "hr", "Record or cancel leave on somebody's behalf, and adjust a balance"),
    ("manage_leave_types", "hr", "Manage leave types, entitlement rules and the public holiday list"),
    ("view_leave_reports", "hr", "View leave balances and entitlement across the company"),
]

# Everybody can ask. Admin is listed explicitly rather than assumed, because
# the Admin role's grants are generated from the registry at seed time and a
# live database may predate this code.
ASKS = [
    "Admin", "Executive", "Finance", "HR", "Receptionist",
    "Supervisor", "Project Manager", "Technician", "Employee",
]

GRANTS = [
    # (code, scope, roles)
    ("request_leave", SCOPE_ALL, ASKS),
    ("view_leave", SCOPE_ALL, ["Admin", "HR", "Executive"]),
    # Their own crew, read-only — the same shape as their attendance grant.
    ("view_leave", SCOPE_OWN_TEAM, ["Supervisor"]),
    ("approve_leave", SCOPE_ALL, ["Admin", "HR"]),
    ("manage_leave", SCOPE_ALL, ["Admin", "HR"]),
    ("manage_leave_types", SCOPE_ALL, ["Admin", "HR"]),
    ("view_leave_reports", SCOPE_ALL, ["Admin", "HR", "Executive"]),
]


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    for code, module, description in PERMISSIONS:
        Permission.objects.get_or_create(
            code=code, defaults={"module": module, "description": description}
        )

    lookup = {p.code: p for p in Permission.objects.filter(
        code__in=[code for code, _m, _d in PERMISSIONS]
    )}

    for code, scope, role_names in GRANTS:
        permission = lookup.get(code)
        if permission is None:
            continue
        for name in role_names:
            role = Role.objects.filter(name=name).first()
            if role is None:
                continue
            # get_or_create on (role, permission) alone: a role that somehow
            # already holds this at a different scope keeps the scope it has,
            # because widening somebody's access silently is the one thing a
            # permission migration must not do.
            RolePermission.objects.get_or_create(
                role=role, permission=permission, defaults={"scope": scope}
            )


def withdraw(apps, schema_editor):
    RolePermission = apps.get_model("accounts", "RolePermission")
    RolePermission.objects.filter(
        permission__code__in=[code for code, _m, _d in PERMISSIONS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("accounts", "0007_create_project")]

    operations = [migrations.RunPython(grant, withdraw)]
