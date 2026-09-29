"""
Reading a job before the day it happens.

A technician could see that something was coming and nothing about what it
was: "Coming up" listed a reference and a time on a flat card that was not
even a link, so the instructions, the site and what to bring were out of
reach until the morning of. Somebody who needs to load a van the night
before was being asked to guess.

The job opens now. Checking in is the one thing held back to the day.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site
from hr.models import Employee
from projects.models import Project, Task

from .models import FieldJob

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class PlanningAheadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar Installation")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="EL Organization")
        cls.site = Site.objects.create(customer=cls.customer, name="Main address")

        cls.technician = make_user("tech@t.local", ["Technician"])
        Employee.objects.create(user=cls.technician, staff_id="A1-001")
        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Array", customer=cls.customer, site=cls.site,
            service_type=cls.service, status=cls.status,
        )

    def setUp(self):
        self.tomorrow = FieldJob.objects.create(
            reference="FJ-8002", project=self.project, customer=self.customer, site=self.site,
            service_type=self.service, assigned_to=self.technician,
            scheduled_for=timezone.now() + timedelta(days=1),
            instructions="Bring the 63 A isolator and the long ladder.",
        )
        self.client.force_login(self.technician)

    # -- the list ---------------------------------------------------------

    def test_a_job_still_to_come_can_be_opened_from_the_list(self):
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertContains(response, reverse("fieldjobs-job-detail", args=[self.tomorrow.pk]))

    def test_the_card_says_what_the_job_is_and_where(self):
        """A reference and a time are not enough to plan from."""
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertContains(response, "Solar Installation")
        self.assertContains(response, "Main address")
        self.assertContains(response, "Bring the 63 A isolator")

    def test_nothing_today_is_not_the_same_as_nothing_at_all(self):
        """Telling somebody with a job tomorrow to go and ask their
        supervisor sends them on an errand the screen could have saved."""
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertEqual(response.context["next_up"], self.tomorrow)
        self.assertContains(response, "Your next is FJ-8002")
        self.assertNotContains(response, "check with them")

    def test_with_nothing_at_all_it_says_so(self):
        self.tomorrow.delete()
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertIsNone(response.context["next_up"])
        self.assertContains(response, "check with them")

    # -- the job itself ---------------------------------------------------

    def test_the_instructions_can_be_read_before_the_day(self):
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.tomorrow.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bring the 63 A isolator and the long ladder.")
        self.assertContains(response, "Solar Installation")

    def test_what_to_bring_is_readable_before_the_day_too(self):
        """The checklist pinned to the visit is exactly what a van gets
        loaded from."""
        Task.objects.create(
            field_job=self.tomorrow, title="Collect the isolator from stores",
            assignee=self.technician,
        )
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.tomorrow.pk]))
        self.assertContains(response, "Collect the isolator from stores")

    def test_checking_in_is_held_back_to_the_day(self):
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.tomorrow.pk]))
        self.assertTrue(response.context["before_the_day"])
        self.assertContains(response, "Not today")
        self.assertNotContains(response, "Check in on site")

    def test_and_appears_on_the_day(self):
        today = FieldJob.objects.create(
            reference="FJ-8003", project=self.project, customer=self.customer, site=self.site,
            service_type=self.service, assigned_to=self.technician, scheduled_for=timezone.now(),
        )
        response = self.client.get(reverse("fieldjobs-job-detail", args=[today.pk]))
        self.assertFalse(response.context["before_the_day"])
        self.assertContains(response, "Check in on site")

    def test_a_job_that_was_missed_can_still_be_checked_in_to(self):
        """Late is not early. A visit that slipped a day is still done on
        the site, and the check-in is the record of that."""
        overdue = FieldJob.objects.create(
            reference="FJ-8004", project=self.project, customer=self.customer, site=self.site,
            service_type=self.service, assigned_to=self.technician,
            scheduled_for=timezone.now() - timedelta(days=2),
        )
        response = self.client.get(reverse("fieldjobs-job-detail", args=[overdue.pk]))
        self.assertFalse(response.context["before_the_day"])
        self.assertContains(response, "Check in on site")

    def test_somebody_elses_future_job_is_still_not_theirs_to_read(self):
        stranger = make_user("other@t.local", ["Technician"])
        Employee.objects.create(user=stranger, staff_id="A1-002")
        self.client.force_login(stranger)

        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.tomorrow.pk]))
        self.assertEqual(response.status_code, 404)
