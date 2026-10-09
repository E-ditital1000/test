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

    # -- site -------------------------------------------------------------

    def test_the_new_ticket_page_can_offer_a_customers_sites_before_submit(self):
        # The list used to fill only on submit, and submitting created the
        # ticket, so no site could ever be chosen for a new one.
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("crm-ticket-create"))
        self.assertContains(response, 'id="site-picker"')
        self.assertEqual(
            response.context["form"].site_picker()["sites"][self.customer.pk],
            [{"id": self.site.pk, "label": "Duport Road"}],
        )

    def test_a_ticket_keeps_the_site_chosen_for_it(self):
        self.client.force_login(self.receptionist)
        self.client.post(reverse("crm-ticket-create"), {
            "customer": self.customer.pk,
            "site": self.site.pk,
            "service_type": self.service_type.pk,
            "priority": "high",
            "description": "Inverter fault light since Tuesday.",
        })
        self.assertEqual(Ticket.objects.get().site, self.site)

    def test_a_site_of_another_customer_is_refused(self):
        other = Customer.objects.create(name="Somewhere Else")
        elsewhere = Site.objects.create(customer=other, name="Their depot")
        self.client.force_login(self.receptionist)
        response = self.client.post(reverse("crm-ticket-create"), {
            "customer": self.customer.pk,
            "site": elsewhere.pk,
            "service_type": self.service_type.pk,
            "priority": "high",
            "description": "Inverter fault light since Tuesday.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("site"))
        self.assertFalse(Ticket.objects.exists())

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


class TicketWorkWindowTests(TestCase):
    """
    When a ticket is handed to a technician, over what days.

    `assigned_at` records the moment a supervisor pressed the button, which
    is not the same question. Without these, handing a ticket over said only
    that it was somebody else's problem now.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.service_type = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.supervisor = make_user("sup.window@test.local", ["Supervisor"])
        cls.technician = make_user("tech.window@test.local", ["Technician"])

    def _ticket(self):
        return Ticket.objects.create(
            reference="TKT-7001", customer=self.customer,
            service_type=self.service_type, status=self.status,
            description="Inverter fault.",
        )

    def test_assigning_records_the_days_the_work_is_expected(self):
        from datetime import date, timedelta

        ticket = self._ticket()
        self.client.force_login(self.supervisor)
        start = date.today()
        due = start + timedelta(days=3)
        self.client.post(reverse("crm-ticket-assign", args=[ticket.pk]), {
            "assigned_to": self.technician.pk,
            "start_date": start.isoformat(),
            "due_date": due.isoformat(),
        })
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to, self.technician)
        self.assertEqual(ticket.start_date, start)
        self.assertEqual(ticket.due_date, due)

    def test_the_dates_are_optional(self):
        """Plenty of tickets are "today, when you get a minute"."""
        ticket = self._ticket()
        self.client.force_login(self.supervisor)
        self.client.post(reverse("crm-ticket-assign", args=[ticket.pk]), {
            "assigned_to": self.technician.pk,
        })
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to, self.technician)
        self.assertIsNone(ticket.start_date)

    def test_a_window_that_ends_before_it_starts_is_refused(self):
        from datetime import date, timedelta

        ticket = self._ticket()
        self.client.force_login(self.supervisor)
        self.client.post(reverse("crm-ticket-assign", args=[ticket.pk]), {
            "assigned_to": self.technician.pk,
            "start_date": date.today().isoformat(),
            "due_date": (date.today() - timedelta(days=1)).isoformat(),
        })
        ticket.refresh_from_db()
        self.assertIsNone(ticket.assigned_to, "a backwards window assigns nobody")

    def test_the_window_is_on_the_ticket_history(self):
        """So "when was this due" is answerable months later."""
        from datetime import date, timedelta

        ticket = self._ticket()
        self.client.force_login(self.supervisor)
        self.client.post(reverse("crm-ticket-assign", args=[ticket.pk]), {
            "assigned_to": self.technician.pk,
            "start_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=2)).isoformat(),
        })
        detail = [e.detail for e in ticket.events.all() if e.action in ("Assigned", "Reassigned")]
        self.assertTrue(any("–" in d or "due" in d for d in detail), detail)


class AssignWhileRaisingTests(TestCase):
    """
    Whoever raises a ticket and already knows who is going can say so.

    The step was a disabled box reading "A Supervisor assigns from the ticket
    list" — shown to supervisors too. Somebody who had just agreed on the
    phone which technician was attending had to save, leave, find the ticket
    again and assign it there. The waiting was the system's, not the work's.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service_type = ServiceType.objects.create(code="solar", name="Solar")
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True, order=1
        )
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="assigned", label="Assigned", order=2
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        # Only Admin holds both create_ticket and assign_ticket today: a
        # Receptionist raises and cannot assign, a Supervisor assigns and
        # cannot raise. That is a role setting, not a rule of the system —
        # see the last test.
        cls.admin = make_user("admin.raise@test.local", ["Admin"])
        cls.receptionist = make_user("recep.raise@test.local", ["Receptionist"])
        cls.technician = make_user("tech.raise@test.local", ["Technician"])

    def _payload(self, **extra):
        payload = {
            "customer": self.customer.pk, "site": "",
            "service_type": self.service_type.pk, "priority": "high",
            "description": "Inverter fault light since Tuesday.",
        }
        payload.update(extra)
        return payload

    def test_whoever_may_assign_can_do_it_as_they_raise_it(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("crm-ticket-create"),
            self._payload(assigned_to=self.technician.pk),
        )
        self.assertEqual(response.status_code, 302)

        ticket = Ticket.objects.get()
        self.assertEqual(ticket.assigned_to, self.technician)
        self.assertIsNotNone(ticket.assigned_at)

    def test_it_goes_through_the_same_door_as_the_ticket_list(self):
        """Status advanced, trail written, technician told — not a lesser
        version of assigning because it happened here."""
        from django.core import mail

        mail.outbox = []
        self.client.force_login(self.admin)
        self.client.post(
            reverse("crm-ticket-create"),
            self._payload(assigned_to=self.technician.pk),
        )
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.status.code, "assigned")
        self.assertIn("Assigned", [e.action for e in ticket.events.all()])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["tech.raise@test.local"])

    def test_the_days_can_be_set_while_raising_it(self):
        from datetime import date, timedelta

        start = date.today()
        self.client.force_login(self.admin)
        self.client.post(reverse("crm-ticket-create"), self._payload(
            assigned_to=self.technician.pk,
            start_date=start.isoformat(),
            due_date=(start + timedelta(days=2)).isoformat(),
        ))
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.start_date, start)
        self.assertEqual(ticket.due_date, start + timedelta(days=2))

    def test_leaving_it_unassigned_still_works(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("crm-ticket-create"), self._payload())
        ticket = Ticket.objects.get()
        self.assertIsNone(ticket.assigned_to)
        self.assertTrue(ticket.status.is_default, "it goes to the unassigned queue")

    def test_a_receptionist_is_not_offered_it_at_all(self):
        """Absent, not disabled: no condition would make it usable for them."""
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("crm-ticket-create"))
        self.assertFalse(response.context["form"].may_assign)
        self.assertNotIn("assigned_to", response.context["form"].fields)
        self.assertContains(response, "A Supervisor assigns this from the ticket list")

    def test_and_cannot_assign_by_posting_one_anyway(self):
        """The form is a convenience; the permission is the control."""
        self.client.force_login(self.receptionist)
        self.client.post(
            reverse("crm-ticket-create"),
            self._payload(assigned_to=self.technician.pk),
        )
        self.assertIsNone(Ticket.objects.get().assigned_to)

    def test_a_backwards_window_is_refused_here_too(self):
        from datetime import date, timedelta

        self.client.force_login(self.admin)
        response = self.client.post(reverse("crm-ticket-create"), self._payload(
            assigned_to=self.technician.pk,
            start_date=date.today().isoformat(),
            due_date=(date.today() - timedelta(days=1)).isoformat(),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ticket.objects.exists())

    def test_a_receptionist_could_be_given_this_by_a_role_change_alone(self):
        """
        The reason the control is permission-driven rather than hardcoded to
        a job title. Nobody holds both create_ticket and assign_ticket but an
        Admin today; tick assign_ticket onto Receptionist in Settings and the
        person actually taking the call can assign, with no release.
        """
        from accounts.models import Permission, Role, RolePermission
        from accounts.permissions import forget_permissions

        RolePermission.objects.create(
            role=Role.objects.get(name="Receptionist"),
            permission=Permission.objects.get(code="assign_ticket"),
            scope="all",
        )
        forget_permissions(self.receptionist)

        self.client.force_login(self.receptionist)
        response = self.client.post(
            reverse("crm-ticket-create"),
            self._payload(assigned_to=self.technician.pk),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ticket.objects.get().assigned_to, self.technician)
