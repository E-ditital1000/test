"""
Cross-module search, for the box in the header.

Each section is gated on the permission that governs the module it reads,
and scoped the same way that module scopes itself — so search can never be
the way somebody sees a record the screens would refuse them. A search that
leaked would be the most quietly dangerous screen in the system, because
nobody would think to check it.

The set of things searched is fixed for the same reason the report set is:
what a person can find should be a decision somebody made, not a side effect
of which models happen to exist.
"""
from dataclasses import dataclass

from django.db.models import Q
from django.urls import reverse

from accounts.decorators import user_has_permission
from accounts.scoping import apply_scope

LIMIT_PER_SECTION = 5


@dataclass
class Hit:
    kind: str
    label: str
    detail: str
    url: str


def search(user, query):
    """Sections of hits, in the order a person is most likely to want them."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    sections = []

    if user_has_permission(user, "view_ticket_status"):
        from crm.models import Ticket

        rows = Ticket.objects.filter(
            Q(reference__icontains=query)
            | Q(description__icontains=query)
            | Q(customer__name__icontains=query)
        ).select_related("customer", "status").order_by("-created_at")
        sections.append(_section("Tickets", [
            Hit("Ticket", f"{t.reference} — {t.description[:52]}",
                f"{t.customer.name} · {t.status.label}",
                reverse("crm-ticket-detail", args=[t.pk]))
            for t in rows[:LIMIT_PER_SECTION]
        ]))

    if user_has_permission(user, "view_customers"):
        from crm.models import Customer

        rows = Customer.objects.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query)
        ).order_by("name")
        sections.append(_section("Customers", [
            Hit("Customer", c.name, c.phone or c.email or "No contact details",
                reverse("crm-customer-detail", args=[c.pk]))
            for c in rows[:LIMIT_PER_SECTION]
        ]))

    if user_has_permission(user, "view_projects"):
        from projects.models import Project

        rows = apply_scope(
            Project.objects.filter(
                Q(reference__icontains=query)
                | Q(name__icontains=query)
                | Q(customer__name__icontains=query)
            ).select_related("customer"),
            user,
            "view_projects",
            own_projects_filter=Q(manager=user) | Q(crew__employee__user=user),
        ).distinct().order_by("-created_at")
        sections.append(_section("Projects", [
            Hit("Project", f"{p.reference} — {p.name[:48]}",
                f"{p.customer.name} · {p.get_stage_display()}",
                reverse("projects-detail", args=[p.pk]))
            for p in rows[:LIMIT_PER_SECTION]
        ]))

    if user_has_permission(user, "view_invoices"):
        from finance.models import Invoice

        rows = Invoice.objects.filter(
            Q(number__icontains=query) | Q(customer__name__icontains=query)
        ).select_related("customer").order_by("-created_at")
        sections.append(_section("Invoices", [
            Hit("Invoice", f"{i.number} — {i.customer.name}", i.get_state_display(),
                reverse("finance-invoice-detail", args=[i.pk]))
            for i in rows[:LIMIT_PER_SECTION]
        ]))

    if user_has_permission(user, "view_employees"):
        from hr.models import Employee

        rows = apply_scope(
            Employee.objects.filter(
                Q(staff_id__icontains=query)
                | Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
            ).select_related("user"),
            user,
            "view_employees",
            own_team_filter=Q(supervisor__user=user),
        ).order_by("staff_id")
        sections.append(_section("Employees", [
            Hit("Employee", e.full_name, f"{e.staff_id} · {e.job_title or 'No job title'}",
                reverse("hr-employees"))
            for e in rows[:LIMIT_PER_SECTION]
        ]))

    return [section for section in sections if section["hits"]]


def _section(label, hits):
    return {"label": label, "hits": hits}
