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

from accounts.templatetags.a1 import a1daterange
from accounts import audit
from config import notifications, removal
from config.redirects import back_to
from config.pagination import paginate
from config.references import next_reference
from accounts.decorators import (
    require_any_permission,
    require_permission,
    user_has_permission,
)
from accounts.scoping import apply_scope

from .forms import (
    ProjectCrewForm,
    ProjectDocumentForm,
    RequisitionItemFormSet,
    RequisitionRequestForm,
    TaskCompletionForm,
    ProjectForm,
    TaskForm,
)
from .models import Project, ProjectCrew, Requisition, Task
from .services import approval_note, notify_project_manager, raise_requisition


def _visible_projects(user):
    """
    Scope qualifies permission: a Project Manager's reach is their own
    projects. Applied here so no screen in this module can forget it.
    """
    return apply_scope(
        Project.objects.select_related("customer", "site", "service_type", "status", "manager"),
        user,
        "view_projects",
        own_projects_filter=(
            Q(manager=user)
            | Q(crew__employee__user=user)
            | Q(tasks__assignee=user)
            | Q(field_jobs__assigned_to=user)
            | Q(field_jobs__crew__employee__user=user)
        ),
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

    from fieldjobs.models import FieldJob

    visits = list(
        project.field_jobs.select_related("assigned_to", "site").order_by("scheduled_for")
    )
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
            "field_jobs": visits,
            # How far through the sites a contract is. There is no list of
            # the twelve facilities to count against — a visit is the record
            # that one exists — so this counts what has been booked, which is
            # the honest figure rather than a percentage of a guess.
            "visits_total": len(visits),
            "visits_done": sum(1 for v in visits if v.state == FieldJob.COMPLETED),
            "stage_events": project.stage_events.select_related("actor"),
            # Cost is a separate permission from seeing the project at all.
            "can_see_cost": can_see_cost,
            "cost": project_cost(project) if can_see_cost else None,
            "can_manage": user_has_permission(request.user, "manage_project"),
            "can_complete_work": _can_complete_project_work(request.user, project),
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


def _can_complete_project_work(user, project):
    """Only people assigned to this project's work can close its work."""
    return (
        project.manager_id == user.pk
        or project.crew.filter(employee__user=user).exists()
        or project.tasks.filter(assignee=user).exists()
        or project.field_jobs.filter(
            Q(assigned_to=user) | Q(crew__employee__user=user)
        ).exists()
    )


@require_permission("view_projects")
@transaction.atomic
def project_work_toggle(request, pk):
    """Record or reopen operational completion without advancing billing stages."""
    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    if not _can_complete_project_work(request.user, project):
        raise PermissionDenied("only people assigned to this project's work can complete it")
    if request.method != "POST":
        return redirect("projects-detail", pk=project.pk)

    from django.utils import timezone

    before = audit.snapshot(project, fields=["work_completed_at", "work_completed_by"])
    if project.work_completed_at:
        project.work_completed_at = None
        project.work_completed_by = None
        action = "project.work_reopened"
        message = f"{project.reference} work reopened."
    else:
        project.work_completed_at = timezone.now()
        project.work_completed_by = request.user
        action = "project.work_completed"
        message = f"{project.reference} work marked complete."
    project.save(update_fields=["work_completed_at", "work_completed_by"])
    audit.record_change(
        actor=request.user,
        action=action,
        target=project,
        before=before,
        after=audit.snapshot(project, fields=["work_completed_at", "work_completed_by"]),
    )
    messages.success(request, message)
    return redirect("projects-detail", pk=project.pk)


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
                notifications.send(
                    to=task.assignee.email,
                    subject=f"Task on {project.reference} — {task.title}",
                    template="task_assigned",
                    context={
                        "task": task,
                        "project": project,
                        "visit": task.field_job,
                        "first_name": task.assignee.first_name or "Hello",
                        "assigned_by": request.user.get_full_name() or request.user.email,
                        "dates": a1daterange(task.start_date, task.due_date),
                        "url": notifications.link("projects-detail", project.pk),
                    },
                )
                who = task.assignee.get_full_name() or task.assignee.email
                messages.success(
                    request,
                    f"Task assigned to {who}. It is on their dashboard and their phone now.",
                )
            else:
                messages.success(request, "Task added, unassigned for now.")
        else:
            # The form knows exactly what is wrong. Saying only "could not be
            # saved" throws that away and leaves somebody guessing at a date
            # they cannot see any more, which is how a form stops being used.
            problems = "; ".join(
                error
                if field == "__all__"
                else f"{form.fields[field].label or field}: {error}"
                for field, errors in form.errors.items()
                for error in errors
            )
            messages.error(
                request,
                f"That task could not be saved. {problems}" if problems
                else "That task could not be saved.",
            )
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
        get_object_or_404(_correctable_projects(request.user), pk=task.project_id)

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

    # A task on a visit with no project of its own belongs to the visit.
    if task.project_id:
        return back_to(request, "projects-detail", task.project_id)
    return back_to(request, "fieldjobs-job-detail", task.field_job_id)


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


@require_permission("create_project")
@transaction.atomic
def project_create(request):
    """
    Start a project that did not come from a ticket.

    Most projects are converted from one, and that route is unchanged: it is
    what keeps a customer's phone call joined to the invoice at the end. This
    is for the work that never was a phone call — a contract signed with an
    institution, to install across a dozen sites over months.

    It mints its own job reference, the way a quotation written before there
    was a project does. Everything downstream joins the project itself, so a
    project that started here behaves like any other from the moment it
    exists.
    """
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False)
        project.reference = next_reference(Project, "PRJ")
        project.save()
        project.advance_to(
            project.stage,
            request.user,
            note="Started directly — not converted from a ticket",
        )
        # Crew, chosen on this same page. Asking for it on a second screen
        # meant most projects were created with nobody on them, and the task
        # form — which narrows its assignee list to the crew — then offered
        # every account in the company.
        added, _removed = form.sync_crew(project, actor=request.user)
        notify_project_manager(project, actor=request.user)
        messages.success(
            request,
            f"{project.reference} started"
            + (f" with {added} on the crew." if added else ".")
            + " Schedule its field jobs from here, one for each site it covers.",
        )
        return redirect("projects-detail", pk=project.pk)
    return render(request, "projects/project_form.html", {"form": form})


# --------------------------------------------------------------------------
# Correcting and removing
#
# A-1 asked for these directly: an Admin or HR must be able to fix a project
# with the wrong customer on it and take out a task created by mistake.
# Neither was possible — a project could be created and advanced through its
# stages but never edited, and a task could be ticked complete and nothing
# else. A record you cannot correct is one people work around.
#
# `manage_records` carries the right. Whoever already manages the project can
# correct it too; removing is the stronger act and asks for the explicit
# permission.
# --------------------------------------------------------------------------

def _correctable_projects(user):
    """
    The projects somebody may correct.

    Scope decides what you can *see* — a project manager reads their own.
    `manage_records` is the right to fix anything, so it has to reach
    everything: HR holds `view_projects` at own-projects scope and is crewed
    on none of them, which filtered every project out and answered 404 to
    the person the permission was created for.
    """
    if user_has_permission(user, "manage_records"):
        return Project.objects.all()
    return _visible_projects(user)


@require_any_permission("manage_project", "manage_records")
@transaction.atomic
def project_edit(request, pk):
    """Fix a project. Everything about it except the stage it is at, which
    moves through `project_advance` so each move stays an event."""
    project = get_object_or_404(_correctable_projects(request.user), pk=pk)
    form = ProjectForm(request.POST or None, instance=project)

    if request.method == "POST" and form.is_valid():
        before = audit.snapshot_stored(project, fields=["name", "customer", "site", "manager",
                                                        "start_date", "target_end_date", "description"])
        saved = form.save()
        audit.record_change(
            actor=request.user,
            action="project.corrected",
            target=saved,
            before=before,
            after=audit.snapshot(saved, fields=["name", "customer", "site", "manager",
                                                "start_date", "target_end_date", "description"]),
        )
        added, removed = form.sync_crew(saved, actor=request.user)
        crew_note = ""
        if added or removed:
            parts = []
            if added:
                parts.append(f"{added} added to the crew")
            if removed:
                parts.append(f"{removed} taken off")
            crew_note = " " + ", ".join(parts).capitalize() + "."
        messages.success(request, f"{saved.reference} updated.{crew_note}")
        return redirect("projects-detail", pk=saved.pk)

    return render(
        request, "projects/project_form.html", {"form": form, "project": project}
    )


@require_permission("manage_records")
@transaction.atomic
def project_remove(request, pk):
    """
    Take a project out entirely.

    It goes only while nothing that is a record of what happened depends on
    it: a requisition, a visit, an expense, an invoice or a quotation all
    hold it back, and the refusal says which. Its own stages, crew, documents
    and tasks are part of it and go with it.
    """
    project = get_object_or_404(_correctable_projects(request.user), pk=pk)
    if request.method != "POST":
        return redirect("projects-detail", pk=project.pk)

    try:
        label = removal.remove(
            project,
            actor=request.user,
            reason=(request.POST.get("reason") or "").strip(),
            audit_action="project.removed",
        )
    except removal.CannotRemove as error:
        messages.error(request, str(error))
        return redirect("projects-detail", pk=project.pk)

    messages.success(request, f"{label} removed.")
    return redirect("projects-list")


@require_any_permission("manage_project", "manage_records")
@transaction.atomic
def task_edit(request, pk):
    """Correct a task: what it says, who has it, and when it runs."""
    task = get_object_or_404(
        Task.objects.select_related("project"), pk=pk
    )
    get_object_or_404(_visible_projects(request.user), pk=task.project_id)

    fields = ["title", "assignee", "field_job", "start_date", "start_time",
              "due_date", "due_time", "description"]
    form = TaskForm(request.POST or None, instance=task, project=task.project)

    if request.method == "POST" and form.is_valid():
        before = audit.snapshot_stored(task, fields=fields)
        saved = form.save()
        audit.record_change(
            actor=request.user,
            action="task.corrected",
            target=saved,
            before=before,
            after=audit.snapshot(saved, fields=fields),
        )
        messages.success(request, "Task updated.")
        return redirect("projects-detail", pk=task.project_id)

    return render(request, "projects/task_form.html", {"form": form, "task": task})


@require_permission("manage_records")
@transaction.atomic
def task_remove(request, pk):
    """
    Take a task out. Nothing in the system points at a task, so this always
    succeeds — which is why a task created by mistake is simply gone rather
    than retired into a list somebody has to read past.
    """
    task = get_object_or_404(Task.objects.select_related("project"), pk=pk)
    project_id = task.project_id
    get_object_or_404(_correctable_projects(request.user), pk=project_id)

    if request.method != "POST":
        return redirect("projects-detail", pk=project_id)

    label = removal.remove(
        task,
        actor=request.user,
        reason=(request.POST.get("reason") or "").strip(),
        audit_action="task.removed",
    )
    messages.success(request, f"Task “{label}” removed.")
    return redirect("projects-detail", pk=project_id)
