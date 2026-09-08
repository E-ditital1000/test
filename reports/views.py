"""
Reports — a fixed set, not a report builder.

One view renders any report in the registry, which is what keeps the set
fixed: a user cannot compose a query, only choose from reports someone
decided to ship. Each report carries its own permission, checked here in
addition to the module gate, so reaching the module never implies reaching
every report in it.

Every figure is computed at query time from source rows, and the totals are
summed from the same rows the table shows — so a report cannot disagree with
itself.
"""
import csv
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from config.pagination import paginate
from accounts.decorators import require_permission, user_has_permission

from .reports import REPORTS, available_to, compute_totals


def _period(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        if not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month
    return year, month


@require_permission("view_reports")
def index(request, key=None):
    reachable = available_to(request.user)
    if not reachable:
        # Holds view_reports but no individual report. Say so rather than
        # showing an empty chrome that looks broken.
        return render(request, "reports/index.html", {"reports": [], "report": None})

    report = REPORTS.get(key) or reachable[0]
    if not user_has_permission(request.user, report.permission):
        raise PermissionDenied(f"missing permission: {report.permission}")

    year, month = _period(request)
    rows = report.build(request.user, year, month)
    totals = compute_totals(report, rows)

    if request.GET.get("export") == "csv":
        if not user_has_permission(request.user, "export_reports"):
            raise PermissionDenied("missing permission: export_reports")
        return _csv(report, rows, year, month)

    today = timezone.localdate()
    return render(
        request,
        "reports/index.html",
        {
            "reports": reachable,
            "report": report,
            # The table is paged; the totals are not. They are summed from
            # every row the report produced, so page 2 does not change them.
            "rows": paginate(request, rows),
            "totals": totals,
            "year": year,
            "month": month,
            "months": [(i, date(2000, i, 1).strftime("%B")) for i in range(1, 13)],
            "years": list(range(today.year - 2, today.year + 1)),
            "can_export": user_has_permission(request.user, "export_reports"),
        },
    )


def _csv(report, rows, year, month):
    """
    The export is the same rows the screen shows, in the same order. It is
    not a second query, so the two can never disagree.
    """
    response = HttpResponse(content_type="text/csv")
    suffix = f"-{year}-{month:02d}" if report.period else ""
    response["Content-Disposition"] = f'attachment; filename="{report.key}{suffix}.csv"'

    writer = csv.writer(response)
    writer.writerow([column.label for column in report.columns])
    for row in rows:
        writer.writerow([_flat(row.get(column.key, "")) for column in report.columns])
    return response


def _flat(value):
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="minutes") if hasattr(value, "hour") else value.isoformat()
    return value
