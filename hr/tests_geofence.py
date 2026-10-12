"""
Being where you say you are.

A-1's concern, in their words: "I don't want for an employee to sit home and
then check in attendance."

Until now nothing stopped that. The clock captured a GPS fix from the first
day and compared it to nothing, because there was nowhere in the system to
say what "here" meant — company details held a text address and no
coordinates. Somebody could clock in from their living room and the record
would faithfully store its position.

Scanning a code did not close it either, and the code model says why: a
printed code is a shared secret, and a photograph of it works exactly as
well as the paper. Possession is not presence.

The rules these pin, which are A-1's choices and not defaults:

  * outside the area and the phone is confident  -> refused
  * outside, but inside the phone's own error    -> recorded and flagged
  * no fix at all, with a scanned code           -> allowed
  * no fix at all, without one                   -> refused
  * no office set                                -> nothing judged
  * a supervisor recording for somebody          -> never judged
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import PolicySetting
from hr import services
from hr.models import AttendanceCode, AttendanceEvent, Employee

User = get_user_model()

# The A-1 office, for the sake of the arithmetic. Any coordinate does.
OFFICE_LAT, OFFICE_LON = 6.300800, -10.797200
# Roughly 1.1 km north — unambiguously somewhere else.
HOME_LAT, HOME_LON = 6.310800, -10.797200
# Roughly 90 m north: outside a 50 m circle, inside most phones' error.
NEARBY_LAT, NEARBY_LON = 6.301600, -10.797200


def make_user(email, role_names=()):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345"
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class ClockingFromSomewhereElseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.worker = make_user("fence@test.local", ["Employee"])
        cls.employee = Employee.objects.create(
            user=cls.worker, staff_id="A1-600", start_date=timezone.localdate()
        )
        cls.supervisor = make_user("fence.sup@test.local", ["Admin"])

    def setUp(self):
        self.set_office(radius=50)
        self.code = AttendanceCode.objects.create(
            description="Main office", location_name="Monrovia",
            expires_at=timezone.now() + timedelta(days=30),
        )

    def set_office(self, latitude=OFFICE_LAT, longitude=OFFICE_LON, radius=50):
        for key, value in (
            (PolicySetting.OFFICE_LATITUDE, latitude),
            (PolicySetting.OFFICE_LONGITUDE, longitude),
            (PolicySetting.GEOFENCE_RADIUS_M, radius),
        ):
            PolicySetting.objects.update_or_create(
                key=key, defaults={"value": "" if value is None else str(value)}
            )

    def _clock(self, latitude=None, longitude=None, accuracy=None,
               code=None, recorded_by=None, kind="in"):
        return services.record_clock_event(
            employee=self.employee,
            kind=kind,
            client_uuid=uuid.uuid4(),
            latitude=latitude,
            longitude=longitude,
            accuracy_m=accuracy,
            location_unavailable=latitude is None,
            attendance_code=code,
            recorded_by=recorded_by,
            require_code=False,
        )

    # ---- the thing A-1 asked for ----------------------------------------

    @tag("acceptance")
    def test_clocking_in_from_home_is_refused(self):
        with self.assertRaises(services.ClockError) as caught:
            self._clock(HOME_LAT, HOME_LON, accuracy=20, code=self.code)
        message = str(caught.exception)
        self.assertIn("from the office", message)
        self.assertIn("supervisor", message, "it must say what to do instead")
        self.assertFalse(AttendanceEvent.objects.exists())

    @tag("acceptance")
    def test_a_scanned_code_does_not_excuse_being_somewhere_else(self):
        """
        The hole the code model admits to: a printed code is a shared
        secret and a photograph of it works from a sofa. The code proves
        possession; the fix is what proves presence.
        """
        with self.assertRaises(services.ClockError):
            self._clock(HOME_LAT, HOME_LON, accuracy=10, code=self.code)

    def test_clocking_in_at_the_office_just_works(self):
        event, created = self._clock(OFFICE_LAT, OFFICE_LON, accuracy=12, code=self.code)
        self.assertTrue(created)
        self.assertEqual(event.location_check, AttendanceEvent.AT_THE_PLACE)
        self.assertLess(event.distance_m, 50)

    # ---- the benefit of the doubt ---------------------------------------

    def test_a_vague_fix_just_outside_is_recorded_and_flagged(self):
        """
        A cheap handset in a concrete yard is honestly wrong by a hundred
        metres. Refusing that strands somebody standing right there, so the
        phone's own stated error is allowed before anybody is turned away.
        """
        event, _ = self._clock(NEARBY_LAT, NEARBY_LON, accuracy=120, code=self.code)
        self.assertEqual(event.location_check, AttendanceEvent.NEAR_ENOUGH)
        self.assertGreater(event.distance_m, 50)

    def test_a_confident_fix_just_outside_is_refused(self):
        """The same position, from a phone claiming to be sure of it."""
        with self.assertRaises(services.ClockError):
            self._clock(NEARBY_LAT, NEARBY_LON, accuracy=5, code=self.code)

    # ---- the bypass -----------------------------------------------------

    @tag("acceptance")
    def test_no_location_without_a_code_is_refused(self):
        """
        Otherwise denying the browser location permission is simply how you
        clock in from home, and the whole check is theatre.
        """
        with self.assertRaises(services.ClockError) as caught:
            self._clock(code=None)
        self.assertIn("location", str(caught.exception).lower())

    def test_no_location_with_a_code_is_allowed(self):
        """
        A-1's choice: a phone that cannot see satellites inside a concrete
        building must not stop a real employee working. The code carries it.
        """
        event, created = self._clock(code=self.code)
        self.assertTrue(created)
        self.assertEqual(event.location_check, AttendanceEvent.NOT_CHECKED)
        self.assertIsNone(event.distance_m)

    # ---- when there is nothing to measure against -----------------------

    def test_nothing_is_judged_until_an_office_is_set(self):
        self.set_office(latitude=None, longitude=None)
        event, created = self._clock(HOME_LAT, HOME_LON, accuracy=10, code=self.code)
        self.assertTrue(created, "an undrawn boundary must refuse nobody")
        self.assertEqual(event.location_check, AttendanceEvent.NOT_CHECKED)

    def test_a_broken_setting_fails_open_rather_than_locking_everybody_out(self):
        self.set_office(latitude="not a number", longitude="also not")
        self.assertIsNone(services.office_location())
        event, created = self._clock(HOME_LAT, HOME_LON, accuracy=10, code=self.code)
        self.assertTrue(created)

    def test_a_coordinate_out_of_range_is_ignored(self):
        self.set_office(latitude=999, longitude=-10.7972)
        self.assertIsNone(services.office_location())

    # ---- the exemption that has to exist --------------------------------

    def test_a_supervisor_recording_for_somebody_is_not_judged(self):
        """
        They are at a desk entering an event for a person who was on a site.
        Measuring the supervisor's phone would refuse the very thing the
        exemption exists for.
        """
        event, created = self._clock(
            HOME_LAT, HOME_LON, accuracy=10, recorded_by=self.supervisor
        )
        self.assertTrue(created)
        self.assertEqual(event.location_check, AttendanceEvent.NOT_CHECKED)

    # ---- what the record keeps ------------------------------------------

    def test_the_distance_is_stored_rather_than_recomputed_later(self):
        """
        The office location is a setting somebody can change. Recomputing
        would silently restate where people were on days nobody revisited.
        """
        event, _ = self._clock(NEARBY_LAT, NEARBY_LON, accuracy=120, code=self.code)
        recorded = event.distance_m

        self.set_office(latitude=HOME_LAT, longitude=HOME_LON, radius=50)
        event.refresh_from_db()
        self.assertEqual(
            event.distance_m, recorded, "a moved office must not rewrite history"
        )


class WhereePeopleClockScreenTests(TestCase):
    """HR sets it, and an Admin can too. Nobody else."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.hr = make_user("hr.fence@test.local", ["HR"])
        cls.worker = make_user("tech.fence@test.local", ["Technician"])

    def test_hr_can_set_the_office_location(self):
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("hr-office-location"),
            {"latitude": "6.300800", "longitude": "-10.797200", "radius_m": "180"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(services.office_location(), (6.3008, -10.7972, 180))

    def test_a_technician_cannot(self):
        self.client.force_login(self.worker)
        self.assertEqual(
            self.client.get(reverse("hr-office-location")).status_code, 403
        )

    def test_one_coordinate_without_the_other_is_refused(self):
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("hr-office-location"),
            {"latitude": "6.300800", "longitude": "", "radius_m": "180"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "both numbers")
        self.assertIsNone(services.office_location())

    def test_the_screen_says_plainly_when_nothing_is_being_checked(self):
        self.client.force_login(self.hr)
        response = self.client.get(reverse("hr-office-location"))
        self.assertContains(response, "nothing is being checked")

    def test_clearing_it_turns_the_check_off_rather_than_half_on(self):
        self.client.force_login(self.hr)
        self.client.post(
            reverse("hr-office-location"),
            {"latitude": "6.300800", "longitude": "-10.797200", "radius_m": "180"},
        )
        self.assertIsNotNone(services.office_location())

        self.client.post(
            reverse("hr-office-location"),
            {"latitude": "", "longitude": "", "radius_m": ""},
        )
        self.assertIsNone(services.office_location())
