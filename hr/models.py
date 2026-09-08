"""
HR — attendance tracking only. Payroll, leave, appraisals, contracts,
biometrics and rostering are Phase Two and no field here anticipates them:
there are deliberately no salary, rate or pay fields, not even "for later".

Attendance is an append-only event log. Every displayed figure — the day
row, the roll-call, the monthly report — is derived from those events by
`hr.services.rebuild_attendance_days`, and a correction is a new row that
supersedes an event rather than overwriting it.
"""
import uuid

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


class AttendanceCode(TimeStampedModel):
    """
    A QR code HR generates for a location, prints, and sticks on a wall.
    Scanning it is what records a clock event -- an employee cannot clock in
    or out without one.

    What this does and does not prove
    ---------------------------------
    It proves whoever clocked had the code in front of them. It does not
    prove they were at the site: a printed code is a shared secret, and a
    photograph of it works exactly as well as the paper does until it
    expires. That is inherent to printing a code rather than rotating one on
    a screen, and it is why `expires_at` is mandatory rather than optional --
    the expiry is the only thing limiting a leaked code.

    So the honest control here is not prevention, it is the record: every
    event carries the code it was scanned from, every code lists what was
    scanned from it, and the GPS fix taken at the scan is on the event. A
    code being used from the wrong side of Monrovia is visible afterwards.

    `token` is what the QR image encodes. `short_code` is the same secret in
    a form somebody can read off the wall and type when a camera will not
    start -- possession of the code either way, which is all the QR ever
    established.
    """

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Typed by hand, so: no O/0/I/1 to misread, and short enough to enter on
    # a phone keyboard in the rain.
    short_code = models.CharField(max_length=12, unique=True, editable=False)

    description = models.CharField(max_length=120, help_text="What this code is for, e.g. Main office")
    location_name = models.CharField(max_length=120, help_text="Where it is posted")

    expires_at = models.DateTimeField(
        help_text="After this moment the code stops working and HR prints a new one."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="+",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="+",
    )

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["token"])]

    def __str__(self):
        return f"{self.description} ({self.location_name})"

    @staticmethod
    def new_short_code():
        """
        Nine characters from an alphabet with no look-alikes, grouped for
        reading aloud: A1-4KP-9RT.
        """
        import secrets

        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        body = "".join(secrets.choice(alphabet) for _ in range(6))
        return f"{body[:3]}-{body[3:]}"

    def save(self, *args, **kwargs):
        if not self.short_code:
            for _ in range(12):
                candidate = self.new_short_code()
                if not AttendanceCode.objects.filter(short_code=candidate).exists():
                    self.short_code = candidate
                    break
            else:  # pragma: no cover - needs 12 collisions in a 32^6 space
                raise ValueError("could not allocate an unused short code")
        super().save(*args, **kwargs)

    def status_at(self, moment=None):
        """
        Revoked beats expired: a code HR pulled is pulled, whatever the
        clock says.

        Judged at `moment`, not at now, because a clock event taken with no
        signal reaches the server later. The question the server has to
        answer is whether the code was good when it was scanned -- refusing
        it because it expired during the drive back would throw away a real
        event that a real person really recorded.
        """
        from django.utils import timezone

        moment = moment or timezone.now()
        if self.revoked_at is not None and self.revoked_at <= moment:
            return self.REVOKED
        if self.expires_at <= moment:
            return self.EXPIRED
        return self.ACTIVE

    @property
    def status(self):
        return self.status_at()

    @property
    def is_usable(self):
        return self.status == self.ACTIVE


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
    # Which code this was scanned from. Nullable because every event
    # recorded before codes existed has none, and because a supervisor
    # correction creates history that was never scanned at all. PROTECT, so
    # a code that has been used cannot be deleted out from under its events.
    attendance_code = models.ForeignKey(
        "AttendanceCode",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="events",
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
