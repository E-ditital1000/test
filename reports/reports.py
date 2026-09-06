"""
The fixed report set.

A registry, not a report builder. Each entry names its own permission, its
columns and the query that produces its rows — so adding a report is a
deliberate act with an owner, and no user can compose one that reaches data
their role does not.

Every figure is computed at query time from source rows. Nothing here reads
a stored aggregate, and every number on a report can be traced back to the
rows underneath it: that equality is the release gate, not an aspiration.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from django.db.models import Q
from django.utils import timezone


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    numeric: bool = False
    money: bool = False


@dataclass(frozen=True)
class Report:
    key: str
    label: str
    description: str
    permission: str
    columns: list
    build: Callable
    # A report over a month reads differently from one over "right now".
    period: bool = True
    totals: tuple = field(default=())


def _month_bounds(year, month):
    from calendar import monthrange

    start = timezone.datetime(year, month, 1).date()
    end = timezone.datetime(year, month, monthrange(year, month)[1]).date()
    return start, end


# --------------------------------------------------------------------------
# Jobs completed
# --------------------------------------------------------------------------

def jobs_completed(user, year, month):
    from fieldjobs.models import FieldJob

    start, end = _month_bounds(year, month)
    jobs = (
        FieldJob.objects.filter(
            state=FieldJob.COMPLETED,
            completed_at__date__gte=start,
            completed_at__date__lte=end,
        )
        .select_related("customer", "assigned_to", "ticket", "service_type")
        .order_by("-completed_at")
    )

    rows = []
    for job in jobs:
        # Days open is measured from the ticket that started the job, not
        # from when somebody got round to scheduling it.
        origin = job.ticket.created_at if job.ticket else job.created_at
        rows.append(
            {
                "reference": job.reference,
                "customer": job.customer.name,
                "service_type": job.service_type.name,
                "technician": job.assigned_to.get_full_name() or job.assigned_to.email,
                "completed": job.completed_at,
                "days_open": (job.completed_at - origin).days,
            }
        )
    return rows


# --------------------------------------------------------------------------
# Ticket ageing
# --------------------------------------------------------------------------

def ticket_ageing(user, year, month):
    from crm.models import Ticket

    rows = []
    for ticket in (
        Ticket.objects.open()
        .select_related("customer", "assigned_to", "status", "service_type")
        .order_by("created_at")
    ):
        rows.append(
            {
                "reference": ticket.reference,
                "customer": ticket.customer.name,
                "service_type": ticket.service_type.name,
                "assigned": (
                    ticket.assigned_to.get_full_name() or ticket.assigned_to.email
                    if ticket.assigned_to
                    else "Unassigned"
                ),
                "status": ticket.status.label,
                "age_days": ticket.age_days,
            }
        )
    return rows


# --------------------------------------------------------------------------
# Project profitability
# --------------------------------------------------------------------------

def project_profitability(user, year, month):
    from projects.models import Project
    from projects.views import project_cost

    rows = []
    for project in (
        Project.objects.select_related("customer", "manager")
        .prefetch_related("expenses", "requisitions", "invoices__lines", "invoices__payments")
        .order_by("-created_at")
    ):
        cost = project_cost(project)
        rows.append(
            {
                "reference": project.reference,
                "customer": project.customer.name,
                "stage": project.get_stage_display(),
                "revenue": cost["revenue"],
                "cost": cost["cost"],
                "margin": cost["margin"],
                "margin_pct": cost["margin_pct"] if cost["margin_pct"] is not None else "",
            }
        )
    return rows


# --------------------------------------------------------------------------
# Revenue against expense
# --------------------------------------------------------------------------

def revenue_vs_expense(user, year, month):
    """
    Twelve months back from the month asked for. Both sides are summed from
    their own rows — invoice lines on one side, expenses on the other.
    """
    from finance.models import Expense, Invoice

    start, end = _month_bounds(year, month)
    first = (start.replace(day=1) - timedelta(days=340)).replace(day=1)

    invoices = (
        Invoice.objects.filter(issued_on__isnull=False, issued_on__gte=first, issued_on__lte=end)
        .prefetch_related("lines")
    )
    expenses = Expense.objects.filter(incurred_on__gte=first, incurred_on__lte=end)

    buckets = OrderedDict()
    cursor = first
    while cursor <= end:
        buckets[(cursor.year, cursor.month)] = {"revenue": Decimal("0"), "expense": Decimal("0")}
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    for invoice in invoices:
        key = (invoice.issued_on.year, invoice.issued_on.month)
        if key in buckets:
            buckets[key]["revenue"] += invoice.total
    for expense in expenses:
        key = (expense.incurred_on.year, expense.incurred_on.month)
        if key in buckets:
            buckets[key]["expense"] += expense.amount

    rows = []
    for (bucket_year, bucket_month), figures in buckets.items():
        rows.append(
            {
                "month": timezone.datetime(bucket_year, bucket_month, 1).strftime("%b %Y"),
                "revenue": figures["revenue"],
                "expense": figures["expense"],
                "margin": figures["revenue"] - figures["expense"],
            }
        )
    return rows


# --------------------------------------------------------------------------
# Technician output
# --------------------------------------------------------------------------

def technician_output(user, year, month):
    from django.contrib.auth import get_user_model

    from fieldjobs.models import Assessment, FieldJob

    User = get_user_model()
    start, end = _month_bounds(year, month)

    technicians = User.objects.filter(
        is_active=True, user_roles__role__permissions__code="view_own_job_list"
    ).distinct()

    rows = []
    for technician in technicians:
        jobs = FieldJob.objects.filter(
            assigned_to=technician,
            completed_at__date__gte=start,
            completed_at__date__lte=end,
        )
        assessments = Assessment.objects.filter(
            technician=technician,
            submitted_at__date__gte=start,
            submitted_at__date__lte=end,
        )
        approved = sum(1 for a in assessments if a.approval_state == "approved")
        returned = sum(1 for a in assessments if a.approval_state == "returned")
        rows.append(
            {
                "technician": technician.get_full_name() or technician.email,
                "jobs_completed": jobs.count(),
                "assessments": assessments.count(),
                "approved": approved,
                "returned": returned,
            }
        )
    rows.sort(key=lambda row: (-row["jobs_completed"], row["technician"]))
    return rows


# --------------------------------------------------------------------------
# Attendance summary
# --------------------------------------------------------------------------

def attendance_summary(user, year, month):
    from accounts.scoping import apply_scope
    from hr.models import Employee
    from hr.services import monthly_summary

    team = apply_scope(
        Employee.objects.filter(is_active=True).select_related("user"),
        user,
        "view_attendance_reports",
        own_team_filter=Q(supervisor__user=user),
    )
    summary = monthly_summary(year, month, team=team)

    return [
        {
            "employee": "{}, {}".format(
                row["employee"].user.last_name, row["employee"].user.first_name
            ).strip(", "),
            "staff_id": row["employee"].staff_id,
            "days_present": row["days_present"],
            "days_absent": row["days_absent"],
            "late_arrivals": row["late_arrivals"],
            "total_hours": round(row["total_minutes"] / 60, 2),
            "corrections": row["corrections"],
        }
        for row in summary["rows"]
    ]


# --------------------------------------------------------------------------
# The set
# --------------------------------------------------------------------------

REPORTS = OrderedDict(
    (report.key, report)
    for report in [
        Report(
            key="jobs-completed",
            label="Jobs completed",
            description="Field jobs finished in the month, with how long each was open from the ticket that started it.",
            permission="view_reports",
            columns=[
                Column("reference", "Job"),
                Column("customer", "Customer"),
                Column("service_type", "Service type"),
                Column("technician", "Technician"),
                Column("completed", "Completed"),
                Column("days_open", "Days open", numeric=True),
            ],
            build=jobs_completed,
            totals=("days_open",),
        ),
        Report(
            key="ticket-ageing",
            label="Ticket ageing",
            description="Every open ticket, oldest first. The point of this report is the top of it.",
            permission="view_reports",
            columns=[
                Column("reference", "Ticket"),
                Column("customer", "Customer"),
                Column("service_type", "Service type"),
                Column("assigned", "Assigned"),
                Column("status", "Status"),
                Column("age_days", "Age (days)", numeric=True),
            ],
            build=ticket_ageing,
            period=False,
        ),
        Report(
            key="project-profitability",
            label="Project profitability",
            description="Revenue against cost per project, both summed from their own rows at query time.",
            permission="view_financial_reports",
            columns=[
                Column("reference", "Project"),
                Column("customer", "Customer"),
                Column("stage", "Stage"),
                Column("revenue", "Revenue", numeric=True, money=True),
                Column("cost", "Cost", numeric=True, money=True),
                Column("margin", "Margin", numeric=True, money=True),
                Column("margin_pct", "Margin %", numeric=True),
            ],
            build=project_profitability,
            period=False,
            totals=("revenue", "cost", "margin"),
        ),
        Report(
            key="revenue-vs-expense",
            label="Revenue vs. expense",
            description="Twelve months to the month selected, invoiced against spent.",
            permission="view_financial_reports",
            columns=[
                Column("month", "Month"),
                Column("revenue", "Revenue", numeric=True, money=True),
                Column("expense", "Expense", numeric=True, money=True),
                Column("margin", "Margin", numeric=True, money=True),
            ],
            build=revenue_vs_expense,
            totals=("revenue", "expense", "margin"),
        ),
        Report(
            key="technician-output",
            label="Technician output",
            description="Jobs completed and assessments submitted per technician, with how many came back.",
            permission="view_reports",
            columns=[
                Column("technician", "Technician"),
                Column("jobs_completed", "Jobs completed", numeric=True),
                Column("assessments", "Assessments", numeric=True),
                Column("approved", "Approved", numeric=True),
                Column("returned", "Returned", numeric=True),
            ],
            build=technician_output,
            totals=("jobs_completed", "assessments", "approved", "returned"),
        ),
        Report(
            key="attendance-summary",
            label="Attendance summary",
            description="The month per employee, derived from the append-only attendance event log.",
            permission="view_attendance_reports",
            columns=[
                Column("employee", "Employee"),
                Column("staff_id", "Staff ID"),
                Column("days_present", "Days present", numeric=True),
                Column("days_absent", "Days absent", numeric=True),
                Column("late_arrivals", "Late", numeric=True),
                Column("total_hours", "Total hours", numeric=True),
                Column("corrections", "Corrections", numeric=True),
            ],
            build=attendance_summary,
            totals=("days_present", "days_absent", "late_arrivals", "total_hours", "corrections"),
        ),
    ]
)


def available_to(user):
    """The reports this user's permissions actually reach."""
    from accounts.decorators import user_has_permission

    return [r for r in REPORTS.values() if user_has_permission(user, r.permission)]


def compute_totals(report, rows):
    """Totals summed from the same rows the table shows — never separately."""
    totals = {}
    for key in report.totals:
        values = [row.get(key) for row in rows]
        numbers = [v for v in values if isinstance(v, (int, float, Decimal))]
        totals[key] = sum(numbers) if numbers else 0
    return totals
