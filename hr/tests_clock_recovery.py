"""
Client item: "Clock out needs to be adjusted in terms that, if an employee
leaves without clocking out, the next day to clock in should be available
and accessible."

It was not. `open_clock_in` scanned every event ever recorded, so a single
forgotten clock-out left the employee permanently unable to clock in — the
refusal said "You are already clocked in. Clock out first", and clocking out
wrote a clock-out against a shift that had ended days ago. Four people on
the live system had been stuck like that for three days.

A-1's rule, stated after seeing the first fix: a day closes with the day.
Nobody may come back and clock out against a day the system never saw them
leave, because any end it accepted would be a time somebody chose rather
than a time anybody worked. Such a day keeps its clock-in, shows no end and
no duration, and only a supervisor correction — which carries an actor and a
reason — can settle what was worked.
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

    @tag("acceptance")
    def test_you_cannot_clock_out_against_a_day_you_did_not_close(self):
        """
        A-1's rule. The system never saw them leave, so it has no end to
        record; accepting one later would turn a time somebody picked into
        hours on a payroll export.
        """
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 8))

        with self.assertRaises(services.ClockError) as caught:
            self._clock(AttendanceEvent.CLOCK_OUT, self._at(today, 9))
        message = str(caught.exception)
        self.assertIn("did not clock out that day", message)
        self.assertIn("supervisor", message, "it must say what to do instead")

        self.assertFalse(
            AttendanceEvent.objects.filter(
                employee=self.employee, kind=AttendanceEvent.CLOCK_OUT
            ).exists(),
            "no clock-out may be written against a closed day",
        )

    def test_the_unclosed_day_records_a_start_and_no_hours(self):
        """
        What the day is left showing: they arrived, and nothing else is
        known. Not zero hours worked — no hours recorded.
        """
        from hr.models import AttendanceDay

        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 8))
        services.rebuild_attendance_days()

        day = AttendanceDay.objects.get(employee=self.employee, date=yesterday)
        self.assertIsNotNone(day.first_in, "the arrival is on the record")
        self.assertIsNone(day.last_out, "and nothing is invented for the end")
        self.assertEqual(day.total_minutes, 0)
        self.assertTrue(day.still_clocked_in, "flagged for a supervisor to settle")

    def test_a_closed_day_is_not_shown_as_still_on_the_clock(self):
        """
        "Still clocked in" is only true of today. Three days later it reads
        as somebody at work, and invites waiting for a clock-out that cannot
        come.
        """
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._clock(AttendanceEvent.CLOCK_IN, self._at(yesterday, 8))
        services.rebuild_attendance_days()

        supervisor = make_user("rollcall.sup@test.local", ["Admin"])
        self.client.force_login(supervisor)
        response = self.client.get("/hr/roll-call/", {"date": yesterday.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Still clocked in")
        # The day still says what happened — they were here, and early
        # enough not to be late. The missing end is a quiet note beside it,
        # not a red pill replacing it.
        self.assertContains(response, "Present")
        self.assertContains(response, "no clock-out")

    def test_clocking_out_with_nothing_open_is_still_refused(self):
        with self.assertRaises(services.ClockError) as caught:
            self._clock(AttendanceEvent.CLOCK_OUT, self._at(timezone.localdate(), 17))
        self.assertIn("not clocked in", str(caught.exception))

    def test_the_unclosed_day_is_known_without_nagging_the_employee(self):
        """
        The service still knows — the refusal message for a late clock-out
        depends on it, and so does the supervisor's roll-call.

        The employee's own screen says nothing about it. A banner used to
        tell them to go and ask their supervisor, which asked for an action
        that does not exist: the day cannot be reopened. Their last seven
        days are on that screen and the missing end shows as a dash, which is
        how their supervisor sees it too.
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
        self.assertNotContains(response, "never clocked out")
        self.assertNotContains(response, "ask your supervisor to correct")

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
