"""
Dashboard and attention-queue tests.

The queue's contract is narrow and worth defending: everything blocked on
this person, ranked by how long it has waited, and nothing they could not
act on. An item somebody cannot clear is noise in a list whose whole value
is that every row is actionable.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import PolicySetting, ServiceType, StatusOption
from crm.models import Customer, Ticket
from hr.models import Employee
from projects.models import Project, Requisition

from .services import attention_queue

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class AttentionQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(key=PolicySetting.REQUISITION_THRESHOLD, value="2500")

        cls.service_type = ServiceType.objects.create(code="solar", name="Solar")
        cls.ticket_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.project_status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Ducor Hotel")

        cls.supervisor = make_user("sup@test.local", ["Supervisor"])
        cls.finance = make_user("fin@test.local", ["Finance"])
        cls.executive = make_user("exec@test.local", ["Executive"])
        cls.manager = make_user("pm@test.local", ["Project Manager"])
        cls.technician = make_user("tech@test.local", ["Technician"])

        sup_employee = Employee.objects.create(user=cls.supervisor, staff_id="A1-001")
        Employee.objects.create(user=cls.technician, staff_id="A1-002", supervisor=sup_employee)

        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Array", customer=cls.customer,
            service_type=cls.service_type, status=cls.project_status, manager=cls.manager,
        )

    def _ticket(self, reference, days_old):
        ticket = Ticket.objects.create(
            reference=reference, customer=self.customer, service_type=self.service_type,
            status=self.ticket_status, description="Needs somebody", raised_by=self.supervisor,
        )
        Ticket.objects.filter(pk=ticket.pk).update(
            created_at=timezone.now() - timedelta(days=days_old)
        )
        ticket.refresh_from_db()
        return ticket

    def _requisition(self, reference, amount):
        requisition = Requisition.objects.create(
            project=self.project, reference=reference, description="Breakers",
            amount=amount, raised_by=self.manager,
        )
        requisition.record_decision(decision="submitted", actor=self.manager)
        return requisition

    # -- ranking ----------------------------------------------------------

    @tag("acceptance")
    def test_the_queue_is_ranked_by_how_long_each_item_has_waited(self):
        self._ticket("TKT-0001", days_old=1)
        self._ticket("TKT-0002", days_old=9)
        self._ticket("TKT-0003", days_old=4)

        queue = attention_queue(self.supervisor)
        self.assertEqual([item.label.split(" ")[0] for item in queue][:3],
                         ["TKT-0002", "TKT-0003", "TKT-0001"])
        waits = [item.waiting_since for item in queue]
        self.assertEqual(waits, sorted(waits), "the oldest blocked thing is always first")

    def test_the_queue_is_not_grouped_by_type(self):
        """Ranking by wait means an old ticket outranks a new requisition."""
        self._ticket("TKT-0001", days_old=9)
        self._requisition("RQ-0001", 410)

        admin = make_user("admin@test.local", ["Admin"])
        kinds = [item.kind for item in attention_queue(admin)]
        self.assertEqual(kinds[0], "Ticket", "the oldest item leads regardless of its type")
        self.assertIn("Requisition", kinds)

    # -- nothing appears that the viewer cannot clear ---------------------

    @tag("acceptance")
    def test_a_requisition_above_the_threshold_stays_out_of_a_finance_queue(self):
        """
        Finance cannot approve above the threshold, so putting it in their
        queue would be noise they can do nothing about.
        """
        self._requisition("RQ-0001", 4200)

        finance_kinds = [item.kind for item in attention_queue(self.finance)]
        self.assertNotIn("Requisition", finance_kinds)

        executive_kinds = [item.kind for item in attention_queue(self.executive)]
        self.assertIn("Requisition", executive_kinds)

    def test_below_the_threshold_it_reaches_finance(self):
        self._requisition("RQ-0002", 410)
        self.assertIn("Requisition", [item.kind for item in attention_queue(self.finance)])

    def test_a_technician_has_no_queue(self):
        self._ticket("TKT-0001", days_old=5)
        self._requisition("RQ-0001", 410)
        self.assertEqual(attention_queue(self.technician), [])

    def test_every_item_carries_a_link_to_the_record(self):
        self._ticket("TKT-0001", days_old=2)
        for item in attention_queue(self.supervisor):
            self.assertTrue(item.url.startswith("/"), f"{item.kind} has no link")

    # -- the screens ------------------------------------------------------

    def test_the_dashboard_shows_the_queue(self):
        self._ticket("TKT-0001", days_old=3)
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("dashboard-index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("TKT-0001", response.content.decode())

    def test_the_command_view_is_executive_only(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("dashboard-command-view")).status_code, 403)

        self.client.force_login(self.executive)
        self.assertEqual(self.client.get(reverse("dashboard-command-view")).status_code, 200)

    def test_command_view_figures_are_counted_from_rows(self):
        self._ticket("TKT-0001", days_old=1)
        self._ticket("TKT-0002", days_old=2)

        self.client.force_login(self.executive)
        context = self.client.get(reverse("dashboard-command-view")).context
        self.assertEqual(context["open_tickets"], Ticket.objects.open().count())
        self.assertEqual(context["unassigned"], Ticket.objects.unassigned().count())
        self.assertEqual(
            context["active_projects"],
            Project.objects.filter(completed_at__isnull=True).count(),
        )

    def test_a_user_with_no_module_permissions_is_told_so(self):
        """Distinct from an empty system: they hold nothing, not nothing exists."""
        bare = make_user("bare@test.local", ["Employee"])
        self.client.force_login(bare)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertIn("Nothing is assigned to your account yet", body)
