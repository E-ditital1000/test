"""
The first account on a fresh database.

`create_admin` is the only way onto a brand-new system: it reads an email and
a temporary password from the environment and makes one Admin account, so
somebody can sign in and take everyone else on. These tests hold the two
things that make it safe to run on every deploy — it grants full access, and
it never touches an account that already exists — plus the quiet no-op when
the variables are not set.
"""
import os
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounts.models import Role, UserRole
from accounts.permissions import permission_map
from accounts.permission_registry import PERMISSION_CODES

User = get_user_model()


def with_env(**values):
    """Run the command with exactly these ADMIN_* variables set."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ADMIN_")}
    clean.update(values)
    return mock.patch.dict(os.environ, clean, clear=True)


class CreateAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # A deployed system seeds permissions before this command runs.
        call_command("seed_permissions", verbosity=0)

    def test_creates_an_admin_that_can_sign_in_and_do_everything(self):
        with with_env(
            ADMIN_EMAIL="daniel@a1.lr",
            ADMIN_PASSWORD="Temp!Pass123",
            ADMIN_NAME="Daniel Padmore",
        ):
            call_command("create_admin", verbosity=0)

        user = User.objects.get(email="daniel@a1.lr")
        self.assertEqual(user.username, "daniel@a1.lr")
        self.assertEqual(user.first_name, "Daniel")
        self.assertEqual(user.last_name, "Padmore")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)

        # Full access, both ways: the Admin role is attached...
        self.assertTrue(
            UserRole.objects.filter(user=user, role__name="Admin").exists()
        )
        # ...and the permission map resolves to every code (the superuser
        # bypass, which is what lets the very first account work).
        self.assertEqual(set(permission_map(user)), set(PERMISSION_CODES))

        # The temporary password works, by email, through the real backend.
        self.assertIsNotNone(
            authenticate(email="daniel@a1.lr", password="Temp!Pass123")
        )

    def test_forces_a_password_change_at_first_sign_in(self):
        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)
        self.assertTrue(User.objects.get(email="ceo@a1.lr").must_reset_password)

    def test_defaults_the_name_to_the_ceo(self):
        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)
        user = User.objects.get(email="ceo@a1.lr")
        self.assertEqual(user.get_full_name(), "Daniel Padmore")

    def test_a_three_part_name_keeps_its_middle_name(self):
        with with_env(
            ADMIN_EMAIL="ceo@a1.lr",
            ADMIN_PASSWORD="Temp!Pass123",
            ADMIN_NAME="Daniel M Padmore",
        ):
            call_command("create_admin", verbosity=0)
        user = User.objects.get(email="ceo@a1.lr")
        self.assertEqual(user.first_name, "Daniel")
        self.assertEqual(user.middle_name, "M")
        self.assertEqual(user.last_name, "Padmore")

    def test_running_twice_does_not_create_a_second_account(self):
        env = dict(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123")
        with with_env(**env):
            call_command("create_admin", verbosity=0)
        with with_env(**env):
            call_command("create_admin", verbosity=0)
        self.assertEqual(User.objects.filter(email="ceo@a1.lr").count(), 1)

    def test_a_second_run_never_rewrites_the_password(self):
        """
        The account exists because the person already signed in and chose a
        password. A later deploy must not put the temporary one back.
        """
        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)
        user = User.objects.get(email="ceo@a1.lr")
        user.set_password("TheirOwnPassw0rd!")
        user.must_reset_password = False
        user.save()

        # Same email, different (stale) temporary password in the environment.
        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)

        user.refresh_from_db()
        self.assertTrue(user.check_password("TheirOwnPassw0rd!"))
        self.assertFalse(user.check_password("Temp!Pass123"))
        self.assertFalse(user.must_reset_password)

    def test_restores_access_to_an_existing_account_that_lost_it(self):
        """An admin whose superuser flag was cleared gets it back, additively."""
        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)
        user = User.objects.get(email="ceo@a1.lr")
        user.is_superuser = False
        user.is_staff = False
        user.save()
        UserRole.objects.filter(user=user).delete()

        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)

        user.refresh_from_db()
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(
            UserRole.objects.filter(user=user, role__name="Admin").exists()
        )

    def test_matches_an_existing_email_case_insensitively(self):
        with with_env(ADMIN_EMAIL="ceo@a1.lr", ADMIN_PASSWORD="Temp!Pass123"):
            call_command("create_admin", verbosity=0)
        with with_env(ADMIN_EMAIL="CEO@A1.LR", ADMIN_PASSWORD="Another!1"):
            call_command("create_admin", verbosity=0)
        self.assertEqual(User.objects.filter(email__iexact="ceo@a1.lr").count(), 1)

    def test_does_nothing_without_the_variables(self):
        with with_env():  # neither ADMIN_EMAIL nor ADMIN_PASSWORD
            call_command("create_admin", verbosity=0)
        self.assertEqual(User.objects.count(), 0)

    def test_does_nothing_with_an_email_but_no_password(self):
        with with_env(ADMIN_EMAIL="ceo@a1.lr"):
            call_command("create_admin", verbosity=0)
        self.assertEqual(User.objects.count(), 0)
