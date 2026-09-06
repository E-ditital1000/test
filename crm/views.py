"""
Customers & Tickets.

A ticket is where a job originates and where its lineage begins. Converting
one to a project carries `job_ref` across and links both records permanently
— that single thread from first call to final payment is the whole premise
of the product, so the conversion is the most carefully guarded thing here.

Every mutation writes an append-only TicketEvent, so the activity log is a
read of history rather than a summary somebody has to remember to update.
"""
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.decorators import require_permission, user_has_permission
from config.models import StatusOption
from config.references import next_reference

from .forms import (
    ContactForm,
    CustomerForm,
    SiteForm,
    TicketAssignForm,
    TicketForm,
    TicketStatusForm,
)
from .models import Contact, Customer, Site, Ticket


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------

@require_permission("view_customers")
def customers(request):
    query = request.GET.get("q", "").strip()
    show_inactive = request.GET.get("inactive") == "1"

    register = Customer.objects.annotate(
        open_tickets=Count("tickets", filter=Q(tickets__closed_at__isnull=True), distinct=True),
        site_count=Count("sites", distinct=True),
    )
    if not show_inactive:
        register = register.filter(is_active=True)
    if query:
        register = register.filter(
            Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(sites__name__icontains=query)
        ).distinct()

    return render(
        request,
        "crm/customers.html",
        {
            "customers": register,
            "query": query,
            "show_inactive": show_inactive,
            "total_customers": Customer.objects.count(),
        },
    )


@require_permission("view_customers")
def customer_detail(request, pk):
    """
    The customer record with its contacts, sites and a service history
    timeline drawn from tickets and the projects they became — one place to
    answer "what have we done for these people".
    """
    customer = get_object_or_404(
        Customer.objects.prefetch_related("contacts", "sites"), pk=pk
    )
    tickets = (
        customer.tickets.select_related("service_type", "status", "assigned_to", "site")
        .prefetch_related("project")
        .order_by("-created_at")
    )
    return render(
        request,
        "crm/customer_detail.html",
        {
            "customer": customer,
            "tickets": tickets,
            "open_tickets": tickets.filter(closed_at__isnull=True).count(),
            "projects": customer.projects.select_related("status").order_by("-created_at"),
        },
    )


@require_permission("create_customer")
def customer_edit(request, pk=None):
    instance = get_object_or_404(Customer, pk=pk) if pk else None
    form = CustomerForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        customer = form.save(commit=False)
        if instance is None:
            customer.created_by = request.user
        customer.save()
        messages.success(request, f"Customer “{customer.name}” saved.")
        return redirect("crm-customer-detail", pk=customer.pk)
    return render(
        request,
        "crm/customer_form.html",
        {"form": form, "instance": instance},
    )


@require_permission("create_customer")
def site_create(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = SiteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        site = form.save(commit=False)
        site.customer = customer
        site.save()
        messages.success(request, f"Site “{site.name}” added.")
        return redirect("crm-customer-detail", pk=customer.pk)
    return render(
        request,
        "crm/related_form.html",
        {
            "form": form,
            "customer": customer,
            "title": f"Add a site for {customer.name}",
            "subtitle": "A place work is carried out — a branch, a depot, a plot.",
        },
    )


@require_permission("create_customer")
def contact_create(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.customer = customer
        contact.save()
        messages.success(request, f"Contact “{contact.name}” added.")
        return redirect("crm-customer-detail", pk=customer.pk)
    return render(
        request,
        "crm/related_form.html",
        {
            "form": form,
            "customer": customer,
            "title": f"Add a contact for {customer.name}",
            "subtitle": "The person answering the phone when this customer calls.",
        },
    )


# --------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------

FILTERS = {
    "all": "All open",
    "unassigned": "Unassigned",
    "ageing": "Ageing over 24h",
    "closed": "Closed",
}


@require_permission("view_ticket_status")
def tickets(request):
    query = request.GET.get("q", "").strip()
    active_filter = request.GET.get("filter", "all")
    if active_filter not in FILTERS:
        active_filter = "all"

    rows = Ticket.objects.select_related(
        "customer", "site", "service_type", "status", "assigned_to"
    ).prefetch_related("project")

    if active_filter == "unassigned":
        rows = rows.unassigned()
    elif active_filter == "ageing":
        rows = rows.ageing()
    elif active_filter == "closed":
        rows = rows.filter(closed_at__isnull=False)
    else:
        rows = rows.open()

    if query:
        rows = rows.filter(
            Q(reference__icontains=query)
            | Q(description__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(site__name__icontains=query)
        )

    open_tickets = Ticket.objects.open()
    return render(
        request,
        "crm/tickets.html",
        {
            "tickets": rows.order_by("-created_at"),
            "query": query,
            "active_filter": active_filter,
            "active_filter_label": FILTERS[active_filter],
            "filters": FILTERS,
            "open_count": open_tickets.count(),
            "unassigned_count": open_tickets.filter(assigned_to__isnull=True).count(),
            # Assignment has to be possible in one action from this list.
            "assign_form": TicketAssignForm() if user_has_permission(request.user, "assign_ticket") else None,
        },
    )


@require_permission("view_ticket_status")
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related(
            "customer", "site", "service_type", "status", "assigned_to", "raised_by"
        ),
        pk=pk,
    )
    project = getattr(ticket, "project", None)

    return render(
        request,
        "crm/ticket_detail.html",
        {
            "ticket": ticket,
            "project": project,
            "events": ticket.events.select_related("actor"),
            "status_form": TicketStatusForm(instance=ticket),
            "assign_form": TicketAssignForm(initial={"assigned_to": ticket.assigned_to}),
            # The conversion is offered only when it is actually possible:
            # a ticket already converted has nothing left to convert.
            "can_convert": project is None and user_has_permission(
                request.user, "convert_ticket_to_project"
            ),
        },
    )


@require_permission("create_ticket")
@transaction.atomic
def ticket_create(request):
    form = TicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ticket = form.save(commit=False)
        ticket.reference = next_reference(Ticket, "TKT")
        ticket.raised_by = request.user
        status = StatusOption.default_for(StatusOption.TICKET)
        if status is None:
            messages.error(
                request,
                "No ticket statuses are configured. An Admin must add at least "
                "one in Settings → Status lists before tickets can be raised.",
            )
            return redirect("crm-tickets")
        ticket.status = status
        ticket.save()
        ticket.log(request.user, "Ticket created", f"Raised by {request.user.get_full_name() or request.user.email}")
        messages.success(request, f"Ticket {ticket.reference} created.")
        return redirect("crm-ticket-detail", pk=ticket.pk)

    return render(request, "crm/ticket_form.html", {"form": form})


@require_permission("assign_ticket")
@transaction.atomic
def ticket_assign(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method != "POST":
        return redirect("crm-ticket-detail", pk=ticket.pk)

    form = TicketAssignForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a technician to assign this ticket to.")
        return redirect(request.POST.get("next") or reverse("crm-ticket-detail", args=[ticket.pk]))

    previous = ticket.assigned_to
    ticket.assigned_to = form.cleaned_data["assigned_to"]
    ticket.assigned_at = timezone.now()

    # Status values are Settings-owned, so advancing is best-effort: if the
    # business has renamed or removed "assigned", the assignment still
    # happens and the status simply stays where it is.
    if ticket.status.is_default:
        assigned_status = StatusOption.objects.filter(
            kind=StatusOption.TICKET, code="assigned", is_active=True
        ).first()
        if assigned_status:
            ticket.status = assigned_status

    ticket.save(update_fields=["assigned_to", "assigned_at", "status"])
    ticket.log(
        request.user,
        "Reassigned" if previous else "Assigned",
        f"to {ticket.assigned_to.get_full_name() or ticket.assigned_to.email}"
        + (f" (was {previous.get_full_name() or previous.email})" if previous else ""),
    )
    messages.success(request, f"{ticket.reference} assigned to {ticket.assigned_to.get_full_name() or ticket.assigned_to.email}.")
    return redirect(request.POST.get("next") or reverse("crm-ticket-detail", args=[ticket.pk]))


@require_permission("view_ticket_status")
@transaction.atomic
def ticket_update(request, pk):
    """Status and priority, from the ticket detail screen."""
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method != "POST":
        return redirect("crm-ticket-detail", pk=ticket.pk)

    before_status, before_priority = ticket.status, ticket.priority
    form = TicketStatusForm(request.POST, instance=ticket)
    if form.is_valid():
        updated = form.save()
        if updated.status != before_status:
            updated.log(request.user, "Status changed", f"{before_status.label} → {updated.status.label}")
        if updated.priority != before_priority:
            updated.log(request.user, "Priority changed", f"{before_priority} → {updated.priority}")
        messages.success(request, "Ticket updated.")
    else:
        messages.error(request, "That change could not be saved.")
    return redirect("crm-ticket-detail", pk=ticket.pk)


@require_permission("close_ticket")
@transaction.atomic
def ticket_close(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method != "POST":
        return redirect("crm-ticket-detail", pk=ticket.pk)

    if ticket.closed_at:
        messages.info(request, f"{ticket.reference} is already closed.")
        return redirect("crm-ticket-detail", pk=ticket.pk)

    terminal = StatusOption.objects.filter(
        kind=StatusOption.TICKET, is_terminal=True, is_active=True
    ).first()
    if terminal is None:
        messages.error(
            request,
            "No closing status is configured. An Admin must mark one ticket "
            "status as closing in Settings → Status lists.",
        )
        return redirect("crm-ticket-detail", pk=ticket.pk)

    ticket.status = terminal
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=["status", "closed_at"])
    ticket.log(request.user, "Ticket closed", request.POST.get("reason", ""))
    messages.success(request, f"{ticket.reference} closed.")
    return redirect("crm-ticket-detail", pk=ticket.pk)


@require_permission("convert_ticket_to_project")
@transaction.atomic
def ticket_convert(request, pk):
    """
    The hinge of the product. Customer, site, service type and description
    carry across; `job_ref` carries across too, which is what makes the job
    traceable from this ticket through to the invoice. Both records stay
    linked permanently — neither is consumed by the other.
    """
    from projects.models import Project

    ticket = get_object_or_404(
        Ticket.objects.select_related("customer", "site", "service_type"), pk=pk
    )

    if getattr(ticket, "project", None) is not None:
        messages.info(
            request,
            f"{ticket.reference} is already linked to {ticket.project.reference}.",
        )
        return redirect("crm-ticket-detail", pk=ticket.pk)

    if request.method != "POST":
        return redirect("crm-ticket-detail", pk=ticket.pk)

    status = StatusOption.default_for(StatusOption.PROJECT)
    if status is None:
        messages.error(
            request,
            "No project statuses are configured. An Admin must add at least "
            "one in Settings → Status lists before a ticket can be converted.",
        )
        return redirect("crm-ticket-detail", pk=ticket.pk)

    project = Project.objects.create(
        reference=next_reference(Project, "PRJ"),
        name=f"{ticket.customer.name} — {ticket.service_type.name}",
        description=ticket.description,
        job_ref=ticket.job_ref,  # the lineage, carried forward
        ticket=ticket,
        customer=ticket.customer,
        site=ticket.site,
        service_type=ticket.service_type,
        status=status,
        stage=Project.SITE_ASSESSMENT,
        manager=request.user if user_has_permission(request.user, "manage_project") else None,
    )
    project.stage_events.create(
        from_stage="", to_stage=Project.SITE_ASSESSMENT, actor=request.user,
        note=f"Converted from ticket {ticket.reference}",
    )
    ticket.log(request.user, "Converted to project", project.reference)

    messages.success(
        request,
        f"{ticket.reference} converted to {project.reference}. Both records stay linked.",
    )
    return redirect("projects-detail", pk=project.pk)
