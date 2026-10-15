"""
Where somebody actually signed in.

A-1's second item: "the location of staff can't be shown where they login.
If they sign in in the field the GPS coordinate should indicate where they
sign in."

The fix was captured on every clock event from the first day. It was never
shown. The geofence work that followed added a verdict — "At the office",
"Edge of the area" — which is a judgement with no evidence under it: a
supervisor who wants to know where a technician was standing could read
that the system approved, and nothing else.

So the coordinate is now on the two screens that answer the question, as a
link to a map. Nothing is sent anywhere by rendering it; the numbers travel
only if somebody clicks.
"""
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from accounts.templatetags.a1 import a1coords, a1mapurl
from hr.models import AttendanceEvent, Employee

User = get_user_model()

# Out at a site, well away from the office.
FIELD_LAT, FIELD_LON = 6.310800, -10.797200


def make_user(email, role_names=(), **extra):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345", **extra
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class CoordinateFormatting(TestCase):
    def test_a_fix_reads_as_a_pair_of_numbers(self):
        event = AttendanceEvent(latitude=FIELD_LAT, longitude=FIELD_LON)
        self.assertEqual(a1coords(event), "6.310800, -10.797200")

    def test_a_missing_fix_says_nothing_rather_than_none(self):
        self.assertEqual(a1coords(AttendanceEvent()), "")
        self.assertEqual(a1coords(None), "")
        self.assertEqual(a1mapurl(AttendanceEvent()), "")

    def test_the_map_link_carries_the_coordinate(self):
        event = AttendanceEvent(latitude=FIELD_LAT, longitude=FIELD_LON)
        self.assertEqual(
            a1mapurl(event), "https://www.google.com/maps?q=6.310800,-10.797200"
        )


class WhereTheyClockedIn(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.hr = make_user("hr.loc@test.local", ["HR"], first_name="Grace")
        cls.worker = make_user("worker.loc@test.local", ["Employee"], first_name="Moses")
        cls.employee = Employee.objects.create(user=cls.worker, staff_id="A1TES-201")

    def an_arrival(self, **extra):
        fields = {
            "employee": self.employee,
            "kind": AttendanceEvent.CLOCK_IN,
            "client_uuid": uuid.uuid4(),
            "device_timestamp": timezone.now(),
            "latitude": FIELD_LAT,
            "longitude": FIELD_LON,
            "accuracy_m": 12.0,
            "distance_m": 1113,
            "location_check": AttendanceEvent.NOT_CHECKED,
        }
        fields.update(extra)
        return AttendanceEvent.objects.create(**fields)

    @tag("acceptance")
    def test_the_attendance_record_shows_where_the_event_was_recorded(self):
        self.an_arrival()
        self.client.force_login(self.hr)
        body = self.client.get(
            reverse("hr-employee-attendance", args=[self.employee.pk])
        ).content.decode()
        self.assertIn("6.310800, -10.797200", body)
        self.assertIn("https://www.google.com/maps?q=6.310800,-10.797200", body)
        self.assertIn("1113 m from the office", body)

    @tag("acceptance")
    def test_the_roll_call_shows_where_they_signed_in(self):
        self.an_arrival(location_check=AttendanceEvent.AT_THE_PLACE)
        self.client.force_login(self.hr)
        body = self.client.get(reverse("hr-roll-call")).content.decode()
        self.assertIn("6.310800, -10.797200", body)
        self.assertIn("1113 m out", body)

    def test_a_clock_in_with_no_fix_says_so_rather_than_showing_nothing(self):
        """
        The clock is never blocked on a missing fix, so "no coordinate" is
        a real state and has to read as one — not as a blank that looks
        like the screen failed to load.
        """
        self.an_arrival(
            latitude=None, longitude=None, accuracy_m=None,
            distance_m=None, location_unavailable=True,
        )
        self.client.force_login(self.hr)
        body = self.client.get(
            reverse("hr-employee-attendance", args=[self.employee.pk])
        ).content.decode()
        self.assertIn("Location unavailable", body)
        self.assertNotIn("https://www.google.com/maps", body)

    def test_the_roll_call_carries_the_arrival_itself(self):
        """
        The row used to hold only the verdict. It needs the event to show
        anything about where the person was.
        """
        from hr import services

        event = self.an_arrival(location_check=AttendanceEvent.AT_THE_PLACE)
        rows = services.roll_call(on_date=timezone.localdate())
        row = next(r for r in rows if r["employee"].pk == self.employee.pk)
        self.assertEqual(row["arrival"], event)

    def test_the_first_clock_in_is_the_arrival(self):
        """Somebody who clocks in twice arrived once."""
        from hr import services

        first = self.an_arrival(
            device_timestamp=timezone.now() - timezone.timedelta(hours=2)
        )
        self.an_arrival()
        rows = services.roll_call(on_date=timezone.localdate())
        row = next(r for r in rows if r["employee"].pk == self.employee.pk)
        self.assertEqual(row["arrival"], first)

    def test_a_clock_out_is_not_mistaken_for_an_arrival(self):
        from hr import services

        self.an_arrival(
            kind=AttendanceEvent.CLOCK_OUT,
            device_timestamp=timezone.now() - timezone.timedelta(hours=3),
        )
        arrival = self.an_arrival()
        rows = services.roll_call(on_date=timezone.localdate())
        row = next(r for r in rows if r["employee"].pk == self.employee.pk)
        self.assertEqual(row["arrival"], arrival)

    def test_reading_a_roll_call_takes_a_fixed_number_of_queries(self):
        """
        The arrival comes from the query that was already being run for the
        location verdict. Fetching it per row would be thirteen extra
        queries on the screen HR opens every morning.
        """
        from hr import services

        for index in range(6):
            person = make_user(f"many{index}.loc@test.local", ["Employee"])
            employee = Employee.objects.create(user=person, staff_id=f"A1TES-3{index:02d}")
            AttendanceEvent.objects.create(
                employee=employee, kind=AttendanceEvent.CLOCK_IN,
                client_uuid=uuid.uuid4(), device_timestamp=timezone.now(),
                latitude=FIELD_LAT, longitude=FIELD_LON, accuracy_m=10.0,
            )
        with self.assertNumQueries(4):
            services.roll_call(on_date=timezone.localdate())
