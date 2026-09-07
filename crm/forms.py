from django import forms
from django.contrib.auth import get_user_model

from config.models import ServiceType, StatusOption

from .models import Contact, Customer, Site, Ticket

User = get_user_model()


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "notes"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class SiteForm(forms.ModelForm):
    """A place work happens. Coordinates are optional — most sites are
    described by address, and the GPS match on an assessment only needs them
    when somebody wants to check a technician was where they said."""

    class Meta:
        model = Site
        fields = ["name", "address", "latitude", "longitude"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["name", "job_title", "phone", "email", "is_primary"]
        labels = {"is_primary": "Main contact for this customer"}


class TicketForm(forms.ModelForm):
    """
    The receptionist's intake form — the highest-frequency screen in the
    system, filled in under time pressure while a customer is talking.

    Customer comes first because it is the first thing said on the phone.
    Assignment is deliberately absent: a Supervisor assigns from the ticket
    list, and leaving it blank sends the ticket to the unassigned queue,
    which is where it should go.
    """

    class Meta:
        model = Ticket
        fields = ["customer", "site", "service_type", "priority", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}
        labels = {"service_type": "Service type"}
        help_texts = {
            "service_type": "Sets the assessment questions the technician answers on site.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["service_type"].queryset = ServiceType.objects.filter(is_active=True)
        # Sites belong to a customer. Until one is chosen there is nothing
        # sensible to offer, and offering every site in the company invites
        # picking the wrong one.
        customer_id = self.data.get("customer") or getattr(self.instance, "customer_id", None)
        if customer_id:
            self.fields["site"].queryset = Site.objects.filter(
                customer_id=customer_id, is_active=True
            )
        else:
            self.fields["site"].queryset = Site.objects.none()
        self.fields["site"].required = False
        self.fields["site"].empty_label = "Choose a customer first"


class TicketAssignForm(forms.Form):
    """One action from the ticket list, as the brief requires."""

    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=True,
        label="Technician",
        empty_label="Assign to…",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Who can be sent to a job is decided by permission, not by job title
        # and not by whether HR has caught up: a technician is someone who
        # holds their own job list. Filtering on the employee register instead
        # left the dropdown silently empty for a new starter.
        self.fields["assigned_to"].queryset = (
            User.objects.filter(
                is_active=True,
                user_roles__role__permissions__code="view_own_job_list",
            )
            .distinct()
            .order_by("first_name", "last_name")
        )


class TicketStatusForm(forms.ModelForm):
    """
    Status is a row in the Settings-owned list, never a hardcoded enum, so
    Operations can rename or add one without a release.
    """

    class Meta:
        model = Ticket
        fields = ["status", "priority"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].queryset = StatusOption.objects.filter(
            kind=StatusOption.TICKET, is_active=True
        )
