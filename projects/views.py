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

from config.pagination import paginate
from accounts.decorators import require_permission, user_has_permission
from accounts.scoping import apply_scope

from .forms import (
    ProjectCrewForm,
    ProjectDocumentForm,
    RequisitionItemFormSet,
    RequisitionRequestForm,
    TaskCompletionForm,
    TaskForm,
)
from .models import Project, ProjectCrew, Requisition, Task
from .services import approval_note, raise_requisition


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
            "projects": paginate(request, rows.order_by("-created_at")),
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
            "requisitions": project.requisitions.select_related("raised_by").prefetch_related("items"),
            "field_jobs": project.field_jobs.select_related("assigned_to", "site").order_by("scheduled_for"),
            "stage_events": project.stage_events.select_related("actor"),
            # Cost is a separate permission from seeing the project at all.
            "can_see_cost": can_see_cost,
            "cost": project_cost(project) if can_see_cost else None,
            "can_manage": user_has_permission(request.user, "manage_project"),
            "task_form": TaskForm(project=project),
            "crew_form": ProjectCrewForm(project=project),
            "document_form": ProjectDocumentForm(),
            "can_requisition": user_has_permission(request.user, "raise_requisition"),
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

    The lead of a visit can finish that visit's work too. They are the one
    person accountable for what happened on site, and a checklist item done
    by the crew in front of them is not worth a phone call to the office.
    """
    task = get_object_or_404(Task.objects.select_related("project", "field_job"), pk=pk)

    is_mine = task.assignee_id == request.user.pk
    leads_the_visit = task.field_job is not None and task.field_job.is_led_by(request.user)
    if not (is_mine or leads_the_visit):
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

    if request.POST.get("next"):
        return redirect(request.POST["next"])
    # A task on a visit with no project of its own belongs to the visit.
    if task.project_id:
        return redirect("projects-detail", pk=task.project_id)
    return redirect("fieldjobs-job-detail", pk=task.field_job_id)


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
def requisition_create(request, pk):
    """
    What the project needs, itemised. This goes through the same service as
    the technician's screen on the job, so both produce one kind of record:
    a list of items, totalled from them, submitted into the approvals trail.
    """
    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    form = RequisitionRequestForm(request.POST or None)
    formset = RequisitionItemFormSet(request.POST or None)

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        requisition = raise_requisition(
            project=project,
            actor=request.user,
            description=form.cleaned_data["description"],
            items=formset.filled,
            needed_by=form.cleaned_data.get("needed_by"),
        )
        messages.success(request, f"{requisition.reference} raised. " + approval_note(requisition))
        return redirect("projects-detail", pk=project.pk)

    return render(
        request,
        "projects/requisition_form.html",
        {"project": project, "form": form, "formset": formset},
    )
