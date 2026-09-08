"""
The derivation layer for attendance. AttendanceDay is a cache, never a
source: this module is the single routine that rebuilds it from the
append-only event log plus corrections, and `hr.tests` proves that wiping
the table and rebuilding reconstructs it identically.
"""
import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from config.models import PolicySetting

from .models import AttendanceCode, AttendanceCorrection, AttendanceDay, AttendanceEvent


def _late_policy():
    """
    The workday start and the grace period are business settings, not
    constants here. A missing or unparsable value means "do not judge
    lateness" rather than a crash on the roll-call screen.
    """
    start_raw = PolicySetting.get_value(PolicySetting.WORKDAY_START)
    grace_raw = PolicySetting.get_value(PolicySetting.LATE_AFTER_MINUTES, "0")
    if not start_raw:
        return None, 0
    try:
        hour, minute = (int(part) for part in start_raw.split(":")[:2])
        return time(hour, minute), int(grace_raw)
    except (ValueError, TypeError):
        return None, 0


def effective_events(employee=None, since=None, until=None):
    """
    Every clock event with its corrections applied: voided events drop out
    and amended times use the corrected value. Returns
    (employee_id, local_date, kind, timestamp, was_corrected) tuples.
    """
    qs = AttendanceEvent.objects.select_related("employee").prefetch_related("corrections")
    if employee is not None:
        qs = qs.filter(employee=employee)
    if since is not None:
        qs = qs.filter(device_timestamp__gte=since)
    if until is not None:
        qs = qs.filter(device_timestamp__lte=until)

    rows = []
    for event in qs:
        correction = event.latest_correction()
        if correction and correction.action == "void":
            continue
        stamp = event.device_timestamp
        was_corrected = False
        if correction and correction.corrected_timestamp:
            stamp = correction.corrected_timestamp
            was_corrected = True
        rows.append(
            (
                event.employee_id,
                timezone.localtime(stamp).date(),
                event.kind,
                stamp,
                was_corrected,
            )
        )
    rows.sort(key=lambda row: (row[0], row[3]))
    return rows


def _summarise(day_rows, workday_start, grace_minutes):
    """
    Pair clock-ins with clock-outs for one employee-day. An unmatched
    clock-in at the end of the day is "still clocked in", not an error —
    the roll-call shows it as an exception rather than discarding it.
    """
    first_in = None
    last_out = None
    total_seconds = 0
    open_in = None
    corrections = 0

    for _emp, _date, kind, stamp, was_corrected in day_rows:
        if was_corrected:
            corrections += 1
        if kind == AttendanceEvent.CLOCK_IN:
            if open_in is None:
                open_in = stamp
            if first_in is None:
                first_in = stamp
        else:
            if open_in is not None:
                total_seconds += (stamp - open_in).total_seconds()
                open_in = None
            last_out = stamp

    still_clocked_in = open_in is not None

    if first_in is None:
        state = AttendanceDay.ABSENT
    elif workday_start is not None:
        local_in = timezone.localtime(first_in)
        cutoff = datetime.combine(local_in.date(), workday_start)
        cutoff = timezone.make_aware(cutoff, local_in.tzinfo)
        cutoff = cutoff + timedelta(minutes=grace_minutes)
        state = AttendanceDay.LATE if local_in > cutoff else AttendanceDay.PRESENT
    else:
        state = AttendanceDay.PRESENT

    return {
        "first_in": first_in,
        "last_out": last_out,
        "total_minutes": int(total_seconds // 60),
        "state": state,
        "still_clocked_in": still_clocked_in,
        "correction_count": corrections,
    }


@transaction.atomic
def rebuild_attendance_days(employee=None, since=None, until=None):
    """
    Recompute the derived rows for the given slice and return how many were
    written. Safe to run at any time: it is a pure function of the event
    log, so running it twice produces the same table.
    """
    workday_start, grace_minutes = _late_policy()

    grouped = defaultdict(list)
    for row in effective_events(employee=employee, since=since, until=until):
        grouped[(row[0], row[1])].append(row)

    scope = AttendanceDay.objects.all()
    if employee is not None:
        scope = scope.filter(employee=employee)
    if since is not None:
        scope = scope.filter(date__gte=timezone.localtime(since).date())
    if until is not None:
        scope = scope.filter(date__lte=timezone.localtime(until).date())
    scope.delete()

    days = [
        AttendanceDay(
            employee_id=employee_id,
            date=date,
            **_summarise(rows, workday_start, grace_minutes),
        )
        for (employee_id, date), rows in grouped.items()
    ]
    AttendanceDay.objects.bulk_create(days)
    return len(days)


# --------------------------------------------------------------------------
# Writing events
# --------------------------------------------------------------------------

class ClockError(Exception):
    """A clock event the log will not accept, with a message for the phone."""


def open_clock_in(employee, on_date=None):
    """
    The employee's unmatched clock-in, if they are currently on the clock.
    Read from the event log rather than a status field, so it stays true
    even after a correction rewrites history.
    """
    rows = [row for row in effective_events(employee=employee) if row[0] == employee.pk]
    open_in = None
    for _emp, _date, kind, stamp, _corrected in rows:
        if kind == AttendanceEvent.CLOCK_IN:
            open_in = stamp
        else:
            open_in = None
    return open_in


def resolve_code(raw):
    """
    Find the code a phone scanned or a person typed.

    The QR encodes the token; the wall also shows a short code for when a
    camera will not start. Both are the same secret, so both resolve here
    and neither is treated as weaker than the other.
    """
    value = (raw or "").strip()
    if not value:
        return None

    code = AttendanceCode.objects.filter(short_code__iexact=value).first()
    if code is not None:
        return code
    try:
        return AttendanceCode.objects.filter(token=uuid.UUID(value)).first()
    except (ValueError, AttributeError, TypeError):
        return None


@transaction.atomic
def record_clock_event(
    *,
    employee,
    kind,
    client_uuid,
    device_timestamp=None,
    latitude=None,
    longitude=None,
    accuracy_m=None,
    location_unavailable=False,
    recorded_by=None,
    attendance_code=None,
    require_code=True,
):
    """
    Append one clock event, idempotently.

    `client_uuid` is generated on the phone before the event leaves it, so
    the same event submitted twice — after a failed response, or from a
    second device — resolves to the one row that already exists. That is the
    whole offline-sync contract: resubmission is safe.

    A missing GPS fix never blocks the event; it is recorded with
    `location_unavailable` instead. A missing or dead attendance code DOES
    block it: the code is the thing the employee had to be in front of, and
    an event with no code proves nothing at all.

    `require_code=False` exists for a supervisor recording on somebody's
    behalf, where there was never a scan to record.
    """
    existing = AttendanceEvent.objects.filter(client_uuid=client_uuid).first()
    if existing is not None:
        # Already synced. Not an error — the phone is retrying.
        return existing, False

    when = device_timestamp or timezone.now()

    if require_code:
        if attendance_code is None:
            raise ClockError(
                "Scan the attendance code posted at your location, or type the "
                "short code printed under it."
            )
        # Judged at the moment of the scan, not now: an event taken with no
        # signal reaches the server later, and refusing it because the code
        # expired during the drive back would throw away a real event that a
        # real person really recorded.
        status = attendance_code.status_at(when)
        if status == AttendanceCode.REVOKED:
            raise ClockError(
                "That code has been withdrawn. Ask HR for the current one at "
                "your location."
            )
        if status == AttendanceCode.EXPIRED:
            raise ClockError(
                "That code had already expired when you scanned it. Ask HR "
                "for the current one at your location."
            )

    if kind == AttendanceEvent.CLOCK_OUT and open_clock_in(employee) is None:
        raise ClockError(
            "You are not clocked in, so there is nothing to clock out of. "
            "If you forgot to clock in, ask your supervisor for a correction."
        )
    if kind == AttendanceEvent.CLOCK_IN and open_clock_in(employee) is not None:
        raise ClockError(
            "You are already clocked in. Clock out first, or ask your "
            "supervisor for a correction."
        )

    event = AttendanceEvent.objects.create(
        employee=employee,
        kind=kind,
        client_uuid=client_uuid,
        device_timestamp=when,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        location_unavailable=location_unavailable or latitude is None,
        recorded_by=recorded_by,
        attendance_code=attendance_code,
    )
    rebuild_attendance_days(
        employee=employee,
        since=event.device_timestamp - timedelta(days=1),
        until=event.device_timestamp + timedelta(days=1),
    )
    return event, True


@transaction.atomic
def record_correction(*, event, actor, reason, action, corrected_timestamp=None, note=""):
    """
    Corrections supersede; they never overwrite. The original event row is
    untouched and stays visible alongside this record, and a blank reason
    cannot be submitted — the reason is what makes the record defensible.
    """
    if reason is None:
        raise ClockError("A correction needs a reason from the list.")
    if action == AttendanceCorrection.AMEND_TIME and corrected_timestamp is None:
        raise ClockError("Amending a time needs the corrected time.")

    correction = AttendanceCorrection.objects.create(
        event=event,
        action=action,
        original_timestamp=event.device_timestamp,
        corrected_timestamp=corrected_timestamp,
        reason=reason,
        note=note,
        corrected_by=actor,
    )
    rebuild_attendance_days(
        employee=event.employee,
        since=event.device_timestamp - timedelta(days=2),
        until=event.device_timestamp + timedelta(days=2),
    )
    return correction


def roll_call(supervisor_employee=None, on_date=None, team=None):
    """
    Today's team with a state per person, exceptions first. Every figure is
    derived from the event log at query time.
    """
    on_date = on_date or timezone.localdate()
    from .models import Employee

    people = team if team is not None else Employee.objects.filter(is_active=True)
    if supervisor_employee is not None and team is None:
        people = people.filter(supervisor=supervisor_employee)
    people = people.select_related("user")

    days = {
        day.employee_id: day
        for day in AttendanceDay.objects.filter(employee__in=people, date=on_date)
    }

    # Exceptions sort to the top: absent, then still clocked in, then late,
    # then everyone who simply turned up.
    priority = {
        AttendanceDay.ABSENT: 0,
        AttendanceDay.LATE: 2,
        AttendanceDay.PRESENT: 3,
    }
    rows = []
    for person in people:
        day = days.get(person.pk)
        state = day.state if day else AttendanceDay.ABSENT
        order = 1 if (day and day.still_clocked_in) else priority.get(state, 3)
        rows.append({"employee": person, "day": day, "state": state, "order": order})

    rows.sort(key=lambda row: (row["order"], row["employee"].staff_id))
    return rows


def monthly_summary(year, month, team=None):
    """
    The per-employee month. These columns are the contract a Phase Two
    payroll engine consumes unchanged — see docs/attendance-columns.md.
    """
    from calendar import monthrange

    from .models import Employee

    first = timezone.datetime(year, month, 1).date()
    last = timezone.datetime(year, month, monthrange(year, month)[1]).date()

    people = team if team is not None else Employee.objects.filter(is_active=True)
    people = people.select_related("user")

    days = AttendanceDay.objects.filter(employee__in=people, date__gte=first, date__lte=last)
    by_employee = defaultdict(list)
    for day in days:
        by_employee[day.employee_id].append(day)

    rows = []
    for person in people:
        person_days = by_employee.get(person.pk, [])
        present = [d for d in person_days if d.state != AttendanceDay.ABSENT]
        rows.append(
            {
                "employee": person,
                "days_present": len(present),
                "days_absent": sum(1 for d in person_days if d.state == AttendanceDay.ABSENT),
                "late_arrivals": sum(1 for d in person_days if d.state == AttendanceDay.LATE),
                "total_minutes": sum(d.total_minutes for d in person_days),
                "corrections": sum(d.correction_count for d in person_days),
            }
        )
    rows.sort(key=lambda row: row["employee"].staff_id)
    return {"first": first, "last": last, "rows": rows}
