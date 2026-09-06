"""
Customers & Tickets. A ticket is where a job originates and where its
lineage begins: the ticket mints `job_ref`, and the project, field jobs and
invoice that follow carry the same value.
"""
from django.conf import settings
from django.db import models

from config.mixins import JobLineageModel, SoftDeleteModel, TimeStampedModel


class Customer(SoftDeleteModel, TimeStampedModel):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Site(SoftDeleteModel, TimeStampedModel):
    """A physical location work is carried out at, belonging to a customer."""

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sites")
    name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ["customer__name", "name"]

    def __str__(self):
        return f"{self.customer.name} — {self.name}"


class Contact(SoftDeleteModel, TimeStampedModel):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="contacts")
    name = models.CharField(max_length=120)
    job_title = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "name"]

    def __str__(self):
        return self.name


class TicketQuerySet(models.QuerySet):
    """
    The Dashboard has to be able to ask for unassigned and ageing tickets, so
    those questions live here rather than being re-expressed as a slightly
    different filter in every screen that asks them.
    """

    def open(self):
        return self.filter(closed_at__isnull=True)

    def unassigned(self):
        return self.open().filter(assigned_to__isnull=True)

    def ageing(self, hours=24):
        from datetime import timedelta

        from django.utils import timezone

        return self.open().filter(created_at__lte=timezone.now() - timedelta(hours=hours))


class Ticket(JobLineageModel, TimeStampedModel):
    """
    Lifecycle: new -> assigned -> in progress -> closed, with the status
    values themselves drawn from the Settings-owned list rather than a
    hardcoded enum.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    PRIORITIES = [
        (LOW, "Low"),
        (NORMAL, "Normal"),
        (HIGH, "High"),
        (URGENT, "Urgent"),
    ]

    reference = models.CharField(max_length=20, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="tickets")
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, null=True, blank=True, related_name="tickets"
    )
    service_type = models.ForeignKey(
        "config.ServiceType", on_delete=models.PROTECT, related_name="tickets"
    )
    status = models.ForeignKey(
        "config.StatusOption", on_delete=models.PROTECT, related_name="tickets"
    )
    priority = models.CharField(max_length=10, choices=PRIORITIES, default=NORMAL)
    description = models.TextField()
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="tickets_raised",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets_assigned",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assigned_to", "closed_at"]),
            models.Index(fields=["job_ref"]),
        ]

    @property
    def is_open(self):
        return self.closed_at is None

    @property
    def age_days(self):
        from django.utils import timezone

        end = self.closed_at or timezone.now()
        return (end - self.created_at).days

    @property
    def age_label(self):
        """`22h` under a day, `3d` beyond it — the column is scanned, not read."""
        from django.utils import timezone

        end = self.closed_at or timezone.now()
        hours = int((end - self.created_at).total_seconds() // 3600)
        return f"{hours}h" if hours < 24 else f"{hours // 24}d"

    @property
    def is_ageing(self):
        return self.is_open and self.age_days >= 1

    def log(self, actor, action, detail=""):
        """Append-only ticket history. Never updates an existing row."""
        return TicketEvent.objects.create(
            ticket=self, actor=actor, action=action, detail=detail
        )

    def __str__(self):
        return f"{self.reference} — {self.customer.name}"


class TicketEvent(TimeStampedModel):
    """Append-only ticket history: status changes and assignments."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    action = models.CharField(max_length=60)
    detail = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ticket.reference}: {self.action}"
