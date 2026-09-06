"""
Settings-owned configuration. Service types and their assessment question
sets, the ticket/project status lists, company details and the handful of
tunable policy values live here as data, so A-1 adds a service or changes
the requisition threshold without a code release.
"""
from django.conf import settings
from django.db import models

from .mixins import SoftDeleteModel, TimeStampedModel


class CompanyDetail(TimeStampedModel):
    """Singleton — company details shown across the system and on invoices."""

    name = models.CharField(max_length=120, default="A-1 E-Digital Network")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    tax_number = models.CharField(max_length=60, blank=True)
    logo = models.ImageField(upload_to="company/", blank=True, null=True)

    class Meta:
        verbose_name = "company details"
        verbose_name_plural = "company details"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.name


class ServiceType(SoftDeleteModel, TimeStampedModel):
    """
    The kind of work A-1 does — electrical install, solar install, service
    call. Each carries its own assessment question set, which is what makes
    the field assessment form configuration rather than code.
    """

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def active_questions(self):
        return self.questions.filter(retired_at__isnull=True)

    def __str__(self):
        return self.name


class AssessmentQuestion(TimeStampedModel):
    """
    One question in a service type's assessment set. Questions are retired,
    never deleted: a retired question stops appearing on new assessments but
    every historical answer still resolves to its text and its position.
    """

    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    ANSWER_TYPES = [
        (TEXT, "Short text"),
        (LONG_TEXT, "Long text"),
        (NUMBER, "Number"),
        (BOOLEAN, "Yes / No"),
        (CHOICE, "Choice"),
    ]

    TECHNICIAN = "technician"
    CLIENT = "client"
    RESPONDENTS = [
        (TECHNICIAN, "Technician"),
        (CLIENT, "Client"),
    ]

    service_type = models.ForeignKey(
        ServiceType, on_delete=models.PROTECT, related_name="questions"
    )
    text = models.CharField(max_length=300)
    help_text = models.CharField(max_length=300, blank=True)
    answer_type = models.CharField(max_length=20, choices=ANSWER_TYPES, default=TEXT)
    # The client's own responses are captured and shown as a separate
    # labelled group, never mixed into the technician's answers.
    respondent = models.CharField(max_length=20, choices=RESPONDENTS, default=TECHNICIAN)
    choices = models.JSONField(default=list, blank=True)
    is_required = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    retired_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["order", "id"]

    @property
    def is_retired(self):
        return self.retired_at is not None

    def retire(self, actor=None):
        from django.utils import timezone

        self.retired_at = timezone.now()
        self.retired_by = actor
        self.save(update_fields=["retired_at", "retired_by"])

    def __str__(self):
        return self.text


class StatusOption(SoftDeleteModel, TimeStampedModel):
    """
    The ticket and project status lists, editable in Settings. Statuses are
    referenced by row, not by a hardcoded enum, so Operations can rename or
    add one without a release.
    """

    TICKET = "ticket"
    PROJECT = "project"
    KINDS = [
        (TICKET, "Ticket"),
        (PROJECT, "Project"),
    ]

    kind = models.CharField(max_length=20, choices=KINDS)
    code = models.SlugField(max_length=40)
    label = models.CharField(max_length=60)
    order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(default=False)
    is_terminal = models.BooleanField(default=False)

    class Meta:
        ordering = ["kind", "order"]
        unique_together = ("kind", "code")

    @classmethod
    def default_for(cls, kind):
        return (
            cls.objects.filter(kind=kind, is_active=True)
            .order_by("-is_default", "order")
            .first()
        )

    def __str__(self):
        return f"{self.get_kind_display()}: {self.label}"


class PolicySetting(TimeStampedModel):
    """
    Named policy values the business owns rather than the code: the
    requisition approval threshold above which executive approval is
    required, and the attendance policy figures behind "late".
    """

    REQUISITION_THRESHOLD = "requisition_approval_threshold"
    WORKDAY_START = "attendance_workday_start"
    LATE_AFTER_MINUTES = "attendance_late_after_minutes"

    key = models.SlugField(max_length=60, unique=True)
    value = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["key"]

    @classmethod
    def get_value(cls, key, default=None):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    def __str__(self):
        return f"{self.key} = {self.value}"


class CorrectionReason(SoftDeleteModel, TimeStampedModel):
    """
    The fixed list an attendance correction must pick from. A correction
    cannot be submitted with a blank reason, and the list is Settings-owned
    so HR can extend it.
    """

    code = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return self.label
