"""
The office can see the whole field job schedule, not only its own jobs.

Field Jobs had one screen — a technician's own day — so an Admin opening it
was told "No jobs assigned today" and shown nothing, however much work was
out there. Seeing what the company has scheduled, and who scheduled it, is a
different question from carrying a job, so it is a different permission.

Granted to the roles that already run the work: Admin, Executive, who read
everything, and Supervisor and Project Manager, who put the jobs on phones
in the first place. A technician is deliberately not given it — their own
list is their screen, and this one is the office's.

Added to the existing roles in place. `seed_permissions` creates the
permission row itself on the next deploy, but only rewrites a pre-built
role's grants under --reset-system-roles, which would discard whatever an
Admin has since changed in Settings.
"""
from django.db import migrations

CODE = "view_field_jobs"
MODULE = "fieldjobs"
DESCRIPTION = "View the field job schedule across the company"
SCOPE_ALL = "all"
ROLES = ["Admin", "Executive", "Supervisor", "Project Manager"]


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    permission, _ = Permission.objects.get_or_create(
        code=CODE, defaults={"module": MODULE, "description": DESCRIPTION}
    )
    for name in ROLES:
        role = Role.objects.filter(name=name).first()
        # A database seeded later gets these from the registry instead.
        if role is None:
            continue
        RolePermission.objects.get_or_create(
            role=role, permission=permission, defaults={"scope": SCOPE_ALL}
        )


def withdraw(apps, schema_editor):
    """The grants this migration would have made. The permission row itself
    stays: `seed_permissions` reports an orphan rather than deleting one,
    and a role an Admin has since granted it to is not this migration's to
    take it from."""
    RolePermission = apps.get_model("accounts", "RolePermission")
    RolePermission.objects.filter(
        role__name__in=ROLES, permission__code=CODE, scope=SCOPE_ALL
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("accounts", "0004_technician_sees_own_projects")]

    operations = [migrations.RunPython(grant, withdraw)]
