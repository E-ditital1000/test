"""
Raising a requisition, in one place.

Two screens reach it — the technician standing on the site, and the office
looking at the project — and both must produce the same record: itemised,
totalled from its items, and submitted into the shared approvals trail. A
second entry point that skipped a step would be a second kind of
requisition, and Finance would have to know which kind it was looking at.
"""
from decimal import Decimal

from django.db import transaction

from accounts.templatetags.a1 import a1date, a1money
from config import notifications
from config.references import next_reference

from .models import Requisition, RequisitionItem


@transaction.atomic
def raise_requisition(*, project, actor, description, items, needed_by=None):
    """
    Create a requisition with its items and submit it for approval.

    `items` is a list of dicts: description, and optionally quantity, unit
    and estimated_unit_cost. The amount is the total of the estimates, never
    typed: a figure that could disagree with the list under it is worse than
    no figure, since the approval threshold is applied to it.
    """
    rows = [item for item in items if (item.get("description") or "").strip()]
    if not rows:
        raise ValueError("A requisition needs at least one item.")

    requisition = Requisition.objects.create(
        project=project,
        reference=next_reference(Requisition, "RQ"),
        description=description.strip(),
        amount=Decimal("0"),
        raised_by=actor,
        needed_by=needed_by,
    )
    RequisitionItem.objects.bulk_create([
        RequisitionItem(
            requisition=requisition,
            description=item["description"].strip(),
            quantity=item.get("quantity") or Decimal("1"),
            unit=(item.get("unit") or "").strip(),
            estimated_unit_cost=item.get("estimated_unit_cost"),
            order=index,
        )
        for index, item in enumerate(rows)
    ])
    requisition.refresh_from_db()
    requisition.amount = requisition.items_total
    requisition.save(update_fields=["amount"])
    requisition.record_decision(decision="submitted", actor=actor)

    # Tell whoever decides. A requisition raised on a site is waiting on an
    # office that may not have the screen open, and the whole point of it is
    # that somebody is standing there needing the parts.
    notifications.send(
        to=notifications.recipients_holding("approve_requisition", exclude=actor),
        subject=f"Requisition to approve — {requisition.reference}",
        template="requisition_raised",
        context={
            "requisition": requisition,
            "amount": a1money(requisition.amount),
            "raised_by": actor.get_full_name() or actor.email,
            "needed": a1date(requisition.needed_by),
            "threshold_note": approval_note(requisition),
            "url": notifications.link("finance-requisitions"),
        },
    )
    return requisition


def approval_note(requisition):
    """The line shown to whoever raised it, so they know what happens next."""
    if requisition.requires_executive_approval():
        return "It is above the threshold, so it needs an Executive."
    if not requisition.has_estimates:
        return "No costs were given, so Finance will price it before approving."
    return "Finance can approve it."


def notify_project_manager(project, *, actor):
    """
    Tell whoever owns a project that they own it.

    A project with a manager who does not know is a project nobody is
    running. Sent whichever way it came about — converted from a ticket or
    started as a contract — because the person's position is the same
    either way.
    """
    from accounts.templatetags.a1 import a1daterange
    from config import notifications

    if project.manager is None or not project.manager.email:
        return
    if actor is not None and project.manager_id == actor.pk:
        # They just made it. They know.
        return

    notifications.send(
        to=project.manager.email,
        subject=f"{project.reference} is yours to run",
        template="project_assigned",
        context={
            "project": project,
            "first_name": project.manager.first_name or "Hello",
            "started_by": actor.get_full_name() or actor.email if actor else "the office",
            "dates": a1daterange(project.start_date, project.target_end_date),
            "from_ticket": project.ticket.reference if project.ticket_id else "",
            "url": notifications.link("projects-detail", project.pk),
        },
    )
