"""
Finance acceptance tests.

The two that matter: an invoice's paid state is derived from its payments
rather than set by hand, and a requisition above the Settings-owned threshold
is refused to anyone who is not an Executive — server-side, regardless of
what the client rendered.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from approvals.models import Approval
from config.models import PolicySetting, ServiceType, StatusOption
from crm.models import Customer
from projects.models import Project, Requisition

from .models import Expense, ExpenseCategory, Invoice, InvoiceLine

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class FinanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(
            key=PolicySetting.REQUISITION_THRESHOLD, value="2500"
        )
        cls.service_type = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )
        cls.customer = Customer.objects.create(name="Ducor Hotel")
        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Array", customer=cls.customer,
            service_type=cls.service_type, status=cls.status,
        )
        cls.category = ExpenseCategory.objects.create(code="materials", name="Materials")

        cls.finance = make_user("fin@test.local", ["Finance"])
        cls.executive = make_user("exec@test.local", ["Executive"])
        cls.manager = make_user("pm@test.local", ["Project Manager"])
        cls.technician = make_user("tech@test.local", ["Technician"])

        # A Project Manager holds view_projects at own-projects scope, so the
        # project has to actually be theirs for them to raise against it.
        cls.project.manager = cls.manager
        cls.project.save(update_fields=["manager"])

    def _draft(self):
        self.client.force_login(self.finance)
        self.client.post(reverse("finance-invoice-create"), {"project": self.project.pk, "due_on": "", "notes": ""})
        invoice = Invoice.objects.latest("id")
        InvoiceLine.objects.create(invoice=invoice, description="Array", quantity=1, unit_price=1000)
        return invoice

    # -- invoices ---------------------------------------------------------

    def test_an_invoice_carries_the_projects_job_reference(self):
        invoice = self._draft()
        self.assertEqual(invoice.job_ref, self.project.job_ref)
        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(invoice.number, "INV-0001")

    def test_an_empty_invoice_cannot_be_sent(self):
        self.client.force_login(self.finance)
        self.client.post(reverse("finance-invoice-create"), {"project": self.project.pk, "due_on": "", "notes": ""})
        invoice = Invoice.objects.latest("id")
        self.client.post(reverse("finance-invoice-issue", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.state, Invoice.DRAFT)

    @tag("acceptance")
    def test_the_paid_state_is_derived_from_the_payments(self):
        invoice = self._draft()
        self.client.post(reverse("finance-invoice-issue", args=[invoice.pk]))

        today = timezone.localdate().isoformat()
        self.client.post(
            reverse("finance-payment-record", args=[invoice.pk]),
            {"amount": "400", "paid_on": today, "method": "Cash", "reference": ""},
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.state, Invoice.PART_PAID)
        self.assertEqual(invoice.outstanding, 600)

        self.client.post(
            reverse("finance-payment-record", args=[invoice.pk]),
            {"amount": "600", "paid_on": today, "method": "Transfer", "reference": ""},
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.state, Invoice.PAID)
        self.assertEqual(invoice.outstanding, 0)

    def test_a_draft_invoice_cannot_take_a_payment(self):
        invoice = self._draft()
        self.client.post(
            reverse("finance-payment-record", args=[invoice.pk]),
            {"amount": "100", "paid_on": timezone.localdate().isoformat(), "method": "", "reference": ""},
        )
        self.assertEqual(invoice.payments.count(), 0)

    # -- expenses ---------------------------------------------------------

    def test_an_expense_attaches_to_a_project_and_lands_in_its_cost(self):
        from projects.views import project_cost

        self.client.force_login(self.finance)
        self.client.post(
            reverse("finance-expense-create"),
            {
                "project": self.project.pk, "category": self.category.pk,
                "item": "Cable", "amount": "250",
                "incurred_on": timezone.localdate().isoformat(),
            },
        )
        expense = Expense.objects.get()
        self.assertEqual(expense.project, self.project)
        self.assertEqual(expense.logged_by, self.finance)
        # Cost is computed from the rows, never stored.
        self.assertEqual(project_cost(self.project)["cost"], 250)

    def test_a_technician_cannot_reach_finance(self):
        self.client.force_login(self.technician)
        for name in ("finance-invoices", "finance-expenses", "finance-requisitions"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    # -- requisitions and the threshold -----------------------------------

    def _requisition(self, amount):
        self.client.force_login(self.manager)
        self.client.post(
            reverse("projects-requisition-create", args=[self.project.pk]),
            {"description": "Breakers", "amount": str(amount), "needed_by": ""},
        )
        return Requisition.objects.latest("id")

    def test_below_the_threshold_finance_can_approve(self):
        requisition = self._requisition(410)
        self.assertFalse(requisition.requires_executive_approval())

        self.client.force_login(self.finance)
        self.client.post(
            reverse("finance-requisition-decide", args=[requisition.pk]),
            {"decision": Approval.APPROVED, "comment": ""},
        )
        self.assertTrue(requisition.is_approved)

    @tag("acceptance")
    def test_above_the_threshold_finance_is_refused_and_an_executive_is_not(self):
        requisition = self._requisition(4200)
        self.assertTrue(requisition.requires_executive_approval())

        # Holding approve_requisition is not enough above the threshold.
        self.client.force_login(self.finance)
        self.client.post(
            reverse("finance-requisition-decide", args=[requisition.pk]),
            {"decision": Approval.APPROVED, "comment": ""},
        )
        self.assertFalse(requisition.is_approved, "finance must not clear an executive-level amount")

        self.client.force_login(self.executive)
        self.client.post(
            reverse("finance-requisition-decide", args=[requisition.pk]),
            {"decision": Approval.APPROVED, "comment": ""},
        )
        self.assertTrue(requisition.is_approved)

    def test_the_threshold_is_configuration_not_a_constant(self):
        requisition = self._requisition(4200)
        self.assertTrue(requisition.requires_executive_approval())

        PolicySetting.objects.filter(key=PolicySetting.REQUISITION_THRESHOLD).update(value="10000")
        self.assertFalse(
            requisition.requires_executive_approval(),
            "raising the threshold in Settings must change who can approve",
        )

    def test_returning_a_requisition_requires_a_comment(self):
        requisition = self._requisition(410)
        self.client.force_login(self.finance)
        self.client.post(
            reverse("finance-requisition-decide", args=[requisition.pk]),
            {"decision": Approval.RETURNED, "comment": "  "},
        )
        self.assertEqual(requisition.approval_state, Approval.SUBMITTED)

    def test_only_approved_requisitions_count_towards_project_cost(self):
        from projects.views import project_cost

        requisition = self._requisition(410)
        self.assertEqual(project_cost(self.project)["requisitions"], 0)

        self.client.force_login(self.finance)
        self.client.post(
            reverse("finance-requisition-decide", args=[requisition.pk]),
            {"decision": Approval.APPROVED, "comment": ""},
        )
        self.assertEqual(project_cost(self.project)["requisitions"], 410)
