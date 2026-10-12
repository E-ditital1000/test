"""
Let the people who do the work be given the work.

A-1 reported that their technicians' names were missing from the ticket
assignment picker. The picker offers whoever holds `view_own_job_list`;
their nine field staff were all on the Employee role, which carried
clocking, leave and the dashboard and nothing else. So every one of them was
filtered out, the list quietly offered office accounts instead, and nothing
on the screen said why.

A-1's rule: "there is no way we have a technician who is not an employee."
So the Employee role carries everything a technician needs — the job list,
GPS check-in, submitting assessments, and raising requisitions against the
projects they are crewed on. Technician becomes the same set under a name
the office recognises. Holding the two apart was my distinction and not the
company's.

Requisitions and project visibility are scoped to own projects, so somebody
not crewed on anything holds the right and reaches nothing. That is what
makes it safe on a role every account starts with.

The job list goes to every pre-built role, because a role may only be
granted by somebody who already holds every permission in it. HR gets the
rest of the technician floor for the same reason: without it HR cannot
create an Employee, which is HR's entire job, and the symptom is an
onboarding form that silently refuses to submit.

`seed_permissions` leaves an existing role's grants alone, by design, so a
reseed would never have applied this to the live Employee role — the people
it is for would have stayed unassignable. Hence a migration, additive, in
the same shape as 0008.
"""
from django.db import migrations

SCOPE_ALL = "all"
SCOPE_OWN_PROJECTS = "own_projects"

# Everybody can be sent to a job, so everybody can see their own job list.
CODE = "view_own_job_list"

# What makes an employee a technician. Granted to the floor role, and to HR
# so that HR can still grant the floor role.
TECHNICIAN_FLOOR = [
    ("gps_check_in", SCOPE_ALL),
    ("submit_assessment", SCOPE_ALL),
    ("view_projects", SCOPE_OWN_PROJECTS),
    ("raise_requisition", SCOPE_OWN_PROJECTS),
]
DOES_THE_WORK = ["Employee", "HR"]
# Every pre-built role. Admin and Technician already hold it; get_or_create
# leaves those alone. A role an administrator invented in Settings is not
# touched — their grants are theirs.
ROLES = [
    "Admin", "Executive", "Finance", "HR", "Receptionist",
    "Supervisor", "Project Manager", "Technician", "Employee",
]


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    permission = Permission.objects.filter(code=CODE).first()
    if permission is None:
        # A database that predates the permission is seeded from the registry
        # on its next deploy, which carries the grant anyway.
        return
    for name in ROLES:
        role = Role.objects.filter(name=name).first()
        if role is None:
            continue
        RolePermission.objects.get_or_create(
            role=role, permission=permission, defaults={"scope": SCOPE_ALL}
        )

    for name in DOES_THE_WORK:
        role = Role.objects.filter(name=name).first()
        if role is None:
            continue
        for code, scope in TECHNICIAN_FLOOR:
            extra = Permission.objects.filter(code=code).first()
            if extra is None:
                continue
            RolePermission.objects.get_or_create(
                role=role, permission=extra, defaults={"scope": scope}
            )


def withdraw(apps, schema_editor):
    RolePermission = apps.get_model("accounts", "RolePermission")
    # Technician and Admin held this before the migration, so reversing must
    # not strip it from them.
    RolePermission.objects.filter(
        role__name__in=[r for r in ROLES if r not in ("Admin", "Technician")],
        permission__code=CODE,
        scope=SCOPE_ALL,
    ).delete()
    RolePermission.objects.filter(
        role__name__in=DOES_THE_WORK,
        permission__code__in=[code for code, _scope in TECHNICIAN_FLOOR],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("accounts", "0008_leave_permissions")]

    operations = [migrations.RunPython(grant, withdraw)]
