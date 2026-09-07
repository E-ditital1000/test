"""
The attention queue.

One list of everything blocked on this particular person, ranked by how long
it has been waiting — not grouped by type. The oldest blocked thing is always
first on screen, because that is the one costing the business money.

Every entry is gathered under the permission that governs it and through the
same scoping the module itself uses, so the queue can never surface a record
its owner could not open. An item that cannot be acted on does not belong in
a queue called "needs your attention".
"""
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.decorators import user_has_permission
from accounts.scoping import apply_scope


@dataclass
class Item:
    kind: str
    label: str
    detail: str
    waiting_since: datetime
    url: str
    tone: str = "wait"

    @property
    def waiting_for(self):
        return timezone.now() - self.waiting_since


def attention_queue(user, limit=12):
    """Everything waiting on this user, oldest first."""
    items = []

    # -- assessments this person can actually review --------------------
    if user_has_permission(user, "review_assessment"):
        from approvals.models import latest_decisions
        from fieldjobs.models import Assessment

        submitted = apply_scope(
            Assessment.objects.filter(state=Assessment.SUBMITTED).select_related(
                "field_job__customer", "technician", "service_type"
            ),
            user,
            "review_assessment",
            own_team_filter=Q(technician__employee__supervisor__user=user),
        )
        rows = list(submitted)
        decided = latest_decisions(Assessment, [a.pk for a in rows])
        for assessment in rows:
            latest = decided.get(assessment.pk)
            if latest and latest.decision in ("approved", "returned"):
                continue
            items.append(
                Item(
                    kind="Assessment",
                    label=f"{assessment.field_job.reference} — {assessment.service_type.name}",
                    detail=f"Submitted by {assessment.technician.get_full_name() or assessment.technician.email}",
                    waiting_since=assessment.submitted_at or assessment.server_received_at,
                    url=reverse("approvals-assessment-review", args=[assessment.pk]),
                )
            )

    # -- tickets nobody owns --------------------------------------------
    if user_has_permission(user, "assign_ticket"):
        from crm.models import Ticket

        for ticket in Ticket.objects.unassigned().select_related("customer")[:20]:
            items.append(
                Item(
                    kind="Ticket",
                    label=f"{ticket.reference} — {ticket.description[:48]}",
                    detail=f"Unassigned · {ticket.customer.name}",
                    waiting_since=ticket.created_at,
                    url=reverse("crm-ticket-detail", args=[ticket.pk]),
                    tone="over" if ticket.is_ageing else "wait",
                )
            )

    # -- money waiting on a decision ------------------------------------
    if user_has_permission(user, "approve_requisition"):
        from finance.views import _pending_requisitions

        is_executive = user_has_permission(user, "view_executive_dashboard")
        for requisition in _pending_requisitions():
            needs_executive = requisition.requires_executive_approval()
            # Do not put an item in somebody's queue that they are not
            # allowed to clear.
            if needs_executive and not is_executive:
                continue
            items.append(
                Item(
                    kind="Requisition",
                    label=f"{requisition.reference} — {requisition.description[:44]}",
                    detail=f"{requisition.project.reference} · raised by "
                    f"{requisition.raised_by.get_full_name() or requisition.raised_by.email}",
                    waiting_since=requisition.created_at,
                    url=reverse("finance-requisitions"),
                )
            )

    # -- invoices past their date ---------------------------------------
    if user_has_permission(user, "view_invoices"):
        from finance.models import Invoice

        overdue = (
            Invoice.objects.filter(state__in=[Invoice.SENT, Invoice.PART_PAID])
            .select_related("customer")
            .prefetch_related("lines", "payments")
        )
        for invoice in overdue:
            if invoice.days_overdue <= 0:
                continue
            items.append(
                Item(
                    kind="Invoice",
                    label=f"{invoice.number} — {invoice.customer.name}",
                    detail=f"{invoice.days_overdue} days overdue · {invoice.outstanding} outstanding",
                    waiting_since=timezone.now() - timezone.timedelta(days=invoice.days_overdue),
                    url=reverse("finance-invoice-detail", args=[invoice.pk]),
                    tone="over",
                )
            )

    # -- attendance exceptions on this supervisor's team -----------------
    if user_has_permission(user, "correct_attendance"):
        from hr.models import AttendanceDay, Employee

        team = apply_scope(
            Employee.objects.filter(is_active=True).select_related("user"),
            user,
            "correct_attendance",
            own_team_filter=Q(supervisor__user=user),
        )
        stuck = AttendanceDay.objects.filter(
            employee__in=team, still_clocked_in=True, date__lt=timezone.localdate()
        ).select_related("employee__user")
        for day in stuck:
            items.append(
                Item(
                    kind="Attendance",
                    label=f"{day.employee.full_name} never clocked out",
                    detail=f"Still on the clock from {day.date}",
                    waiting_since=day.first_in or timezone.now(),
                    url=reverse("hr-employee-attendance", args=[day.employee.pk]),
                    tone="over",
                )
            )

    # -- your own overdue work ------------------------------------------
    # No permission gate: this is the person's own task, the same way their
    # own job list needs no permission beyond having one.
    from projects.models import Task

    for task in Task.objects.for_person(user).overdue().select_related("project"):
        items.append(
            Item(
                kind="Task",
                label=task.title,
                detail=f"{task.project.reference} · due {task.due_date:%d %b}",
                # Ranked from when it fell due, so the longest-overdue leads.
                waiting_since=timezone.make_aware(
                    datetime.combine(task.due_date, datetime.min.time())
                ),
                url=reverse("projects-detail", args=[task.project_id]) + "#tasks",
                tone="over",
            )
        )

    items.sort(key=lambda item: item.waiting_since)
    return items[:limit]


def attention_count(user):
    """
    Just the number, for the bell in the header.

    The full queue builds objects and resolves URLs; this runs on every page
    load, so it counts rows and stops there. It must agree with
    `attention_queue` — the same permissions and the same scoping — or the
    badge sends people to a list that does not match it.
    """
    total = 0

    if user_has_permission(user, "review_assessment"):
        from approvals.models import latest_decisions
        from fieldjobs.models import Assessment

        submitted = apply_scope(
            Assessment.objects.filter(state=Assessment.SUBMITTED),
            user,
            "review_assessment",
            own_team_filter=Q(technician__employee__supervisor__user=user),
        )
        rows = list(submitted)
        decided = latest_decisions(Assessment, [a.pk for a in rows])
        total += sum(
            1 for a in rows
            if (decided.get(a.pk).decision if decided.get(a.pk) else None)
            not in ("approved", "returned")
        )

    if user_has_permission(user, "assign_ticket"):
        from crm.models import Ticket

        total += Ticket.objects.unassigned().count()

    if user_has_permission(user, "approve_requisition"):
        from finance.views import _pending_requisitions

        is_executive = user_has_permission(user, "view_executive_dashboard")
        total += sum(
            1 for r in _pending_requisitions()
            if is_executive or not r.requires_executive_approval()
        )

    if user_has_permission(user, "view_invoices"):
        from finance.models import Invoice

        total += sum(
            1 for invoice in Invoice.objects.filter(
                state__in=[Invoice.SENT, Invoice.PART_PAID]
            ).prefetch_related("lines", "payments")
            if invoice.days_overdue > 0
        )

    if user_has_permission(user, "correct_attendance"):
        from hr.models import AttendanceDay, Employee

        team = apply_scope(
            Employee.objects.filter(is_active=True),
            user,
            "correct_attendance",
            own_team_filter=Q(supervisor__user=user),
        )
        total += AttendanceDay.objects.filter(
            employee__in=team, still_clocked_in=True, date__lt=timezone.localdate()
        ).count()

    from projects.models import Task

    total += Task.objects.for_person(user).overdue().count()

    return total
