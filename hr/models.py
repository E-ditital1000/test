"""
HR — attendance tracking only. Payroll, leave, appraisals, contracts,
biometrics and rostering are Phase Two and no field here anticipates them:
there are deliberately no salary, rate or pay fields, not even "for later".

Attendance is an append-only event log. Every displayed figure — the day
row, the roll-call, the monthly report — is derived from those events by
`hr.services.rebuild_attendance_days`, and a correction is a new row that
supersedes an event rather than overwriting it.
"""
from django.conf import settings
from django.db import models

from config.mixins import (
    MobileOriginatedModel,
    SoftDeleteModel,
    TimeStampedModel,
)


class Employee(SoftDeleteModel, TimeStampedModel):
    """
    The employee register. Deactivation preserves all history — the record
    and every attendance event it owns stay queryable.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee"
    )
    staff_id = models.CharField(max_length=30, unique=True)
    job_title = models.CharField(max_length=80, blank=True)
    department = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    supervisor = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_members",
    )
    start_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["staff_id"]

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    def __str__(self):
        return f"{self.staff_id} — {self.full_name}"


class AttendanceEvent(MobileOriginatedModel):
    """
    Append-only clock event. Written locally on the phone first and synced
    with a client-generated UUID, so the same event submitted twice — or
    from two devices — resolves to one row. A missing GPS fix records the
    event anyway with `location_unavailable`; the clock is never blocked on
    location.

    Rows are never updated after insert. An amendment is an
    AttendanceCorrection pointing at this event.
    """

    CLOCK_IN = "in"
    CLOCK_OUT = "out"
    KINDS = [
        (CLOCK_IN, "Clock in"),
        (CLOCK_OUT, "Clock out"),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="attendance_events"
    )
    kind = models.CharField(max_length=8, choices=KINDS)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Set when a supervisor records on the employee's behalf.",
    )

    class Meta:
        ordering = ["device_timestamp"]
        indexes = [models.Index(fields=["employee", "device_timestamp"])]

    def latest_correction(self):
        return self.corrections.order_by("-created_at").first()

    @property
    def effective_timestamp(self):
        """The device timestamp unless a correction has superseded it."""
        correction = self.latest_correction()
        if correction and correction.corrected_timestamp:
            return correction.corrected_timestamp
        return self.device_timestamp

    @property
    def is_voided(self):
        correction = self.latest_correction()
        return bool(correction and correction.action == AttendanceCorrection.VOID)

    def __str__(self):
        return f"{self.employee} {self.get_kind_display()} @ {self.device_timestamp:%Y-%m-%d %H:%M}"


class AttendanceCorrection(TimeStampedModel):
    """
    Append-only amendment to a clock event. Carries actor, timestamp, the
    old value, the new value and a mandatory reason drawn from the fixed
    Settings list — a blank reason cannot be submitted. The original event
    row is untouched and stays visible alongside the correction.
    """

    AMEND_TIME = "amend_time"
    VOID = "void"
    ACTIONS = [
        (AMEND_TIME, "Amend time"),
        (VOID, "Void event"),
    ]

    event = models.ForeignKey(
        AttendanceEvent, on_delete=models.PROTECT, related_name="corrections"
    )
    action = models.CharField(max_length=20, choices=ACTIONS, default=AMEND_TIME)
    original_timestamp = models.DateTimeField()
    corrected_timestamp = models.DateTimeField(null=True, blank=True)
    reason = models.ForeignKey(
        "config.CorrectionReason", on_delete=models.PROTECT, related_name="corrections"
    )
    note = models.TextField(blank=True)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_corrections"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Correction on {self.event_id} by {self.corrected_by}"


class AttendanceDay(models.Model):
    """
    DERIVED — never written by hand and safe to wipe. Every row here is
    reconstructed from AttendanceEvent plus AttendanceCorrection by
    `hr.services.rebuild_attendance_days`; a test proves a wiped table
    rebuilds identically. Reports read this table only as a cache of that
    computation.
    """

    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    STATES = [
        (PRESENT, "Present"),
        (LATE, "Late"),
        (ABSENT, "Absent"),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="attendance_days"
    )
    date = models.DateField()
    first_in = models.DateTimeField(null=True, blank=True)
    last_out = models.DateTimeField(null=True, blank=True)
    total_minutes = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=10, choices=STATES, default=ABSENT)
    still_clocked_in = models.BooleanField(default=False)
    correction_count = models.PositiveIntegerField(default=0)
    rebuilt_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "employee__staff_id"]
        unique_together = ("employee", "date")
        indexes = [models.Index(fields=["date", "employee"])]

    @property
    def total_hours(self):
        return round(self.total_minutes / 60, 2)

    def __str__(self):
        return f"{self.employee} {self.date} ({self.get_state_display()})"
