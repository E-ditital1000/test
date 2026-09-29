"""
A technician can open the projects they are on.

They already carried work belonging to a project and could raise a
requisition against one, but held no right to open it — so the job they were
sent to do had no context they could reach, and Projects was absent from
their shell entirely.

Granted at "own projects" scope, which is the ones they are crewed on or
manage. The scoping is what does the limiting, exactly as it does for a
Project Manager, so no screen has to know a technician is reading it.

Applied to the role in place rather than by re-seeding: `seed_permissions`
only rewrites a pre-built role under --reset-system-roles, and that would
discard whatever an Admin has since changed in Settings on every other role
too. A grant already there is left alone for the same reason — an Admin who
has deliberately set a different scope keeps it.
"""
from django.db import migrations

ROLE = "Technician"
CODE = "view_projects"
SCOPE_OWN_PROJECTS = "own_projects"


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    role = Role.objects.filter(name=ROLE).first()
    permission = Permission.objects.filter(code=CODE).first()
    # A database seeded later gets this from the registry instead.
    if role is None or permission is None:
        return
    RolePermission.objects.get_or_create(
        role=role, permission=permission, defaults={"scope": SCOPE_OWN_PROJECTS}
    )


def withdraw(apps, schema_editor):
    """Only the grant this migration would have made, at the scope it used."""
    RolePermission = apps.get_model("accounts", "RolePermission")
    RolePermission.objects.filter(
        role__name=ROLE, permission__code=CODE, scope=SCOPE_OWN_PROJECTS
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("accounts", "0003_profile_show_birthday")]

    operations = [migrations.RunPython(grant, withdraw)]
