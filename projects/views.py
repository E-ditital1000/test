"""
Projects.

A project carries the job lineage forward from the ticket it was converted
from. Stage advancement is recorded as an event with actor and timestamp, so
the lifecycle bar on the record is a read of history rather than a stored
field somebody could set directly.

Running cost is computed at query time from linked expenses, requisitions and
invoices. No total is stored anywhere in this module.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.decorators import require_permission, user_has_permission
from accounts.scoping import apply_scope

from .forms import (
    ProjectCrewForm,
    ProjectDocumentForm,
    RequisitionForm,
    TaskCompletionForm,
    TaskForm,
)
from .models import Project, ProjectCrew, Requisition, Task


def _visible_projects(user):
    """
    Scope qualifies permission: a Project Manager's reach is their own
    projects. Applied here so no screen in this module can forget it.
    """
    return apply_scope(
        Project.objects.select_related("customer", "site", "service_type", "status", "manager"),
        user,
        "view_projects",
        own_projects_filter=Q(manager=user) | Q(crew__employee__user=user),
    )


@require_permission("view_projects")
def project_list(request):
    query = request.GET.get("q", "").strip()
    stage = request.GET.get("stage", "")

    rows = _visible_projects(request.user)
    if stage:
        rows = rows.filter(stage=stage)
    if query:
        rows = rows.filter(
            Q(reference__icontains=query)
            | Q(name__icontains=query)
            | Q(customer__name__icontains=query)
        )

    everything = _visible_projects(request.user)
    return render(
        request,
        "projects/projects.html",
        {
            "projects": rows.order_by("-created_at"),
            "query": query,
            "stage": stage,
            "stages": Project.STAGES,
            "total_projects": everything.count(),
            "active_count": everything.filter(completed_at__isnull=True).count(),
        },
    )


@require_permission("view_projects")
def project_detail(request, pk):
    project = get_object_or_404(_visible_projects(request.user), pk=pk)

    # The lifecycle bar, read from the stage the project has actually
    # reached rather than from a separate progress field.
    reached = Project.STAGE_ORDER.index(project.stage)
    lifecycle = [
        {
            "code": code,
            "label": label,
            "state": "done" if index < reached else ("now" if index == reached else "todo"),
        }
        for index, (code, label) in enumerate(Project.STAGES)
    ]

    can_see_cost = user_has_permission(request.user, "view_project_cost")
    return render(
        request,
        "projects/project_detail.html",
        {
            "project": project,
            "lifecycle": lifecycle,
            "tasks": project.tasks.select_related("assignee", "assigned_by", "completed_by"),
            "open_task_count": project.tasks.open().count(),
            "overdue_task_count": project.tasks.overdue().count(),
            "crew": project.crew.select_related("employee__user"),
            "documents": project.documents.select_related("uploaded_by"),
            "requisitions": project.requisitions.select_related("raised_by"),
            "field_jobs": project.field_jobs.select_related("assigned_to", "site").order_by("scheduled_for"),
            "stage_events": project.stage_events.select_related("actor"),
            # Cost is a separate permission from seeing the project at all.
            "can_see_cost": can_see_cost,
            "cost": project_cost(project) if can_see_cost else None,
            "can_manage": user_has_permission(request.user, "manage_project"),
            "task_form": TaskForm(project=project),
            "crew_form": ProjectCrewForm(project=project),
            "document_form": ProjectDocumentForm(),
            "requisition_form": RequisitionForm() if user_has_permission(request.user, "raise_requisition") else None,
            "next_stage": _next_stage(project),
        },
    )


def project_cost(project):
    """
    Computed at query time from the rows themselves — never a stored total,
    so a figure can always be traced back to what produced it.
    """
    expenses = sum((e.amount for e in project.expenses.all()), 0)
    requisitions = sum(
        (r.amount for r in project.requisitions.all() if r.is_approved), 0
    )
    revenue = sum((i.total for i in project.invoices.all()), 0)
    cost = expenses + requisitions
    return {
        "expenses": expenses,
        "requisitions": requisitions,
        "cost": cost,
        "revenue": revenue,
        "margin": revenue - cost,
        "margin_pct": round((revenue - cost) / revenue * 100, 1) if revenue else None,
    }


def _next_stage(project):
    index = Project.STAGE_ORDER.index(project.stage)
    if index + 1 >= len(Project.STAGE_ORDER):
        return None
    code = Project.STAGE_ORDER[index + 1]
    return {"code": code, "label": dict(Project.STAGES)[code]}


@require_permission("manage_project")
@transaction.atomic
def project_advance(request, pk):
    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    if request.method != "POST":
        return redirect("projects-detail", pk=project.pk)

    target = request.POST.get("stage", "")
    if target not in Project.STAGE_ORDER:
        messages.error(request, "That is not a stage this project can move to.")
        return redirect("projects-detail", pk=project.pk)

    project.advance_to(target, request.user, note=request.POST.get("note", ""))
    if target == Project.INVOICE:
        # Reaching the invoice stage is the closest thing to "done" the
        # lifecycle has; the completion date is derived from it.
        from django.utils import timezone

        project.completed_at = timezone.now()
        project.save(update_fields=["completed_at"])

    messages.success(request, f"{project.reference} moved to {dict(Project.STAGES)[target]}.")
    return redirect("projects-detail", pk=project.pk)


@require_permission("manage_project")
def task_create(request, pk):
    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    if request.method == "POST":
        form = TaskForm(request.POST, project=project)
        if form.is_valid():
            task = form.save(commit=False)
            task.project = project
            # Who set this, so "who told her to do that" has an answer.
            task.assigned_by = request.user
            task.save()
            if task.assignee:
                who = task.assignee.get_full_name() or task.assignee.email
                messages.success(
                    request,
                    f"Task assigned to {who}. It is on their dashboard and their phone now.",
                )
            else:
                messages.success(request, "Task added, unassigned for now.")
        else:
            messages.error(request, "That task could not be saved.")
    return redirect("projects-detail", pk=project.pk)


def task_toggle(request, pk):
    """
    Completion is a timestamp, an actor and — where somebody bothers — a
    note, not a checkbox.

    Whoever the task belongs to can finish it. Requiring manage_project would
    mean a technician cannot tick off their own work, which is the surest way
    to have a task list nobody keeps up to date.
    """
    task = get_object_or_404(Task.objects.select_related("project"), pk=pk)

    is_mine = task.assignee_id == request.user.pk
    if not is_mine:
        if not user_has_permission(request.user, "manage_project"):
            raise PermissionDenied("missing permission: manage_project")
        get_object_or_404(_visible_projects(request.user), pk=task.project_id)

    if request.method == "POST":
        from django.utils import timezone

        if task.completed_at:
            task.completed_at, task.completed_by = None, None
            task.completion_note = ""
            messages.success(request, "Task reopened.")
        else:
            form = TaskCompletionForm(request.POST)
            task.completed_at, task.completed_by = timezone.now(), request.user
            task.completion_note = (
                form.cleaned_data["completion_note"] if form.is_valid() else ""
            )
            messages.success(request, f"“{task.title}” marked done.")
        task.save(
            update_fields=["completed_at", "completed_by", "completion_note"]
        )

    return redirect(request.POST.get("next") or reverse("projects-detail", args=[task.project_id]))


@require_permission("manage_project")
def crew_add(request, pk):
    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    if request.method == "POST":
        form = ProjectCrewForm(request.POST, project=project)
        if form.is_valid():
            crew = form.save(commit=False)
            crew.project = project
            crew.assigned_by = request.user
            crew.save()
            messages.success(request, f"{crew.employee.full_name} added to the crew.")
        else:
            messages.error(request, "That person is already on this crew.")
    return redirect("projects-detail", pk=project.pk)


@require_permission("manage_project")
def crew_remove(request, pk):
    crew = get_object_or_404(ProjectCrew, pk=pk)
    project = get_object_or_404(_visible_projects(request.user), pk=crew.project_id)
    if request.method == "POST":
        crew.delete()
        messages.success(request, "Removed from the crew.")
    return redirect("projects-detail", pk=project.pk)


@require_permission("manage_project")
def document_upload(request, pk):
    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    if request.method == "POST":
        form = ProjectDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.project = project
            document.uploaded_by = request.user
            document.save()
            messages.success(request, f"“{document.label}” attached.")
        else:
            messages.error(request, "That document could not be attached.")
    return redirect("projects-detail", pk=project.pk)


@require_permission("raise_requisition")
@transaction.atomic
def requisition_create(request, pk):
    """Raised against the project, then approved through the shared mechanism."""
    from config.references import next_reference

    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    if request.method == "POST":
        form = RequisitionForm(request.POST)
        if form.is_valid():
            requisition = form.save(commit=False)
            requisition.project = project
            requisition.reference = next_reference(Requisition, "RQ")
            requisition.raised_by = request.user
            requisition.save()
            requisition.record_decision(decision="submitted", actor=request.user)
            messages.success(
                request,
                f"{requisition.reference} raised. "
                + (
                    "It is above the threshold, so it needs an Executive."
                    if requisition.requires_executive_approval()
                    else "Finance can approve it."
                ),
            )
        else:
            messages.error(request, "That requisition could not be raised.")
    return redirect("projects-detail", pk=project.pk)
