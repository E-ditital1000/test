"""
Finance.

Every cost and every invoice attaches to a project, so project profitability
is computable at query time and no denormalised total is stored anywhere. An
invoice's paid state is derived from the payments recorded against it, not
set by hand.

Requisitions above the Settings-owned threshold need an Executive; below it,
Finance is enough. The threshold is configuration, never a constant here.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import require_permission, user_has_permission
from approvals.models import Approval, latest_decisions
from config.models import PolicySetting
from config.references import next_reference
from projects.models import Requisition

from .forms import ExpenseForm, InvoiceForm, InvoiceLineForm, PaymentForm
from .models import Expense, Invoice, InvoiceLine, Payment


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

@require_permission("view_invoices")
def invoices(request):
    query = request.GET.get("q", "").strip()
    rows = Invoice.objects.select_related("customer", "project").prefetch_related(
        "lines", "payments"
    )
    if query:
        rows = rows.filter(
            Q(number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(project__reference__icontains=query)
        )
    rows = list(rows.order_by("-created_at"))

    outstanding = sum((invoice.outstanding for invoice in rows), Decimal("0"))
    overdue = [i for i in rows if i.days_overdue > 30]
    month_start = timezone.localdate().replace(day=1)
    expenses_this_month = sum(
        (e.amount for e in Expense.objects.filter(incurred_on__gte=month_start)), Decimal("0")
    )
    pending = _pending_requisitions()

    return render(
        request,
        "finance/invoices.html",
        {
            "invoices": rows,
            "query": query,
            "outstanding": outstanding,
            "overdue_total": sum((i.outstanding for i in overdue), Decimal("0")),
            "overdue_count": len(overdue),
            "expenses_this_month": expenses_this_month,
            "pending_requisitions": len(pending),
            "above_threshold": sum(1 for r in pending if r.requires_executive_approval()),
            "can_issue": user_has_permission(request.user, "issue_invoice"),
        },
    )


@require_permission("view_invoices")
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "project").prefetch_related(
            "lines", "payments__recorded_by"
        ),
        pk=pk,
    )
    return render(
        request,
        "finance/invoice_detail.html",
        {
            "invoice": invoice,
            "line_form": InvoiceLineForm(),
            "payment_form": PaymentForm(),
            "can_issue": user_has_permission(request.user, "issue_invoice"),
            "can_record_payment": user_has_permission(request.user, "record_payment"),
        },
    )


@require_permission("issue_invoice")
@transaction.atomic
def invoice_create(request):
    form = InvoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        invoice = form.save(commit=False)
        invoice.number = next_reference(Invoice, "INV", field="number")
        invoice.customer = invoice.project.customer
        # The lineage carries through to the invoice: this is the same job
        # that started as a phone call.
        invoice.job_ref = invoice.project.job_ref
        invoice.issued_by = request.user
        invoice.save()
        messages.success(request, f"{invoice.number} created as a draft. Add its lines next.")
        return redirect("finance-invoice-detail", pk=invoice.pk)
    return render(request, "finance/invoice_form.html", {"form": form})


@require_permission("issue_invoice")
def invoice_line_add(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == "POST":
        if invoice.state != Invoice.DRAFT:
            messages.error(request, "Only a draft invoice can have lines changed.")
            return redirect("finance-invoice-detail", pk=invoice.pk)
        form = InvoiceLineForm(request.POST)
        if form.is_valid():
            line = form.save(commit=False)
            line.invoice = invoice
            line.save()
            messages.success(request, "Line added.")
        else:
            messages.error(request, "That line could not be added.")
    return redirect("finance-invoice-detail", pk=invoice.pk)


@require_permission("issue_invoice")
def invoice_line_remove(request, pk):
    line = get_object_or_404(InvoiceLine.objects.select_related("invoice"), pk=pk)
    invoice = line.invoice
    if request.method == "POST" and invoice.state == Invoice.DRAFT:
        line.delete()
        messages.success(request, "Line removed.")
    return redirect("finance-invoice-detail", pk=invoice.pk)


@require_permission("issue_invoice")
@transaction.atomic
def invoice_issue(request, pk):
    """draft → sent. After this the lines are fixed."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method != "POST":
        return redirect("finance-invoice-detail", pk=invoice.pk)

    if invoice.state != Invoice.DRAFT:
        messages.info(request, f"{invoice.number} has already been sent.")
    elif not invoice.lines.exists():
        messages.error(request, "An invoice needs at least one line before it can be sent.")
    else:
        invoice.state = Invoice.SENT
        invoice.issued_on = timezone.localdate()
        if invoice.due_on is None:
            invoice.due_on = invoice.issued_on + timedelta(days=30)
        invoice.save(update_fields=["state", "issued_on", "due_on"])
        messages.success(request, f"{invoice.number} marked as sent.")
    return redirect("finance-invoice-detail", pk=invoice.pk)


@require_permission("record_payment")
@transaction.atomic
def payment_record(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method != "POST":
        return redirect("finance-invoice-detail", pk=invoice.pk)

    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "That payment could not be recorded.")
        return redirect("finance-invoice-detail", pk=invoice.pk)

    if invoice.state == Invoice.DRAFT:
        messages.error(request, "A draft invoice has not been sent, so it cannot be paid yet.")
        return redirect("finance-invoice-detail", pk=invoice.pk)

    payment = form.save(commit=False)
    payment.invoice = invoice
    payment.recorded_by = request.user
    payment.save()

    # The state is a read of the payments, not something set by hand.
    invoice.state = invoice.derived_state()
    invoice.save(update_fields=["state"])
    messages.success(request, f"Payment recorded. {invoice.number} is now {invoice.get_state_display().lower()}.")
    return redirect("finance-invoice-detail", pk=invoice.pk)


# --------------------------------------------------------------------------
# Expenses
# --------------------------------------------------------------------------

@require_permission("log_expense")
def expenses(request):
    query = request.GET.get("q", "").strip()
    rows = Expense.objects.select_related("project__customer", "category", "logged_by")
    if query:
        rows = rows.filter(
            Q(item__icontains=query)
            | Q(project__reference__icontains=query)
            | Q(category__name__icontains=query)
        )
    rows = rows.order_by("-incurred_on")

    month_start = timezone.localdate().replace(day=1)
    return render(
        request,
        "finance/expenses.html",
        {
            "expenses": rows,
            "query": query,
            "total": sum((e.amount for e in rows), Decimal("0")),
            "this_month": sum(
                (e.amount for e in rows if e.incurred_on >= month_start), Decimal("0")
            ),
            "with_receipt": sum(1 for e in rows if e.receipt),
        },
    )


@require_permission("log_expense")
@transaction.atomic
def expense_create(request):
    import uuid

    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.logged_by = request.user
        # Logged from a phone, so it carries the offline-first fields even
        # when it happens to be typed at a desk.
        expense.client_uuid = request.POST.get("client_uuid") or uuid.uuid4()
        expense.device_timestamp = timezone.now()
        expense.location_unavailable = True
        expense.save()
        messages.success(
            request, f"{expense.item} logged against {expense.project.reference}."
        )
        return redirect("finance-expenses")
    return render(request, "finance/expense_form.html", {"form": form})


# --------------------------------------------------------------------------
# Requisitions
# --------------------------------------------------------------------------

def _pending_requisitions():
    """
    Raised but not yet decided — computed from the approval trail.

    The whole trail is read once rather than once per requisition: asking
    each row for its own state turned a queue of forty into forty round
    trips.
    """
    rows = list(
        Requisition.objects.select_related("project__customer", "raised_by")
        .order_by("created_at")
    )
    decisions = latest_decisions(Requisition, [r.pk for r in rows])
    pending = []
    for requisition in rows:
        latest = decisions.get(requisition.pk)
        if latest and latest.decision in (Approval.APPROVED, Approval.RETURNED):
            continue
        pending.append(requisition)
    return pending


@require_permission("approve_requisition")
def requisitions(request):
    pending = _pending_requisitions()
    threshold = PolicySetting.get_value(PolicySetting.REQUISITION_THRESHOLD, "0")
    is_executive = user_has_permission(request.user, "view_executive_dashboard")

    rows = [
        {
            "requisition": requisition,
            "needs_executive": requisition.requires_executive_approval(),
            # Below the threshold Finance is enough; above it, only an
            # Executive can sign. The client hides what it must not offer and
            # the server refuses it regardless.
            "can_decide": is_executive or not requisition.requires_executive_approval(),
        }
        for requisition in pending
    ]
    return render(
        request,
        "finance/requisitions.html",
        {"rows": rows, "threshold": threshold, "is_executive": is_executive},
    )


@require_permission("approve_requisition")
@transaction.atomic
def requisition_decide(request, pk):
    requisition = get_object_or_404(Requisition, pk=pk)
    if request.method != "POST":
        return redirect("finance-requisitions")

    decision = request.POST.get("decision")
    comment = (request.POST.get("comment") or "").strip()

    if decision not in (Approval.APPROVED, Approval.RETURNED):
        messages.error(request, "That is not a decision this screen can record.")
        return redirect("finance-requisitions")

    # Enforced server-side: holding approve_requisition is not enough when
    # the amount is above the threshold.
    if requisition.requires_executive_approval() and not user_has_permission(
        request.user, "view_executive_dashboard"
    ):
        messages.error(
            request,
            f"{requisition.reference} is above the approval threshold, so it needs "
            "an Executive. Your decision was not recorded.",
        )
        return redirect("finance-requisitions")

    try:
        requisition.record_decision(decision=decision, actor=request.user, comment=comment)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("finance-requisitions")

    messages.success(
        request,
        f"{requisition.reference} {'approved' if decision == Approval.APPROVED else 'returned'}.",
    )
    return redirect("finance-requisitions")
