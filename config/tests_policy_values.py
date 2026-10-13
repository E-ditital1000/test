"""
Policy values, checked against the shape the code reading them expects.

Found on the live system: the lateness grace period — a number of minutes —
had been given "8:30". `int("8:30")` raises, the reader treats an unparsable
policy as "do not judge lateness", and lateness was switched off for the
whole company. Everybody read as present whatever time they arrived, for
days, and nothing anywhere said so.

Failing open is right. A missing policy must not mark a workforce late.
Failing open silently, on a value somebody typed by hand into a free text
box, is what these exist to stop.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag

from config.forms import PolicySettingForm
from config.models import PolicySetting

User = get_user_model()


class PolicyValueTests(TestCase):
    def _form(self, key, value):
        return PolicySettingForm(
            {"key": key, "value": value, "description": "set in a test"}
        )

    # ---- the one that bit --------------------------------------------------

    @tag("acceptance")
    def test_a_time_in_the_grace_period_is_refused(self):
        form = self._form(PolicySetting.LATE_AFTER_MINUTES, "8:30")
        self.assertFalse(form.is_valid())
        said = " ".join(form.errors["value"])
        self.assertIn("MINUTES", said)
        self.assertIn("switches lateness off", said)

    def test_the_grace_period_takes_minutes(self):
        self.assertTrue(self._form(PolicySetting.LATE_AFTER_MINUTES, "30").is_valid())
        self.assertTrue(self._form(PolicySetting.LATE_AFTER_MINUTES, "0").is_valid())

    def test_an_absurd_grace_period_is_refused(self):
        self.assertFalse(self._form(PolicySetting.LATE_AFTER_MINUTES, "-5").is_valid())
        self.assertFalse(self._form(PolicySetting.LATE_AFTER_MINUTES, "abc").is_valid())

    # ---- the rest of the values --------------------------------------------

    def test_the_workday_start_is_a_time(self):
        self.assertTrue(self._form(PolicySetting.WORKDAY_START, "08:00").is_valid())
        self.assertFalse(self._form(PolicySetting.WORKDAY_START, "800").is_valid())
        self.assertFalse(self._form(PolicySetting.WORKDAY_START, "25:00").is_valid())

    def test_a_geofence_tighter_than_a_phone_can_place_itself_is_refused(self):
        """
        20 m was set on the live system. A fix is routinely 20–50 m out, so
        staff standing at the office would be refused.
        """
        form = self._form(PolicySetting.GEOFENCE_RADIUS_M, "20")
        self.assertFalse(form.is_valid())
        self.assertIn("tighter than most phones", " ".join(form.errors["value"]))

        self.assertTrue(self._form(PolicySetting.GEOFENCE_RADIUS_M, "180").is_valid())

    def test_coordinates_have_to_be_coordinates(self):
        self.assertTrue(self._form(PolicySetting.OFFICE_LATITUDE, "6.253964").is_valid())
        self.assertFalse(self._form(PolicySetting.OFFICE_LATITUDE, "999").is_valid())
        self.assertFalse(self._form(PolicySetting.OFFICE_LONGITUDE, "east").is_valid())

    def test_every_day_cannot_be_a_rest_day(self):
        self.assertTrue(self._form(PolicySetting.LEAVE_REST_DAYS, "6").is_valid())
        self.assertTrue(self._form(PolicySetting.LEAVE_REST_DAYS, "5,6").is_valid())
        self.assertFalse(
            self._form(PolicySetting.LEAVE_REST_DAYS, "0,1,2,3,4,5,6").is_valid()
        )
        self.assertFalse(self._form(PolicySetting.LEAVE_REST_DAYS, "Sunday").is_valid())

    def test_a_tracked_from_date_is_a_date(self):
        self.assertTrue(
            self._form(PolicySetting.LEAVE_TRACKED_FROM, "2026-10-13").is_valid()
        )
        self.assertFalse(
            self._form(PolicySetting.LEAVE_TRACKED_FROM, "13/10/2026").is_valid()
        )

    def test_a_key_with_no_checker_is_still_accepted(self):
        """
        An unknown key is somebody's own setting, not a mistake. Validation
        is per key and absent ones pass — adding a policy is not blocked on
        writing a checker first.
        """
        self.assertTrue(self._form("something_a1_invented", "whatever").is_valid())


class LatenessVisibilityTests(TestCase):
    """What the screens say when the policy cannot be read."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        from accounts.models import Role, UserRole

        cls.admin = User.objects.create_user(
            username="policy@test.local", email="policy@test.local",
            password="Testing!12345",
        )
        cls.admin.must_reset_password = False
        cls.admin.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=cls.admin, role=Role.objects.get(name="Admin"))

    def test_lateness_is_judged_when_the_policy_reads(self):
        from hr import services

        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")
        self.assertTrue(services.lateness_is_judged())

    @tag("acceptance")
    def test_an_unreadable_policy_says_so_rather_than_marking_nobody_late(self):
        from hr import services

        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="07:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="8:30")
        self.assertFalse(services.lateness_is_judged())

        self.client.force_login(self.admin)
        body = self.client.get("/hr/roll-call/").content.decode()
        self.assertIn("Nobody is being marked late", body)
        self.assertIn("minutes", body)

    def test_nothing_is_said_when_the_policy_is_fine(self):
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")
        self.client.force_login(self.admin)
        body = self.client.get("/hr/roll-call/").content.decode()
        self.assertNotIn("Nobody is being marked late", body)
