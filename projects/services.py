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
