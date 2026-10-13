"""
Tasks that run for an hour, and tasks that run for a fortnight.

A-1: "some tasks may run for more than a week or two, some tasks it's just
for an hour or minutes. So let the user decide if they want to specify
time."

Dates alone made "today to today" the only way to express a job needed
before a ten o'clock visit — and left it reading as on time until midnight,
which is most of a working day spent looking fine after the visit has
already happened.

Times are optional on purpose. A task with no time is genuinely a different
thing from one at midnight, which is why these are TimeFields beside the
dates rather than the dates becoming datetimes.
"""
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer
from projects.models import Project, Task

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


class TaskTimesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Catholic Relief Services")
        cls.manager = make_user("pm.times@test.local", ["Project Manager"])
        cls.project = Project.objects.create(
            reference="PRJ-7001", name="Solar at Zwedru", customer=cls.customer,
            service_type=cls.service, status=cls.status, manager=cls.manager,
        )

    def setUp(self):
        self.client.force_login(self.manager)

    def _post(self, **extra):
        payload = {
            "title": "Torque the array bolts",
            "description": "38 Nm, both rails.",
            "assignee": self.manager.pk,
            "start_date": "", "start_time": "", "due_date": "", "due_time": "",
        }
        payload.update(extra)
        return self.client.post(
            reverse("projects-task-create", args=[self.project.pk]), payload
        )

    # ---- what A-1 asked for ---------------------------------------------

    @tag("acceptance")
    def test_a_task_can_run_for_part_of_one_day(self):
        response = self._post(
            start_date="2026-11-02", start_time="09:00",
            due_date="2026-11-02", due_time="11:30",
        )
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        self.assertEqual(task.start_time, time(9, 0))
        self.assertEqual(task.due_time, time(11, 30))

    @tag("acceptance")
    def test_a_task_can_still_be_days_only(self):
        """The times are optional, not a new requirement."""
        response = self._post(start_date="2026-11-02", due_date="2026-11-16")
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        self.assertIsNone(task.start_time)
        self.assertIsNone(task.due_time)
        self.assertFalse(task.has_times)

    def test_a_task_can_have_no_dates_at_all(self):
        """Plenty of work is "whenever you get to it" and always was."""
        self.assertEqual(self._post().status_code, 302)
        task = Task.objects.get()
        self.assertIsNone(task.start_date)
        self.assertIsNone(task.due_date)

    def test_one_end_can_carry_a_time_and_the_other_not(self):
        self._post(start_date="2026-11-02", due_date="2026-11-02", due_time="16:00")
        task = Task.objects.get()
        self.assertIsNone(task.start_time)
        self.assertEqual(task.due_time, time(16, 0))

    # ---- what it refuses -------------------------------------------------

    def _refusal(self, response):
        """
        The task screen posts and redirects, so the reason arrives as a
        message rather than on a re-rendered form. It has to arrive: the
        view used to say only "That task could not be saved" and throw the
        reasons away, leaving somebody guessing at a date they could no
        longer see.
        """
        self.assertEqual(response.status_code, 302)
        followed = self.client.get(response["Location"])
        return " ".join(str(m) for m in followed.context["messages"])

    def test_a_time_without_its_day_is_refused(self):
        """A time on no particular day is one nobody can act on."""
        said = self._refusal(self._post(due_time="16:00"))
        self.assertIn("Give the day this is due", said)
        self.assertFalse(Task.objects.exists())

    def test_the_end_cannot_precede_the_start(self):
        said = self._refusal(self._post(start_date="2026-11-10", due_date="2026-11-02"))
        self.assertIn("cannot be before the start date", said)
        self.assertFalse(Task.objects.exists())

    def test_on_one_day_the_due_time_cannot_precede_the_start_time(self):
        said = self._refusal(self._post(
            start_date="2026-11-02", start_time="14:00",
            due_date="2026-11-02", due_time="09:00",
        ))
        self.assertIn("cannot be before the start time", said)
        self.assertFalse(Task.objects.exists())

    @tag("acceptance")
    def test_a_refusal_says_which_field_and_why(self):
        """
        Not "that task could not be saved". The form knows the reason and
        the person has just lost what they typed.
        """
        said = self._refusal(self._post(due_time="16:00"))
        self.assertIn("Due", said, "it must name the field")
        self.assertNotEqual(said.strip(), "That task could not be saved.")

    def test_an_earlier_time_on_a_later_day_is_fine(self):
        """Two in the afternoon on Monday to nine on Tuesday is a real span."""
        response = self._post(
            start_date="2026-11-02", start_time="14:00",
            due_date="2026-11-03", due_time="09:00",
        )
        self.assertEqual(response.status_code, 302)

    # ---- overdue, to the minute -----------------------------------------

    @tag("acceptance")
    def test_overdue_is_judged_to_the_minute_when_a_time_was_given(self):
        now = timezone.localtime()
        task = Task.objects.create(
            project=self.project, title="Before the visit", assignee=self.manager,
            due_date=now.date(), due_time=(now - timedelta(hours=1)).time(),
        )
        self.assertTrue(
            task.is_overdue,
            "due an hour ago must read overdue, not on time until midnight",
        )

    def test_a_time_later_today_is_not_overdue_yet(self):
        now = timezone.localtime()
        task = Task.objects.create(
            project=self.project, title="Later", assignee=self.manager,
            due_date=now.date(), due_time=(now + timedelta(hours=2)).time(),
        )
        self.assertFalse(task.is_overdue)

    def test_without_a_time_overdue_is_still_judged_by_the_day(self):
        task = Task.objects.create(
            project=self.project, title="Today, no time", assignee=self.manager,
            due_date=timezone.localdate(),
        )
        self.assertFalse(task.is_overdue, "the day is not over yet")

        task.due_date = timezone.localdate() - timedelta(days=1)
        task.save(update_fields=["due_date"])
        self.assertTrue(task.is_overdue)

    def test_a_finished_task_is_never_overdue(self):
        task = Task.objects.create(
            project=self.project, title="Done", assignee=self.manager,
            due_date=date(2020, 1, 1), due_time=time(9, 0),
            completed_at=timezone.now(),
        )
        self.assertFalse(task.is_overdue)

    # ---- how it reads ----------------------------------------------------

    def test_the_span_reads_as_one_phrase_wherever_it_appears(self):
        from accounts.templatetags.a1 import a1span

        self.assertEqual(
            a1span(date(2026, 9, 3), time(9, 0), date(2026, 9, 3), time(11, 30)),
            "3 Sept, 09:00–11:30",
        )
        self.assertEqual(
            a1span(date(2026, 9, 3), None, date(2026, 9, 12), None), "3–12 Sept"
        )
        self.assertEqual(
            a1span(date(2026, 9, 3), time(9, 0), date(2026, 9, 3), None),
            "3 Sept from 09:00",
        )
        self.assertEqual(
            a1span(date(2026, 9, 3), None, date(2026, 9, 3), time(11, 30)),
            "3 Sept by 11:30",
        )
        self.assertEqual(a1span(None, None, None, None), "")

    def test_the_time_shows_on_the_persons_own_work_screen(self):
        Task.objects.create(
            project=self.project, title="Before the ten o'clock visit",
            assignee=self.manager,
            start_date=date(2026, 11, 2), start_time=time(9, 0),
            due_date=date(2026, 11, 2), due_time=time(11, 30),
        )
        response = self.client.get(reverse("dashboard-my-tasks"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "09:00")
