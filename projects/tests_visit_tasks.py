"""
Work pinned to a visit.

A task used to attach to a project and nowhere nearer, so a technician
standing on a site saw "things to do on this project" with nothing saying
which belonged to the visit they were on — and the office had no way to say
"do this one on Tuesday's visit".

A task now points at the field job it is part of, where it is part of one.
The two stay separate records on purpose: a task is "someone should do
this", a field job is a promise to a customer to be at their site at a time,
carrying a reference, an assessment and an approval trail. What they share
is a person and a day's work, so they share a list — not a table.
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
from fieldjobs.models import FieldJob, FieldJobCrew
from hr.models import Employee

from .models import Project, ProjectCrew, Task

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class VisitTaskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar install")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Liberty Gold")
        cls.site = Site.objects.create(customer=cls.customer, name="Main yard")

        cls.manager = make_user("pm@t.local", ["Project Manager"])
        cls.lead = make_user("lead@t.local", ["Technician"])
        cls.mate = make_user("mate@t.local", ["Technician"])
        cls.lead_employee = Employee.objects.create(user=cls.lead, staff_id="A1-001")
        cls.mate_employee = Employee.objects.create(user=cls.mate, staff_id="A1-002")

        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Yard solar", customer=cls.customer, site=cls.site,
            service_type=cls.service, status=cls.status, manager=cls.manager,
        )
        ProjectCrew.objects.create(project=cls.project, employee=cls.lead_employee)
        ProjectCrew.objects.create(project=cls.project, employee=cls.mate_employee)

        cls.visit = FieldJob.objects.create(
            reference="FJ-0001", project=cls.project, customer=cls.customer, site=cls.site,
            service_type=cls.service, assigned_to=cls.lead,
            scheduled_for=timezone.now(), job_ref=cls.project.job_ref,
        )
        FieldJobCrew.objects.create(field_job=cls.visit, employee=cls.mate_employee)


class TaskBelongsToAVisitTests(VisitTaskTestCase):
    def test_a_task_on_a_visit_takes_the_visits_project(self):
        """Work on a visit is work on the project the visit serves, so the
        project is not asked for twice and answered differently."""
        task = Task.objects.create(field_job=self.visit, title="Torque the array bolts")
        self.assertEqual(task.project, self.project)

    def test_a_task_on_a_visit_with_no_project_belongs_to_the_visit_alone(self):
        """A visit can be scheduled straight off a ticket. Its checklist
        still has to live somewhere."""
        standalone = FieldJob.objects.create(
            reference="FJ-0002", customer=self.customer, site=self.site,
            service_type=self.service, assigned_to=self.lead, scheduled_for=timezone.now(),
        )
        task = Task.objects.create(field_job=standalone, title="Read the meter")
        self.assertIsNone(task.project_id)
        self.assertEqual(task.field_job, standalone)

    def test_a_task_on_no_visit_is_unchanged(self):
        task = Task.objects.create(project=self.project, title="Order the cable")
        self.assertIsNone(task.field_job_id)
        self.assertEqual(task.project, self.project)

    def test_loose_work_is_the_work_on_no_visit(self):
        Task.objects.create(field_job=self.visit, title="On the visit")
        Task.objects.create(project=self.project, title="Not on a visit")
        self.assertEqual(
            [t.title for t in Task.objects.loose()], ["Not on a visit"]
        )


class OfficePinsWorkToAVisitTests(VisitTaskTestCase):
    """The flow this was built for: an admin schedules a visit for somebody
    on the project, then says what to do on it."""

    def setUp(self):
        self.client.force_login(self.manager)

    def test_the_form_offers_only_this_projects_visits(self):
        from .forms import TaskForm

        elsewhere = Project.objects.create(
            reference="PRJ-0002", name="Somewhere else", customer=self.customer,
            service_type=self.service, status=self.status,
        )
        FieldJob.objects.create(
            reference="FJ-0009", project=elsewhere, customer=self.customer,
            service_type=self.service, assigned_to=self.lead, scheduled_for=timezone.now(),
        )
        offered = list(TaskForm(project=self.project).fields["field_job"].queryset)
        self.assertEqual(offered, [self.visit])

    def test_work_can_be_pinned_to_a_visit_when_it_is_set(self):
        response = self.client.post(
            reverse("projects-task-create", args=[self.project.pk]),
            {
                "title": "Torque the array bolts", "assignee": self.mate.pk,
                "field_job": self.visit.pk, "start_date": "", "due_date": "",
                "description": "38 Nm, both rails.",
            },
        )
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get()
        self.assertEqual(task.field_job, self.visit)
        self.assertEqual(task.project, self.project)
        self.assertEqual(task.assignee, self.mate)

    def test_a_visit_is_optional(self):
        self.client.post(
            reverse("projects-task-create", args=[self.project.pk]),
            {"title": "Order the cable", "assignee": "", "field_job": "",
             "start_date": "", "due_date": "", "description": ""},
        )
        self.assertIsNone(Task.objects.get().field_job_id)

    def test_the_project_page_says_which_visit_a_task_is_on(self):
        Task.objects.create(field_job=self.visit, title="Torque the array bolts")
        response = self.client.get(reverse("projects-detail", args=[self.project.pk]))
        self.assertContains(response, "FJ-0001")


class TheCrewReadsItOnTheVisitTests(VisitTaskTestCase):
    def setUp(self):
        self.task = Task.objects.create(
            field_job=self.visit, title="Torque the array bolts", assignee=self.mate
        )

    def test_the_visit_shows_what_there_is_to_do_on_it(self):
        self.client.force_login(self.lead)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.visit.pk]))
        self.assertContains(response, "Torque the array bolts")
        self.assertEqual(response.context["open_task_count"], 1)

    def test_the_crew_sees_it_too_not_only_the_lead(self):
        self.client.force_login(self.mate)
        response = self.client.get(reverse("fieldjobs-job-detail", args=[self.visit.pk]))
        self.assertContains(response, "Torque the array bolts")

    def test_whoever_it_belongs_to_can_finish_it_from_the_visit(self):
        self.client.force_login(self.mate)
        response = self.client.post(
            reverse("projects-task-toggle", args=[self.task.pk]),
            {"completion_note": "38 Nm both rails."},
        )
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_complete)
        self.assertEqual(self.task.completed_by, self.mate)

    def test_the_lead_can_finish_the_visits_work_that_is_not_their_own(self):
        """They are accountable for the day. A checklist item done by the
        crew in front of them is not worth a phone call to the office."""
        self.client.force_login(self.lead)
        self.client.post(reverse("projects-task-toggle", args=[self.task.pk]), {})
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_complete)
        self.assertEqual(self.task.completed_by, self.lead)

    def test_somebody_on_neither_the_visit_nor_the_task_cannot(self):
        outsider = make_user("outsider@t.local", ["Technician"])
        Employee.objects.create(user=outsider, staff_id="A1-003")
        self.client.force_login(outsider)

        response = self.client.post(reverse("projects-task-toggle", args=[self.task.pk]), {})
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_complete)


class OneListNotTwoTests(VisitTaskTestCase):
    """The phone shows a technician one day's work, not two lists that
    happen to be about the same day."""

    def setUp(self):
        self.on_the_visit = Task.objects.create(
            field_job=self.visit, title="Torque the array bolts", assignee=self.lead
        )
        self.loose = Task.objects.create(
            project=self.project, title="Order more cable", assignee=self.lead,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(self.lead)

    def test_work_on_a_visit_is_counted_on_the_visit(self):
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertEqual(response.context["next_job"].to_do, 1)
        self.assertContains(response, "1 to do")

    def test_and_is_not_repeated_in_the_list_below(self):
        """Saying it twice in two places reads as two different jobs."""
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        titles = [task.title for task in response.context["tasks"]]
        self.assertEqual(titles, ["Order more cable"])

    def test_work_on_no_visit_still_reaches_them(self):
        response = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertContains(response, "Order more cable")
        self.assertContains(response, "Not on a visit")

    def test_the_attention_queue_still_carries_it(self):
        from dashboard.services import attention_queue

        labels = [item.label for item in attention_queue(self.lead)]
        self.assertIn("Order more cable", labels)
