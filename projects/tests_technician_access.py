"""
What a technician can reach on Projects.

They carry work that belongs to a project and can raise a requisition
against one, but held no right to open it: Projects was absent from their
shell and the route refused them, so the job they were sent to do had no
context they could reach.

They now see the projects they are on and no others — the rule their job
list already follows. Scope does the limiting, so this is asserted on what
comes back rather than on any screen remembering to filter.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, UserRole
from accounts.navigation import visible_items
from config.models import ServiceType, StatusOption
from crm.models import Customer
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


class TechnicianProjectAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)

        service_type = ServiceType.objects.create(code="solar", name="Solar")
        status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        customer = Customer.objects.create(name="Riverbend Estate")

        cls.technician = make_user("tech@test.local", ["Technician"])
        cls.employee = Employee.objects.create(user=cls.technician, staff_id="A1-002")
        cls.manager = make_user("pm@test.local", ["Project Manager"])

        cls.theirs = Project.objects.create(
            reference="PRJ-0001", name="Block A rewire", customer=customer,
            service_type=service_type, status=status, manager=cls.manager,
        )
        ProjectCrew.objects.create(project=cls.theirs, employee=cls.employee)

        cls.somebody_elses = Project.objects.create(
            reference="PRJ-0002", name="Somewhere they are not", customer=customer,
            service_type=service_type, status=status, manager=cls.manager,
        )

    def setUp(self):
        self.client.force_login(self.technician)

    # -- what they can reach ----------------------------------------------

    def test_the_project_list_holds_only_the_projects_they_are_on(self):
        response = self.client.get(reverse("projects-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Block A rewire")
        self.assertNotContains(response, "Somewhere they are not")

    def test_they_can_open_a_project_they_are_crewed_on(self):
        response = self.client.get(reverse("projects-detail", args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 200)

    def test_a_project_they_are_not_on_is_not_found(self):
        """Not 403: a record outside their scope does not exist as far as
        they are concerned, and a refusal would confirm it is there."""
        response = self.client.get(reverse("projects-detail", args=[self.somebody_elses.pk]))
        self.assertEqual(response.status_code, 404)

    def test_projects_appears_in_their_shell(self):
        self.assertIn("Projects", {item.label for item in visible_items(self.technician)})

    # -- and what they still cannot do ------------------------------------

    def test_they_are_not_shown_what_the_job_is_worth(self):
        """Cost is its own permission, and a technician does not hold it."""
        response = self.client.get(reverse("projects-detail", args=[self.theirs.pk]))
        self.assertFalse(response.context["can_see_cost"])
        self.assertIsNone(response.context["cost"])

    def test_they_cannot_manage_the_project(self):
        response = self.client.get(reverse("projects-detail", args=[self.theirs.pk]))
        self.assertFalse(response.context["can_manage"])

    def test_they_cannot_add_a_task_to_it(self):
        """The screen offers no form; the route refuses it regardless."""
        response = self.client.post(
            reverse("projects-task-create", args=[self.theirs.pk]),
            {"title": "Something I set myself", "assignee": "", "description": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Task.objects.exists())

    def test_they_cannot_advance_its_stage(self):
        response = self.client.post(reverse("projects-advance", args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 403)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.stage, Project.REQUEST)

    def test_a_technician_on_no_project_at_all_sees_an_empty_list(self):
        nobody = make_user("spare@test.local", ["Technician"])
        Employee.objects.create(user=nobody, staff_id="A1-003")
        self.client.force_login(nobody)

        response = self.client.get(reverse("projects-list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Block A rewire")
