"""
Finance. Every cost and every invoice attaches to a project, so project
profitability is computable at query time — no denormalised totals are
stored anywhere in this module.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from config.mixins import (
    JobLineageModel,
    MobileOriginatedModel,
    SoftDeleteModel,
    TimeStampedModel,
)


class ExpenseCategory(SoftDeleteModel, TimeStampedModel):
    """The Finance-owned chart of expense categories."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "expense categories"

    def __str__(self):
        return self.name


class Expense(MobileOriginatedModel):
    """
    Logged against a project with a receipt captured as a photo or PDF —
    usable from a phone, so it carries the offline-first fields.
    """

    project = models.ForeignKey(
        "projects.Project", on_delete=models.PROTECT, related_name="expenses"
    )
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    item = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    incurred_on = models.DateField()
    receipt = models.FileField(upload_to="receipts/", blank=True, null=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expenses_logged"
    )

    class Meta:
        ordering = ["-incurred_on"]
        indexes = [models.Index(fields=["project", "incurred_on"])]

    def __str__(self):
        return f"{self.item} ({self.amount})"


class Invoice(JobLineageModel, TimeStampedModel):
    """
    draft -> sent -> part paid -> paid. The paid state is derived from the
    payments recorded against the invoice, not set by hand.
    """

    DRAFT = "draft"
    SENT = "sent"
    PART_PAID = "part_paid"
    PAID = "paid"
    STATES = [
        (DRAFT, "Draft"),
        (SENT, "Sent"),
        (PART_PAID, "Part paid"),
        (PAID, "Paid"),
    ]

    number = models.CharField(max_length=20, unique=True)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.PROTECT, related_name="invoices"
    )
    # The offer this bill came from, when it came from one. Optional: work
    # still gets invoiced without a quotation, and every invoice raised
    # before quotations existed has none.
    quotation = models.ForeignKey(
        "finance.Quotation", on_delete=models.PROTECT, null=True, blank=True,
        related_name="invoices",
    )
    customer = models.ForeignKey("crm.Customer", on_delete=models.PROTECT, related_name="invoices")
    state = models.CharField(max_length=20, choices=STATES, default=DRAFT)
    issued_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["job_ref"]), models.Index(fields=["state", "due_on"])]

    @property
    def total(self):
        return sum((line.line_total for line in self.lines.all()), 0)

    @property
    def paid_total(self):
        return sum((p.amount for p in self.payments.all()), 0)

    @property
    def outstanding(self):
        return self.total - self.paid_total

    @property
    def days_overdue(self):
        from django.utils import timezone

        if not self.due_on or self.outstanding <= 0:
            return 0
        delta = (timezone.localdate() - self.due_on).days
        return max(delta, 0)

    def derived_state(self):
        if self.state == self.DRAFT:
            return self.DRAFT
        if self.paid_total <= 0:
            return self.SENT
        return self.PAID if self.outstanding <= 0 else self.PART_PAID

    def __str__(self):
        return self.number


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return self.description


class Payment(TimeStampedModel):
    """Append-only: a mistaken payment is corrected by a further row."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_on = models.DateField()
    method = models.CharField(max_length=60, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments_recorded"
    )

    class Meta:
        ordering = ["-paid_on"]

    def __str__(self):
        return f"{self.amount} on {self.invoice.number}"


class Quotation(JobLineageModel, TimeStampedModel):
    """
    What A-1 offered the customer, itemised and priced, before any work is
    billed for.

    This is the document an invoice should be able to point at. Without it,
    invoice lines were typed from memory or from a paper quote, and nothing
    in the system said what the customer had agreed to pay: profitability
    compared money out (expenses, requisitions) against money in (invoices)
    with no record of what was sold in between.

    draft -> sent -> accepted | declined, and a sent quotation lapses on its
    own once it is past its valid-until date. Only a draft can be edited: a
    quotation the customer has been sent is evidence, and changing it after
    the fact would make the trail a lie.
    """

    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    STATES = [
        (DRAFT, "Draft"),
        (SENT, "Sent"),
        (ACCEPTED, "Accepted"),
        (DECLINED, "Declined"),
        (EXPIRED, "Expired"),
    ]
    OPEN_STATES = [DRAFT, SENT]

    number = models.CharField(max_length=20, unique=True)
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.PROTECT, related_name="quotations"
    )
    # A quotation usually comes before there is a project — that is the
    # point of it — so both of these are optional. Either one carries the
    # job lineage through when it is there.
    project = models.ForeignKey(
        "projects.Project", on_delete=models.PROTECT, null=True, blank=True,
        related_name="quotations",
    )
    ticket = models.ForeignKey(
        "crm.Ticket", on_delete=models.PROTECT, null=True, blank=True,
        related_name="quotations",
    )
    title = models.CharField(max_length=150)
    state = models.CharField(max_length=20, choices=STATES, default=DRAFT)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    sent_on = models.DateField(null=True, blank=True)
    decided_on = models.DateField(null=True, blank=True)
    decision_reference = models.CharField(
        max_length=120, blank=True,
        help_text="Who accepted it, and their order number if they gave one.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["state", "valid_until"]), models.Index(fields=["job_ref"])]

    @property
    def total(self):
        return sum((line.line_total for line in self.lines.all()), Decimal("0"))

    @property
    def cost_total(self):
        """What the priced items are expected to cost A-1, where that is known."""
        return sum((line.cost_total for line in self.lines.all()), Decimal("0"))

    @property
    def margin(self):
        return self.total - self.cost_total

    @property
    def margin_percent(self):
        if not self.total:
            return None
        return (self.margin / self.total) * 100

    @property
    def has_costs(self):
        return any(line.unit_cost is not None for line in self.lines.all())

    def is_expired(self, today=None):
        """
        A sent quotation lapses on its own. Derived rather than written by a
        job, so a price cannot appear to still stand because nothing ran.
        """
        from django.utils import timezone

        if self.state != self.SENT or not self.valid_until:
            return False
        return (today or timezone.localdate()) > self.valid_until

    def derived_state(self):
        return self.EXPIRED if self.is_expired() else self.state

    @property
    def is_editable(self):
        return self.state == self.DRAFT

    def carry_lineage(self):
        """
        Take the job reference of whatever this quotation belongs to.

        A quotation is usually written before there is a project, so it
        mints its own reference and stands alone. The moment it is attached
        to a project or a ticket, that job's reference is the one that
        counts: the project's already runs through the ticket it came from,
        the field jobs worked under it and any invoice raised on it, and
        moving those to match a quote would rewrite the history of work
        already done. So the quote joins the job, never the other way round.

        Returns whether it moved, so a caller can say so.
        """
        source = self.project or self.ticket
        if source is None or self.job_ref == source.job_ref:
            return False
        self.job_ref = source.job_ref
        return True

    @property
    def invoiced_total(self):
        return sum((invoice.total for invoice in self.invoices.all()), Decimal("0"))

    def __str__(self):
        return self.number


class QuotationLine(models.Model):
    """
    One priced item. `unit_cost` is optional and is A-1's own figure: it
    never prints on the customer's copy, and it is what makes the margin
    visible while the price is still being decided rather than afterwards.
    """

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    @property
    def cost_total(self):
        return self.quantity * (self.unit_cost or Decimal("0"))

    @property
    def margin(self):
        return self.line_total - self.cost_total

    def __str__(self):
        return self.description
