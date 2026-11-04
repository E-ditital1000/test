"""
Writes the frozen permission list and the pre-built roles into the
database. Idempotent: safe to run on every deploy, and the authoritative
direction is always registry -> database, never the reverse.

Permissions that disappear from the registry are reported rather than
deleted, because a role may still grant them and silently dropping a row
would silently change who can do what.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Permission, Role, RolePermission
from accounts.permission_registry import PERMISSIONS
from accounts.seed_data import ROLE_FLOORS, ROLE_GRANTS


class Command(BaseCommand):
    help = "Seed the permission list and the pre-built roles from the frozen registry."

    quiet = False

    def _say(self, message):
        if not self.quiet:
            self.stdout.write(message)

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-system-roles",
            action="store_true",
            help="Rewrite the grants on pre-built roles to match the registry. "
            "Custom roles created by an admin are never touched.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # Called from test fixtures with verbosity=0. Honouring it keeps the
        # acceptance gate's own output readable.
        self.quiet = options.get("verbosity", 1) == 0
        created, updated = 0, 0
        for code, module, description in PERMISSIONS:
            permission, was_created = Permission.objects.update_or_create(
                code=code, defaults={"module": module, "description": description}
            )
            created += int(was_created)
            updated += int(not was_created)

        self._say(
            self.style.SUCCESS(f"Permissions: {created} created, {updated} refreshed.")
        )

        registry_codes = {code for code, _m, _d in PERMISSIONS}
        orphans = Permission.objects.exclude(code__in=registry_codes)
        if orphans.exists():
            self._say(
                self.style.WARNING(
                    "These permissions are in the database but no longer in the "
                    "registry — decide explicitly before removing them: "
                    + ", ".join(orphans.values_list("code", flat=True))
                )
            )

        lookup = {p.code: p for p in Permission.objects.all()}
        for role_name, grants in ROLE_GRANTS.items():
            role, was_created = Role.objects.get_or_create(
                name=role_name, defaults={"is_system": True}
            )
            if not was_created and not options["reset_system_roles"]:
                self._say(f"  role '{role_name}' exists — grants left as they are.")
                continue
            RolePermission.objects.filter(role=role).delete()
            RolePermission.objects.bulk_create(
                [
                    RolePermission(role=role, permission=lookup[code], scope=scope)
                    for code, scope in grants
                    if code in lookup
                ]
            )
            verb = "created" if was_created else "reset"
            self._say(self.style.SUCCESS(f"  role '{role_name}' {verb} ({len(grants)} grants)."))

        # Ensure the floor on every pre-built role, additively.
        #
        # This runs whether or not --reset-system-roles was passed, and
        # removes nothing. It is what heals a role that drifted — created or
        # edited by hand after the leave/field-staff migrations ran, and so
        # silently missing `request_leave` or `view_own_job_list`, which is an
        # employee who cannot ask for leave or tick a task done. See
        # accounts.seed_data.ROLE_FLOORS for why this is separate from the
        # reset above.
        restored = 0
        for role_name, floor in ROLE_FLOORS.items():
            role = Role.objects.filter(name=role_name).first()
            if role is None:
                continue
            for code, scope in floor:
                permission = lookup.get(code)
                if permission is None:
                    continue
                # get_or_create on (role, permission) alone: a role that holds
                # this at another scope keeps it. Never widen silently.
                _rp, made = RolePermission.objects.get_or_create(
                    role=role, permission=permission, defaults={"scope": scope}
                )
                if made:
                    restored += 1
                    self._say(
                        self.style.WARNING(
                            f"  floor: restored '{code}' on '{role_name}' "
                            "(was missing — a drifted role)."
                        )
                    )
        if restored:
            self._say(
                self.style.SUCCESS(
                    f"Floors: restored {restored} missing grant(s) so field "
                    "staff can clock, ask for leave and finish their work."
                )
            )
