"""
Finance. Every cost and every invoice attaches to a project, so project
profitability is computable at query time — no denormalised totals are
stored anywhere in this module.
"""
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
