from django import forms
from django.utils import timezone

from projects.models import Project

from .models import Expense, ExpenseCategory, Invoice, InvoiceLine, Payment


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
