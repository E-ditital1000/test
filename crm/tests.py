"""
Customers & Tickets acceptance tests.

The one that matters most is the lineage: a ticket converted to a project
must carry `job_ref` across and link both records permanently. That single
thread from first call to final payment is the premise of the product, so it
is asserted directly rather than inferred from the screens.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from projects.models import Project

from .models import Customer, Site, Ticket

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class TicketFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.service_type = ServiceType.objects.create(code="solar", name="Solar Installation")
        cls.new_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True, order=1
        )
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="assigned", label="Assigned", order=2
        )
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="closed", label="Closed", is_terminal=True, order=3
        )
        StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True, order=1
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.site = Site.objects.create(customer=cls.customer, name="Duport Road")

        cls.receptionist = make_user("recep@test.local", ["Receptionist"])
        cls.supervisor = make_user("sup@test.local", ["Supervisor"])
        cls.manager = make_user("pm@test.local", ["Project Manager"])
        cls.technician = make_user("tech@test.local", ["Technician"])

    def _raise_ticket(self):
        self.client.force_login(self.receptionist)
        self.client.post(
            reverse("crm-ticket-create"),
            {
                "customer": self.customer.pk,
                "site": "",
                "service_type": self.service_type.pk,
                "priority": "high",
                "description": "Inverter fault light since Tuesday.",
            },
        )
        return Ticket.objects.latest("id")

    # -- raising ----------------------------------------------------------

    def test_raising_a_ticket_allocates_a_reference_and_logs_it(self):
        ticket = self._raise_ticket()
        self.assertEqual(ticket.reference, "TKT-0001")
        self.assertEqual(ticket.raised_by, self.receptionist)
        self.assertEqual(ticket.status, self.new_status)
        self.assertTrue(ticket.is_open)
        self.assertEqual([e.action for e in ticket.events.all()], ["Ticket created"])

    def test_references_do_not_collide(self):
        first, second = self._raise_ticket(), self._raise_ticket()
        self.assertNotEqual(first.reference, second.reference)
        self.assertEqual(second.reference, "TKT-0002")

    # -- assignment -------------------------------------------------------

    def test_supervisor_assigns_in_one_action_from_the_list(self):
        ticket = self._raise_ticket()
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("crm-ticket-assign", args=[ticket.pk]),
            {"assigned_to": self.technician.pk, "next": reverse("crm-tickets")},
        )
        ticket.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ticket.assigned_to, self.technician)
        self.assertIsNotNone(ticket.assigned_at)
        # Status advances only because the business happens to have an
        # "assigned" row; it is Settings-owned, not hardcoded behaviour.
        self.assertEqual(ticket.status.code, "assigned")
        self.assertIn("Assigned", [e.action for e in ticket.events.all()])

    def test_receptionist_cannot_assign(self):
        ticket = self._raise_ticket()
        self.client.force_login(self.receptionist)
        response = self.client.post(
            reverse("crm-ticket-assign", args=[ticket.pk]),
            {"assigned_to": self.technician.pk},
        )
        ticket.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(ticket.assigned_to)

    # -- the lineage ------------------------------------------------------

    @tag("acceptance")
    def test_conversion_carries_the_job_lineage_and_links_both_records(self):
        ticket = self._raise_ticket()
        self.client.force_login(self.manager)
        response = self.client.post(reverse("crm-ticket-convert", args=[ticket.pk]))
        ticket.refresh_from_db()
        project = Project.objects.latest("id")

        self.assertEqual(response.status_code, 302)
        # One job, one identity, carried across.
        self.assertEqual(project.job_ref, ticket.job_ref)
        # Linked both ways, permanently — neither record consumes the other.
        self.assertEqual(project.ticket, ticket)
        self.assertEqual(ticket.project, project)
        # Customer, site, service type and description carry across.
        self.assertEqual(project.customer, ticket.customer)
        self.assertEqual(project.service_type, ticket.service_type)
        self.assertEqual(project.description, ticket.description)
        self.assertEqual(project.stage, Project.SITE_ASSESSMENT)
        # The stage is an event first and a field second.
        self.assertEqual(
            [(e.from_stage, e.to_stage) for e in project.stage_events.all()],
            [("", Project.SITE_ASSESSMENT)],
        )
        self.assertIn("Converted to project", [e.action for e in ticket.events.all()])

    @tag("acceptance")
    def test_a_ticket_converts_only_once(self):
        ticket = self._raise_ticket()
        self.client.force_login(self.manager)
        self.client.post(reverse("crm-ticket-convert", args=[ticket.pk]))
        self.client.post(reverse("crm-ticket-convert", args=[ticket.pk]))
        self.assertEqual(Project.objects.filter(ticket=ticket).count(), 1)

    @tag("acceptance")
    def test_supervisor_cannot_convert(self):
        """A Supervisor assigns work; a Project Manager owns projects."""
        ticket = self._raise_ticket()
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse("crm-ticket-convert", args=[ticket.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Project.objects.count(), 0)

    # -- closing ----------------------------------------------------------

    def test_closing_uses_the_configured_terminal_status(self):
        ticket = self._raise_ticket()
        self.client.force_login(self.supervisor)
        self.client.post(reverse("crm-ticket-close", args=[ticket.pk]))
        ticket.refresh_from_db()
        self.assertFalse(ticket.is_open)
        self.assertTrue(ticket.status.is_terminal)
        self.assertIsNotNone(ticket.closed_at)

    # -- the queries the Dashboard depends on -----------------------------

    def test_unassigned_and_ageing_are_queryable(self):
        fresh = self._raise_ticket()
        old = self._raise_ticket()
        Ticket.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=30)
        )
        assigned = self._raise_ticket()
        assigned.assigned_to = self.technician
        assigned.save(update_fields=["assigned_to"])

        self.assertEqual(Ticket.objects.open().count(), 3)
        self.assertEqual(
            set(Ticket.objects.unassigned().values_list("pk", flat=True)),
            {fresh.pk, old.pk},
        )
        self.assertEqual(
            list(Ticket.objects.ageing().values_list("pk", flat=True)), [old.pk]
        )

    def test_age_label_reads_in_hours_then_days(self):
        ticket = self._raise_ticket()
        self.assertTrue(ticket.age_label.endswith("h"))
        Ticket.objects.filter(pk=ticket.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.age_label, "3d")
