"""
A drifted role heals itself on the next deploy.

The gap this closes, in A-1's words: an employee who cannot put in for leave,
and a technician with no way to tick a task done. The cause was never the
screens — it was the live database. New grants reach an existing role only
through a one-shot migration, and `seed_permissions` deliberately leaves an
existing role's grants alone so an admin's own additions survive a deploy.
Between the two, a role created or edited by hand AFTER the leave and
field-staff migrations ran could be missing `request_leave` or
`view_own_job_list` for good, because the migration never runs again.

`ROLE_FLOORS` is the documented minimum each role must always carry, and
`seed_permissions` now re-asserts it every deploy — additively, removing
nothing, so this is not `--reset-system-roles` in disguise. These tests hold
that it heals what drifted and touches nothing else.
"""
from django.core.management import call_command
from django.test import TestCase

from accounts.models import Permission, Role, RolePermission
from accounts.seed_data import ROLE_FLOORS


class FloorHealingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # A realistic live database: the permission list and roles already
        # exist, exactly as a running system has them.
        call_command("seed_permissions", verbosity=0)

    def _codes(self, role_name):
        role = Role.objects.get(name=role_name)
        return set(
            RolePermission.objects.filter(role=role).values_list(
                "permission__code", flat=True
            )
        )

    def test_a_field_role_that_lost_request_leave_gets_it_back(self):
        """The reported symptom: an employee who cannot ask for leave."""
        role = Role.objects.get(name="Employee")
        RolePermission.objects.filter(
            role=role, permission__code="request_leave"
        ).delete()
        self.assertNotIn("request_leave", self._codes("Employee"))

        call_command("seed_permissions", verbosity=0)
        self.assertIn("request_leave", self._codes("Employee"))

    def test_a_field_role_that_lost_the_job_list_gets_it_back(self):
        """The other half: no `view_own_job_list` is no way to tick a task done."""
        role = Role.objects.get(name="Technician")
        RolePermission.objects.filter(
            role=role, permission__code="view_own_job_list"
        ).delete()

        call_command("seed_permissions", verbosity=0)
        self.assertIn("view_own_job_list", self._codes("Technician"))

    def test_every_pre_built_role_can_ask_for_leave(self):
        """Asking for leave is a floor like clocking in — everyone holds it."""
        for role_name in ROLE_FLOORS:
            RolePermission.objects.filter(
                role__name=role_name, permission__code="request_leave"
            ).delete()
        call_command("seed_permissions", verbosity=0)
        for role_name in ROLE_FLOORS:
            with self.subTest(role=role_name):
                self.assertIn("request_leave", self._codes(role_name))

    def test_healing_removes_nothing_an_admin_added(self):
        """
        The whole reason seed_permissions leaves existing roles alone: an
        admin's own additions in Settings must survive a deploy. Healing the
        floor must not undo that.
        """
        role = Role.objects.get(name="Employee")
        extra = Permission.objects.get(code="view_customers")
        RolePermission.objects.create(role=role, permission=extra, scope="all")

        call_command("seed_permissions", verbosity=0)
        self.assertIn("view_customers", self._codes("Employee"))

    def test_healing_does_not_widen_a_scope_already_set(self):
        """
        A floor grant the role holds at a narrower scope is left exactly as
        it is. Widening access silently is the one thing this must never do.
        """
        role = Role.objects.get(name="Employee")
        rp = RolePermission.objects.get(
            role=role, permission__code="view_projects"
        )
        rp.scope = "own_projects"
        rp.save(update_fields=["scope"])

        call_command("seed_permissions", verbosity=0)
        rp.refresh_from_db()
        self.assertEqual(rp.scope, "own_projects")

    def test_a_role_with_its_floor_intact_is_left_untouched(self):
        """A healthy system heals nothing — the pass is a no-op there."""
        before = {
            name: self._codes(name) for name in ROLE_FLOORS
        }
        call_command("seed_permissions", verbosity=0)
        after = {name: self._codes(name) for name in ROLE_FLOORS}
        self.assertEqual(before, after)

    def test_the_field_floor_matches_what_the_role_is_meant_to_grant(self):
        """
        The floor for Employee and Technician is their documented grant set,
        not a smaller subset that would heal half a technician.
        """
        from accounts.seed_data import ROLE_GRANTS

        for role_name in ("Employee", "Technician"):
            with self.subTest(role=role_name):
                floor = {code for code, _scope in ROLE_FLOORS[role_name]}
                grants = {code for code, _scope in ROLE_GRANTS[role_name]}
                self.assertEqual(floor, grants)
