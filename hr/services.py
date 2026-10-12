"""
The derivation layer for attendance. AttendanceDay is a cache, never a
source: this module is the single routine that rebuilds it from the
append-only event log plus corrections, and `hr.tests` proves that wiping
the table and rebuilding reconstructs it identically.
"""
import logging
import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from config.geo import metres_between
from config.models import PolicySetting

from .models import AttendanceCode, AttendanceCorrection, AttendanceDay, AttendanceEvent

logger = logging.getLogger("a1360.attendance")


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


def approved_leave_days(employee=None, since=None, until=None, team=None, cap_at_today=True):
    """
    {(employee_id, date): LeaveType} for every day approved leave covers in
    this slice.

    **The single answer to "was this person on approved leave that day".**
    Three callers share it, deliberately: the rebuild below writes it into
    AttendanceDay so the export and payroll see it, and the roll-call and the
    monthly report read it directly. They have to read it directly as well as
    through the table, because somebody on leave generates no clock event, so
    nothing triggers a rebuild for their day — and a derived table that is
    only refreshed by activity cannot describe an absence.

    `cap_at_today` is what keeps the stored table honest: AttendanceDay
    describes days that have happened, and writing rows for leave granted for
    next March would put months of fictional history in the table every
    report reads. The screens pass False when they are showing a window that
    runs into the future.
    """
    from .leave import only_approved, working_days
    from .models import LeaveRequest

    today = timezone.localdate()
    first = timezone.localtime(since).date() if hasattr(since, "tzinfo") and since is not None else since
    last = timezone.localtime(until).date() if hasattr(until, "tzinfo") and until is not None else until
    if cap_at_today:
        last = min(last, today) if last is not None else today

    requests = LeaveRequest.objects.select_related("leave_type", "employee")
    if employee is not None:
        requests = requests.filter(employee=employee)
    if team is not None:
        requests = requests.filter(employee__in=team)
    if last is not None:
        requests = requests.filter(start_date__lte=last)
    if first is not None:
        requests = requests.filter(end_date__gte=first)

    covered = {}
    # Approval is read from the trail, not from a column, so this cannot
    # drift from what the leave screens show — in one query, not one per
    # request, because the roll-call calls this on every page load.
    for request in only_approved(requests):
        window_start = request.start_date if first is None else max(request.start_date, first)
        window_end = request.end_date if last is None else min(request.end_date, last)
        for day in working_days(window_start, window_end):
            covered[(request.employee_id, day)] = request.leave_type
    return covered


@transaction.atomic
def rebuild_attendance_days(employee=None, since=None, until=None):
    """
    Recompute the derived rows for the given slice and return how many were
    written. Safe to run at any time: it is a pure function of the event log
    and of approved leave, so running it twice produces the same table.

    A day with clock events is summarised from them. A day with no events
    that approved leave covers becomes an `on_leave` row, so it stops
    reading as an absence. A day with both keeps what the events say and
    carries `on_leave` as well — somebody recalled from leave worked, and
    the record says so.
    """
    workday_start, grace_minutes = _late_policy()

    grouped = defaultdict(list)
    for row in effective_events(employee=employee, since=since, until=until):
        grouped[(row[0], row[1])].append(row)

    on_leave = approved_leave_days(employee=employee, since=since, until=until)

    scope = AttendanceDay.objects.all()
    if employee is not None:
        scope = scope.filter(employee=employee)
    if since is not None:
        scope = scope.filter(date__gte=timezone.localtime(since).date())
    if until is not None:
        scope = scope.filter(date__lte=timezone.localtime(until).date())
    scope.delete()

    days = []
    for (employee_id, date), rows in grouped.items():
        leave_type = on_leave.get((employee_id, date))
        days.append(
            AttendanceDay(
                employee_id=employee_id,
                date=date,
                on_leave=leave_type is not None,
                leave_type=leave_type,
                **_summarise(rows, workday_start, grace_minutes),
            )
        )

    # Leave days nobody clocked on. These have no events at all, so they
    # would otherwise have no row — which is exactly how an approved absence
    # used to read as an unexplained one.
    for (employee_id, date), leave_type in on_leave.items():
        if (employee_id, date) in grouped:
            continue
        days.append(
            AttendanceDay(
                employee_id=employee_id,
                date=date,
                state=AttendanceDay.ON_LEAVE,
                on_leave=True,
                leave_type=leave_type,
                first_in=None,
                last_out=None,
                total_minutes=0,
                still_clocked_in=False,
                correction_count=0,
            )
        )

    AttendanceDay.objects.bulk_create(days)
    return len(days)


# --------------------------------------------------------------------------
# Writing events
# --------------------------------------------------------------------------

class ClockError(Exception):
    """A clock event the log will not accept, with a message for the phone."""

# --------------------------------------------------------------------------
# Being where you say you are
# --------------------------------------------------------------------------

def office_location():
    """
    (latitude, longitude, radius_m) for the place people clock, or None when
    nobody has set one.

    None means the boundary has not been drawn, and nothing is enforced. A
    half-configured geofence that refuses people is worse than none, so an
    unset or unparsable value fails open and the screens say it is not set.
    """
    latitude = PolicySetting.get_value(PolicySetting.OFFICE_LATITUDE)
    longitude = PolicySetting.get_value(PolicySetting.OFFICE_LONGITUDE)
    if not latitude or not longitude:
        return None
    try:
        latitude, longitude = float(latitude), float(longitude)
        radius = int(float(PolicySetting.get_value(PolicySetting.GEOFENCE_RADIUS_M, "200")))
    except (TypeError, ValueError):
        logger.warning("the office location is not a usable coordinate")
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or radius <= 0:
        logger.warning("the office location is out of range: %s, %s", latitude, longitude)
        return None
    return latitude, longitude, radius


def judge_location(*, latitude, longitude, accuracy_m, has_code):
    """
    Decide whether a clock event was taken at the place.

    Returns (verdict, distance_m). Raising is the caller's job, so the rule
    can be read in one piece:

    **No office set** — nothing to measure against, so nothing is judged.

    **No fix** — allowed if they scanned the code, refused otherwise. That
    asymmetry is the whole design: without it, denying the browser location
    permission is simply how you clock in from home, and the check is
    theatre. With a code, possession stands in for position.

    **A fix** — distance from the office. Inside the radius is clean.
    Outside the radius but inside the radius plus the phone's own stated
    accuracy is recorded and flagged, because a cheap handset in a concrete
    yard is honestly wrong by that much and refusing it strands somebody who
    is standing right there. Beyond both, the phone is confident and it is
    confidently somewhere else.

    Scanning alone does not exempt a fix from being judged. A printed code
    is a shared secret — AttendanceCode says so itself — and a photograph of
    it works from a sofa. The code proves possession; the fix is what
    proves presence.
    """
    from .models import AttendanceEvent

    place = office_location()
    if place is None:
        return AttendanceEvent.NOT_CHECKED, None

    if latitude is None or longitude is None:
        if has_code:
            return AttendanceEvent.NOT_CHECKED, None
        raise ClockError(
            "Your phone did not give a location, so the clock cannot tell "
            "where you are. Turn location on and try again, or scan the "
            "attendance code posted at your location."
        )

    office_lat, office_lon, radius = place
    distance = metres_between(office_lat, office_lon, latitude, longitude)
    if distance is None:
        return AttendanceEvent.NOT_CHECKED, None

    if distance <= radius:
        return AttendanceEvent.AT_THE_PLACE, distance

    # The phone's own error is given the benefit of the doubt before anybody
    # is turned away. A fix claiming 150m accuracy cannot refute being 200m
    # out of a 100m circle.
    margin = int(accuracy_m or 0)
    if distance <= radius + margin:
        return AttendanceEvent.NEAR_ENOUGH, distance

    raise ClockError(
        f"You appear to be about {distance} m from the office, which is too "
        f"far to clock in or out. If you are actually here, your phone has "
        f"the wrong position — ask your supervisor to record it for you."
    )


def open_clock_in(employee, on_date=None):
    """
    The employee's unmatched clock-in, if they are currently on the clock.
    Read from the event log rather than a status field, so it stays true
    even after a correction rewrites history.

    `on_date` limits the question to one local day, which is the rule A-1
    set: a day closes with the day. Both directions are bounded by it.

    Starting a shift asks only about today, so a shift somebody forgot to
    close on Friday does not stop them working on Monday. That was the bug —
    this scanned every event ever recorded, and one forgotten clock-out
    locked the employee out of the clock permanently. Four people on the
    live system had been unable to clock in for three days.

    Closing one asks only about today as well, so nobody can clock out
    against a day the system never saw them leave. The honest consequence is
    that such a day carries a start, no end, and no hours; a supervisor
    correction is what settles it, because that carries an actor and a
    reason where a late clock-out would carry neither.

    The cost, stated rather than hidden: a shift that runs past midnight
    cannot be clocked out of the next morning. On a six-day week that ends
    in the evening this is the right trade, and it is A-1's to revisit if
    night work ever becomes normal.
    """
    rows = [row for row in effective_events(employee=employee) if row[0] == employee.pk]
    open_in = None
    for _emp, local_date, kind, stamp, _corrected in rows:
        if on_date is not None and local_date != on_date:
            continue
        if kind == AttendanceEvent.CLOCK_IN:
            open_in = stamp
        else:
            open_in = None
    return open_in


def stale_clock_in(employee, on_date=None):
    """
    An unclosed shift from a day before this one, if there is one.

    Surfaced rather than silently ignored: the employee is not blocked by
    it, but somebody has a day on record with no end to it, and only a
    supervisor correction can settle what hours it was.
    """
    on_date = on_date or timezone.localdate()
    open_any = open_clock_in(employee)
    if open_any is None:
        return None
    if timezone.localtime(open_any).date() >= on_date:
        return None
    return open_any


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

    # A day closes with the day. An employee who went home without clocking
    # out cannot come back and clock out against it later: the system never
    # saw them leave, so any end it accepted would be a time somebody chose
    # rather than a time anybody worked, and the hours it produced would be
    # fiction presented as a record. A-1's rule, and the right one.
    #
    # The day keeps its clock-in and shows no end and no duration. Settling
    # what was actually worked is a supervisor correction, which carries an
    # actor and a reason — unlike a late clock-out, which would carry
    # neither.
    if kind == AttendanceEvent.CLOCK_OUT:
        today = timezone.localtime(when).date()
        if open_clock_in(employee, on_date=today) is None:
            if stale_clock_in(employee, on_date=today) is not None:
                raise ClockError(
                    "You did not clock out that day, and it has already been "
                    "closed. The system cannot say what hours you worked, so "
                    "it records none. Ask your supervisor to correct that day."
                )
            raise ClockError(
                "You are not clocked in, so there is nothing to clock out of. "
                "If you forgot to clock in, ask your supervisor for a correction."
            )
    # Starting one looks only at today. A shift somebody forgot to close on
    # Friday is a record to correct, not a reason to keep them off the clock
    # on Monday — which is exactly what it used to be.
    if kind == AttendanceEvent.CLOCK_IN:
        today = timezone.localtime(when).date()
        if open_clock_in(employee, on_date=today) is not None:
            raise ClockError(
                "You are already clocked in today. Clock out first, or ask "
                "your supervisor for a correction."
            )

    # Where they were, decided before anything is written. A supervisor
    # recording on somebody's behalf is exempt: they are standing in an
    # office entering a correction for a person who was on a site, and
    # judging the supervisor's phone would refuse the very thing the
    # exemption exists for.
    if recorded_by is None:
        verdict, distance = judge_location(
            latitude=latitude,
            longitude=longitude,
            accuracy_m=accuracy_m,
            has_code=attendance_code is not None,
        )
    else:
        verdict, distance = AttendanceEvent.NOT_CHECKED, None

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
        distance_m=distance,
        location_check=verdict,
    )
    rebuild_attendance_days(
        employee=employee,
        since=event.device_timestamp - timedelta(days=1),
        until=event.device_timestamp + timedelta(days=1),
    )
    _notify_supervisor_of_clock(event)
    return event, True


def _notify_supervisor_of_clock(event):
    """
    Tell the supervisor whose roll-call this person appears on, and the
    administrators.

    An employee with no supervisor still reaches the administrators, so a
    clock event is never recorded with nobody told. The person clocking is
    never written to about their own event — they were standing there.

    On volume: thirteen people clocking twice a day is twenty-six messages,
    and every administrator receives all of them. That is a decision, not an
    accident. If it becomes a mailbox nobody reads, the shape that survives
    is a roll-call digest once a day rather than an alert each time — a
    notification nobody reads is worse than none, because it teaches people
    to ignore the ones that matter.
    """
    from accounts.templatetags.a1 import a1datetime, a1metres
    from accounts.services import admin_user_ids
    from config import notifications
    from django.contrib.auth import get_user_model

    addresses = []
    supervisor = event.employee.supervisor
    if supervisor is not None and supervisor.user.email:
        addresses.append(supervisor.user.email)

    administrators = (
        get_user_model()
        .objects.filter(pk__in=admin_user_ids(), is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    addresses.extend(administrators)

    # Nobody hears about their own clock, and nobody hears twice.
    own = event.employee.user.email
    addresses = [a for a in dict.fromkeys(addresses) if a and a != own]
    if not addresses:
        return

    notifications.send(
        to=addresses,
        subject=f"{event.employee.full_name} clocked {event.kind}",
        template="clock_event",
        context={
            "employee": event.employee,
            "employee_name": event.employee.full_name,
            "direction": event.kind,
            "at": a1datetime(event.device_timestamp),
            "location": (
                "not available" if event.location_unavailable
                else a1metres(event.accuracy_m)
            ),
            "code": event.attendance_code.location_name if event.attendance_code_id else "",
            "url": notifications.link("hr-employee-attendance", event.employee.pk),
        },
    )


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
        for day in AttendanceDay.objects.filter(
            employee__in=people, date=on_date
        ).select_related("leave_type")
    }
    # Read directly rather than trusting a row to exist. Somebody on leave
    # files no clock event, so nothing triggers a rebuild for their day.
    #
    # Uncapped, because this screen takes a date: a supervisor looking at
    # next Tuesday has to see who is already away on it. The cap exists to
    # keep *stored* rows free of fictional history, which is not this.
    on_leave = approved_leave_days(
        team=people, since=on_date, until=on_date, cap_at_today=False
    )

    # Exceptions sort to the top: an unexplained absence first, then somebody
    # who never clocked out, then somebody who worked a day they were granted
    # off, then lateness. People who simply turned up, and people who are
    # away with permission, are not exceptions and sort last.
    priority = {
        AttendanceDay.ABSENT: 0,
        AttendanceDay.LATE: 3,
        AttendanceDay.PRESENT: 4,
        AttendanceDay.ON_LEAVE: 5,
    }
    # Where each person's clock events for the day were judged to be. One
    # query for the whole roll-call; the verdict lives on the event, and the
    # derived day row deliberately does not carry it.
    from .models import AttendanceEvent

    flags = {}
    for employee_id, check in (
        AttendanceEvent.objects.filter(
            employee__in=people, device_timestamp__date=on_date
        )
        .exclude(location_check=AttendanceEvent.NOT_CHECKED)
        .values_list("employee_id", "location_check")
    ):
        # A flagged event outranks a clean one: the exception is the thing
        # worth seeing.
        if check == AttendanceEvent.NEAR_ENOUGH or employee_id not in flags:
            flags[employee_id] = check

    rows = []
    for person in people:
        day = days.get(person.pk)
        leave_type = on_leave.get((person.pk, on_date))
        state = day.state if day else AttendanceDay.ABSENT

        worked = state in AttendanceDay.WORKED
        # Granted the day off and no sign of them: expected, not an exception.
        if leave_type is not None and not worked:
            state = AttendanceDay.ON_LEAVE
        worked_while_on_leave = leave_type is not None and worked

        if day and day.still_clocked_in:
            order = 1
        elif worked_while_on_leave:
            order = 2
        else:
            order = priority.get(state, 4)

        rows.append(
            {
                "employee": person,
                "day": day,
                "state": state,
                "order": order,
                "leave_type": leave_type,
                "worked_while_on_leave": worked_while_on_leave,
                "location_check": flags.get(person.pk),
            }
        )

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

    days = AttendanceDay.objects.filter(
        employee__in=people, date__gte=first, date__lte=last
    ).select_related("leave_type")
    by_employee = defaultdict(list)
    for day in days:
        by_employee[day.employee_id].append(day)

    # The same single answer the roll-call and the rebuild use. Read here
    # too rather than trusting rows to exist: a month somebody spent on
    # leave produced no clock events, so nothing refreshed their rows.
    on_leave = approved_leave_days(team=people, since=first, until=last)

    rows = []
    for person in people:
        person_days = by_employee.get(person.pk, [])
        # Days somebody worked, named explicitly. This used to be "not
        # absent", which was right with three states and would silently have
        # counted paid leave as attendance the moment there were four.
        worked_dates = {d.date for d in person_days if d.state in AttendanceDay.WORKED}
        leave_dates = {
            day: leave_type
            for (employee_id, day), leave_type in on_leave.items()
            if employee_id == person.pk
        }
        # Worked a day they were granted off: paid as worked, and surfaced as
        # an exception rather than quietly counted as both.
        both = worked_dates & set(leave_dates)
        away = {day: lt for day, lt in leave_dates.items() if day not in worked_dates}

        rows.append(
            {
                "employee": person,
                "days_present": len(worked_dates),
                # An absence explained by approved leave is not an absence.
                "days_absent": sum(
                    1
                    for d in person_days
                    if d.state == AttendanceDay.ABSENT and d.date not in leave_dates
                ),
                "late_arrivals": sum(1 for d in person_days if d.state == AttendanceDay.LATE),
                # Split, because payroll pays one and not the other. A day
                # whose type has somehow gone missing counts as paid — the
                # error that costs the employee nothing.
                "days_on_leave_paid": sum(
                    1 for lt in away.values() if lt is None or lt.is_paid
                ),
                "days_on_leave_unpaid": sum(
                    1 for lt in away.values() if lt is not None and not lt.is_paid
                ),
                "worked_while_on_leave": len(both),
                "total_minutes": sum(d.total_minutes for d in person_days),
                "corrections": sum(d.correction_count for d in person_days),
            }
        )
    rows.sort(key=lambda row: row["employee"].staff_id)
    return {"first": first, "last": last, "rows": rows}


# --------------------------------------------------------------------------
# Taking somebody on
# --------------------------------------------------------------------------

@transaction.atomic
def onboard_employee(*, actor, data, roles):
    """
    Create the person, their sign-in and their access in one transaction.

    Either all three exist afterwards or none of them do. Half-done onboarding
    is the failure this replaces: an account with no employee record cannot
    clock in, and an employee record with no account cannot sign in, and
    whoever was interrupted has no way to tell which they are looking at.

    Returns (employee, temporary_password). The password is shown once and
    never stored in readable form -- the forced reset replaces it at first
    sign-in, so nobody but the new starter ever knows the lasting one.
    """
    from django.contrib.auth import get_user_model
    from accounts import audit
    from accounts.models import UserRole
    from accounts.permissions import forget_permissions
    from accounts.services import (
        can_grant_role, notify_account_created, temporary_password,
    )

    from .models import Employee

    User = get_user_model()

    # Re-checked here, not just narrowed in the form: the queryset is a
    # convenience for the person filling it in, never the control.
    for role in roles:
        if not can_grant_role(actor, role):
            raise PermissionDenied(
                f"You cannot assign '{role.name}' because it holds permissions "
                "you do not hold yourself."
            )

    email = data["email"].strip().lower()
    temporary = temporary_password()

    user = User(
        username=email,
        email=email,
        first_name=data["first_name"].strip(),
        middle_name=data.get("middle_name", "").strip(),
        last_name=data["last_name"].strip(),
        must_reset_password=True,
    )
    user.set_password(temporary)
    user.save()

    employee = Employee.objects.create(
        user=user,
        staff_id=data["staff_id"].strip(),
        job_title=data.get("job_title", "").strip(),
        department=data.get("department") or None,
        phone=data.get("phone", "").strip(),
        supervisor=data.get("supervisor"),
        start_date=data.get("start_date"),
    )

    for role in roles:
        UserRole.objects.get_or_create(user=user, role=role)
    forget_permissions(user)

    audit.record_change(
        actor=actor,
        action="employee.onboarded",
        target=employee,
        before=None,
        after={
            "staff_id": employee.staff_id,
            "email": user.email,
            "roles": sorted(role.name for role in roles),
        },
        reason=data.get("reason", "") or "Onboarded through the HR register",
    )
    notify_account_created(
        user=user, actor=actor, roles=roles, staff_id=employee.staff_id
    )
    return employee, temporary
