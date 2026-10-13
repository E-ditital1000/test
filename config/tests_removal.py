"""
Correcting and removing records.

A-1: "admin or HR to have the right to edit and delete anything on the
platform... task, project, field job, an employee, and any other thing."

Two of those could not even be *edited*. A project could be created and
advanced through its stages and never corrected; a task could be ticked
complete and nothing else; a visit booked for the wrong day was permanent.

On deleting, A-1 chose: erase a plan outright, void a record of what
happened. The schema already drew that line and nobody had noticed —
everything that is evidence is PROTECT, everything that is part of the thing
being removed is CASCADE. So the rule is enforced by the database rather
than by a view remembering to check, and what these pin is that the refusal
explains itself instead of arriving as a 500.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditEntry, Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site
from fieldjobs.models import Assessment, FieldJob
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


class RemovingRecordsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Catholic Relief Services")
        cls.site = Site.objects.create(customer=cls.customer, name="Zwedru Clinic")

        cls.admin = make_user("admin.rm@test.local", ["Admin"])
        cls.hr = make_user("hr.rm@test.local", ["HR"])
        cls.technician = make_user("tech.rm@test.local", ["Technician"])

    def setUp(self):
        self.project = Project.objects.create(
            reference="PRJ-9001", name="Solar at Zwedru", customer=self.customer,
            site=self.site, service_type=self.service, status=self.status,
            manager=self.admin,
        )

    # ---- who holds it ----------------------------------------------------

    @tag("acceptance")
    def test_hr_can_remove_a_record(self):
        """A-1 asked for Admin or HR, and HR did not hold it before."""
        from accounts.decorators import user_has_permission

        self.assertTrue(user_has_permission(self.hr, "manage_records"))
        self.assertTrue(user_has_permission(self.admin, "manage_records"))

    def test_a_technician_does_not(self):
        from accounts.decorators import user_has_permission

        self.assertFalse(user_has_permission(self.technician, "manage_records"))

    def test_money_is_not_included(self):
        """
        A-1's choice: invoices, payments and quotations stay with Finance, so
        the person who records a payment is not the person who can make it
        disappear.
        """
        from accounts.decorators import user_has_permission

        for code in ("issue_invoice", "record_payment", "manage_quotations"):
            self.assertFalse(
                user_has_permission(self.hr, code),
                f"manage_records must not have quietly brought {code} with it",
            )

    # ---- a plan goes -----------------------------------------------------

    @tag("acceptance")
    def test_a_task_created_by_mistake_is_simply_gone(self):
        task = Task.objects.create(
            project=self.project, title="Raised against the wrong project",
            assignee=self.technician,
        )
        self.client.force_login(self.hr)
        response = self.client.post(reverse("projects-task-remove", args=[task.pk]),
                                    {"reason": "Wrong project"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_removing_is_recorded_with_who_and_why(self):
        """The audit row is the only trace the record ever existed."""
        task = Task.objects.create(project=self.project, title="Duplicate")
        self.client.force_login(self.hr)
        self.client.post(reverse("projects-task-remove", args=[task.pk]),
                         {"reason": "Entered twice"})

        entry = AuditEntry.objects.filter(action="task.removed").latest("created_at")
        self.assertEqual(entry.actor, self.hr)
        self.assertEqual(entry.reason, "Entered twice")
        self.assertIn("Duplicate", str(entry.before))

    def test_an_untouched_project_goes_with_its_own_parts(self):
        Task.objects.create(project=self.project, title="Part of the project")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("projects-remove", args=[self.project.pk]),
                                    {"reason": "Created twice"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(Task.objects.exists(), "its tasks are part of it")

    # ---- evidence holds it back ------------------------------------------

    @tag("acceptance")
    def test_a_visit_somebody_assessed_cannot_be_erased(self):
        """
        The line A-1 drew: erase the plan, never the history. An assessment
        is the evidence a site was in a given state, and PROTECT is what
        makes that true rather than a view remembering to check.
        """
        job = FieldJob.objects.create(
            reference="FJ-9001", project=self.project, customer=self.customer,
            site=self.site, service_type=self.service, assigned_to=self.technician,
            scheduled_for=timezone.now(),
        )
        Assessment.objects.create(
            field_job=job, service_type=self.service, technician=self.technician,
            client_uuid="00000000-0000-0000-0000-000000009001",
            device_timestamp=timezone.now(), location_unavailable=True,
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("fieldjobs-job-remove", args=[job.pk]),
                                    {"reason": "Booked twice"}, follow=True)
        self.assertTrue(FieldJob.objects.filter(pk=job.pk).exists())
        said = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("cannot be removed", said)
        self.assertIn("assessment", said, "it must name what is in the way")

    def test_a_refusal_leaves_no_audit_row_claiming_it_went(self):
        job = FieldJob.objects.create(
            reference="FJ-9002", project=self.project, customer=self.customer,
            site=self.site, service_type=self.service, assigned_to=self.technician,
            scheduled_for=timezone.now(),
        )
        Assessment.objects.create(
            field_job=job, service_type=self.service, technician=self.technician,
            client_uuid="00000000-0000-0000-0000-000000009002",
            device_timestamp=timezone.now(), location_unavailable=True,
        )
        self.client.force_login(self.admin)
        self.client.post(reverse("fieldjobs-job-remove", args=[job.pk]), {"reason": "x"})
        self.assertFalse(
            AuditEntry.objects.filter(action="fieldjob.removed").exists(),
            "nothing was removed, so an audit row saying so would be a lie",
        )

    def test_a_visit_nobody_has_been_to_goes(self):
        job = FieldJob.objects.create(
            reference="FJ-9003", project=self.project, customer=self.customer,
            site=self.site, service_type=self.service, assigned_to=self.technician,
            scheduled_for=timezone.now() + timedelta(days=3),
        )
        self.client.force_login(self.hr)
        self.client.post(reverse("fieldjobs-job-remove", args=[job.pk]),
                         {"reason": "Customer moved it"})
        self.assertFalse(FieldJob.objects.filter(pk=job.pk).exists())

    def test_a_project_with_a_visit_on_it_is_held_back(self):
        FieldJob.objects.create(
            reference="FJ-9004", project=self.project, customer=self.customer,
            site=self.site, service_type=self.service, assigned_to=self.technician,
            scheduled_for=timezone.now(),
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("projects-remove", args=[self.project.pk]),
                                    {"reason": "Wrong"}, follow=True)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        said = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("field job", said)

    # ---- correcting ------------------------------------------------------

    @tag("acceptance")
    def test_a_project_can_be_corrected(self):
        """It could be created and advanced, and never edited."""
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("projects-edit", args=[self.project.pk]),
            {
                "name": "Solar at Zwedru Health Centre",
                "customer": self.customer.pk,
                "site": self.site.pk,
                "service_type": self.service.pk,
                "status": self.status.pk,
                "manager": self.admin.pk,
                "description": "Corrected name.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "Solar at Zwedru Health Centre")

    def test_correcting_is_audited(self):
        self.client.force_login(self.hr)
        self.client.post(
            reverse("projects-edit", args=[self.project.pk]),
            {
                "name": "A better name", "customer": self.customer.pk,
                "site": self.site.pk, "service_type": self.service.pk,
                "status": self.status.pk, "manager": self.admin.pk,
                "description": "",
            },
        )
        self.assertTrue(AuditEntry.objects.filter(action="project.corrected").exists())

    def test_a_technician_cannot_correct_or_remove(self):
        task = Task.objects.create(project=self.project, title="Theirs to do")
        self.client.force_login(self.technician)
        self.assertEqual(
            self.client.post(reverse("projects-task-remove", args=[task.pk])).status_code,
            403,
        )
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())
