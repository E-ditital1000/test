"""
The derivation layer for leave.

Two rules shape everything here, and both are inherited from the attendance
module next door rather than invented.

A balance is never stored. It is entitlement from the rules table, plus the
adjustments HR has recorded, minus the days approved requests consumed —
computed at the moment somebody asks. A balance column would be the field
that silently drifts and that nobody can reconstruct afterwards.

A request's state is never stored either. It is the latest append-only
decision on it from `approvals.Approval`, carrying actor, timestamp and
comment, which is what makes a declined request defensible when it is
disputed months later.

The one derived figure that *is* stored is a request's own `working_days`.
The public holiday list can change after a request is settled, and the
number HR approved is the number that was agreed; recomputing it later
would restate a decision nobody revisited.

Policy decisions behind the figures — the six-day week, the statutory
bands, HR as the only approver — are recorded in
docs/leave-management-plan.md.
"""
import logging
from calendar import monthrange
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from approvals.models import Approval, latest_decisions
from config import notifications
from config.models import PolicySetting

from .models import LeaveAdjustment, LeaveRequest, LeaveType, PublicHoliday

logger = logging.getLogger("a1360.leave")

# Sunday, as Python counts weekdays. A-1 works Monday to Saturday, so this
# is the fallback when nobody has said otherwise in Settings.
DEFAULT_REST_DAYS = frozenset({6})


class LeaveError(Exception):
    """Refused for a reason the person is told, not a crash."""


def with_states(requests):
    """
    [(request, decision_string_or_None)] for many requests, in one query.

    `LeaveRequest.is_approved` reads the approval trail, which costs a query
    per request — right when you are looking at one, wrong when you are
    filtering ninety. Every bulk path in this module goes through here, for
    the same reason `approvals.latest_decisions` exists: a leave calendar
    and three assignment pickers were otherwise one round trip per request
    on every page that renders them.
    """
    rows = list(requests)
    if not rows:
        return []
    decisions = latest_decisions(LeaveRequest, [row.pk for row in rows])
    return [
        (row, decisions[row.pk].decision if row.pk in decisions else None)
        for row in rows
    ]


def only_approved(requests):
    """The approved ones, without a query per row."""
    return [row for row, state in with_states(requests) if state == Approval.APPROVED]


def only_live(requests):
    """
    Approved or still waiting. A declined or withdrawn request is not a
    clash and not a commitment.
    """
    return [
        row
        for row, state in with_states(requests)
        if state in (Approval.APPROVED, Approval.SUBMITTED, Approval.RETURNED)
    ]


# --------------------------------------------------------------------------
# The working calendar
# --------------------------------------------------------------------------

def rest_days():
    """
    Which weekdays nobody is expected in, as Python weekday numbers.

    An unparsable setting falls back to Sunday rather than raising: a typo
    in Settings should not take the leave screens down, and a six-day week
    is A-1's actual pattern.
    """
    raw = PolicySetting.get_value(PolicySetting.LEAVE_REST_DAYS)
    if not raw:
        return DEFAULT_REST_DAYS
    try:
        days = {int(part) for part in str(raw).split(",") if part.strip() != ""}
    except (TypeError, ValueError):
        logger.warning("leave_rest_days is not a list of weekday numbers: %r", raw)
        return DEFAULT_REST_DAYS
    days = {day for day in days if 0 <= day <= 6}
    # Every day a rest day would make every request zero days long, which is
    # a configuration mistake and not a policy anybody means.
    if not days or len(days) == 7:
        return DEFAULT_REST_DAYS
    return frozenset(days)


def holidays_between(start, end):
    """The public holidays falling in this span, as a set of dates."""
    return set(
        PublicHoliday.objects.filter(date__gte=start, date__lte=end).values_list(
            "date", flat=True
        )
    )


def working_days(start, end):
    """
    Every day in this span that somebody is expected at work: rest days and
    public holidays dropped.

    Returns a list of dates, so a caller can show which days a request
    actually consumes rather than only how many.
    """
    if end < start:
        return []
    off = rest_days()
    public = holidays_between(start, end)
    span = (end - start).days + 1
    return [
        day
        for day in (start + timedelta(days=offset) for offset in range(span))
        if day.weekday() not in off and day not in public
    ]


def count_working_days(start, end):
    return len(working_days(start, end))


def tracked_from():
    """
    The date leave started being recorded here, or None.

    A-1's records before go-live were not reconstructable, so the balance
    screens say what they cover instead of implying a full year.
    """
    raw = PolicySetting.get_value(PolicySetting.LEAVE_TRACKED_FROM)
    if not raw:
        return None
    from django.utils.dateparse import parse_date

    try:
        return parse_date(str(raw))
    except (TypeError, ValueError):
        logger.warning("leave_tracked_from is not a date: %r", raw)
        return None


# --------------------------------------------------------------------------
# Entitlement and balance
# --------------------------------------------------------------------------

def current_leave_year():
    return timezone.localdate().year


def tenure_months(employee, on=None):
    """
    Completed months of continuous service, or None when the register does
    not say when they started.

    None is not zero, and the screens show it as "needs a start date". A
    missing date would otherwise quietly hand somebody the first band of an
    entitlement they may have outgrown by years.
    """
    if employee.start_date is None:
        return None
    on = on or timezone.localdate()
    if on < employee.start_date:
        return 0
    months = (on.year - employee.start_date.year) * 12 + (
        on.month - employee.start_date.month
    )
    if on.day < employee.start_date.day:
        months -= 1
    return max(months, 0)


def _reference_date(year):
    """
    The date tenure is judged at for a leave year: today inside the current
    year, the year end for one already finished. A band crossed in March
    raises that year's allowance from March, which is how service-based
    entitlement is meant to work.
    """
    today = timezone.localdate()
    year_end = date(year, 12, 31)
    return min(today, year_end)


def entitlement_for(employee, leave_type, year=None):
    """
    Days this employee is entitled to of this type in this leave year.

    None means the figure cannot be stated — either the type carries no
    allowance (unpaid leave has none to run down) or the register has no
    start date to measure service from. The two are told apart by
    `leave_type.is_tracked`.
    """
    year = year or current_leave_year()
    if not leave_type.is_tracked:
        return None
    months = tenure_months(employee, _reference_date(year))
    if months is None:
        return None
    return leave_type.entitlement_after(months)


def _year_bounds(year):
    return date(year, 1, 1), date(year, 12, 31)


def approved_requests(employee, leave_type=None, year=None):
    """
    This employee's approved leave overlapping the given year.

    Approval is read from the trail, not from a column, so this is the one
    place that knows how to ask — every balance figure in the system comes
    through here.
    """
    requests = LeaveRequest.objects.filter(employee=employee).select_related(
        "leave_type"
    )
    if leave_type is not None:
        requests = requests.filter(leave_type=leave_type)
    if year is not None:
        first, last = _year_bounds(year)
        requests = requests.filter(start_date__lte=last, end_date__gte=first)
    return only_approved(requests)


def days_taken(employee, leave_type, year=None):
    """
    Working days of this type consumed in this leave year.

    A request sitting wholly inside the year contributes the figure that was
    agreed when it was decided. One straddling New Year is counted again for
    the part inside this year, because no agreed figure exists for a
    fraction of it.
    """
    year = year or current_leave_year()
    first, last = _year_bounds(year)
    total = 0
    for request in approved_requests(employee, leave_type, year):
        if request.start_date >= first and request.end_date <= last:
            total += request.working_days
        else:
            total += count_working_days(
                max(request.start_date, first), min(request.end_date, last)
            )
    return total


def adjustment_total(employee, leave_type, year=None):
    """Days HR has added or taken away by hand, with a reason, for this year."""
    year = year or current_leave_year()
    return sum(
        LeaveAdjustment.objects.filter(
            employee=employee, leave_type=leave_type, leave_year=year
        ).values_list("days", flat=True)
    )


def balance_for(employee, leave_type, year=None):
    """
    The whole picture for one employee and one leave type, computed now.

    `remaining` is None when `entitlement` is — an untracked type and a
    missing start date both mean the figure cannot honestly be stated, and a
    screen showing 0 in either case would be telling somebody they have no
    leave left when nobody has worked out how much they have.
    """
    year = year or current_leave_year()
    entitlement = entitlement_for(employee, leave_type, year)
    taken = days_taken(employee, leave_type, year)
    adjusted = adjustment_total(employee, leave_type, year)
    booked = pending_days(employee, leave_type, year)

    remaining = None
    if entitlement is not None:
        remaining = entitlement + adjusted - taken

    return {
        "leave_type": leave_type,
        "year": year,
        "entitlement": entitlement,
        "adjustment": adjusted,
        "taken": taken,
        # Waiting on a decision. Shown separately rather than deducted: it is
        # not spent until somebody approves it, and HR deciding needs to see
        # both numbers.
        "pending": booked,
        "remaining": remaining,
        "needs_start_date": leave_type.is_tracked and employee.start_date is None,
        "is_tracked": leave_type.is_tracked,
    }


def pending_days(employee, leave_type, year=None):
    """Working days asked for and not yet answered, inside this leave year."""
    year = year or current_leave_year()
    first, last = _year_bounds(year)
    requests = LeaveRequest.objects.filter(
        employee=employee,
        leave_type=leave_type,
        start_date__lte=last,
        end_date__gte=first,
    )
    return sum(
        request.working_days
        for request, state in with_states(requests)
        if state in (Approval.SUBMITTED, Approval.RETURNED)
    )


def balances_for(employee, year=None):
    """Every leave type for one employee, in Settings order."""
    year = year or current_leave_year()
    return [
        balance_for(employee, leave_type, year)
        for leave_type in LeaveType.objects.filter(is_active=True).prefetch_related(
            "entitlement_rules"
        )
    ]


def balances_for_many(employees, year=None, leave_types=None):
    """
    {employee_id: [balance, ...]} for a whole page of the register.

    `balance_for` costs a handful of queries, which is right for one person
    and ruinous for a screen: twenty-five employees across six leave types
    is six hundred round trips, and this deployment reaches its database
    over a public proxy where every one of those carries real latency. The
    register screen was unusable before this existed.

    So the inputs are fetched once — requests, their decisions, adjustments,
    the types and their bands — and the arithmetic happens in Python. The
    figures are identical to `balance_for`; a test asserts that rather than
    trusting it.
    """
    year = year or current_leave_year()
    employees = list(employees)
    if leave_types is None:
        leave_types = list(
            LeaveType.objects.filter(is_active=True).prefetch_related(
                "entitlement_rules"
            )
        )
    if not employees:
        return {}

    first, last = _year_bounds(year)
    reference = _reference_date(year)

    # Every request touching the year, for everybody on the page, once.
    requests = LeaveRequest.objects.filter(
        employee__in=employees, start_date__lte=last, end_date__gte=first
    ).select_related("leave_type")

    taken = {}
    pending = {}
    for request, state in with_states(requests):
        key = (request.employee_id, request.leave_type_id)
        if state == Approval.APPROVED:
            if request.start_date >= first and request.end_date <= last:
                days = request.working_days
            else:
                days = count_working_days(
                    max(request.start_date, first), min(request.end_date, last)
                )
            taken[key] = taken.get(key, 0) + days
        elif state in (Approval.SUBMITTED, Approval.RETURNED):
            pending[key] = pending.get(key, 0) + request.working_days

    adjustments = {}
    for row in LeaveAdjustment.objects.filter(
        employee__in=employees, leave_year=year
    ).values_list("employee_id", "leave_type_id", "days"):
        key = (row[0], row[1])
        adjustments[key] = adjustments.get(key, 0) + row[2]

    out = {}
    for employee in employees:
        months = tenure_months(employee, reference)
        rows = []
        for leave_type in leave_types:
            key = (employee.pk, leave_type.pk)
            tracked = bool(list(leave_type.entitlement_rules.all()))
            entitlement = None
            if tracked and months is not None:
                entitlement = leave_type.entitlement_after(months)
            adjusted = adjustments.get(key, 0)
            used = taken.get(key, 0)
            rows.append(
                {
                    "leave_type": leave_type,
                    "year": year,
                    "entitlement": entitlement,
                    "adjustment": adjusted,
                    "taken": used,
                    "pending": pending.get(key, 0),
                    "remaining": None
                    if entitlement is None
                    else entitlement + adjusted - used,
                    "needs_start_date": tracked and employee.start_date is None,
                    "is_tracked": tracked,
                }
            )
        out[employee.pk] = rows
    return out


# --------------------------------------------------------------------------
# Who is away
# --------------------------------------------------------------------------

def on_leave_between(start, end):
    """
    Everybody with approved leave touching this span, as
    {user_id: LeaveRequest}.

    This is what the schedulers consult. It returns the request and not just
    a flag, so a picker can say *when* somebody is away rather than only
    that they are — "on leave 12-16 Oct" is actionable and "unavailable" is
    not.
    """
    requests = (
        LeaveRequest.objects.filter(start_date__lte=end, end_date__gte=start)
        .select_related("employee__user", "leave_type")
        .order_by("start_date")
    )
    away = {}
    for request in only_approved(requests):
        away.setdefault(request.employee.user_id, request)
    return away


def who_is_away(on_date=None):
    """Approved leave covering one day, for the roll-call and the calendar."""
    on_date = on_date or timezone.localdate()
    return on_leave_between(on_date, on_date)


def overlapping_requests(employee, start, end, exclude=None):
    """
    This employee's other live requests touching these dates.

    Live means approved or still waiting: a declined or cancelled request is
    not a clash. Two overlapping requests are almost always a resubmission
    somebody forgot about, and the form says so rather than silently
    double-counting the days.
    """
    requests = LeaveRequest.objects.filter(
        employee=employee, start_date__lte=end, end_date__gte=start
    ).select_related("leave_type")
    if exclude is not None:
        requests = requests.exclude(pk=exclude.pk)
    return only_live(requests)


def days_in_month(year, month):
    """Every day of a month, for the calendar grid."""
    last = monthrange(year, month)[1]
    return [date(year, month, day) for day in range(1, last + 1)]


# How far ahead a scheduling screen looks when marking who is away. Long
# enough to cover the window work is actually booked in, short enough that
# somebody's leave next June is not clutter on today's job form.
LOOKAHEAD_DAYS = 90


def away_labels(start=None, end=None):
    """
    {user_id: "on leave 12-16 Oct"} for the people a scheduler should see are
    away.

    Built for the assignment pickers, which **mark rather than filter**. The
    field job form already establishes that pattern for project crew — a
    supervisor sending an extra body to a site is ordinary, and a form that
    refuses it gets worked around by not using the form. The same reasoning
    holds harder here: a technician can be recalled from leave, and a
    scheduler who cannot see the person they need will schedule them outside
    the system entirely. So the picker says when they are away and lets the
    scheduler decide.
    """
    from accounts.templatetags.a1 import a1daterange

    start = start or timezone.localdate()
    end = end or (start + timedelta(days=LOOKAHEAD_DAYS))
    return {
        user_id: "on leave {}".format(
            a1daterange(request.start_date, request.end_date)
        )
        for user_id, request in on_leave_between(start, end).items()
    }


def conflict_for(user, start, end=None):
    """
    The approved leave that covers these dates for this person, or None.

    Used at save time to warn. It never refuses: see `away_labels` for why
    a refusal here would be worked around rather than obeyed.
    """
    if user is None or start is None:
        return None
    user_id = getattr(user, "pk", user)
    return on_leave_between(start, end or start).get(user_id)


def mark_away(label, user_id, labels):
    """Append the away note to a picker label, if there is one."""
    note = labels.get(user_id)
    return f"{label} - {note}" if note else label


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

@transaction.atomic
def submit_request(
    *,
    employee,
    leave_type,
    start_date,
    end_date,
    reason,
    actor,
    document=None,
    handover_note="",
    contact_while_away="",
):
    """
    Put in for leave. Raises LeaveError with something the person can act on.

    The working-day count is taken here, once, and stored — see this
    module's docstring for why it is not recomputed later.
    """
    if end_date < start_date:
        raise LeaveError("The last day cannot be before the first day.")

    counted = count_working_days(start_date, end_date)
    if counted == 0:
        raise LeaveError(
            "Those dates are all rest days or public holidays, so there is "
            "nothing to ask for."
        )

    request = LeaveRequest.objects.create(
        employee=employee,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        working_days=counted,
        reason=reason,
        document=document,
        handover_note=handover_note,
        contact_while_away=contact_while_away,
        submitted_by=actor,
    )
    request.record_decision(decision=Approval.SUBMITTED, actor=actor)
    notify_of_request(request)
    return request


@transaction.atomic
def decide_request(*, request, decision, actor, comment=""):
    """
    Approve or decline. Declining without a reason is refused by the
    approval model, not by the template, so no second door can skip it.
    """
    if decision not in (Approval.APPROVED, Approval.DECLINED, Approval.RETURNED):
        raise LeaveError("That is not a decision this screen can record.")
    if request.is_settled:
        raise LeaveError(
            "That request has already been settled. Reload the page to see "
            "where it got to."
        )

    request.record_decision(decision=decision, actor=actor, comment=comment)
    _refresh_attendance(request)
    notify_of_decision(request, decision=decision, actor=actor, comment=comment)
    return request


@transaction.atomic
def cancel_request(*, request, actor, comment=""):
    """
    Withdraw a request. The employee can withdraw their own before it is
    decided; HR can cancel one they have already approved, because plans
    change and the days should go back.
    """
    if request.is_cancelled:
        raise LeaveError("That request has already been withdrawn.")
    if request.is_declined:
        raise LeaveError("That request was declined, so there is nothing to withdraw.")

    request.record_decision(decision=Approval.CANCELLED, actor=actor, comment=comment)
    _refresh_attendance(request)
    notify_of_cancellation(request, actor=actor, comment=comment)
    return request


def _refresh_attendance(request):
    """
    Rebuild the derived attendance rows this decision changes.

    Approving leave in the past turns absences into accounted-for days, and
    cancelling it turns them back. The roll-call and the monthly report read
    approved leave directly as well, so this is not what makes them correct —
    it is what keeps the stored table, and therefore the payroll export, in
    step with the decision just taken.
    """
    from datetime import datetime, time

    from django.utils import timezone as django_timezone

    from . import services

    today = django_timezone.localdate()
    if request.start_date > today:
        # Nothing has happened yet, so there are no rows to restate.
        return

    def as_moment(day):
        return django_timezone.make_aware(datetime.combine(day, time.min))

    services.rebuild_attendance_days(
        employee=request.employee,
        since=as_moment(request.start_date - timedelta(days=1)),
        until=as_moment(min(request.end_date, today) + timedelta(days=1)),
    )


# --------------------------------------------------------------------------
# Telling people
# --------------------------------------------------------------------------

def _employee_address(request):
    address = request.employee.user.email
    return [address] if address else []

def _describe(request):
    from accounts.templatetags.a1 import a1daterange

    return {
        "request": request,
        "employee_name": request.employee.full_name,
        "leave_type": request.leave_type.label,
        "dates": a1daterange(request.start_date, request.end_date),
        "days": request.working_days,
        "reason": request.reason,
    }


def notify_of_request(request):
    """
    Whoever may decide it hears, and nobody else.

    Read from the permission rather than a role name, so moving leave
    approval to a different desk in Settings moves the mail with it. The
    person who typed the request is excluded: HR raising one on somebody's
    behalf does not need telling about it.
    """
    addresses = notifications.recipients_holding(
        "approve_leave", exclude=request.submitted_by
    )
    if not addresses:
        logger.warning(
            "Leave request %s has nobody holding approve_leave to tell.", request.pk
        )
        return False
    return notifications.send(
        to=addresses,
        subject=f"Leave request from {request.employee.full_name}",
        template="leave_requested",
        context={
            **_describe(request),
            "balance": balance_for(request.employee, request.leave_type),
            "url": notifications.link("hr-leave-detail", request.pk),
        },
    )


def notify_of_decision(request, *, decision, actor, comment=""):
    """
    The employee hears the answer, with the reason attached when it is no.

    Only them. Leave is between a person and HR, and copying the office on
    somebody's sick leave is not a notification, it is a disclosure.
    """
    addresses = _employee_address(request)
    if not addresses:
        return False

    wording = {
        Approval.APPROVED: "approved",
        Approval.DECLINED: "declined",
        Approval.RETURNED: "sent back for more detail",
    }[decision]
    return notifications.send(
        to=addresses,
        subject=f"Your leave request was {wording}",
        template="leave_decided",
        context={
            **_describe(request),
            "outcome": wording,
            "was_approved": decision == Approval.APPROVED,
            "comment": comment,
            "decided_by": actor.get_full_name() or actor.email,
            "url": notifications.link("hr-leave-mine"),
        },
    )


def notify_of_cancellation(request, *, actor, comment=""):
    """
    Whoever did not do the cancelling hears about it.

    An employee withdrawing their own request tells HR, so nobody decides
    something that has been taken back. HR cancelling approved leave tells
    the employee, because somebody who thinks they are off next week needs
    to know they are not.
    """
    by_the_employee = actor == request.employee.user
    if by_the_employee:
        to = notifications.recipients_holding("approve_leave", exclude=actor)
        subject = f"Leave request withdrawn by {request.employee.full_name}"
    else:
        to = _employee_address(request)
        subject = "Your approved leave has been cancelled"

    if not to:
        return False
    return notifications.send(
        to=to,
        subject=subject,
        template="leave_cancelled",
        context={
            **_describe(request),
            "by_the_employee": by_the_employee,
            "cancelled_by": actor.get_full_name() or actor.email,
            "comment": comment,
            "url": notifications.link(
                "hr-leave-mine" if not by_the_employee else "hr-leave-detail",
                *([] if not by_the_employee else [request.pk]),
            ),
        },
    )
