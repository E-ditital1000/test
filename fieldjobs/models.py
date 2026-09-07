"""
Field Jobs — the technician's mobile surface. Everything a phone creates is
offline-first: it is written locally, carries a client-generated UUID and a
device timestamp, and syncs idempotently. GPS is captured at the check-in
and at assessment submission only.

The assessment form is generated from the service type's question set in
Settings and is never hardcoded here.
"""
from django.conf import settings
from django.db import models

from approvals.models import ApprovalTrailMixin
from config.mixins import (
    JobLineageModel,
    MobileOriginatedModel,
    TimeStampedModel,
)


class FieldJob(JobLineageModel, TimeStampedModel):
    """A visit to a site, carrying the lineage of the ticket/project it serves."""

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    STATES = [
        (SCHEDULED, "Scheduled"),
        (IN_PROGRESS, "In progress"),
        (COMPLETED, "Completed"),
    ]

    reference = models.CharField(max_length=20, unique=True)
    ticket = models.ForeignKey(
        "crm.Ticket", on_delete=models.PROTECT, null=True, blank=True, related_name="field_jobs"
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="field_jobs",
    )
    customer = models.ForeignKey("crm.Customer", on_delete=models.PROTECT, related_name="field_jobs")
    site = models.ForeignKey(
        "crm.Site", on_delete=models.PROTECT, null=True, blank=True, related_name="field_jobs"
    )
    service_type = models.ForeignKey(
        "config.ServiceType", on_delete=models.PROTECT, related_name="field_jobs"
    )
    # The lead. One person is accountable for the visit and is the only one
    # who can submit its assessment — an assessment with three possible
    # authors is an assessment nobody owns. Everyone else on site is crew.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="field_jobs"
    )
    scheduled_for = models.DateTimeField()
    state = models.CharField(max_length=20, choices=STATES, default=SCHEDULED)
    instructions = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["scheduled_for"]
        indexes = [
            models.Index(fields=["assigned_to", "scheduled_for"]),
            models.Index(fields=["job_ref"]),
        ]

    @property
    def crew_size(self):
        """The lead plus anyone else on site."""
        return 1 + self.crew.count()

    def is_led_by(self, user):
        return self.assigned_to_id == user.pk

    def includes(self, user):
        """Lead or crew — everyone who should see this job on their phone."""
        return self.is_led_by(user) or self.crew.filter(employee__user=user).exists()

    def __str__(self):
        return f"{self.reference} — {self.customer.name}"


class FieldJobCrew(TimeStampedModel):
    """
    Who else is on site for this visit, drawn from the HR employee register.

    A crew member sees the job on their own phone and can check in against
    it, but the assessment stays with the lead. That keeps one author per
    report while still recording who was actually there — which is what a
    supervisor needs when a customer disputes what happened on the day.

    Where the work splits across people rather than sharing one visit, the
    answer is several field jobs on the project, not several leads on one.
    """

    field_job = models.ForeignKey(FieldJob, on_delete=models.CASCADE, related_name="crew")
    employee = models.ForeignKey(
        "hr.Employee", on_delete=models.PROTECT, related_name="field_job_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="+",
    )

    class Meta:
        ordering = ["employee__staff_id"]
        unique_together = ("field_job", "employee")

    def __str__(self):
        return f"{self.employee} on {self.field_job.reference}"


class CheckIn(MobileOriginatedModel):
    """GPS check-in on arrival. Append-only; a missing fix does not block it."""

    field_job = models.ForeignKey(FieldJob, on_delete=models.PROTECT, related_name="check_ins")
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="check_ins"
    )

    class Meta:
        ordering = ["device_timestamp"]

    def __str__(self):
        return f"Check-in {self.field_job.reference} by {self.technician}"


class Assessment(ApprovalTrailMixin, MobileOriginatedModel):
    """
    A structured site assessment. `client_uuid` makes resubmission
    idempotent — the same assessment pushed from two devices creates one
    row. The answers are keyed to the question rows that were live when it
    was filled in, so retiring a question later never rewrites history.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    STATES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
    ]

    field_job = models.ForeignKey(FieldJob, on_delete=models.PROTECT, related_name="assessments")
    service_type = models.ForeignKey(
        "config.ServiceType", on_delete=models.PROTECT, related_name="assessments"
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assessments"
    )
    state = models.CharField(max_length=20, choices=STATES, default=DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-device_timestamp"]

    @property
    def completeness(self):
        """(answered, required) for the question set as it stands."""
        required = self.service_type.active_questions().filter(is_required=True)
        answered = self.answers.filter(question__in=required).exclude(value_text="").count()
        return answered, required.count()

    @property
    def gps_match_distance_m(self):
        """
        Metres between where the assessment was submitted and the site on
        record, so a reviewer can see whether the technician was there.
        """
        site = self.field_job.site
        if not (site and site.latitude and site.longitude and self.has_location):
            return None
        from math import atan2, cos, radians, sin, sqrt

        lat1, lon1 = radians(float(site.latitude)), radians(float(site.longitude))
        lat2, lon2 = radians(float(self.latitude)), radians(float(self.longitude))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return round(6371000 * 2 * atan2(sqrt(a), sqrt(1 - a)))

    def __str__(self):
        return f"Assessment for {self.field_job.reference}"


class AssessmentAnswer(TimeStampedModel):
    """
    One answer. `question_text` is copied at submission so a question later
    retired or reworded still reads correctly on the historical assessment.
    """

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(
        "config.AssessmentQuestion", on_delete=models.PROTECT, related_name="answers"
    )
    question_text = models.CharField(max_length=300)
    respondent = models.CharField(max_length=20)
    value_text = models.TextField(blank=True)

    class Meta:
        ordering = ["question__order", "id"]
        unique_together = ("assessment", "question")

    def __str__(self):
        return f"{self.question_text}: {self.value_text[:40]}"


class AssessmentPhoto(TimeStampedModel):
    """Each photo is tagged with what it shows — untagged photos are not accepted."""

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="photos")
    client_uuid = models.UUIDField(unique=True)
    image = models.ImageField(upload_to="assessments/")
    label = models.CharField(max_length=150)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.label
