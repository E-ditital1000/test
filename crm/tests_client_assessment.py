"""
The items A-1 raised after using the system, and what each one turned out
to be.

Kept together and named after the report, because the pattern matters more
than any single fix: not one of these was a crash. Every one was the system
working exactly as written and the person in front of it being unable to get
their job done — a clock that locked people out for being human, a menu that
did nothing on a phone, a dropdown that silently offered the wrong people,
and work assigned to somebody who had no screen on which to see it.
"""
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site, Ticket

User = get_user_model()
ROOT = Path(__file__).resolve().parent.parent


def make_user(email, role_names=(), **extra):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345", **extra
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class TicketsAreVisibleToWhoeverHasToDoThem(TestCase):
    """
    Client item: "Assigned task to a specific person is not displaying from
    their end."

    It was not displaying because there was nowhere for it to display.
    Assigning a ticket moved its status, wrote its trail and sent an email,
    and the person it had been handed to had no screen that listed it and no
    permission to open it.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.site = Site.objects.create(customer=cls.customer, name="Main building")
        cls.technician = make_user("tech.ca@test.local", ["Technician"], first_name="Moses")
        cls.other_tech = make_user("other.ca@test.local", ["Technician"], first_name="Sam")
        cls.supervisor = make_user("sup.ca@test.local", ["Supervisor"])

    def setUp(self):
        self.ticket = Ticket.objects.create(
            reference="TKT-7001",
            customer=self.customer,
            site=self.site,
            service_type=self.service,
            status=self.status,
            description="Inverter fault light since Tuesday.",
            assigned_to=self.technician,
            start_date=date.today(),
            due_date=date.today() + timedelta(days=2),
        )

    @tag("acceptance")
    def test_the_assignee_can_open_the_ticket_given_to_them(self):
        self.client.force_login(self.technician)
        response = self.client.get(reverse("crm-ticket-detail", args=[self.ticket.pk]))
        self.assertEqual(
            response.status_code, 200,
            "a technician must be able to read the job they were handed",
        )
        self.assertContains(response, "TKT-7001")

    def test_a_technician_cannot_read_somebody_elses_ticket(self):
        """Being given one ticket is not being given the register."""
        self.client.force_login(self.other_tech)
        response = self.client.get(reverse("crm-ticket-detail", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 403)

    def test_the_office_still_reads_every_ticket(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("crm-ticket-detail", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 200)

    @tag("acceptance")
    def test_an_assigned_ticket_appears_on_the_persons_own_work_screen(self):
        self.client.force_login(self.technician)
        response = self.client.get(reverse("dashboard-my-tasks"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TKT-7001")
        self.assertContains(response, "Tickets assigned to you")

    def test_somebody_elses_ticket_is_not_on_your_work_screen(self):
        self.client.force_login(self.other_tech)
        response = self.client.get(reverse("dashboard-my-tasks"))
        self.assertNotContains(response, "TKT-7001")

    def test_a_closed_ticket_drops_off_the_work_screen(self):
        from django.utils import timezone

        self.ticket.closed_at = timezone.now()
        self.ticket.save(update_fields=["closed_at"])
        self.client.force_login(self.technician)
        self.assertNotContains(
            self.client.get(reverse("dashboard-my-tasks")), "TKT-7001"
        )


class TheAssignmentPickerExplainsItself(TestCase):
    """
    Client item: "To assign ticket to technicians, and their names not
    displaying."

    They were not displaying because the field staff held only the Employee
    role, which grants clocking and leave and nothing else, so the picker —
    which offers whoever can be sent to a job — filtered every one of them
    out and listed office accounts instead. An empty or wrong dropdown looks
    like a dropdown, so it now says what it is filtering on.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)

    @tag("acceptance")
    def test_somebody_on_the_employee_role_can_be_given_a_ticket(self):
        """
        The item as A-1 reported it. Their nine field staff were all on the
        Employee role, which could not be sent to a job, so the picker
        filtered every one of them out and offered office accounts instead.

        Being assignable is part of the floor now — chosen over moving them
        all to Technician, which also carries GPS check-in, assessments and
        raising requisitions.
        """
        from crm.forms import who_can_be_sent_to_a_job

        floor = make_user("floor.ca@test.local", ["Employee"], first_name="Peterson")
        self.assertIn(
            floor, who_can_be_sent_to_a_job(),
            "the people doing the work must be offerable in the picker",
        )

    @tag("acceptance")
    def test_the_floor_role_can_do_a_technicians_work(self):
        """
        A-1's rule: there is no technician who is not an employee. So the
        floor role carries the job list, GPS check-in, assessments and
        requisitions — I had held these apart, which was my distinction and
        not the company's, and it left the people who do the work unable to
        be given any.
        """
        from accounts.decorators import user_has_permission

        floor = make_user("floor2.ca@test.local", ["Employee"])
        for code in (
            "view_own_job_list", "gps_check_in", "submit_assessment",
            "view_projects", "raise_requisition",
        ):
            self.assertTrue(
                user_has_permission(floor, code),
                f"somebody on the floor role must hold {code}",
            )

    def test_the_floor_reaches_only_its_own_projects(self):
        """
        What keeps requisitions safe on a role every account starts with:
        the right is held at own-projects scope, so a person crewed on
        nothing reaches nothing.
        """
        from accounts.scoping import user_scopes_for

        floor = make_user("floor3.ca@test.local", ["Employee"])
        self.assertEqual(user_scopes_for(floor, "raise_requisition"), {"own_projects"})
        self.assertEqual(user_scopes_for(floor, "view_projects"), {"own_projects"})

    def test_the_floor_still_holds_nothing_the_office_holds(self):
        """It does a technician's work, not a supervisor's or finance's."""
        from accounts.decorators import user_has_permission

        floor = make_user("floor4.ca@test.local", ["Employee"])
        for withheld in (
            "approve_requisition", "assign_ticket", "manage_users",
            "approve_leave", "view_project_cost", "issue_invoice",
        ):
            self.assertFalse(
                user_has_permission(floor, withheld),
                f"the floor must not carry {withheld}",
            )

    def test_a_technician_is_offered(self):
        from crm.forms import who_can_be_sent_to_a_job

        tech = make_user("t.ca@test.local", ["Technician"], first_name="Moses")
        self.assertIn(tech, who_can_be_sent_to_a_job())

    def test_an_empty_picker_says_what_to_do_about_it(self):
        """
        Still reachable on a system where nobody holds the permission at
        all — a fresh deployment, or roles edited in Settings. An empty
        dropdown looks like a dropdown, so it has to explain itself.
        """
        from crm.forms import TicketAssignForm

        help_text = TicketAssignForm().fields["assigned_to"].help_text
        self.assertIn("Technician", help_text)
        self.assertIn("Settings", help_text)

    def test_a_populated_picker_says_how_many_and_why_somebody_is_missing(self):
        from crm.forms import TicketAssignForm

        make_user("t2.ca@test.local", ["Technician"], first_name="Moses")
        help_text = TicketAssignForm().fields["assigned_to"].help_text
        self.assertIn("1 person", help_text)
        self.assertIn("Technician", help_text)


class TheTicketListShowsWhenWorkIsDue(TestCase):
    """
    Client items: "Time Indication to ticket area" and "Button to search
    customers → to ticket bar".

    Tickets carry a start and due date — added so assignment could say when
    work was expected — and the screen that schedules them showed neither.
    It showed age, which is how long a ticket has existed and not at all the
    same question.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.supervisor = make_user("sup.tl@test.local", ["Supervisor"])

    def setUp(self):
        self.client.force_login(self.supervisor)

    def _ticket(self, **extra):
        return Ticket.objects.create(
            reference="TKT-7100", customer=self.customer, service_type=self.service,
            status=self.status, description="No power to the theatre.", **extra
        )

    def test_the_expected_dates_are_on_the_list(self):
        self._ticket(
            start_date=date(2026, 11, 2), due_date=date(2026, 11, 4)
        )
        response = self.client.get(reverse("crm-tickets"))
        self.assertContains(response, "Expected")
        self.assertContains(response, "Nov")

    def test_a_ticket_with_no_dates_says_so_rather_than_showing_a_blank(self):
        self._ticket()
        self.assertContains(self.client.get(reverse("crm-tickets")), "Not set")

    def test_the_customer_search_button_is_on_the_ticket_bar(self):
        response = self.client.get(reverse("crm-tickets"))
        self.assertContains(response, "Search customers")
        self.assertContains(response, reverse("crm-customers"))

    def test_overdue_is_about_the_due_date_not_the_age(self):
        """
        A ticket raised three weeks ago and due next Friday is old and
        perfectly on time. One raised yesterday and due yesterday is not.
        """
        on_time = self._ticket(due_date=date.today() + timedelta(days=7))
        self.assertFalse(on_time.is_overdue)

        late = Ticket.objects.create(
            reference="TKT-7101", customer=self.customer, service_type=self.service,
            status=self.status, description="Late one.",
            due_date=date.today() - timedelta(days=1),
        )
        self.assertTrue(late.is_overdue)

    def test_a_closed_ticket_is_never_overdue(self):
        from django.utils import timezone

        late = Ticket.objects.create(
            reference="TKT-7102", customer=self.customer, service_type=self.service,
            status=self.status, description="Done late, but done.",
            due_date=date.today() - timedelta(days=5),
            closed_at=timezone.now(),
        )
        self.assertFalse(late.is_overdue)


class TheMenuWorksOnAPhone(TestCase):
    """
    Client item: "Hand burger menu not working."

    Below 900px the sidebar is `display: none` and the burger toggled
    `nav-collapsed`, which narrows a sidebar that is already gone — so on a
    phone it did nothing at all, and Projects, Tickets and Finance could not
    be reached. The bottom tab bar carries a technician's two screens and
    was never the menu.

    Asserted against the shipped CSS and markup rather than a browser: the
    browser suite covers behaviour, and this covers the pieces being present
    at all, which is what was missing.
    """

    def test_the_drawer_opens_below_the_sidebar_breakpoint(self):
        css = (ROOT / "static" / "css" / "a1.css").read_text(encoding="utf-8")
        self.assertIn("body.nav-open .a1-side", css)
        self.assertIn(".nav-scrim", css)
        narrow = css[css.index("@media (max-width: 900px)"):]
        self.assertIn("transform: translateX(-100%)", narrow)
        self.assertNotIn(
            ".a1-side { display: none; }", narrow,
            "hiding the sidebar outright is what left the phone with no menu",
        )

    def test_the_scrim_is_invisible_above_the_breakpoint(self):
        """
        It is a <button>. Styled only inside the mobile media query, it
        rendered on desktop as an empty box in normal flow, above the app,
        pushing the whole page down — a visible strip above the header from
        an element nobody was meant to see at that width.
        """
        css = (ROOT / "static" / "css" / "a1.css").read_text(encoding="utf-8")
        breakpoint_at = css.index("@media (max-width: 900px)")
        base, mobile = css[:breakpoint_at], css[breakpoint_at:]

        self.assertIn(
            ".nav-scrim { display: none; }", base,
            "without a base rule the scrim takes up space on every desktop page",
        )
        rule = mobile[mobile.index(".nav-scrim {"):]
        self.assertIn("display: block;", rule[:220], "and it must come back below 900px")

    def test_the_page_carries_the_scrim_and_a_focusable_drawer(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('id="nav-scrim"', base)
        self.assertIn('id="a1-side"', base)
        self.assertIn('tabindex="-1"', base)

    def test_the_burger_knows_which_mode_it_is_in(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("max-width: 900px", base, "the script must know the breakpoint")
        self.assertIn("nav-open", base)
        self.assertIn("Escape", base, "a drawer must close from the keyboard")
