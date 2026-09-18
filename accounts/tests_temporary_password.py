"""
Issuing a temporary password: the way back in for somebody who forgot their
password or locked themselves out. What matters is that it works, that it is
shown once and recorded nowhere, and that it cannot be used to take over an
account more powerful than your own.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditEntry, Permission, Role, RolePermission, UserRole
from accounts.services import TEMPORARY_PASSWORD_ALPHABET, temporary_password

User = get_user_model()
PASSWORD = "Testing!12345"
TEMP = __import__("re").compile(r"New temporary password for [^:]+: (\w+)")


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password=PASSWORD)
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class TemporaryPasswordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.admin = make_user("admin@test.local", ["Admin"])
        cls.worker = make_user("worker@test.local", ["Employee"])

    def issue(self, target, actor=None, reason="Forgot it"):
        self.client.force_login(actor or self.admin)
        response = self.client.post(
            reverse("settings-user-temporary-password", args=[target.pk]),
            {"reason": reason},
            follow=True,
        )
        match = TEMP.search(response.content.decode())
        return response, (match.group(1) if match else None)

    def test_the_new_password_signs_in_and_forces_a_change(self):
        _response, temporary = self.issue(self.worker)
        self.assertIsNotNone(temporary)

        fresh = Client()
        response = fresh.post(reverse("login"), {"email": self.worker.email, "password": temporary})
        self.assertRedirects(response, reverse("password-reset-required"), fetch_redirect_response=False)
        # The old one no longer works.
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.check_password(PASSWORD))

    def test_it_releases_a_lockout(self):
        self.worker.failed_login_attempts = 5
        self.worker.locked_until = timezone.now() + timedelta(minutes=15)
        self.worker.save()
        self.issue(self.worker)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_locked_out())
        self.assertEqual(self.worker.failed_login_attempts, 0)

    def test_it_signs_the_person_out_everywhere(self):
        their_phone = Client()
        their_phone.force_login(self.worker)
        self.issue(self.worker)
        self.assertEqual(their_phone.get(reverse("profile")).status_code, 302)

    def test_it_is_shown_once_and_never_written_down(self):
        response, temporary = self.issue(self.worker)
        # Shown in a toast that stays until closed.
        self.assertIn("data-sticky", response.content.decode())
        entry = AuditEntry.objects.get(action="user.temporary_password_issued")
        self.assertEqual(entry.reason, "Forgot it")
        self.assertNotIn(temporary, str(entry.before) + str(entry.after) + entry.reason)
        # And it is gone from the next page.
        self.assertNotIn(temporary, self.client.get(reverse("settings-users")).content.decode())

    def test_you_cannot_take_over_an_account_more_powerful_than_your_own(self):
        """
        Setting somebody's password is signing in as them. A user manager
        without the Admin role's powers must not be able to reset an Admin.
        """
        limited = Role.objects.create(name="User desk")
        RolePermission.objects.create(role=limited, permission=Permission.objects.get(code="manage_users"))
        desk = make_user("desk@test.local")
        UserRole.objects.create(user=desk, role=limited)

        _response, temporary = self.issue(self.admin, actor=desk)
        self.assertIsNone(temporary)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(PASSWORD))

        # An Employee holds clock_in_out, which the desk does not: refused too.
        _response, temporary = self.issue(self.worker, actor=desk)
        self.assertIsNone(temporary)

        # An account holding nothing beyond the desk's own powers is fine.
        no_roles = make_user("new.starter@test.local")
        _response, temporary = self.issue(no_roles, actor=desk)
        self.assertIsNotNone(temporary)

    def test_your_own_password_is_changed_from_your_profile(self):
        _response, temporary = self.issue(self.admin)
        self.assertIsNone(temporary)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(PASSWORD))

    def test_a_deactivated_account_is_refused(self):
        self.worker.is_active = False
        self.worker.save()
        _response, temporary = self.issue(self.worker)
        self.assertIsNone(temporary)

    def test_a_role_without_manage_users_is_refused(self):
        hr = make_user("hr@test.local", ["HR"])
        self.client.force_login(hr)
        response = self.client.post(reverse("settings-user-temporary-password", args=[self.worker.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_get_only_asks_for_confirmation(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("settings-user-temporary-password", args=[self.worker.pk]))
        self.assertContains(response, "Issue temporary password")
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.check_password(PASSWORD))

    def test_temporary_passwords_have_no_characters_that_read_as_two(self):
        for _ in range(50):
            value = temporary_password()
            self.assertEqual(len(value), 12)
            self.assertTrue(set(value) <= set(TEMPORARY_PASSWORD_ALPHABET))
        for confusing in "0O1lI":
            self.assertNotIn(confusing, TEMPORARY_PASSWORD_ALPHABET)
