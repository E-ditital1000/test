"""
HR — attendance tracking only.

Every figure on every screen here is derived from the append-only event log
by `hr.services`. Nothing writes to AttendanceDay directly, and no screen
edits a clock event: a correction is a new row that supersedes, carrying
actor, timestamp, old value, new value and a reason from the fixed list.
"""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts import audit
from accounts.decorators import require_permission, user_has_permission
from accounts.scoping import apply_scope

from . import services
from .forms import AttendanceCorrectionForm, EmployeeForm
from .models import AttendanceDay, AttendanceEvent, Employee


# --------------------------------------------------------------------------
# The employee register
# --------------------------------------------------------------------------

@require_permission("view_employees")
def employees(request):
    query = request.GET.get("q", "").strip()
    show_inactive = request.GET.get("inactive") == "1"

    register = Employee.objects.select_related("user", "supervisor__user")
    if not show_inactive:
        register = register.filter(is_active=True)
    if query:
        register = register.filter(
            Q(staff_id__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(department__icontains=query)
        )

    # A supervisor granted "view_employees" at own-team scope sees their
    # own team only; the filter is applied here, not left to the template.
    register = apply_scope(
        register,
        request.user,
        "view_employees",
        own_team_filter=Q(supervisor__user=request.user),
    )

    return render(
        request,
        "hr/employees.html",
        {
            "employees": register,
            "query": query,
            "show_inactive": show_inactive,
            "total_employees": Employee.objects.count(),
        },
    )


@require_permission("manage_employees")
def employee_edit(request, pk=None):
    instance = get_object_or_404(Employee, pk=pk) if pk else None
    form = EmployeeForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        tracked = ["staff_id", "job_title", "department", "phone", "is_active"]
        before = audit.snapshot(instance, fields=tracked)
        employee = form.save()
        audit.record_change(
            actor=request.user,
            action="employee.created" if instance is None else "employee.updated",
            target=employee,
            before=before,
            after=audit.snapshot(employee, fields=tracked),
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, "Employee record saved.")
        return redirect("hr-employees")
    return render(request, "hr/employee_form.html", {"form": form, "instance": instance})


@require_permission("manage_employees")
def employee_deactivate(request, pk):
    """Deactivation preserves all history — the record is never removed."""
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        before = audit.snapshot(employee, fields=["is_active"])
        employee.deactivate(actor=request.user)
        audit.record_change(
            actor=request.user,
            action="employee.deactivated",
            target=employee,
            before=before,
            after=audit.snapshot(employee, fields=["is_active"]),
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, "{} deactivated. Attendance history is retained.".format(employee))
        return redirect("hr-employees")
    return render(
        request,
        "accounts/confirm.html",
        {
            "title": "Deactivate {}?".format(employee),
            "body": "Their attendance history stays in the system and in reports.",
            "action_label": "Deactivate",
            "cancel_url": reverse("hr-employees"),
        },
    )


# --------------------------------------------------------------------------
# The employee's own clock
# --------------------------------------------------------------------------

@require_permission("clock_in_out")
def clock(request):
    """
    One primary action cycling clock in → on the clock → clock out, plus the
    employee's own last seven days including any correction a supervisor
    made. Seeing their own record is what makes attendance read as pay
    protection rather than surveillance.
    """
    employee = Employee.objects.filter(user=request.user).select_related("user").first()

    # A scan arrives as ?code=<token> from the QR on the wall. Resolve it so
    # the screen can confirm which location it is for before anybody taps.
    scanned = services.resolve_code(request.GET.get("code"))
    context = {
        "employee": employee,
        "tab_active": "clock",
        "scanned_code": scanned,
        "scanned_status": scanned.status if scanned else None,
        "scanned_raw": (request.GET.get("code") or "").strip(),
    }

    if employee is not None:
        open_in = services.open_clock_in(employee)
        context.update(
            {
                "open_since": open_in,
                "elapsed_minutes": (
                    int((timezone.now() - open_in).total_seconds() // 60) if open_in else 0
                ),
                "recent_days": list(
                    AttendanceDay.objects.filter(employee=employee).order_by("-date")[:7]
                ),
                "today": AttendanceDay.objects.filter(
                    employee=employee, date=timezone.localdate()
                ).first(),
            }
        )
    return render(request, "hr/clock.html", context)


@require_permission("clock_in_out")
def clock_event(request):
    """
    Accepts a clock event from the phone.

    The payload carries a client-generated UUID, so a retry after a dropped
    connection resolves to the row that already exists rather than creating a
    second one. Answers JSON for the offline queue and redirects for a plain
    form post, so the screen still works with JavaScript switched off.
    """
    employee = Employee.objects.filter(user=request.user).first()
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method != "POST" or employee is None:
        return redirect("hr-clock")

    def answer(ok, message, status=200):
        if wants_json:
            return JsonResponse({"ok": ok, "message": message}, status=status)
        (messages.success if ok else messages.error)(request, message)
        return redirect("hr-clock")

    client_uuid = request.POST.get("client_uuid", "").strip()
    if not client_uuid:
        return answer(False, "This event has no client reference and cannot be synced.", 400)

    def decimal_or_none(name):
        raw = (request.POST.get(name) or "").strip()
        try:
            return Decimal(raw) if raw else None
        except (InvalidOperation, ValueError):
            return None

    device_timestamp = parse_datetime(request.POST.get("device_timestamp") or "")
    if device_timestamp and timezone.is_naive(device_timestamp):
        device_timestamp = timezone.make_aware(device_timestamp)

    accuracy_raw = (request.POST.get("accuracy_m") or "").strip()
    try:
        accuracy = float(accuracy_raw) if accuracy_raw else None
    except ValueError:
        accuracy = None

    # The QR encodes the token; the wall also carries a short code to type
    # when a camera will not start. Either resolves to the same record.
    scanned = (request.POST.get("code") or "").strip()
    attendance_code = services.resolve_code(scanned)
    if scanned and attendance_code is None:
        return answer(
            False,
            "That code was not recognised. Check the short code printed under "
            "the QR, or ask HR for the current one.",
            409,
        )

    try:
        event, created = services.record_clock_event(
            employee=employee,
            kind=request.POST.get("kind"),
            client_uuid=client_uuid,
            device_timestamp=device_timestamp,
            latitude=decimal_or_none("latitude"),
            longitude=decimal_or_none("longitude"),
            accuracy_m=accuracy,
            location_unavailable=request.POST.get("location_unavailable") == "1",
            attendance_code=attendance_code,
        )
    except services.ClockError as error:
        # An actionable error, not a stack trace: a clock-out with no open
        # clock-in tells the technician what to do about it.
        return answer(False, str(error), 409)
    except (ValueError, ValidationError) as error:
        return answer(False, str(error), 400)

    if not created:
        return answer(True, "Already recorded — nothing was duplicated.")

    verb = "Clocked in" if event.kind == AttendanceEvent.CLOCK_IN else "Clocked out"
    where = "location unavailable" if event.location_unavailable else "location captured"
    return answer(
        True,
        f"{verb} at {timezone.localtime(event.device_timestamp):%H:%M} · {where}.",
    )


# --------------------------------------------------------------------------
# Supervisor roll-call and corrections
# --------------------------------------------------------------------------

def _team_for(user, code):
    """The employees this user may see under `code`, honouring its scope."""
    return apply_scope(
        Employee.objects.filter(is_active=True).select_related("user"),
        user,
        code,
        own_team_filter=Q(supervisor__user=user),
    )


@require_permission("view_attendance_records")
def roll_call(request):
    """Today's team, exceptions sorted to the top."""
    on_date = parse_date(request.GET.get("date") or "") or timezone.localdate()
    rows = services.roll_call(on_date=on_date, team=_team_for(request.user, "view_attendance_records"))

    counts = {"present": 0, "late": 0, "absent": 0, "still_in": 0}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
        if row["day"] and row["day"].still_clocked_in:
            counts["still_in"] += 1

    return render(
        request,
        "hr/roll_call.html",
        {
            "rows": rows,
            "on_date": on_date,
            "counts": counts,
        },
    )


@require_permission("view_attendance_records")
def employee_attendance(request, pk):
    """
    One person's event log, with every correction visible against it.

    Gated on viewing, not correcting. It used to require correct_attendance,
    which meant an Executive -- who holds view_attendance_records over
    everyone and correct_attendance over nobody -- could see the roll-call
    summary and never open a single person behind it. Reading a record and
    changing one are different rights; the correction controls below are
    still the second.
    """
    employee = get_object_or_404(_team_for(request.user, "view_attendance_records"), pk=pk)
    events = (
        AttendanceEvent.objects.filter(employee=employee)
        .prefetch_related("corrections__reason", "corrections__corrected_by")
        .order_by("-device_timestamp")[:60]
    )
    return render(
        request,
        "hr/employee_attendance.html",
        {
            "employee": employee,
            "events": events,
            "days": AttendanceDay.objects.filter(employee=employee).order_by("-date")[:14],
            "can_correct": user_has_permission(request.user, "correct_attendance"),
        },
    )


@require_permission("correct_attendance")
def correction_create(request, pk):
    """
    Writes a new row and never touches the event it corrects, so the
    original value stays visible with who changed it and when.
    """
    event = get_object_or_404(
        AttendanceEvent.objects.select_related("employee__user"), pk=pk
    )
    get_object_or_404(_team_for(request.user, "correct_attendance"), pk=event.employee_id)

    form = AttendanceCorrectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.record_correction(
                event=event,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                action=form.cleaned_data["action"],
                corrected_timestamp=form.cleaned_data.get("corrected_timestamp"),
                note=form.cleaned_data.get("note", ""),
            )
        except services.ClockError as error:
            messages.error(request, str(error))
        else:
            audit.record_change(
                actor=request.user,
                action="attendance.corrected",
                target=event,
                before={"device_timestamp": str(event.device_timestamp)},
                after={"corrected_timestamp": str(form.cleaned_data.get("corrected_timestamp"))},
                reason=str(form.cleaned_data["reason"]),
            )
            messages.success(request, "Correction recorded. The original event is unchanged.")
            return redirect("hr-employee-attendance", pk=event.employee_id)

    return render(
        request,
        "hr/correction_form.html",
        {"form": form, "event": event, "employee": event.employee},
    )


# --------------------------------------------------------------------------
# The monthly report
# --------------------------------------------------------------------------

@require_permission("view_attendance_reports")
def monthly_report(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        if not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month

    summary = services.monthly_summary(
        year, month, team=_team_for(request.user, "view_attendance_reports")
    )
    totals = {
        key: sum(row[key] for row in summary["rows"])
        for key in ("days_present", "days_absent", "late_arrivals", "total_minutes", "corrections")
    }

    if request.GET.get("export") == "csv":
        return _monthly_csv(summary, year, month)

    return render(
        request,
        "hr/monthly_report.html",
        {
            "summary": summary,
            "totals": totals,
            "year": year,
            "month": month,
            "months": [(i, date(2000, i, 1).strftime("%B")) for i in range(1, 13)],
            "years": list(range(today.year - 2, today.year + 1)),
        },
    )


def _monthly_csv(summary, year, month):
    """
    The column contract a Phase Two payroll engine consumes unchanged.
    Changing a header here is a breaking change for that consumer, so the
    headers are written out literally rather than generated.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        'attachment; filename="attendance-{}-{:02d}.csv"'.format(year, month)
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "staff_id",
            "surname",
            "first_name",
            "department",
            "days_present",
            "days_absent",
            "late_arrivals",
            "total_hours",
            "corrections",
        ]
    )
    for row in summary["rows"]:
        employee = row["employee"]
        writer.writerow(
            [
                employee.staff_id,
                employee.user.last_name,
                employee.user.first_name,
                employee.department,
                row["days_present"],
                row["days_absent"],
                row["late_arrivals"],
                round(row["total_minutes"] / 60, 2),
                row["corrections"],
            ]
        )
    return response
