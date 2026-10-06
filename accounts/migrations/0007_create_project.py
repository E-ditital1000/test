"""
Some work does not begin with a phone call.

A ticket is a small job: a customer rings, or it comes off the day's
assignments. A project can be a contract — fifty kilowatts of solar across
twelve health facilities over six months, signed with an institution. That
never was a service ticket, and routing it through one would have put a
fiction at the head of the job just to satisfy the shape of the system.

Granted to whoever may already turn a ticket into a project, because the
judgement is the same one.
"""
from django.db import migrations

CODE = "create_project"
MODULE = "projects"
DESCRIPTION = "Start a project that did not come from a ticket"
SCOPE_ALL = "all"
# The roles that already hold convert_ticket_to_project.
ROLES = ["Admin", "Project Manager"]


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    permission, _ = Permission.objects.get_or_create(
        code=CODE, defaults={"module": MODULE, "description": DESCRIPTION}
    )
    for name in ROLES:
        role = Role.objects.filter(name=name).first()
        if role is None:
            continue
        RolePermission.objects.get_or_create(
            role=role, permission=permission, defaults={"scope": SCOPE_ALL}
        )


def withdraw(apps, schema_editor):
    RolePermission = apps.get_model("accounts", "RolePermission")
    RolePermission.objects.filter(
        role__name__in=ROLES, permission__code=CODE, scope=SCOPE_ALL
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("accounts", "0006_middle_name_and_departments")]

    operations = [migrations.RunPython(grant, withdraw)]
