"""
The office's view of everybody's field work.

Field Jobs had one screen — a technician's own day — so an Admin opening it
was told "No jobs assigned today" and shown nothing, however much work was
out there. The page even called them a Technician.

Two questions, two screens: `my_jobs` answers "what am I doing today" and is
scoped to one person by construction; the board answers "who is where, and
who sent them". Who scheduled a visit is recorded now, because more than one
person in the office can.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from accounts.navigation import visible_items
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site
from hr.models import Employee
from projects.models import Project

from .models import FieldJob

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class ScheduleBoardTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar install")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Liberty Gold")
        cls.site = Site.objects.create(customer=cls.customer, name="Main yard")

        cls.admin = make_user("admin@t.local", ["Admin"])
        cls.supervisor = make_user("sup@t.local", ["Supervisor"])
        cls.technician = make_user("tech@t.local", ["Technician"])
        cls.other_tech = make_user("tech2@t.local", ["Technician"])
        Employee.objects.create(user=cls.technician, staff_id="A1-001")
        Employee.objects.create(user=cls.other_tech, staff_id="A1-002")

        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Yard solar", customer=cls.customer, site=cls.site,
            service_type=cls.service, status=cls.status,
        )

    @classmethod
    def job(cls, reference, technician, when, scheduled_by=None, **extra):
        return FieldJob.objects.create(
            reference=reference, project=cls.project, customer=cls.customer, site=cls.site,
            service_type=cls.service, assigned_to=technician, scheduled_for=when,
            scheduled_by=scheduled_by, **extra
        )


class TheOfficeSeesEverybodysWorkTests(ScheduleBoardTestCase):
    def setUp(self):
        now = timezone.now()
        self.today = self.job("FJ-0001", self.technician, now, scheduled_by=self.supervisor)
        self.someone_elses = self.job(
            "FJ-0002", self.other_tech, now, scheduled_by=self.admin
        )
        self.tomorrow = self.job("FJ-0003", self.technician, now + timedelta(days=1))

    def test_an_admin_sees_every_job_today_not_their_own_empty_day(self):
        """The bug as reported: an Admin opening Field Jobs was told nothing
        was assigned, while two crews were out."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FJ-0001")
        self.assertContains(response, "FJ-0002")
        self.assertNotContains(response, "No jobs assigned today")

    def test_the_board_says_who_sent_each_crew(self):
        """There is more than one person in the office who can schedule, so
        "the office sent you" is not an answer."""
        self.supervisor.first_name, self.supervisor.last_name = "Grace", "Toe"
        self.supervisor.save(update_fields=["first_name", "last_name"])
        self.admin.first_name, self.admin.last_name = "Comfort", "Kollie"
        self.admin.save(update_fields=["first_name", "last_name"])

        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"))
        self.assertContains(response, "Scheduled by")
        self.assertContains(response, "G. Toe")
        self.assertContains(response, "C. Kollie")

    def test_field_jobs_in_the_shell_takes_an_admin_to_the_board(self):
        item = next(i for i in visible_items(self.admin) if i.label == "Field Jobs")
        self.assertEqual(item.url_name, "fieldjobs-board")

    def test_and_takes_a_technician_to_their_own_day(self):
        item = next(i for i in visible_items(self.technician) if i.label == "Field Jobs")
        self.assertEqual(item.url_name, "fieldjobs-my-jobs")

    def test_a_technician_cannot_open_the_board(self):
        """Their own list is their screen. This one is the office's."""
        self.client.force_login(self.technician)
        self.assertEqual(self.client.get(reverse("fieldjobs-board")).status_code, 403)

    def test_today_is_what_it_opens_on(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"))
        self.assertNotContains(response, "FJ-0003")
        self.assertEqual(response.context["today_count"], 2)

    def test_still_to_come_reaches_tomorrow(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"), {"filter": "upcoming"})
        self.assertContains(response, "FJ-0003")
        self.assertNotContains(response, "FJ-0001")

    def test_completed_work_has_its_own_view(self):
        self.someone_elses.state = FieldJob.COMPLETED
        self.someone_elses.completed_at = timezone.now()
        self.someone_elses.save(update_fields=["state", "completed_at"])

        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"), {"filter": "completed"})
        self.assertContains(response, "FJ-0002")
        self.assertNotContains(response, "FJ-0001")
        self.assertEqual(response.context["completed_count"], 1)

    def test_finished_work_reads_newest_first(self):
        """It is history, not a queue: what was done last week is not the
        first thing anybody wants to see."""
        older = self.job(
            "FJ-0004", self.technician, timezone.now() - timedelta(days=9),
            state=FieldJob.COMPLETED,
        )
        newer = self.job(
            "FJ-0005", self.technician, timezone.now() - timedelta(days=1),
            state=FieldJob.COMPLETED,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"), {"filter": "completed"})
        self.assertEqual(
            [job.reference for job in response.context["jobs"]],
            [newer.reference, older.reference],
        )

    def test_every_filter_the_board_offers_actually_works(self):
        self.client.force_login(self.admin)
        for key in ("today", "upcoming", "unfinished", "completed", "all"):
            with self.subTest(filter=key):
                response = self.client.get(reverse("fieldjobs-board"), {"filter": key})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["filter"], key)

    def test_a_made_up_filter_falls_back_to_today(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-board"), {"filter": "whatever"})
        self.assertEqual(response.context["filter"], "today")

    def test_it_can_be_searched_by_who_is_going(self):
        self.client.force_login(self.admin)
        self.other_tech.first_name, self.other_tech.last_name = "Moses", "Toe"
        self.other_tech.save(update_fields=["first_name", "last_name"])

        response = self.client.get(reverse("fieldjobs-board"), {"filter": "all", "q": "Toe"})
        self.assertContains(response, "FJ-0002")
        self.assertNotContains(response, "FJ-0001")


class OpeningAJobFromTheBoardTests(ScheduleBoardTestCase):
    """
    The board lists every job, so every row has to open. `job_detail` was
    scoped to jobs you are on, so an Admin clicking a row they were not
    crewed on got a 404 from their own schedule.
    """

    def setUp(self):
        self.job = self.job("FJ-0001", self.technician, timezone.now())

    def test_the_office_can_open_a_job_it_is_not_on(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["on_the_job"])

    def test_a_supervisor_can_too_without_a_job_list_of_their_own(self):
        """They hold the schedule but not `view_own_job_list`, so the gate
        has to accept either."""
        self.client.force_login(self.supervisor)
        self.assertEqual(
            self.client.get(reverse("fieldjobs-job-detail", args=[self.job.pk])).status_code,
            200,
        )

    def test_the_office_is_not_offered_the_crews_actions(self):
        """Reading that a crew is at Ganta and standing on that site are
        different rights."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.job.pk]))
        self.assertNotContains(response, "Check in on site")
        self.assertNotContains(response, "What this job needs")
        self.assertContains(response, "From the office")

    def test_and_is_refused_them_by_the_route_as_well(self):
        """Not merely hidden: the check-in still belongs to whoever is
        there."""
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("fieldjobs-check-in", args=[self.job.pk]),
            {"client_uuid": "11111111-1111-1111-1111-111111111111",
             "device_timestamp": timezone.now().isoformat(), "location_unavailable": "1"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.job.check_ins.exists())

    def test_the_crew_still_get_their_own_screen(self):
        self.client.force_login(self.technician)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.job.pk]))
        self.assertTrue(response.context["on_the_job"])
        self.assertContains(response, "Check in on site")

    def test_somebody_with_neither_still_cannot_see_it(self):
        self.client.force_login(self.other_tech)
        self.assertEqual(
            self.client.get(reverse("fieldjobs-job-detail", args=[self.job.pk])).status_code,
            404,
        )


class WhoScheduledItTests(ScheduleBoardTestCase):
    def test_scheduling_records_who_did_it(self):
        self.client.force_login(self.supervisor)
        self.client.post(
            reverse("fieldjobs-schedule-for-project", args=[self.project.pk]),
            {
                "customer": self.customer.pk, "site": self.site.pk,
                "service_type": self.service.pk, "assigned_to": self.technician.pk,
                "scheduled_for": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "instructions": "",
            },
        )
        self.assertEqual(FieldJob.objects.get().scheduled_by, self.supervisor)

    def test_the_technician_is_told_who_sent_them(self):
        job = self.job("FJ-0001", self.technician, timezone.now(), scheduled_by=self.supervisor)
        self.supervisor.first_name, self.supervisor.last_name = "Grace", "Toe"
        self.supervisor.save(update_fields=["first_name", "last_name"])

        self.client.force_login(self.technician)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[job.pk]))
        self.assertContains(response, "Sent by")
        self.assertContains(response, "G. Toe")

    def test_a_job_recorded_before_this_was_asked_for_keeps_an_honest_blank(self):
        job = self.job("FJ-0002", self.technician, timezone.now())
        self.assertIsNone(job.scheduled_by)

        self.client.force_login(self.technician)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[job.pk]))
        self.assertNotContains(response, "Sent by")


class TheOwnListStillBelongsToWhoeverOpensItTests(ScheduleBoardTestCase):
    def test_it_no_longer_calls_everybody_a_technician(self):
        """An Admin who also carries work was being told their job title was
        something it is not."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertContains(response, "your own jobs")
        self.assertNotContains(response, "· Technician")

    def test_somebody_holding_both_can_cross_between_them(self):
        self.client.force_login(self.admin)
        own = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertTrue(own.context["can_see_schedule"])
        self.assertContains(own, reverse("fieldjobs-board"))

        board = self.client.get(reverse("fieldjobs-board"))
        self.assertTrue(board.context["has_own_list"])
        self.assertContains(board, reverse("fieldjobs-my-jobs"))
