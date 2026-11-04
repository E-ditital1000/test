"""
Who can actually do their job.

A report, not a change. For each active account it prints the roles it holds
and whether those roles carry the handful of permissions field staff need —
plus whether the account is joined to a row in the employee register, because
leave is booked against that row and an account without one cannot ask for
leave however its role is set.

This exists because "there is a permission issue" is not something you can
fix by guessing. Run it against the live database and it says exactly which
person is missing exactly which grant:

    python manage.py check_access
    python manage.py check_access --problems     # only the accounts with a gap

`seed_permissions` restores a drifted role's floor on the next deploy; this
is how you confirm it worked, and how you find the account that is on a
custom role the floor does not cover.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import RolePermission, UserRole
from hr.models import Employee

User = get_user_model()

# The grants that decide whether a field-staff account can work. Kept short
# on purpose: this is a triage tool, not the whole permission matrix.
CHECKS = [
    ("request_leave", "put in for leave"),
    ("view_own_job_list", "see their jobs / tick a task done"),
    ("view_dashboard", "reach the dashboard"),
    ("clock_in_out", "clock in and out"),
]


class Command(BaseCommand):
    help = "Report which accounts can clock, ask for leave and finish their work."

    def add_arguments(self, parser):
        parser.add_argument(
            "--problems",
            action="store_true",
            help="Show only accounts that are missing a grant or a register row.",
        )

    def handle(self, *args, **options):
        only_problems = options["problems"]
        codes = [code for code, _label in CHECKS]

        # One query for every account's held permission codes, rather than one
        # per account on a screen somebody is waiting on.
        held = {}
        for user_id, code in RolePermission.objects.filter(
            permission__code__in=codes,
            role__user_roles__user__is_active=True,
        ).values_list("role__user_roles__user_id", "permission__code"):
            held.setdefault(user_id, set()).add(code)

        roles = {}
        for user_id, name in UserRole.objects.filter(
            user__is_active=True
        ).values_list("user_id", "role__name"):
            roles.setdefault(user_id, []).append(name)

        has_register_row = set(
            Employee.objects.filter(user__is_active=True).values_list("user_id", flat=True)
        )

        flagged, total = 0, 0
        for user in User.objects.filter(is_active=True).order_by("first_name", "last_name"):
            total += 1
            mine = held.get(user.pk, set())
            missing = [label for code, label in CHECKS if code not in mine]
            no_row = user.pk not in has_register_row
            # No register row only bites leave; say so rather than listing it
            # as a flat failure.
            is_problem = bool(missing) or no_row
            if only_problems and not is_problem:
                continue
            if is_problem:
                flagged += 1

            name = user.get_full_name() or "(no name)"
            role_list = ", ".join(roles.get(user.pk, [])) or "NO ROLE"
            mark = "  " if not is_problem else "!!"
            self.stdout.write(f"{mark} {name:26} {user.email:34} [{role_list}]")
            if missing:
                self.stdout.write(f"     cannot: {'; '.join(missing)}")
            if no_row:
                self.stdout.write(
                    "     no employee-register row — cannot book leave until HR "
                    "links the account to a staff record"
                )

        self.stdout.write("")
        if only_problems:
            self.stdout.write(
                self.style.SUCCESS(f"{flagged} of {total} active accounts have a gap.")
                if flagged
                else self.style.SUCCESS(f"All {total} active accounts are fine.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"{flagged} of {total} active accounts have a gap.")
                if flagged
                else self.style.SUCCESS(f"All {total} active accounts are fine.")
            )
