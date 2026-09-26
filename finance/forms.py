from datetime import timedelta

from django import forms
from django.utils import timezone

from crm.models import Customer, Ticket
from projects.models import Project

from .models import (
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    Payment,
    Quotation,
    QuotationLine,
)


class InvoiceForm(forms.ModelForm):
    """
    Every invoice attaches to a project, which is what makes project
    profitability computable at query time without storing a total anywhere.
    """

    class Meta:
        model = Invoice
        fields = ["project", "due_on", "notes"]
        labels = {"due_on": "Payment due"}
        widgets = {
            "due_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.select_related("customer").order_by(
            "-created_at"
        )
        self.fields["project"].empty_label = "Which project is this for?"


class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = ["description", "quantity", "unit_price"]


class PaymentForm(forms.ModelForm):
    """
    Payments are append-only: a mistaken one is corrected by a further row,
    never by editing the original. The invoice's paid state is derived from
    them rather than set by hand.
    """

    class Meta:
        model = Payment
        fields = ["amount", "paid_on", "method", "reference"]
        labels = {"paid_on": "Date paid", "reference": "Payment reference"}
        widgets = {"paid_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paid_on"].initial = timezone.localdate()


class ExpenseForm(forms.ModelForm):
    """
    Logged against a project with a receipt captured as a photo or PDF —
    usable from a phone, so the receipt field accepts a camera capture.
    """

    class Meta:
        model = Expense
        fields = ["project", "category", "item", "amount", "incurred_on", "receipt"]
        labels = {"incurred_on": "Date of the cost", "item": "What was bought"}
        widgets = {"incurred_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.select_related("customer").order_by(
            "-created_at"
        )
        self.fields["project"].empty_label = "Which project is this cost for?"
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True)
        self.fields["incurred_on"].initial = timezone.localdate()
        self.fields["receipt"].widget.attrs.update({"accept": "image/*,application/pdf"})
        self.fields["receipt"].help_text = "Photograph the receipt, or attach a PDF."


# --------------------------------------------------------------------------
# Quotations
# --------------------------------------------------------------------------

class QuotationForm(forms.ModelForm):
    """
    The header of an offer. Only the customer and a title are required: a
    quotation is usually written before there is a project, which is why
    the project and the ticket are both optional here and only the invoice
    insists on one.
    """

    class Meta:
        model = Quotation
        fields = ["customer", "title", "project", "ticket", "valid_until", "notes", "terms"]
        labels = {
            "title": "What is being quoted for",
            "valid_until": "Price holds until",
            "notes": "Notes to the customer",
            "terms": "Terms",
        }
        help_texts = {
            "project": "Leave empty if the work has not become a project yet.",
            "ticket": "The call this came from, if there was one.",
            "notes": "Printed on the customer's copy.",
            "terms": "Payment terms, exclusions, anything the price depends on.",
        }
        widgets = {
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "terms": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True).order_by("name")
        self.fields["customer"].empty_label = "Which customer is this for?"
        self.fields["project"].queryset = Project.objects.select_related("customer").order_by("-created_at")
        self.fields["project"].empty_label = "No project yet"
        self.fields["ticket"].queryset = Ticket.objects.select_related("customer").order_by("-created_at")
        self.fields["ticket"].empty_label = "No ticket"
        if not self.instance.pk:
            self.fields["valid_until"].initial = timezone.localdate() + timedelta(days=30)

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        # A quotation naming somebody else's project or ticket would put one
        # customer's prices on another customer's record.
        for field in ("project", "ticket"):
            row = cleaned.get(field)
            if row and customer and row.customer_id != customer.pk:
                self.add_error(field, f"That {field} belongs to {row.customer.name}.")
        valid_until = cleaned.get("valid_until")
        if valid_until and valid_until < timezone.localdate():
            self.add_error("valid_until", "A price cannot hold until a date that has passed.")
        return cleaned


class QuotationLineForm(forms.ModelForm):
    """
    The cost is A-1's own figure and never prints on the customer's copy.
    It is asked for here, while the price is still being decided, because
    afterwards nobody goes back to work out what the margin was.
    """

    class Meta:
        model = QuotationLine
        fields = ["description", "quantity", "unit_price", "unit_cost"]
        labels = {"unit_price": "Unit price", "unit_cost": "Unit cost to A-1"}
        help_texts = {"unit_cost": "Optional. Ours, never shown to the customer."}

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity <= 0:
            raise forms.ValidationError("A quantity has to be more than nothing.")
        return quantity

    def clean_unit_price(self):
        price = self.cleaned_data["unit_price"]
        if price < 0:
            raise forms.ValidationError("A price cannot be negative.")
        return price


class QuotationDecisionForm(forms.Form):
    """What the customer said, recorded against the quotation."""

    ACCEPTED = "accepted"
    DECLINED = "declined"
    decision = forms.ChoiceField(choices=[(ACCEPTED, "Accepted"), (DECLINED, "Declined")])
    decided_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Date")
    decision_reference = forms.CharField(
        max_length=120, required=False, label="Who said so",
        help_text="The person who accepted or declined it, and their order number if they gave one.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["decided_on"].initial = timezone.localdate()

    def clean_decided_on(self):
        decided_on = self.cleaned_data["decided_on"]
        if decided_on > timezone.localdate():
            raise forms.ValidationError("That date is in the future.")
        return decided_on


class QuotationToInvoiceForm(forms.Form):
    """
    An invoice still attaches to a project — that is what keeps project
    profitability computable — so converting asks for one when the
    quotation was written before the project existed.
    """

    project = forms.ModelChoiceField(queryset=Project.objects.none(), label="Invoice against project")
    due_on = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Payment due"
    )

    def __init__(self, *args, quotation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.quotation = quotation
        projects = Project.objects.filter(customer=quotation.customer).order_by("-created_at")
        self.fields["project"].queryset = projects
        self.fields["project"].empty_label = "Which project is this work under?"
        if quotation.project_id:
            self.fields["project"].initial = quotation.project_id
        self.fields["due_on"].initial = timezone.localdate() + timedelta(days=30)
