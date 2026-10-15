"""
The leave screens.

Four surfaces, and which one a person meets is decided by what they hold.

An employee asks and watches their own. HR decides, and sees the queue, the
calendar and the balances. A supervisor sees their own crew's leave read-only
— enough to schedule around, without holding a pen on days whose balance
they cannot see. Everyone else sees none of it.

Every list here reads its approval state through `approvals.latest_decisions`
rather than asking each row in turn: a queue of forty requests was one query
per row the first time this was written, which is the mistake that pattern
exists to prevent.
"""
from datetime import timedelta

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts import audit
from accounts.decorators import require_permission, user_has_permission
from accounts.scoping import apply_scope
from approvals.models import Approval, latest_decisions
from config.pagination import paginate

from . import leave
from .leave_forms import (
    LeaveAdjustmentForm,
    LeaveEntitlementRuleForm,
    LeaveRequestForm,
    LeaveTypeForm,
    PublicHolidayForm,
)
from .models import (
    Employee,
    LeaveAdjustment,
    LeaveEntitlementRule,
    LeaveRequest,
    LeaveType,
    PublicHoliday,
)


def _own_employee(user):
    return Employee.objects.filter(user=user).select_related("user").first()


def _visible_requests(user, code="view_leave"):
    """
    The leave this user may see, with scope applied here so no screen in
    this module can forget it. A supervisor holding `view_leave` at own-team
    scope sees their own crew and nobody else.
    """
    return apply_scope(
        LeaveRequest.objects.select_related(
            "employee__user", "employee__department", "leave_type", "submitted_by"
        ),
        user,
        code,
        own_team_filter=Q(employee__supervisor__user=user),
    )


def _with_states(requests):
    """
    Attach each request's current decision in one query rather than one per
    row, and hand back a list ready to render.
    """
    rows = list(requests)
    decisions = latest_decisions(LeaveRequest, [row.pk for row in rows])
    for row in rows:
        row.current_decision = decisions.get(row.pk)
        row.current_state = row.current_decision.decision if row.current_decision else None
    return rows


# --------------------------------------------------------------------------
# The employee's own surface
# --------------------------------------------------------------------------

@require_permission("request_leave")
def my_leave(request):
    """
    Somebody's own leave: what they have left, what they have asked for, and
    what happened to it.

    Shown to them because a balance nobody can see is a balance nobody
    trusts — the same reason the clock screen shows an employee their own
    attendance.
    """
    employee = _own_employee(request.user)
    context = {
        "employee": employee,
        "tab_active": "my_leave",
        "tracked_from": leave.tracked_from(),
        "year": leave.current_leave_year(),
    }

    if employee is not None:
        context["balances"] = leave.balances_for(employee)
        context["requests"] = _with_states(
            LeaveRequest.objects.filter(employee=employee).select_related("leave_type")
        )
    return render(request, "hr/leave_mine.html", context)


@require_permission("request_leave")
def leave_request_create(request):
    """
    Putting in for leave.

    One form, two doors: an employee asking for themselves, and HR raising
    one on somebody's behalf for a person who cannot reach a screen. The
    second needs `manage_leave`, and the request is still the employee's —
    they are told the outcome, not the person who typed it.
    """
    on_behalf = user_has_permission(request.user, "manage_leave")
    own = _own_employee(request.user)

    if not on_behalf and own is None:
        # An account with no register row has nothing to book leave against.
        # Said on the screen rather than bounced to another one: being sent
        # somewhere else without an explanation is how people conclude a
        # feature is broken.
        return render(request, "hr/leave_form.html", {"no_register_row": True})

    form = LeaveRequestForm(
        request.POST or None,
        request.FILES or None,
        for_employee=None if on_behalf else own,
    )

    if request.method == "POST" and form.is_valid():
        try:
            leave_request = leave.submit_request(
                employee=form.employee_in_question,
                leave_type=form.cleaned_data["leave_type"],
                start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"],
                reason=form.cleaned_data["reason"],
                document=form.cleaned_data.get("document"),
                handover_note=form.cleaned_data.get("handover_note", ""),
                contact_while_away=form.cleaned_data.get("contact_while_away", ""),
                actor=request.user,
            )
        except leave.LeaveError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                f"Leave requested for {leave_request.working_days} working "
                f"day{'' if leave_request.working_days == 1 else 's'}. "
                "It is with HR now, and shows on their queue. An email has "
                "been queued to them as well.",
            )
            if on_behalf and leave_request.employee != own:
                return redirect("hr-leave-detail", pk=leave_request.pk)
            return redirect("hr-leave-mine")

    return render(
        request,
        "hr/leave_form.html",
        {
            "form": form,
            "on_behalf": on_behalf,
            "balances": leave.balances_for(own) if own is not None else [],
            "holidays": PublicHoliday.objects.filter(
                date__gte=timezone.localdate(),
                date__lte=timezone.localdate() + timedelta(days=120),
            ),
        },
    )


@require_permission("request_leave")
def leave_cancel(request, pk):
    """
    Withdrawing a request. Somebody may withdraw their own at any point
    before it is decided; cancelling leave that has already been approved is
    HR's, because the days have to go back and the employee has to be told.
    """
    leave_request = get_object_or_404(
        LeaveRequest.objects.select_related("employee__user", "leave_type"), pk=pk
    )
    is_own = leave_request.employee.user_id == request.user.pk
    may_manage = user_has_permission(request.user, "manage_leave")

    if not (is_own or may_manage):
        messages.error(request, "That is not your request to withdraw.")
        return redirect("hr-leave-mine")
    if is_own and not may_manage and leave_request.is_approved:
        messages.error(
            request,
            "That leave has already been approved. Ask HR to cancel it, so "
            "the days go back on your balance and the schedule is updated.",
        )
        return redirect("hr-leave-mine")

    if request.method != "POST":
        # Withdrawing changes something, so it is never a GET. `hr-leave-mine`
        # takes no arguments, which is why these are two returns rather than
        # one clever line.
        if is_own:
            return redirect("hr-leave-mine")
        return redirect("hr-leave-detail", pk=pk)

    try:
        leave.cancel_request(
            request=leave_request,
            actor=request.user,
            comment=(request.POST.get("comment") or "").strip(),
        )
    except leave.LeaveError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Request withdrawn.")

    return redirect("hr-leave-mine" if is_own else "hr-leave-queue")


# --------------------------------------------------------------------------
# HR's surfaces
# --------------------------------------------------------------------------

@require_permission("view_leave")
def leave_queue(request):
    """
    Leave waiting on a decision, oldest first — the request that has been
    blocked longest is always at the top, and the one that starts soonest
    after that, because a decision on Monday's leave cannot wait.
    """
    rows = _with_states(_visible_requests(request.user))

    waiting = [row for row in rows if row.current_state in ("submitted", "returned")]
    settled = [row for row in rows if row.current_state not in ("submitted", "returned")]

    # Soonest start first among those waiting: a request for next week
    # matters more than one for next quarter, whoever asked first.
    waiting.sort(key=lambda row: (row.start_date, row.created_at))

    today = timezone.localdate()
    return render(
        request,
        "hr/leave_queue.html",
        {
            "waiting": paginate(request, waiting),
            # A glance, not a list to work through, so it stays capped and
            # the screen says so.
            "settled": settled[:20],
            "waiting_count": len(waiting),
            "away_today": leave.who_is_away(today),
            "can_decide": user_has_permission(request.user, "approve_leave"),
            "can_manage": user_has_permission(request.user, "manage_leave"),
            "tab_active": "leave_queue",
        },
    )


@require_permission("view_leave")
def leave_detail(request, pk):
    """
    One request, everything needed to decide it on one screen: the dates,
    the working days they consume, the reason, the handover, and the
    balance it would come out of.
    """
    leave_request = get_object_or_404(
        _visible_requests(request.user).prefetch_related("employee__leave_requests"),
        pk=pk,
    )
    employee = leave_request.employee

    return render(
        request,
        "hr/leave_detail.html",
        {
            "request_row": leave_request,
            "employee": employee,
            "balance": leave.balance_for(
                employee, leave_request.leave_type, leave_request.start_date.year
            ),
            "days": leave.working_days(
                leave_request.start_date, leave_request.end_date
            ),
            "trail": leave_request.approval_trail.select_related("actor"),
            "state": leave_request.approval_state,
            # Who else from the same department is already away then, so a
            # decision is not taken blind to the department being short. Only
            # when a department is actually recorded: comparing two nulls
            # would group everybody unfiled together and call it a clash.
            "clashes": [
                row
                for row in leave.on_leave_between(
                    leave_request.start_date, leave_request.end_date
                ).values()
                if row.pk != leave_request.pk
                and employee.department_id is not None
                and row.employee.department_id == employee.department_id
            ],
            "can_decide": user_has_permission(request.user, "approve_leave"),
            "can_manage": user_has_permission(request.user, "manage_leave"),
            "tab_active": "leave_queue",
        },
    )


@require_permission("approve_leave")
def leave_decide(request, pk):
    """
    The decision. Declining without a reason is refused by the approval
    model rather than by this view, so a second door cannot skip it.
    """
    leave_request = get_object_or_404(_visible_requests(request.user, "approve_leave"), pk=pk)
    if request.method != "POST":
        return redirect("hr-leave-detail", pk=leave_request.pk)

    decision = request.POST.get("decision")
    comment = (request.POST.get("comment") or "").strip()

    try:
        leave.decide_request(
            request=leave_request, decision=decision, actor=request.user, comment=comment
        )
    except (leave.LeaveError, ValueError) as error:
        messages.error(request, str(error))
        return redirect("hr-leave-detail", pk=leave_request.pk)

    name = leave_request.employee.full_name
    if decision == Approval.APPROVED:
        messages.success(
            request,
            f"Approved. The days are off {name}'s balance and it shows on "
            "their own leave screen. An email has been queued to them too.",
        )
    elif decision == Approval.DECLINED:
        messages.success(
            request,
            f"Declined. {name} sees your reason on their own leave screen, "
            "and an email has been queued to them.",
        )
    else:
        messages.success(request, f"Sent back to {name} with your note.")
    return redirect("hr-leave-queue")


@require_permission("view_leave")
def leave_calendar(request):
    """
    Who is away, by month. The screen that stops two people from the same
    crew being off the same week, and the one a supervisor opens before
    booking work.
    """
    today = timezone.localdate()
    anchor = parse_date(request.GET.get("month") or "") or today.replace(day=1)
    days = leave.days_in_month(anchor.year, anchor.month)

    visible = _visible_requests(request.user).filter(
        start_date__lte=days[-1], end_date__gte=days[0]
    )
    approved = [row for row in visible if row.is_approved]

    rest = leave.rest_days()
    holidays = leave.holidays_between(days[0], days[-1])

    # One row per person who is away at all this month, so the grid is as
    # short as the month's actual absence rather than the whole register.
    people = {}
    for row in approved:
        people.setdefault(row.employee_id, {"employee": row.employee, "requests": []})
        people[row.employee_id]["requests"].append(row)

    rows = []
    for entry in sorted(people.values(), key=lambda item: item["employee"].staff_id):
        cells = []
        for day in days:
            covering = next(
                (req for req in entry["requests"] if req.covers(day)), None
            )
            cells.append(
                {
                    "day": day,
                    "request": covering,
                    "is_rest": day.weekday() in rest or day in holidays,
                    "is_today": day == today,
                }
            )
        rows.append({"employee": entry["employee"], "cells": cells})

    previous = (days[0] - timedelta(days=1)).replace(day=1)
    following = days[-1] + timedelta(days=1)

    return render(
        request,
        "hr/leave_calendar.html",
        {
            "days": days,
            "rows": rows,
            "anchor": anchor,
            "previous_month": previous,
            "next_month": following,
            "holidays": PublicHoliday.objects.filter(
                date__gte=days[0], date__lte=days[-1]
            ),
            "away_today": leave.who_is_away(today) if days[0] <= today <= days[-1] else {},
            "tab_active": "leave_calendar",
        },
    )


@require_permission("view_leave_reports")
def leave_balances(request):
    """
    Entitlement, taken and remaining for everybody, for one leave year.

    The column that matters most is the one listing who has no start date on
    their record: tenure-based entitlement cannot be worked out without it,
    and this is where that gap is visible rather than silently rendered as
    zero.
    """
    year = leave.current_leave_year()
    try:
        year = int(request.GET.get("year") or year)
    except (TypeError, ValueError):
        pass

    team = apply_scope(
        Employee.objects.filter(is_active=True).select_related("user", "department"),
        request.user,
        "view_leave_reports",
        own_team_filter=Q(supervisor__user=request.user),
    ).order_by("staff_id")

    types = list(
        LeaveType.objects.filter(is_active=True).prefetch_related("entitlement_rules")
    )
    # Paged first, then costed. Computing balances for the whole register and
    # throwing away all but one page is the same mistake as the N+1 it would
    # otherwise hide inside.
    page = paginate(request, list(team))
    balances = leave.balances_for_many(page.object_list, year=year, leave_types=types)

    # Counted across the whole register, not this page: "three people have no
    # start date" is a data task for HR, and a figure that changed as you
    # paged would be worse than none.
    missing_start_date = sum(1 for employee in team if employee.start_date is None)

    rows = []
    for employee in page.object_list:
        rows.append(
            {
                "employee": employee,
                "months": leave.tenure_months(employee),
                "balances": balances.get(employee.pk, []),
            }
        )
    page.object_list = rows

    return render(
        request,
        "hr/leave_balances.html",
        {
            "rows": page,
            "types": types,
            "year": year,
            "years": range(leave.current_leave_year(), leave.current_leave_year() - 4, -1),
            "tracked_from": leave.tracked_from(),
            "missing_start_date": missing_start_date,
            "can_manage": user_has_permission(request.user, "manage_leave"),
            "tab_active": "leave_balances",
        },
    )


@require_permission("manage_leave")
def leave_adjustment_create(request):
    """
    Stating a balance by hand, with a reason.

    This is how go-live works. A-1's leave records before the system are not
    reconstructable, so rather than invent approved requests for history
    nobody can vouch for, HR records the correction and it stays visible as
    a correction.
    """
    employee = None
    if request.GET.get("employee"):
        employee = Employee.objects.filter(pk=request.GET["employee"]).first()

    form = LeaveAdjustmentForm(request.POST or None, employee=employee)
    if request.method == "POST" and form.is_valid():
        adjustment = form.save(commit=False)
        adjustment.recorded_by = request.user
        adjustment.save()
        audit.record_change(
            actor=request.user,
            action="leave.adjusted",
            target=adjustment,
            before=None,
            after={
                "employee": str(adjustment.employee),
                "leave_type": adjustment.leave_type.label,
                "leave_year": adjustment.leave_year,
                "days": adjustment.days,
                "reason": adjustment.reason,
            },
        )
        messages.success(
            request,
            f"{adjustment.days:+d} day{'' if abs(adjustment.days) == 1 else 's'} "
            f"recorded against {adjustment.employee.full_name}'s "
            f"{adjustment.leave_type.label} for {adjustment.leave_year}.",
        )
        return redirect("hr-leave-balances")

    return render(
        request,
        "hr/leave_adjustment_form.html",
        {
            "form": form,
            "recent": LeaveAdjustment.objects.select_related(
                "employee__user", "leave_type", "recorded_by"
            )[:10],
        },
    )


# --------------------------------------------------------------------------
# Settings: the lists the rest of it reads from
# --------------------------------------------------------------------------

@require_permission("manage_leave_types")
def leave_settings(request):
    """
    The three lists leave is computed from, on one screen: the kinds of
    leave, what each is worth at each length of service, and the public
    holidays no request consumes.
    """
    today = timezone.localdate()
    return render(
        request,
        "hr/leave_settings.html",
        {
            "types": LeaveType.objects.prefetch_related("entitlement_rules"),
            "rules": LeaveEntitlementRule.objects.select_related("leave_type"),
            "holidays": PublicHoliday.objects.filter(date__year__gte=today.year - 1),
            "rest_days": sorted(leave.rest_days()),
            "rest_day_names": [
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][day]
                for day in sorted(leave.rest_days())
            ],
            "tracked_from": leave.tracked_from(),
            "this_year": today.year,
            "tab_active": "leave_settings",
        },
    )


def _settings_edit(request, *, model, form_class, template, redirect_to, action, pk=None, fields):
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        before = audit.snapshot_stored(instance, fields=fields)
        saved = form.save()
        audit.record_change(
            actor=request.user,
            action=action,
            target=saved,
            before=before,
            after=audit.snapshot(saved, fields=fields),
        )
        messages.success(request, f"{saved} saved.")
        return redirect(redirect_to)
    return render(request, template, {"form": form, "instance": instance})


@require_permission("manage_leave_types")
def leave_type_edit(request, pk=None):
    return _settings_edit(
        request,
        model=LeaveType,
        form_class=LeaveTypeForm,
        template="hr/leave_type_form.html",
        redirect_to="hr-leave-settings",
        action="leave_type.saved",
        pk=pk,
        fields=["label", "code", "is_paid", "document_required_after_days", "is_active"],
    )


@require_permission("manage_leave_types")
def leave_rule_edit(request, pk=None):
    return _settings_edit(
        request,
        model=LeaveEntitlementRule,
        form_class=LeaveEntitlementRuleForm,
        template="hr/leave_rule_form.html",
        redirect_to="hr-leave-settings",
        action="leave_rule.saved",
        pk=pk,
        fields=["after_months", "days"],
    )


@require_permission("manage_leave_types")
def leave_holiday_edit(request, pk=None):
    return _settings_edit(
        request,
        model=PublicHoliday,
        form_class=PublicHolidayForm,
        template="hr/leave_holiday_form.html",
        redirect_to="hr-leave-settings",
        action="public_holiday.saved",
        pk=pk,
        fields=["date", "name"],
    )
