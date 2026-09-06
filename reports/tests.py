"""
Reports acceptance tests.

The release gate the brief names by name: every report tallies exactly
against the raw rows for the seeded period. A report that disagrees with the
records underneath it is worse than no report, because somebody will act on
it.

The other guarantee tested here is that the set stays fixed and per-report
permissions hold — reaching the module never implies reaching every report.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Permission, Role, RolePermission, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Ticket
from fieldjobs.models import FieldJob
from finance.models import Expense, ExpenseCategory, Invoice, InvoiceLine
from projects.models import Project

from .reports import REPORTS, available_to, compute_totals

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class ReportSetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.today = timezone.localdate()

        cls.service_type = ServiceType.objects.create(code="solar", name="Solar")
        cls.ticket_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.project_status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Ducor Hotel")

        cls.executive = make_user("exec@test.local", ["Executive"])
        cls.technician = make_user("tech@test.local", ["Technician"])

        # Three completed jobs this month, one open ticket, one invoice and
        # one expense — small enough to tally by hand in the assertions.
        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Array", customer=cls.customer,
            service_type=cls.service_type, status=cls.project_status,
        )
        for index in range(3):
            FieldJob.objects.create(
                reference=f"FJ-000{index + 1}",
                project=cls.project, customer=cls.customer,
                service_type=cls.service_type, assigned_to=cls.technician,
                scheduled_for=timezone.now() - timedelta(days=2),
                state=FieldJob.COMPLETED,
                completed_at=timezone.now() - timedelta(days=1),
            )
        Ticket.objects.create(
            reference="TKT-0001", customer=cls.customer, service_type=cls.service_type,
            status=cls.ticket_status, description="Open one", raised_by=cls.executive,
        )

        cls.invoice = Invoice.objects.create(
            number="INV-0001", project=cls.project, customer=cls.customer,
            state=Invoice.SENT, issued_on=cls.today,
        )
        InvoiceLine.objects.create(
            invoice=cls.invoice, description="Array", quantity=2, unit_price=Decimal("500")
        )
        category = ExpenseCategory.objects.create(code="materials", name="Materials")
        Expense.objects.create(
            project=cls.project, category=category, item="Cable",
            amount=Decimal("250"), incurred_on=cls.today, logged_by=cls.executive,
            client_uuid="11111111-1111-4111-8111-111111111111",
            device_timestamp=timezone.now(),
        )

    # -- the set is fixed -------------------------------------------------

    def test_the_report_set_is_fixed_and_named(self):
        self.assertEqual(
            list(REPORTS),
            [
                "jobs-completed",
                "ticket-ageing",
                "project-profitability",
                "revenue-vs-expense",
                "technician-output",
                "attendance-summary",
            ],
        )

    @tag("acceptance")
    def test_each_report_is_reachable_only_with_its_own_permission(self):
        """Reaching the module never implies reaching every report in it."""
        role = Role.objects.create(name="Ops reader", is_system=False)
        RolePermission.objects.create(
            role=role, permission=Permission.objects.get(code="view_reports")
        )
        reader = make_user("reader@test.local")
        UserRole.objects.create(user=reader, role=role)

        reachable = {report.key for report in available_to(reader)}
        self.assertIn("jobs-completed", reachable)
        # No view_financial_reports, so the money never appears.
        self.assertNotIn("project-profitability", reachable)
        self.assertNotIn("revenue-vs-expense", reachable)

        self.client.force_login(reader)
        self.assertEqual(
            self.client.get(reverse("reports-detail", args=["project-profitability"])).status_code,
            403,
            "a report must refuse server-side, not merely be hidden",
        )

    def test_a_technician_cannot_reach_reports_at_all(self):
        self.client.force_login(self.technician)
        self.assertEqual(self.client.get(reverse("reports-index")).status_code, 403)

    # -- the release gate: every figure tallies against its rows ---------

    @tag("acceptance")
    def test_jobs_completed_tallies_against_the_field_job_rows(self):
        report = REPORTS["jobs-completed"]
        rows = report.build(self.executive, self.today.year, self.today.month)
        raw = FieldJob.objects.filter(
            state=FieldJob.COMPLETED,
            completed_at__date__gte=self.today.replace(day=1),
        ).count()
        self.assertEqual(len(rows), raw)
        self.assertEqual(len(rows), 3)

    @tag("acceptance")
    def test_ticket_ageing_tallies_against_the_open_tickets(self):
        report = REPORTS["ticket-ageing"]
        rows = report.build(self.executive, self.today.year, self.today.month)
        self.assertEqual(len(rows), Ticket.objects.open().count())

    @tag("acceptance")
    def test_project_profitability_tallies_against_its_invoices_and_expenses(self):
        report = REPORTS["project-profitability"]
        rows = report.build(self.executive, self.today.year, self.today.month)
        row = next(r for r in rows if r["reference"] == "PRJ-0001")

        # Computed independently of the report, from the rows themselves.
        revenue = sum(line.line_total for line in self.invoice.lines.all())
        cost = sum(e.amount for e in self.project.expenses.all())

        self.assertEqual(row["revenue"], revenue)
        self.assertEqual(row["cost"], cost)
        self.assertEqual(row["margin"], revenue - cost)

    @tag("acceptance")
    def test_revenue_vs_expense_tallies_against_the_finance_rows(self):
        report = REPORTS["revenue-vs-expense"]
        rows = report.build(self.executive, self.today.year, self.today.month)
        this_month = next(
            r for r in rows if r["month"] == self.today.strftime("%b %Y")
        )
        self.assertEqual(this_month["revenue"], Decimal("1000"))
        self.assertEqual(this_month["expense"], Decimal("250"))
        self.assertEqual(this_month["margin"], Decimal("750"))

    @tag("acceptance")
    def test_technician_output_tallies_against_the_jobs(self):
        report = REPORTS["technician-output"]
        rows = report.build(self.executive, self.today.year, self.today.month)
        row = next(r for r in rows if "tech@test.local" in r["technician"] or r["jobs_completed"] == 3)
        self.assertEqual(row["jobs_completed"], 3)

    @tag("acceptance")
    def test_totals_are_summed_from_the_same_rows_the_table_shows(self):
        """
        A total computed by a second query can drift from the table above it.
        These come from the rendered rows, so they cannot.
        """
        report = REPORTS["revenue-vs-expense"]
        rows = report.build(self.executive, self.today.year, self.today.month)
        totals = compute_totals(report, rows)
        self.assertEqual(totals["revenue"], sum(r["revenue"] for r in rows))
        self.assertEqual(totals["expense"], sum(r["expense"] for r in rows))
        self.assertEqual(totals["margin"], sum(r["margin"] for r in rows))

    # -- export -----------------------------------------------------------

    def test_the_export_carries_the_same_rows_as_the_screen(self):
        self.client.force_login(self.executive)
        page = self.client.get(reverse("reports-detail", args=["jobs-completed"]))
        export = self.client.get(
            reverse("reports-detail", args=["jobs-completed"]), {"export": "csv"}
        )
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv")

        body = export.content.decode().strip().splitlines()
        self.assertEqual(
            body[0], "Job,Customer,Service type,Technician,Completed,Days open"
        )
        # One header plus one line per row on the screen.
        self.assertEqual(len(body) - 1, len(page.context["rows"]))

    def test_export_needs_its_own_permission(self):
        role = Role.objects.create(name="Reader only", is_system=False)
        RolePermission.objects.create(
            role=role, permission=Permission.objects.get(code="view_reports")
        )
        reader = make_user("noexport@test.local")
        UserRole.objects.create(user=reader, role=role)

        self.client.force_login(reader)
        self.assertEqual(
            self.client.get(reverse("reports-detail", args=["jobs-completed"])).status_code, 200
        )
        self.assertEqual(
            self.client.get(
                reverse("reports-detail", args=["jobs-completed"]), {"export": "csv"}
            ).status_code,
            403,
            "reading a report and taking it away are different permissions",
        )

    def test_every_report_renders_for_someone_who_holds_everything(self):
        admin = make_user("admin@test.local", ["Admin"])
        self.client.force_login(admin)
        for key in REPORTS:
            with self.subTest(report=key):
                response = self.client.get(reverse("reports-detail", args=[key]))
                self.assertEqual(response.status_code, 200)
