"""
Projects. A project carries the job lineage forward from the ticket it was
converted from, and surfaces its field jobs, assessments and running cost
by query rather than by storing denormalised totals.
"""
from django.conf import settings
from django.db import models

from approvals.models import ApprovalTrailMixin
from config.mixins import JobLineageModel, TimeStampedModel


class Project(JobLineageModel, TimeStampedModel):
    """
    Visible lifecycle: request -> site assessment -> review and approve ->
    quote -> execution -> handover -> invoice. Every advance is recorded as
    a ProjectStageEvent with actor and timestamp; the `stage` field is a
    cache of the newest event.
    """

    REQUEST = "request"
    SITE_ASSESSMENT = "site_assessment"
    REVIEW_APPROVE = "review_approve"
    QUOTE = "quote"
    EXECUTION = "execution"
    HANDOVER = "handover"
    INVOICE = "invoice"
    STAGES = [
        (REQUEST, "Request"),
        (SITE_ASSESSMENT, "Site assessment"),
        (REVIEW_APPROVE, "Review & approve"),
        (QUOTE, "Quote"),
        (EXECUTION, "Execution"),
        (HANDOVER, "Handover"),
        (INVOICE, "Invoice"),
    ]
    STAGE_ORDER = [s for s, _ in STAGES]

    reference = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    # Permanently links both records; the ticket keeps its own identity.
    ticket = models.OneToOneField(
        "crm.Ticket", on_delete=models.PROTECT, null=True, blank=True, related_name="project"
    )
    customer = models.ForeignKey("crm.Customer", on_delete=models.PROTECT, related_name="projects")
    site = models.ForeignKey(
        "crm.Site", on_delete=models.PROTECT, null=True, blank=True, related_name="projects"
    )
    service_type = models.ForeignKey(
        "config.ServiceType", on_delete=models.PROTECT, related_name="projects"
    )
    status = models.ForeignKey(
        "config.StatusOption", on_delete=models.PROTECT, related_name="projects"
    )
    stage = models.CharField(max_length=30, choices=STAGES, default=REQUEST)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_projects",
    )
    start_date = models.DateField(null=True, blank=True)
    target_end_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["job_ref"]), models.Index(fields=["manager", "stage"])]

    def advance_to(self, stage, actor, note=""):
        """Stage advancement is an event first and a field second."""
        previous = self.stage
        self.stage = stage
        self.save(update_fields=["stage"])
        return ProjectStageEvent.objects.create(
            project=self, from_stage=previous, to_stage=stage, actor=actor, note=note
        )

    def __str__(self):
        return f"{self.reference} — {self.name}"


class ProjectStageEvent(TimeStampedModel):
    """Append-only record of every stage advance."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="stage_events")
    from_stage = models.CharField(max_length=30, blank=True)
    to_stage = models.CharField(max_length=30)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.reference}: {self.from_stage} -> {self.to_stage}"


class Task(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["completed_at", "due_date", "id"]

    @property
    def is_complete(self):
        return self.completed_at is not None

    def __str__(self):
        return self.title


class ProjectCrew(TimeStampedModel):
    """Crew assignment, drawn from the HR employee register."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="crew")
    employee = models.ForeignKey(
        "hr.Employee", on_delete=models.PROTECT, related_name="project_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        unique_together = ("project", "employee")

    def __str__(self):
        return f"{self.employee} on {self.project.reference}"


class Requisition(ApprovalTrailMixin, TimeStampedModel):
    """
    Raised against a project and approved through the shared approvals
    mechanism. Amounts above the Settings-owned threshold require executive
    approval — that threshold is configuration, not a constant here.
    """

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="requisitions")
    reference = models.CharField(max_length=20, unique=True)
    description = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requisitions_raised"
    )
    needed_by = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def requires_executive_approval(self):
        from config.models import PolicySetting

        threshold = PolicySetting.get_value(PolicySetting.REQUISITION_THRESHOLD, "0")
        try:
            return self.amount > float(threshold)
        except (TypeError, ValueError):
            return False

    def __str__(self):
        return f"{self.reference} ({self.amount})"


class ProjectDocument(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    label = models.CharField(max_length=150)
    file = models.FileField(upload_to="project_documents/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label
