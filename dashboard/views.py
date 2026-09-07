"""
The role-aware landing screen and the executive command view.

Every tile is gated on a permission code and every figure is counted from
source rows at request time — nothing here reads a stored aggregate, and no
figure appears that cannot be traced to the rows underneath it.
"""
from datetime import timedelta

from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from accounts.decorators import require_permission, user_has_permission
from accounts.scoping import apply_scope
from crm.models import Ticket
from fieldjobs.models import Assessment, FieldJob
from projects.models import Project

from .search import search
from .services import attention_queue


def _tile(label, value, url_name, hint="", tone=""):
    """
    `tone` drives the severity rail: "alert" for a number that is bad, "ok"
    for one that is good, empty for one that is neither. Flat equal tiles
    tell a reader nothing about which figure needs them.
    """
    return {"label": label, "value": value, "url_name": url_name, "hint": hint, "tone": tone}


@require_permission("view_dashboard")
def index(request):
    user = request.user
    tiles = []

    if user_has_permission(user, "view_ticket_status"):
        open_tickets = Ticket.objects.open()
        unassigned = open_tickets.unassigned().count()
        tiles.append(
            _tile("Open tickets", open_tickets.count(), "crm-tickets", "Not yet closed")
        )
        if unassigned:
            tiles.append(
                _tile("Unassigned tickets", unassigned, "crm-tickets", "Need an owner", "alert")
            )

    if user_has_permission(user, "view_projects"):
        active = apply_scope(
            Project.objects.filter(completed_at__isnull=True),
            user,
            "view_projects",
            own_projects_filter=Q(manager=user) | Q(crew__employee__user=user),
        )
        at_assessment = active.filter(stage=Project.SITE_ASSESSMENT).count()
        tiles.append(
            _tile(
                "Active projects", active.distinct().count(), "projects-list",
                f"{at_assessment} at site assessment",
            )
        )

    if user_has_permission(user, "view_own_job_list"):
        today = timezone.localdate()
        mine = FieldJob.objects.filter(assigned_to=user, scheduled_for__date=today)
        outstanding = mine.exclude(state=FieldJob.COMPLETED).count()
        tiles.append(
            _tile(
                "Today's field jobs", mine.count(), "fieldjobs-my-jobs",
                f"{outstanding} not finished", "alert" if outstanding else "ok",
            )
        )

    if user_has_permission(user, "review_assessment"):
        waiting = apply_scope(
            Assessment.objects.filter(state=Assessment.SUBMITTED),
            user,
            "review_assessment",
            own_team_filter=Q(technician__employee__supervisor__user=user),
        ).count()
        tiles.append(
            _tile(
                "Assessments in", waiting, "approvals-queue", "Awaiting review",
                "alert" if waiting else "",
            )
        )

    if user_has_permission(user, "view_attendance_records"):
        from hr.models import AttendanceDay, Employee

        team = apply_scope(
            Employee.objects.filter(is_active=True),
            user,
            "view_attendance_records",
            own_team_filter=Q(supervisor__user=user),
        )
        today_rows = AttendanceDay.objects.filter(
            employee__in=team, date=timezone.localdate()
        )
        present = today_rows.exclude(state=AttendanceDay.ABSENT).count()
        headcount = team.count()
        tiles.append(
            _tile(
                "On the clock today", f"{present}/{headcount}", "hr-roll-call",
                "Roll-call", "alert" if headcount and present < headcount else "ok",
            )
        )

    # Everyone sees their own tasks. No permission gate: this is the
    # person's own work, like their own job list.
    from projects.models import Task

    my_tasks = Task.objects.for_person(user).open()
    open_count = my_tasks.count()
    if open_count:
        overdue = my_tasks.overdue().count()
        tiles.append(
            _tile(
                "My tasks", open_count, "dashboard-my-tasks",
                f"{overdue} overdue" if overdue else "none overdue",
                "alert" if overdue else "",
            )
        )

    queue = attention_queue(user)
    return render(
        request,
        "dashboard/index.html",
        {
            "tiles": tiles,
            "queue": queue,
            "today": timezone.localdate(),
            "is_executive": user_has_permission(user, "view_executive_dashboard"),
        },
    )


@require_permission("view_executive_dashboard")
def command_view(request):
    """
    How is the business doing. Every figure is derived at query time from
    source events — there is no stored total on this screen, and each one
    links to the rows that produced it.
    """
    today = timezone.localdate()
    month_start = today.replace(day=1)

    active_projects = Project.objects.filter(completed_at__isnull=True)
    overdue_projects = active_projects.filter(
        target_end_date__isnull=False, target_end_date__lt=today
    ).count()

    open_tickets = Ticket.objects.open()

    completed_this_month = FieldJob.objects.filter(
        state=FieldJob.COMPLETED, completed_at__date__gte=month_start
    )

    # On-time completion: jobs finished on or before the day they were
    # scheduled for. Counted from the rows, not stored anywhere.
    finished = FieldJob.objects.filter(
        state=FieldJob.COMPLETED, completed_at__date__gte=today - timedelta(days=30)
    )
    finished_count = finished.count()
    on_time = sum(
        1 for job in finished if job.completed_at and job.completed_at <= job.scheduled_for
    )
    on_time_pct = round(on_time / finished_count * 100) if finished_count else None

    # Twelve months of completions, for the shape rather than the precision.
    months = []
    cursor = month_start
    for _ in range(6):
        nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        count = FieldJob.objects.filter(
            state=FieldJob.COMPLETED, completed_at__date__gte=cursor, completed_at__date__lt=nxt
        ).count()
        months.append({"label": cursor.strftime("%b"), "value": count})
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    peak = max([m["value"] for m in months] + [1])
    for entry in months:
        entry["height"] = round(entry["value"] / peak * 100)

    queue = attention_queue(request.user, limit=8)

    return render(
        request,
        "dashboard/command_view.html",
        {
            "active_projects": active_projects.count(),
            "overdue_projects": overdue_projects,
            "open_tickets": open_tickets.count(),
            "unassigned": open_tickets.unassigned().count(),
            "completed_this_month": completed_this_month.count(),
            "on_time_pct": on_time_pct,
            "on_time_of": finished_count,
            "queue": queue,
            "months": months,
            "today": today,
        },
    )


@require_permission("view_dashboard")
def global_search(request):
    """
    The header search. Every section is gated on the permission that governs
    the module it reads and scoped the way that module scopes itself, so this
    can never become the way somebody sees a record the screens refuse them.
    """
    query = request.GET.get("q", "").strip()
    sections = search(request.user, query)
    return render(
        request,
        "dashboard/search.html",
        {
            "query": query,
            "sections": sections,
            "total": sum(len(section["hits"]) for section in sections),
        },
    )


@require_permission("view_dashboard")
def my_tasks(request):
    """
    Everything assigned to this person, across every project.

    Scoped to themselves by construction: there is no parameter here that
    could be made to show somebody else's work.
    """
    from projects.models import Task

    mine = Task.objects.for_person(request.user).select_related(
        "project__customer", "assigned_by"
    )
    return render(
        request,
        "dashboard/my_tasks.html",
        {
            "open_tasks": mine.open(),
            "overdue_count": mine.overdue().count(),
            "done_tasks": mine.filter(completed_at__isnull=False)[:10],
        },
    )
