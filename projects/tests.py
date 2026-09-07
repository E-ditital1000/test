"""
Job-lineage tests. "One record per job, carried through" is the premise of
the product, so it is asserted at the schema level here: a ticket, the
project it converts to, the field job worked under it and the invoice
raised from it all resolve to one traceable job.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.models import PolicySetting, ServiceType, StatusOption
from crm.models import Customer, Site, Ticket
from fieldjobs.models import FieldJob
from finance.models import Invoice, InvoiceLine

from .models import Project, ProjectStageEvent, Requisition

User = get_user_model()


class JobLineageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm", email="pm@test.local")
        self.service_type = ServiceType.objects.create(code="solar", name="Solar")
        self.ticket_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        self.project_status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        self.customer = Customer.objects.create(name="Riverbend Estate")
        self.site = Site.objects.create(customer=self.customer, name="Block A")

        self.ticket = Ticket.objects.create(
            reference="TKT-0001",
            customer=self.customer,
            site=self.site,
            service_type=self.service_type,
            status=self.ticket_status,
            description="No power to block A",
            raised_by=self.user,
        )

    def _convert_to_project(self):
        """Conversion carries the customer, site, service type and lineage."""
        return Project.objects.create(
            reference="PRJ-0001",
            name="Block A rewire",
            description=self.ticket.description,
            job_ref=self.ticket.job_ref,
            ticket=self.ticket,
            customer=self.ticket.customer,
            site=self.ticket.site,
            service_type=self.ticket.service_type,
            status=self.project_status,
            manager=self.user,
        )

    def test_one_job_ref_runs_from_ticket_to_invoice(self):
        project = self._convert_to_project()
        field_job = FieldJob.objects.create(
            reference="FJ-0001",
            job_ref=project.job_ref,
            ticket=self.ticket,
            project=project,
            customer=self.customer,
            site=self.site,
            service_type=self.service_type,
            assigned_to=self.user,
            scheduled_for=timezone.now(),
        )
        invoice = Invoice.objects.create(
            number="INV-0001",
            job_ref=project.job_ref,
            project=project,
            customer=self.customer,
        )

        job_ref = self.ticket.job_ref
        self.assertEqual(
            {project.job_ref, field_job.job_ref, invoice.job_ref},
            {job_ref},
            "every record in the chain must carry one job identity",
        )

        # And the job is reachable from the identity alone, in either direction.
        self.assertEqual(Ticket.objects.filter(job_ref=job_ref).count(), 1)
        self.assertEqual(Project.objects.filter(job_ref=job_ref).count(), 1)
        self.assertEqual(FieldJob.objects.filter(job_ref=job_ref).count(), 1)
        self.assertEqual(Invoice.objects.filter(job_ref=job_ref).count(), 1)

    def test_conversion_links_both_records_permanently(self):
        project = self._convert_to_project()

        self.assertEqual(project.ticket, self.ticket)
        self.assertEqual(self.ticket.project, project)
        self.assertEqual(project.customer, self.ticket.customer)
        self.assertEqual(project.service_type, self.ticket.service_type)

    def test_stage_advance_is_recorded_as_an_event_with_actor_and_time(self):
        project = self._convert_to_project()
        project.advance_to(Project.SITE_ASSESSMENT, actor=self.user, note="Survey booked")
        project.advance_to(Project.EXECUTION, actor=self.user)

        events = list(ProjectStageEvent.objects.filter(project=project).order_by("created_at"))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].from_stage, Project.REQUEST)
        self.assertEqual(events[0].to_stage, Project.SITE_ASSESSMENT)
        self.assertEqual(events[0].actor, self.user)
        self.assertEqual(events[1].to_stage, Project.EXECUTION)

        project.refresh_from_db()
        self.assertEqual(project.stage, Project.EXECUTION)

    def test_project_cost_and_revenue_are_computed_not_stored(self):
        project = self._convert_to_project()
        invoice = Invoice.objects.create(
            number="INV-0002",
            job_ref=project.job_ref,
            project=project,
            customer=self.customer,
            state=Invoice.SENT,
        )
        InvoiceLine.objects.create(
            invoice=invoice, description="Labour", quantity=2, unit_price=Decimal("25000")
        )
        InvoiceLine.objects.create(
            invoice=invoice, description="Cable", quantity=1, unit_price=Decimal("10000")
        )

        self.assertEqual(invoice.total, Decimal("60000"))
        self.assertEqual(invoice.outstanding, Decimal("60000"))
        self.assertEqual(invoice.derived_state(), Invoice.SENT)

        # No stored total exists to drift from the lines.
        field_names = {f.name for f in Invoice._meta.get_fields()}
        self.assertNotIn("total", field_names)
        self.assertNotIn("amount_paid", field_names)


class RequisitionThresholdTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm2", email="pm2@test.local")
        service_type = ServiceType.objects.create(code="elec", name="Electrical")
        status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active"
        )
        customer = Customer.objects.create(name="Acme")
        self.project = Project.objects.create(
            reference="PRJ-0002",
            name="Acme fitout",
            customer=customer,
            service_type=service_type,
            status=status,
        )
        PolicySetting.objects.create(
            key=PolicySetting.REQUISITION_THRESHOLD, value="100000"
        )

    def test_threshold_comes_from_settings(self):
        small = Requisition.objects.create(
            project=self.project,
            reference="REQ-0001",
            description="Consumables",
            amount=Decimal("50000"),
            raised_by=self.user,
        )
        large = Requisition.objects.create(
            project=self.project,
            reference="REQ-0002",
            description="Switchgear",
            amount=Decimal("250000"),
            raised_by=self.user,
        )

        self.assertFalse(small.requires_executive_approval())
        self.assertTrue(large.requires_executive_approval())

    def test_changing_the_threshold_changes_the_answer_without_a_release(self):
        requisition = Requisition.objects.create(
            project=self.project,
            reference="REQ-0003",
            description="Cable drum",
            amount=Decimal("120000"),
            raised_by=self.user,
        )
        self.assertTrue(requisition.requires_executive_approval())

        PolicySetting.objects.filter(key=PolicySetting.REQUISITION_THRESHOLD).update(
            value="500000"
        )
        self.assertFalse(requisition.requires_executive_approval())


class TaskAssignmentTests(TestCase):
    """
    A task on a project goes to somebody on that project. Offering the whole
    register invites picking a name with nothing to do with the job, and
    buries the few people who are actually on it.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from accounts.models import Role, UserRole
        from config.models import ServiceType, StatusOption
        from crm.models import Customer
        from hr.models import Employee

        from .models import Project, ProjectCrew

        call_command("seed_permissions", verbosity=0)
        User = get_user_model()

        service_type = ServiceType.objects.create(code="solar", name="Solar")
        status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        customer = Customer.objects.create(name="Ducor Hotel")

        def staff(email, staff_id):
            user = User.objects.create_user(
                username=email, email=email, password="Testing!12345"
            )
            user.must_reset_password = False
            user.save(update_fields=["must_reset_password"])
            UserRole.objects.create(user=user, role=Role.objects.get(name="Technician"))
            return Employee.objects.create(user=user, staff_id=staff_id)

        cls.on_crew = staff("on@test.local", "A1-001")
        cls.also_crew = staff("also@test.local", "A1-002")
        cls.elsewhere = staff("else@test.local", "A1-003")
        cls.other = staff("other@test.local", "A1-004")

        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Array", customer=customer,
            service_type=service_type, status=status,
        )
        ProjectCrew.objects.create(project=cls.project, employee=cls.on_crew)
        ProjectCrew.objects.create(project=cls.project, employee=cls.also_crew)

    def test_only_the_projects_crew_can_be_assigned_a_task(self):
        from .forms import TaskForm

        offered = set(TaskForm(project=self.project).fields["assignee"].queryset)
        self.assertEqual(offered, {self.on_crew.user, self.also_crew.user})
        self.assertNotIn(self.elsewhere.user, offered)

    def test_assigning_someone_off_the_crew_is_refused(self):
        """Enforced by the form on save, not merely absent from the dropdown."""
        from .forms import TaskForm

        form = TaskForm(
            {"title": "Run cabling", "assignee": self.elsewhere.user.pk, "due_date": ""},
            project=self.project,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("assignee", form.errors)

    def test_before_a_crew_exists_everyone_is_offered(self):
        """
        A form that cannot be used is worse than a long list, so an empty
        crew falls back to the whole register and says so.
        """
        from config.models import StatusOption
        from crm.models import Customer

        from .forms import TaskForm
        from .models import Project

        bare = Project.objects.create(
            reference="PRJ-0002", name="No crew yet",
            customer=Customer.objects.first(),
            service_type=self.project.service_type,
            status=StatusOption.objects.get(code="active"),
        )
        field = TaskForm(project=bare).fields["assignee"]
        self.assertEqual(field.queryset.count(), 4)
        self.assertIn("No crew on this project yet", field.help_text)


class TaskVisibilityTests(TestCase):
    """
    A task the assignee never sees is a note to whoever wrote it. These
    assert it reaches them.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone as tz

        from accounts.models import Role, UserRole
        from config.models import ServiceType, StatusOption
        from crm.models import Customer
        from hr.models import Employee

        from .models import Project, ProjectCrew, Task

        call_command("seed_permissions", verbosity=0)
        User = get_user_model()

        service_type = ServiceType.objects.create(code="solar", name="Solar")
        status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        customer = Customer.objects.create(name="Ducor Hotel")

        def staff(email, staff_id, role):
            user = User.objects.create_user(
                username=email, email=email, password="Testing!12345"
            )
            user.must_reset_password = False
            user.save(update_fields=["must_reset_password"])
            UserRole.objects.create(user=user, role=Role.objects.get(name=role))
            Employee.objects.create(user=user, staff_id=staff_id)
            return user

        cls.tech = staff("tech@test.local", "A1-001", "Technician")
        cls.other = staff("other@test.local", "A1-002", "Technician")
        cls.manager = staff("pm@test.local", "A1-003", "Project Manager")

        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Array", customer=customer,
            service_type=service_type, status=status, manager=cls.manager,
        )
        ProjectCrew.objects.create(
            project=cls.project, employee=Employee.objects.get(user=cls.tech)
        )

        today = tz.localdate()
        cls.overdue = Task.objects.create(
            project=cls.project, title="Run cabling", assignee=cls.tech,
            due_date=today - timedelta(days=3), assigned_by=cls.manager,
        )
        cls.upcoming = Task.objects.create(
            project=cls.project, title="Commission inverter", assignee=cls.tech,
            due_date=today + timedelta(days=4), assigned_by=cls.manager,
        )
        cls.someone_elses = Task.objects.create(
            project=cls.project, title="Not yours", assignee=cls.other,
            due_date=today - timedelta(days=1),
        )

    def test_open_work_sorts_above_finished_work(self):
        """
        SQLite sorts NULLs first and PostgreSQL sorts them last, so an
        unqualified `ordering` silently inverts between here and the server.
        """
        from django.utils import timezone as tz

        from .models import Task

        self.upcoming.completed_at = tz.now()
        self.upcoming.save(update_fields=["completed_at"])

        order = list(Task.objects.filter(project=self.project).values_list("title", flat=True))
        self.assertLess(
            order.index("Run cabling"), order.index("Commission inverter"),
            "open work must lead, on either database",
        )

    def test_a_task_reaches_the_assignees_dashboard(self):
        self.client.force_login(self.tech)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertIn("My tasks", body)
        # And the overdue one is in the queue, ranked from when it fell due.
        self.assertIn("Run cabling", body)

    def test_a_task_reaches_the_assignees_phone(self):
        self.client.force_login(self.tech)
        body = self.client.get(reverse("fieldjobs-my-jobs")).content.decode()
        self.assertIn("Run cabling", body)
        self.assertIn("Commission inverter", body)

    def test_my_tasks_shows_only_your_own(self):
        self.client.force_login(self.tech)
        body = self.client.get(reverse("dashboard-my-tasks")).content.decode()
        self.assertIn("Run cabling", body)
        self.assertNotIn("Not yours", body)

    def test_the_attention_queue_carries_overdue_tasks_only(self):
        from dashboard.services import attention_queue

        labels = [item.label for item in attention_queue(self.tech)]
        self.assertIn("Run cabling", labels)
        self.assertNotIn("Commission inverter", labels, "a task not yet due is not blocked work")
        self.assertNotIn("Not yours", labels)

    def test_the_assignee_can_finish_their_own_task(self):
        """
        A list only its manager can tick is a list that goes stale. The
        technician holds no manage_project and must still be able to.
        """
        from .models import Task

        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("projects-task-toggle", args=[self.overdue.pk]),
            {"completion_note": "Pulled 60 m, terminated both ends."},
        )
        self.assertEqual(response.status_code, 302)

        task = Task.objects.get(pk=self.overdue.pk)
        self.assertTrue(task.is_complete)
        self.assertEqual(task.completed_by, self.tech)
        self.assertEqual(task.completion_note, "Pulled 60 m, terminated both ends.")

    def test_somebody_elses_task_cannot_be_finished_for_them(self):
        from .models import Task

        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("projects-task-toggle", args=[self.someone_elses.pk]), {}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Task.objects.get(pk=self.someone_elses.pk).is_complete)

    def test_creating_a_task_records_who_assigned_it(self):
        from .models import Task

        self.client.force_login(self.manager)
        self.client.post(
            reverse("projects-task-create", args=[self.project.pk]),
            {
                "title": "Fit the isolator", "assignee": self.tech.pk,
                "due_date": "", "description": "Bring the 63 A unit.",
            },
        )
        task = Task.objects.get(title="Fit the isolator")
        self.assertEqual(task.assigned_by, self.manager)
        self.assertEqual(task.description, "Bring the 63 A unit.")
