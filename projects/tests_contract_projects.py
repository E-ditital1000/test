"""
A project that did not come from a ticket.

A ticket is a small job — a customer rings, or it comes off the day's
assignments. A project can be a contract: fifty kilowatts of solar across
twelve health facilities over six months, signed with an institution. That
never was a service call, and routing it through a ticket would have put an
invented phone call at the head of a six-month job.

Conversion is unchanged and is still the usual route. This is the other kind.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site

from .models import Project

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class ContractProjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar Installation")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Catholic Relief Services")
        cls.site = Site.objects.create(customer=cls.customer, name="Head office")
        cls.manager = make_user("pm@t.local", ["Project Manager"])
        cls.technician = make_user("tech@t.local", ["Technician"])

    def _payload(self, **extra):
        payload = {
            "name": "CRS: 50 kW solar at 12 health facilities",
            "customer": self.customer.pk,
            "site": "",
            "service_type": self.service.pk,
            "status": self.status.pk,
            "manager": self.manager.pk,
            "start_date": date.today().isoformat(),
            "target_end_date": (date.today() + timedelta(days=182)).isoformat(),
            "description": "Twelve facilities, six months.",
        }
        payload.update(extra)
        return payload

    def test_a_contract_can_be_started_without_a_ticket(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("projects-create"), self._payload())
        self.assertEqual(response.status_code, 302)

        project = Project.objects.get()
        self.assertIsNone(project.ticket_id, "a contract was never a phone call")
        self.assertEqual(project.name, "CRS: 50 kW solar at 12 health facilities")
        self.assertEqual(project.manager, self.manager)

    def test_it_gets_its_own_reference_and_its_own_job(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("projects-create"), self._payload())
        project = Project.objects.get()
        self.assertTrue(project.reference.startswith("PRJ-"))
        self.assertIsNotNone(project.job_ref)

    def test_the_six_month_run_is_recorded(self):
        """What was agreed with the customer, which is the thing being tracked."""
        self.client.force_login(self.manager)
        self.client.post(reverse("projects-create"), self._payload())
        project = Project.objects.get()
        self.assertEqual(project.start_date, date.today())
        self.assertEqual(project.target_end_date, date.today() + timedelta(days=182))

    def test_it_cannot_be_due_to_finish_before_it_starts(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("projects-create"), self._payload(
            start_date=date.today().isoformat(),
            target_end_date=(date.today() - timedelta(days=1)).isoformat(),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be due to finish before it starts")
        self.assertFalse(Project.objects.exists())

    def test_how_it_started_is_on_the_record(self):
        """So nobody reading it later wonders which ticket went missing."""
        self.client.force_login(self.manager)
        self.client.post(reverse("projects-create"), self._payload())
        project = Project.objects.get()
        note = project.stage_events.first().note
        self.assertIn("not converted from a ticket", note)

    def test_a_technician_cannot_start_one(self):
        self.client.force_login(self.technician)
        self.assertEqual(self.client.get(reverse("projects-create")).status_code, 403)

    def test_the_button_is_on_the_projects_list(self):
        """The one the client said was missing."""
        self.client.force_login(self.manager)
        response = self.client.get(reverse("projects-list"))
        self.assertContains(response, reverse("projects-create"))

    def test_conversion_still_works_and_still_carries_the_ticket(self):
        """The usual route is unchanged — this adds a door, it does not move one."""
        from crm.models import Ticket

        ticket_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        ticket = Ticket.objects.create(
            reference="TKT-9001", customer=self.customer, service_type=self.service,
            status=ticket_status, description="Inverter down.",
        )
        self.client.force_login(self.manager)
        self.client.post(reverse("crm-ticket-convert", args=[ticket.pk]))

        converted = Project.objects.get(ticket=ticket)
        self.assertEqual(converted.job_ref, ticket.job_ref)
