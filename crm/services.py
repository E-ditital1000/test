"""
Customer records, and the one thing the register quietly owed the field.

A site is where work happens. Most customers have exactly one — the address
somebody typed when the customer was created — but nothing turned that
address into a site, so the picker on a field job was empty and a technician
was sent to a job with no location on it at all.
"""
from .models import Site

MAIN_SITE_NAME = "Main address"


def ensure_main_site(customer):
    """
    Give a customer with an address a site to be visited at.

    Only ever adds the first one: once somebody has named their depots and
    branches, the address on the customer record is not a place of work and
    inventing a site from it would put a wrong option in the picker.
    """
    address = (customer.address or "").strip()
    if not address or customer.sites.exists():
        return None
    return Site.objects.create(customer=customer, name=MAIN_SITE_NAME, address=address)


def assign_ticket(ticket, *, to, actor, start_date=None, due_date=None):
    """
    Hand a ticket to a technician, from wherever it was decided.

    There are two moments this happens: a supervisor working the list, and
    whoever raises the ticket already knowing who is going. They were one
    screen's logic and one screen's only, so the second was impossible — a
    receptionist taking a call from a customer whose technician is obvious
    had to save, leave, find the ticket again and assign it. The waiting was
    the system's, not the work's.

    Everything that follows an assignment lives here now, so neither door
    can quietly do less than the other: the status advance, the trail entry
    and the message to the person being given the work.
    """
    from django.utils import timezone

    from accounts.templatetags.a1 import a1daterange
    from config import notifications
    from config.models import StatusOption

    previous = ticket.assigned_to
    ticket.assigned_to = to
    ticket.assigned_at = timezone.now()
    # When the work is expected, as against when it changed hands. Left alone
    # if this assignment did not say.
    if start_date:
        ticket.start_date = start_date
    if due_date:
        ticket.due_date = due_date

    # Status values are Settings-owned, so advancing is best-effort: if the
    # business has renamed or removed "assigned", the assignment still
    # happens and the status simply stays where it is.
    if ticket.status.is_default:
        assigned_status = StatusOption.objects.filter(
            kind=StatusOption.TICKET, code="assigned", is_active=True
        ).first()
        if assigned_status:
            ticket.status = assigned_status

    ticket.save(
        update_fields=["assigned_to", "assigned_at", "status", "start_date", "due_date"]
    )
    ticket.log(
        actor,
        "Reassigned" if previous else "Assigned",
        f"to {to.get_full_name() or to.email}"
        + (f" (was {previous.get_full_name() or previous.email})" if previous else "")
        + (
            f" · {a1daterange(ticket.start_date, ticket.due_date)}"
            if ticket.start_date or ticket.due_date else ""
        ),
    )
    # Never blocks the assignment: a technician who has been given work has
    # been given it whether or not the mail server agreed.
    notifications.send(
        to=to.email,
        subject=f"{ticket.reference} assigned to you",
        template="ticket_assigned",
        context={
            "ticket": ticket,
            "technician_name": to.first_name or "Hello",
            "assigned_by": actor.get_full_name() or actor.email,
            "dates": a1daterange(ticket.start_date, ticket.due_date),
            "url": notifications.link("crm-ticket-detail", ticket.pk),
        },
    )
    return ticket
