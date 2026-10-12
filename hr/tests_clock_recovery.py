"""
Client item: "Clock out needs to be adjusted in terms that, if an employee
leaves without clocking out, the next day to clock in should be available
and accessible."

It was not. `open_clock_in` scanned every event ever recorded, so a single
forgotten clock-out left the employee permanently unable to clock in — the
refusal said "You are already clocked in. Clock out first", and clocking out
wrote a clock-out against a shift that had ended days ago. Four people on
the live system had been stuck like that for three days.

The rule now differs by direction, because the two questions are different:
closing a shift looks at the last open one whenever it began, so a night
that runs past midnight can still be closed; starting one looks only at
today.
"""
import uuid
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Role, UserRole
from hr import services
from hr.models import AttendanceEvent, Employee

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345"
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class ForgettingToClockOutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.worker = make_user("clockfix@test.local", ["Technician"])
        cls.employee = Employee.objects.create(
            user=cls.worker, staff_id="A1-700", start_date=timezone.localdate()
        )

    def _at(self, day, hour, minute=0):
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    def _clock(self, kind, when):
        return services.record_clock_event(
            employee=self.employee,
            kind=kind,
            client_uuid=uuid.uuid4(),
            device_timestamp=when,
            location_unavailable=True,
            require_code=False,
        )

    @tag("acceptance")
    def test_a_forgotten_clock_out_does_not_lock_the_next_day(self):
        """The item as the client reported it."""
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)

        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 8))
        # They went home without clocking out. The next morning:
        event, created = self._clock(AttendanceEvent.CLOCK_IN, self._at(today, 8))
        self.assertTrue(created)
        self.assertEqual(
            AttendanceEvent.objects.filter(
                employee=self.employee, kind=AttendanceEvent.CLOCK_IN
            ).count(),
            2,
        )

    def test_clocking_in_twice_in_one_day_is_still_refused(self):
        today = timezone.localdate()
        self._clock(AttendanceEvent.CLOCK_IN, self._at(today, 8))
        with self.assertRaises(services.ClockError) as caught:
            self._clock(AttendanceEvent.CLOCK_IN, self._at(today, 9))
        self.assertIn("already clocked in today", str(caught.exception))

    def test_a_night_shift_can_still_be_clocked_out_of(self):
        """
        Closing a shift looks at the last open one whenever it started. Day-
        bounding both directions would have broken work that runs past
        midnight.
        """
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 23))
        event, created = self._clock(AttendanceEvent.CLOCK_OUT, self._at(today, 3))
        self.assertTrue(created)

    def test_clocking_out_with_nothing_open_is_still_refused(self):
        with self.assertRaises(services.ClockError) as caught:
            self._clock(AttendanceEvent.CLOCK_OUT, self._at(timezone.localdate(), 17))
        self.assertIn("not clocked in", str(caught.exception))

    def test_the_unclosed_day_is_surfaced_rather_than_forgotten(self):
        """
        The employee is not blocked, but somebody still has a day on record
        with no end to it. Only a supervisor correction can settle it, so it
        has to be visible.
        """
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 8))

        stale = services.stale_clock_in(self.employee, on_date=today)
        self.assertIsNotNone(stale)
        self.assertEqual(timezone.localtime(stale).date(), yesterday)

        self.client.force_login(self.worker)
        response = self.client.get("/hr/clock/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "never clocked out")

    def test_todays_open_shift_is_not_reported_as_stale(self):
        today = timezone.localdate()
        self._clock(AttendanceEvent.CLOCK_IN, self._at(today, 8))
        self.assertIsNone(services.stale_clock_in(self.employee, on_date=today))

    def test_a_closed_day_leaves_nothing_stale(self):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 8))
        self._clock(AttendanceEvent.CLOCK_OUT, self._at(yesterday, 17))
        self.assertIsNone(services.stale_clock_in(self.employee, on_date=today))
