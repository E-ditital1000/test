"""
HR — attendance and leave. Payroll, appraisals, contracts, biometrics and
rostering remain Phase Two and no field here anticipates them: there are
deliberately no salary, rate or pay fields, not even "for later".

Attendance is an append-only event log. Every displayed figure — the day
row, the roll-call, the monthly report — is derived from those events by
`hr.services.rebuild_attendance_days`, and a correction is a new row that
supersedes an event rather than overwriting it.

Leave follows the same discipline. A request's state is the latest
append-only decision on it from `approvals.Approval`, never a mutable status
field; and a balance is computed from entitlement rules, approved requests
and recorded adjustments at the moment it is asked for, never stored as a
number that can drift. See docs/leave-management-plan.md for the policy
decisions behind the figures.
"""
import uuid

from django.conf import settings
from django.db import models

from approvals.models import ApprovalTrailMixin
from config.mixins import (
    MobileOriginatedModel,
    SoftDeleteModel,
    TimeStampedModel,
)


class Department(SoftDeleteModel, TimeStampedModel):
    """
    The parts of the company somebody can belong to, owned by HR.

    A list rather than a text box because it was a text box: "Operations",
    "operations" and "Ops" were three departments as far as any report was
    concerned, and nobody found out until the roll-call was grouped by one.
    """

    name = models.CharField(max_length=80, unique=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


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
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employees",
    )
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
    reconstructed by `hr.services.rebuild_attendance_days` from
    AttendanceEvent plus AttendanceCorrection, **and from approved leave**;
    a test proves a wiped table rebuilds identically. Reports read this table
    only as a cache of that computation.

    Leave widened that promise deliberately. The alternative was to overlay
    leave at display time in the roll-call and the monthly report separately,
    which reintroduces the bug this codebase keeps finding: the same rule
    living on one screen only, so the second screen disagrees with the first.
    One derivation, two inputs.

    Rows exist for days that have happened. Approved leave in the future is
    not written here — it belongs on the leave calendar, which is the screen
    that exists to show it.
    """

    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    # Approved leave, and no clock event. Without this a person on approved
    # annual leave sat in the supervisor's exceptions list next to the person
    # who simply did not turn up, because a day with no events has no row and
    # the roll-call reads a missing row as absent.
    ON_LEAVE = "on_leave"
    STATES = [
        (PRESENT, "Present"),
        (LATE, "Late"),
        (ABSENT, "Absent"),
        (ON_LEAVE, "On leave"),
    ]
    # The states that mean somebody worked. Payroll must not read a paid
    # leave day as an attended one, so this is named rather than expressed
    # as "not absent" — which is what it used to be, and what quietly broke
    # the moment a fourth state existed.
    WORKED = (PRESENT, LATE)

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
    # Approved leave covered this day. Separate from `state` because the two
    # answer different questions: this says the day was granted, `state` says
    # what actually happened on it. Both true at once is a real case —
    # somebody recalled from leave, or who came in anyway — and the roll-call
    # shows it as an exception rather than discarding either fact.
    on_leave = models.BooleanField(default=False)
    leave_type = models.ForeignKey(
        "LeaveType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_days",
        help_text="Which leave covered the day, so the export can tell paid "
                  "leave from unpaid without joining back to the request.",
    )

    class Meta:
        ordering = ["-date", "employee__staff_id"]
        unique_together = ("employee", "date")
        indexes = [models.Index(fields=["date", "employee"])]

    @property
    def total_hours(self):
        return round(self.total_minutes / 60, 2)

    def __str__(self):
        return f"{self.employee} {self.date} ({self.get_state_display()})"


# --------------------------------------------------------------------------
# Leave
# --------------------------------------------------------------------------

class LeaveType(SoftDeleteModel, TimeStampedModel):
    """
    The kinds of leave A-1 grants, owned by HR in Settings rather than by
    this file. Annual, sick, maternity and the rest are policy, and policy
    changes without a release.

    How many days each kind carries is not here: it is in
    `LeaveEntitlementRule`, because the statutory annual entitlement is a
    function of how long somebody has worked and not a single number. A type
    with no rules at all is untracked rather than zero — unpaid leave has no
    allowance to run down.
    """

    code = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    is_paid = models.BooleanField(
        default=True,
        help_text="Unpaid leave still has to be approved and recorded; it "
                  "just does not count as a paid day downstream.",
    )
    # Sick leave is the case this exists for: the Decent Work Act allows
    # three undocumented sick days in a rolling twelve months and requires
    # medical documentation beyond that. Null means a document is never
    # asked for; 0 means it is always required.
    document_required_after_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Days allowed in a year before a supporting document is "
                  "asked for. Leave empty if one is never needed.",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return self.label

    def entitlement_after(self, months):
        """
        The allowance for somebody with this much service, or None when this
        type carries no allowance at all.

        Reads `.all()` and picks in Python rather than filtering in SQL, so
        that a caller who has prefetched the bands pays nothing: `.filter()`
        on a related manager ignores the prefetch cache and issues a fresh
        query every time. The register screen asks this once per employee
        per type, which is where that mattered. There are never more than a
        handful of bands.
        """
        rules = [
            rule
            for rule in self.entitlement_rules.all()
            if rule.after_months <= months
        ]
        if not rules:
            return None
        return max(rules, key=lambda rule: rule.after_months).days

    @property
    def is_tracked(self):
        # Same reasoning: `.exists()` always hits the database, `.all()`
        # reuses a prefetch when there is one.
        return bool(self.entitlement_rules.all())


class LeaveEntitlementRule(TimeStampedModel):
    """
    One band of a leave type's allowance: "after this many months of
    service, this many days a year".

    The Decent Work Act's annual leave is four such bands — one week in the
    first year, rising to four weeks after five years — so the law lives
    here as data that HR can raise when A-1 becomes more generous. A flat
    allowance for everybody is the same thing with a single band at zero
    months, which is why there is no separate "flat days" field to keep in
    step with this one.

    Days, not weeks: A-1 works a six-day week, so a statutory "week" is six
    working days here. Storing the figure already converted means no screen
    has to know the working week to show an allowance.
    """

    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.CASCADE, related_name="entitlement_rules"
    )
    after_months = models.PositiveIntegerField(
        default=0,
        help_text="Months of continuous service. 0 applies from the first day.",
    )
    days = models.PositiveIntegerField(help_text="Working days a year at this band.")

    class Meta:
        ordering = ["leave_type__order", "after_months"]
        unique_together = ("leave_type", "after_months")

    def __str__(self):
        return f"{self.leave_type.label}: {self.days} days after {self.after_months} months"


class PublicHoliday(TimeStampedModel):
    """
    A day nobody is expected at work, and that no leave request consumes.

    Entered per year rather than derived from rules, deliberately. Several of
    Liberia's eleven move — Decoration Day is the second Wednesday of March,
    Fast and Prayer Day the second Friday of April — and any of them can be
    shifted or added by proclamation. A list HR confirms each December is
    right every year; a clever rule is right until the first proclamation.
    """

    date = models.DateField(unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ["date"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"{self.name} ({self.date:%d %b %Y})"


class LeaveRequest(TimeStampedModel, ApprovalTrailMixin):
    """
    Somebody asking to be away, and the answer.

    The state is not a field on this row. It is the latest append-only
    decision from `approvals.Approval`, the same mechanism assessments and
    requisitions use, so the trail carries who decided, when, and what they
    said — and a declined request cannot be saved without a reason the
    person will read.

    `working_days` is the one derived figure that is stored. The public
    holiday list can change after a request is decided, and the number HR
    approved is the number that was agreed: recomputing it later would
    quietly restate a settled decision. Balances recompute; this does not.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="leave_requests"
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.PROTECT, related_name="requests"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    working_days = models.PositiveIntegerField(
        default=0,
        help_text="Working days consumed, as counted when this was raised.",
    )
    reason = models.TextField(
        help_text="Why the time is needed. HR reads this when deciding."
    )
    document = models.FileField(upload_to="leave/", blank=True, null=True)
    handover_note = models.TextField(
        blank=True, help_text="What is outstanding, and who is picking it up."
    )
    contact_while_away = models.CharField(max_length=80, blank=True)
    # Who typed it. HR can put a request in for somebody who has no account
    # reachable from site, the same way a supervisor records a clock event on
    # a crew member's behalf; the request is still the employee's.
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leave_requests_submitted",
    )

    class Meta:
        ordering = ["-start_date", "-created_at"]
        indexes = [
            models.Index(fields=["employee", "start_date"]),
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self):
        return (
            f"{self.employee} {self.leave_type.label} "
            f"{self.start_date:%d %b} to {self.end_date:%d %b %Y}"
        )

    @property
    def is_declined(self):
        return self.approval_state == "declined"

    @property
    def is_cancelled(self):
        return self.approval_state == "cancelled"

    @property
    def is_waiting(self):
        """Raised and not yet answered — including one sent back for more."""
        return self.approval_state in ("submitted", "returned")

    @property
    def is_settled(self):
        return self.approval_state in ("approved", "declined", "cancelled")

    def covers(self, day):
        return self.start_date <= day <= self.end_date


class LeaveAdjustment(TimeStampedModel):
    """
    A correction to somebody's balance, with a reason and an actor.

    This exists because A-1's leave records before go-live are not
    reconstructable. Rather than invent approved requests for history nobody
    can vouch for, HR states the correction directly and it is visible as a
    correction: the balance screen shows it as an entered figure beside the
    computed ones, so nobody mistakes a judgement for a calculation.

    It also covers the honest exceptions afterwards — a day credited back
    after somebody was recalled from leave, or a day granted outside the
    normal allowance. Negative days take leave away, positive days give it.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="leave_adjustments"
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.PROTECT, related_name="adjustments"
    )
    leave_year = models.PositiveIntegerField()
    days = models.IntegerField(
        help_text="Positive adds days to the balance, negative takes them away."
    )
    reason = models.TextField(help_text="Why. This is the audit trail for the figure.")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="leave_adjustments_recorded",
    )

    class Meta:
        ordering = ["-leave_year", "-created_at"]
        indexes = [models.Index(fields=["employee", "leave_year"])]

    def __str__(self):
        sign = "+" if self.days >= 0 else ""
        return (
            f"{self.employee} {self.leave_type.label} "
            f"{self.leave_year}: {sign}{self.days}"
        )
