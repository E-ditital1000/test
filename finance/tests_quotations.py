"""
Quotations: what A-1 offered, priced and itemised, before anything is billed.

The guarantees worth defending: a quotation the customer holds cannot be
changed afterwards, a lapsed price never reads as though it still stands, an
invoice raised from one carries exactly what was agreed, and A-1's own costs
never reach the customer's copy.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditEntry, Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Ticket
from finance.models import Invoice, Quotation, QuotationLine
from projects.models import Project

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class QuotationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.finance = make_user("finance@t.local", ["Finance"])
        cls.customer = Customer.objects.create(name="Liberty Gold")
        cls.other_customer = Customer.objects.create(name="New Hope Clinic")
        cls.service = ServiceType.objects.create(code="solar", name="Solar install")
        cls.status = StatusOption.objects.create(
            kind="project", code="open", label="Open", is_default=True
        )
        cls.project = cls.make_project("PRJ-0001", "Yard solar", cls.customer)

    @classmethod
    def make_project(cls, reference, name, customer):
        return Project.objects.create(
            reference=reference, name=name, customer=customer,
            service_type=cls.service, status=cls.status,
        )

    def quotation(self, **kwargs):
        fields = {
            "number": kwargs.pop("number", "QUO-0001"),
            "customer": kwargs.pop("customer", self.customer),
            "title": kwargs.pop("title", "Solar array, main yard"),
            "prepared_by": self.finance,
        }
        fields.update(kwargs)
        return Quotation.objects.create(**fields)

    def line(self, quotation, price="1000.00", cost=None, quantity="1", description="Panel"):
        return QuotationLine.objects.create(
            quotation=quotation,
            description=description,
            quantity=Decimal(quantity),
            unit_price=Decimal(price),
            unit_cost=Decimal(cost) if cost is not None else None,
        )


class QuotationMathTests(QuotationTestCase):
    def test_a_total_is_the_sum_of_its_items(self):
        quotation = self.quotation()
        self.line(quotation, price="1200.50", quantity="2")
        self.line(quotation, price="300.00", quantity="1")
        self.assertEqual(quotation.total, Decimal("2701.00"))

    def test_margin_is_shown_where_costs_were_given(self):
        """The point of asking for cost while the price is still being set."""
        quotation = self.quotation()
        self.line(quotation, price="1000.00", cost="600.00", quantity="2")
        self.assertEqual(quotation.cost_total, Decimal("1200.00"))
        self.assertEqual(quotation.margin, Decimal("800.00"))
        self.assertEqual(round(quotation.margin_percent), 40)

    def test_a_line_without_a_cost_counts_as_no_cost_not_as_free(self):
        quotation = self.quotation()
        self.line(quotation, price="500.00", cost=None)
        self.assertEqual(quotation.cost_total, Decimal("0"))
        self.assertFalse(quotation.has_costs)


class QuotationLifecycleTests(QuotationTestCase):
    def setUp(self):
        self.client.force_login(self.finance)

    def test_it_cannot_go_out_empty(self):
        quotation = self.quotation()
        self.client.post(reverse("finance-quotation-send", args=[quotation.pk]))
        quotation.refresh_from_db()
        self.assertEqual(quotation.state, Quotation.DRAFT)

    def test_sending_fixes_it(self):
        quotation = self.quotation()
        self.line(quotation)
        self.client.post(reverse("finance-quotation-send", args=[quotation.pk]))
        quotation.refresh_from_db()
        self.assertEqual(quotation.state, Quotation.SENT)
        self.assertEqual(quotation.sent_on, timezone.localdate())
        self.assertFalse(quotation.is_editable)
        self.assertTrue(AuditEntry.objects.filter(action="quotation.sent").exists())

    def test_a_sent_quotation_cannot_have_its_items_or_prices_changed(self):
        """The customer holds a copy; changing it afterwards would be a lie."""
        quotation = self.quotation()
        line = self.line(quotation)
        self.client.post(reverse("finance-quotation-send", args=[quotation.pk]))

        self.client.post(
            reverse("finance-quotation-line-add", args=[quotation.pk]),
            {"description": "Extra", "quantity": "1", "unit_price": "50"},
        )
        self.client.post(reverse("finance-quotation-line-remove", args=[line.pk]))
        response = self.client.get(reverse("finance-quotation-edit", args=[quotation.pk]))

        self.assertEqual(quotation.lines.count(), 1)
        self.assertRedirects(response, reverse("finance-quotation-detail", args=[quotation.pk]))

    def test_the_answer_is_recorded_with_its_date_and_who_gave_it(self):
        quotation = self.quotation()
        self.line(quotation)
        self.client.post(reverse("finance-quotation-send", args=[quotation.pk]))
        self.client.post(
            reverse("finance-quotation-decide", args=[quotation.pk]),
            {
                "decision": "accepted",
                "decided_on": timezone.localdate().isoformat(),
                "decision_reference": "Amos Kollie, order 4417",
            },
        )
        quotation.refresh_from_db()
        self.assertEqual(quotation.state, Quotation.ACCEPTED)
        self.assertEqual(quotation.decision_reference, "Amos Kollie, order 4417")
        self.assertTrue(AuditEntry.objects.filter(action="quotation.accepted").exists())

    def test_only_a_sent_quotation_can_be_accepted(self):
        quotation = self.quotation()
        self.line(quotation)
        self.client.post(
            reverse("finance-quotation-decide", args=[quotation.pk]),
            {"decision": "accepted", "decided_on": timezone.localdate().isoformat()},
        )
        quotation.refresh_from_db()
        self.assertEqual(quotation.state, Quotation.DRAFT)

    def test_a_price_that_has_lapsed_never_reads_as_still_standing(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        quotation = self.quotation(
            state=Quotation.SENT, valid_until=yesterday, sent_on=yesterday - timedelta(days=20)
        )
        self.line(quotation)
        self.assertTrue(quotation.is_expired())
        self.assertEqual(quotation.derived_state(), Quotation.EXPIRED)
        self.assertContains(
            self.client.get(reverse("finance-quotation-detail", args=[quotation.pk])), "Expired"
        )

    def test_an_accepted_quotation_does_not_lapse(self):
        quotation = self.quotation(
            state=Quotation.ACCEPTED, valid_until=timezone.localdate() - timedelta(days=30)
        )
        self.assertFalse(quotation.is_expired())


class QuotationFormRuleTests(QuotationTestCase):
    def setUp(self):
        self.client.force_login(self.finance)

    def post_new(self, **overrides):
        data = {
            "customer": self.customer.pk,
            "title": "Solar array",
            "valid_until": (timezone.localdate() + timedelta(days=30)).isoformat(),
            "notes": "",
            "terms": "",
        }
        data.update(overrides)
        return self.client.post(reverse("finance-quotation-create"), data)

    def test_a_quotation_can_be_raised_before_there_is_a_project(self):
        response = self.post_new()
        quotation = Quotation.objects.get()
        self.assertRedirects(response, reverse("finance-quotation-detail", args=[quotation.pk]))
        self.assertIsNone(quotation.project)
        self.assertEqual(quotation.number, "QUO-0001")
        self.assertEqual(quotation.prepared_by, self.finance)

    def test_it_cannot_name_another_customers_project(self):
        other = self.make_project("PRJ-0002", "Clinic wiring", self.other_customer)
        response = self.post_new(project=other.pk)
        self.assertContains(response, "belongs to New Hope Clinic")
        self.assertFalse(Quotation.objects.exists())

    def test_a_price_cannot_hold_until_a_date_that_has_passed(self):
        response = self.post_new(valid_until=(timezone.localdate() - timedelta(days=1)).isoformat())
        self.assertContains(response, "date that has passed")

    def test_the_job_carries_through_from_the_project(self):
        self.post_new(project=self.project.pk)
        self.assertEqual(Quotation.objects.get().job_ref, self.project.job_ref)

    def test_a_quotation_written_before_any_project_stands_on_its_own(self):
        """Which is the usual way round: the quote is what brings the work
        about. It mints a reference and waits to be joined to one."""
        self.post_new()
        quotation = Quotation.objects.get()
        self.assertIsNone(quotation.project_id)
        self.assertIsNone(quotation.ticket_id)
        self.assertIsNotNone(quotation.job_ref)
        self.assertNotEqual(quotation.job_ref, self.project.job_ref)

    def test_naming_the_project_afterwards_joins_its_job(self):
        self.post_new()
        quotation = Quotation.objects.get()
        self.assertNotEqual(quotation.job_ref, self.project.job_ref)

        self.client.post(
            reverse("finance-quotation-edit", args=[quotation.pk]),
            {"customer": self.customer.pk, "project": self.project.pk, "ticket": "",
             "title": quotation.title, "valid_until": "", "notes": "", "terms": ""},
        )
        quotation.refresh_from_db()
        self.assertEqual(quotation.project, self.project)
        self.assertEqual(quotation.job_ref, self.project.job_ref)

    def test_an_item_needs_a_real_quantity_and_price(self):
        quotation = self.quotation()
        self.client.post(
            reverse("finance-quotation-line-add", args=[quotation.pk]),
            {"description": "Panel", "quantity": "0", "unit_price": "100"},
        )
        self.assertEqual(quotation.lines.count(), 0)


class QuotationToInvoiceTests(QuotationTestCase):
    def setUp(self):
        self.client.force_login(self.finance)
        self.quote = self.quotation(state=Quotation.ACCEPTED, decided_on=timezone.localdate())
        self.line(self.quote, description="Panels", price="1000.00", cost="700.00", quantity="4")
        self.line(self.quote, description="Install labour", price="850.00", quantity="1")

    def convert(self, **overrides):
        data = {"project": self.project.pk, "due_on": ""}
        data.update(overrides)
        return self.client.post(reverse("finance-quotation-invoice", args=[self.quote.pk]), data)

    @tag("acceptance")
    def test_the_invoice_carries_exactly_what_was_agreed(self):
        """
        The whole point of a quotation: the invoice is worked out from the
        price the customer accepted, not from somebody's memory.
        """
        self.convert()
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.quotation, self.quote)
        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(invoice.project, self.project)
        self.assertEqual(invoice.job_ref, self.project.job_ref)
        self.assertEqual(
            [(line.description, line.quantity, line.unit_price) for line in invoice.lines.all()],
            [
                ("Panels", Decimal("4.00"), Decimal("1000.00")),
                ("Install labour", Decimal("1.00"), Decimal("850.00")),
            ],
        )
        self.assertEqual(invoice.total, self.quote.total)
        self.assertEqual(invoice.state, Invoice.DRAFT)

    @tag("acceptance")
    def test_a_quote_written_first_reads_as_one_job_with_the_project_and_invoice(self):
        """
        The usual way round, and the one that used to break: a quotation
        written before there was a project kept the reference it minted for
        itself, while the project and the invoice shared another. Three
        records, three jobs, for one piece of work.
        """
        standalone = self.quotation(
            number="QUO-0002", state=Quotation.ACCEPTED, decided_on=timezone.localdate()
        )
        self.line(standalone, description="Panels", price="1000.00", quantity="2")
        self.assertNotEqual(standalone.job_ref, self.project.job_ref)

        self.client.post(
            reverse("finance-quotation-invoice", args=[standalone.pk]),
            {"project": self.project.pk, "due_on": ""},
        )

        standalone.refresh_from_db()
        invoice = Invoice.objects.get(quotation=standalone)
        self.assertEqual(standalone.project, self.project, "the quote belongs to it now")
        self.assertEqual(
            {standalone.job_ref, self.project.job_ref, invoice.job_ref},
            {self.project.job_ref},
            "the quote, the project and the invoice must read as one job",
        )

    def test_a_quote_already_on_a_job_is_not_moved_off_it(self):
        """The project's reference already runs through its ticket and its
        field jobs. A second invoice must not rewrite that."""
        self.convert()
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.job_ref, self.project.job_ref)

    def test_a_quotation_written_before_the_project_adopts_it(self):
        self.assertIsNone(self.quote.project)
        self.convert()
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.project, self.project)

    def test_only_an_accepted_quotation_can_be_invoiced(self):
        self.quote.state = Quotation.SENT
        self.quote.save(update_fields=["state"])
        self.convert()
        self.assertFalse(Invoice.objects.exists())

    def test_it_can_only_be_invoiced_against_its_own_customers_project(self):
        other = self.make_project("PRJ-0003", "Clinic wiring", self.other_customer)
        self.convert(project=other.pk)
        self.assertFalse(Invoice.objects.exists())

    def test_what_is_billed_against_it_is_visible_on_it(self):
        self.convert()
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.invoiced_total, self.quote.total)
        body = self.client.get(reverse("finance-quotation-detail", args=[self.quote.pk])).content.decode()
        self.assertIn(Invoice.objects.get().number, body)


class QuotationAccessTests(QuotationTestCase):
    def test_the_customers_copy_never_carries_a1s_costs(self):
        quotation = self.quotation(state=Quotation.SENT, sent_on=timezone.localdate())
        self.line(quotation, description="Panels", price="1000.00", cost="649.99", quantity="2")
        self.client.force_login(self.finance)
        body = self.client.get(reverse("finance-quotation-print", args=[quotation.pk])).content.decode()
        self.assertIn("Panels", body)
        self.assertIn("$2,000.00", body)
        self.assertNotIn("649.99", body)
        self.assertNotIn("Margin", body)

    def test_a_reader_cannot_change_a_quotation(self):
        """Executive holds view_quotations and not manage_quotations."""
        executive = make_user("exec@t.local", ["Executive"])
        quotation = self.quotation()
        self.client.force_login(executive)
        self.assertEqual(
            self.client.get(reverse("finance-quotation-detail", args=[quotation.pk])).status_code, 200
        )
        for name, args in (
            ("finance-quotation-create", []),
            ("finance-quotation-edit", [quotation.pk]),
            ("finance-quotation-send", [quotation.pk]),
        ):
            with self.subTest(endpoint=name):
                self.assertEqual(self.client.post(reverse(name, args=args)).status_code, 403)

    def test_somebody_with_no_finance_rights_is_refused(self):
        technician = make_user("tech@t.local", ["Technician"])
        self.client.force_login(technician)
        self.assertEqual(self.client.get(reverse("finance-quotations")).status_code, 403)

    def test_the_finance_screens_offer_quotations(self):
        self.client.force_login(self.finance)
        body = self.client.get(reverse("finance-invoices")).content.decode()
        self.assertIn(reverse("finance-quotations"), body)
