"""
Projects. A project carries the job lineage forward from the ticket it was
converted from, and surfaces its field jobs, assessments and running cost
by query rather than by storing denormalised totals.
"""
from django.conf import settings
from django.db import models
from django.db.models import F

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


class TaskQuerySet(models.QuerySet):
    """
    The questions every screen asks of a task. Kept here so the dashboard,
    the phone and the attention queue cannot each answer "is this overdue"
    slightly differently.
    """

    def open(self):
        return self.filter(completed_at__isnull=True)

    def overdue(self, on=None):
        from django.utils import timezone

        return self.open().filter(due_date__isnull=False, due_date__lt=on or timezone.localdate())

    def for_person(self, user):
        """Only ever your own work. Scoped by construction, not by a filter
        a screen has to remember to apply."""
        return self.filter(assignee=user)

    def loose(self):
        """Tasks that belong to no particular visit — the office's list,
        as opposed to a checklist for a day on a site."""
        return self.filter(field_job__isnull=True)


class Task(TimeStampedModel):
    """
    A piece of work owned by one person, on a project or on a visit.

    A task the assignee never sees is a note to yourself, so this appears on
    their dashboard and on their phone, and lands in the attention queue once
    it is overdue.

    It is deliberately not the same record as a field job. A task is "someone
    should do this"; a field job is a promise to a customer to be at their
    site at a time, carrying a reference in the job lineage, a service type
    that decides the assessment questions, GPS check-ins and an approval
    trail. What they share is a person and a day's work, so they share a
    list — not a table.
    """

    # A task belongs to a project, to a visit, or to both — because work on
    # a visit is work on the project that visit serves.
    #
    # Both are nullable, and between them one is always set. A visit can be
    # scheduled straight off a ticket with no project behind it, and its
    # checklist still has to live somewhere; a task set in the office often
    # belongs to no particular visit. Where a visit does have a project,
    # `save` fills the project in, so a task is never adrift from the job.
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, null=True, blank=True, related_name="tasks"
    )
    field_job = models.ForeignKey(
        "fieldjobs.FieldJob",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
        help_text="The visit this is part of, where it is part of one.",
    )
    title = models.CharField(max_length=200)
    # A title is a label. What to bring, which client, what "done" looks
    # like — that belongs here, and the person doing it reads it on a phone.
    description = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks_assigned",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    # Work runs over a stretch of days, not on one. `start_date` is the day
    # it can begin, `due_date` the day it must be finished by — so a task
    # reads as a range on the phone and somebody scheduling around it can see
    # when it occupies a person.
    #
    # Both stay optional: plenty of work is "whenever you get to it", and a
    # form that insists on two dates for it would be answered with invented
    # ones. `due_date` remains the end of the range, so "overdue" means the
    # same thing here, on the dashboard and in the attention queue.
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # "Done" on its own answers nothing six weeks later.
    completion_note = models.TextField(blank=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        # Open work first, then soonest due.
        #
        # Both terms are nullable, and NULL placement is not portable:
        # SQLite sorts NULLs first, PostgreSQL sorts them last. Left to the
        # default, this list silently inverts between a developer's machine
        # and the server. Saying where NULLs go makes it mean one thing.
        #
        # completed_at: NULL means open, and open work leads.
        # due_date:     NULL means undated, and undated work trails dated.
        ordering = [
            F("completed_at").asc(nulls_first=True),
            F("due_date").asc(nulls_last=True),
            "id",
        ]

    def save(self, *args, **kwargs):
        """
        Work on a visit is work on the project the visit serves, so the
        project is taken from the visit rather than asked for twice and
        answered differently. A visit with no project of its own leaves this
        alone, and the task belongs to the visit only.
        """
        if self.field_job_id and not self.project_id and self.field_job.project_id:
            self.project_id = self.field_job.project_id
            # A save naming its own fields would otherwise write everything
            # but the one this just worked out.
            if kwargs.get("update_fields"):
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["project"]
        super().save(*args, **kwargs)

    @property
    def is_complete(self):
        return self.completed_at is not None

    @property
    def is_overdue(self):
        from django.utils import timezone

        return (
            not self.is_complete
            and self.due_date is not None
            and self.due_date < timezone.localdate()
        )

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

    @property
    def items_total(self):
        from decimal import Decimal

        return sum((item.estimated_total for item in self.items.all()), Decimal("0"))

    @property
    def is_itemised(self):
        return self.items.exists()

    @property
    def has_estimates(self):
        return any(item.estimated_unit_cost is not None for item in self.items.all())

    def requires_executive_approval(self):
        from config.models import PolicySetting

        threshold = PolicySetting.get_value(PolicySetting.REQUISITION_THRESHOLD, "0")
        try:
            return self.amount > float(threshold)
        except (TypeError, ValueError):
            return False

    def __str__(self):
        return f"{self.reference} ({self.amount})"


class RequisitionItem(models.Model):
    """
    One thing the job needs: what it is, how many, and what it is thought to
    cost.

    A requisition used to be a sentence and a single figure, which is not
    something anybody can check, buy against or price a quotation from. The
    technician standing on the site knows the items; nobody else does, and
    by the time it reaches Finance that knowledge is a paragraph.

    The estimate is optional. A technician is asked what is needed, not what
    it costs — a figure they would have to invent is worse than a blank, and
    whoever buys it fills the real cost in as an expense afterwards.
    """

    requisition = models.ForeignKey(
        "projects.Requisition", on_delete=models.CASCADE, related_name="items"
    )
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit = models.CharField(
        max_length=30, blank=True, help_text="Metres, bags, lengths — whatever it is counted in."
    )
    estimated_unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def estimated_total(self):
        from decimal import Decimal

        return self.quantity * (self.estimated_unit_cost or Decimal("0"))

    @property
    def quantity_label(self):
        """`12 lengths`, or just `12` where nobody said what they are counted in."""
        quantity = self.quantity.normalize() if self.quantity == self.quantity.to_integral() else self.quantity
        return f"{quantity:f} {self.unit}".strip()

    def __str__(self):
        return f"{self.quantity_label} {self.description}".strip()


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
