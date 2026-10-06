from django import forms
from django.contrib.auth import get_user_model

from config.models import ServiceType, StatusOption

from .models import Contact, Customer, Site, Ticket

User = get_user_model()


class CustomerForm(forms.ModelForm):
    """
    Two customers in one register: a person, and an organisation reached
    through a person. Asking both the same five questions made the second
    kind a two-step job — save the organisation, then go and add the human
    being anyone would actually ring.

    The kind is asked first because it decides what the rest of the form
    means. The page follows the choice as it is made; the labels below are
    set from it as well, so the form is right on a device running no
    JavaScript and after a failed submit.
    """

    # The card around these says what they are for and that they are
    # optional, so the fields themselves do not repeat it.
    contact_name = forms.CharField(max_length=120, required=False, label="Contact person")
    contact_job_title = forms.CharField(max_length=80, required=False, label="Their job title")
    contact_phone = forms.CharField(max_length=40, required=False, label="Their phone")
    contact_email = forms.EmailField(required=False, label="Their email")

    CONTACT_FIELDS = ["contact_name", "contact_job_title", "contact_phone", "contact_email"]

    class Meta:
        model = Customer
        fields = ["kind", "name", "phone", "email", "address", "notes"]
        widgets = {
            "kind": forms.RadioSelect,
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"kind": "What kind of customer is this?"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Radios, not a dropdown: two choices that change the form are a
        # decision to be seen, not a list to be opened.
        self.fields["kind"].choices = Customer.KINDS
        self.fields["kind"].required = True

        if self.kind == Customer.INDIVIDUAL:
            self.fields["name"].label = "Full name"
            self.fields["phone"].help_text = "Their own number."
            self.fields["email"].help_text = "Their own email."
        else:
            self.fields["name"].label = "Organisation name"
            self.fields["phone"].help_text = "The number the office answers."
            self.fields["email"].help_text = "A general address, not one person's."

        # An organisation's contact can be filled in later, but asking here
        # is the difference between a register with names in it and one with
        # switchboard numbers.
        if self.instance.pk and self.instance.contacts.exists():
            for name in self.CONTACT_FIELDS:
                del self.fields[name]

    @property
    def kind(self):
        """
        What the form is being filled in as, before it has validated: the
        posted choice, then the record being edited, then the default.
        """
        posted = self.data.get("kind") if self.is_bound else None
        return posted or getattr(self.instance, "kind", None) or Customer.ORGANISATION

    def detail_fields(self):
        """The customer's own details, rendered as one block."""
        return [self[name] for name in ("name", "phone", "email", "address", "notes")]

    def contact_fields(self):
        """The person to ask for — an organisation's half of the form, and
        absent once they have contacts of their own."""
        return [self[name] for name in self.CONTACT_FIELDS if name in self.fields]

    def clean(self):
        cleaned = super().clean()
        if "contact_name" not in self.fields:
            # Editing a customer who already has contacts; they are managed
            # on their own screen, not re-asked here.
            return cleaned

        if cleaned.get("kind") == Customer.INDIVIDUAL:
            # A person is their own contact. Anything typed under the
            # organisation half is dropped rather than saved out of sight.
            for name in self.CONTACT_FIELDS:
                cleaned[name] = ""
            return cleaned

        named = (cleaned.get("contact_name") or "").strip()
        others = [name for name in self.CONTACT_FIELDS[1:] if cleaned.get(name)]
        if others and not named:
            self.add_error(
                "contact_name",
                "Give the contact's name, or clear their details — a job title "
                "and a number with nobody attached cannot be rung.",
            )
        return cleaned

    def save(self, commit=True):
        customer = super().save(commit=commit)
        if commit:
            self.save_contact(customer)
        return customer

    def save_contact(self, customer):
        """The person to ask for, where one was given. The first contact a
        customer has is their main one."""
        name = (self.cleaned_data.get("contact_name") or "").strip()
        if not name:
            return None
        return Contact.objects.create(
            customer=customer,
            name=name,
            job_title=self.cleaned_data.get("contact_job_title", ""),
            phone=self.cleaned_data.get("contact_phone", ""),
            email=self.cleaned_data.get("contact_email", ""),
            is_primary=not customer.contacts.exists(),
        )


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
        # picking the wrong one. The page swaps the list in when a customer
        # is picked (see site_picker); this queryset is what the posted site
        # is checked against, so a site from another customer is refused.
        customer_id = self.data.get("customer") or getattr(self.instance, "customer_id", None)
        limit_sites_to_customer(self.fields["site"], customer_id, prompt="Which site?")

    def site_picker(self):
        return site_picker_data(self.fields["customer"].queryset, prompt="Which site?")


# --------------------------------------------------------------------------
# Choosing a site
#
# A site belongs to a customer, and both are chosen on the same form — here
# and when scheduling a field job. Shared so the two screens cannot come to
# disagree about which sites a customer has or what to call them.
# --------------------------------------------------------------------------

NO_CUSTOMER_YET = "Choose a customer first"
NO_SITES_YET = "No site on this customer yet"
NO_SITES_HELP = (
    "This customer has no site yet. Add one on the customer record, or leave "
    "it empty and say where in the description."
)


def site_label(site):
    """The customer is already chosen, so the site is named by where it is."""
    address = " ".join(site.address.split())
    return f"{site.name} — {address}" if address else site.name


def limit_sites_to_customer(field, customer_id, *, prompt):
    """
    Point a site field at one customer's sites, and say so in the empty
    option. With no customer there is nothing to offer, so it offers nothing
    rather than every site in the company.

    Optional throughout: a job with no site recorded is worse than one whose
    location is in the notes, but a form that refuses to save without one
    would be answered with any site at all.
    """
    field.queryset = (
        Site.objects.filter(customer_id=customer_id, is_active=True).order_by("name")
        if customer_id
        else Site.objects.none()
    )
    field.required = False
    field.label_from_instance = site_label
    if not customer_id:
        field.empty_label = NO_CUSTOMER_YET
    elif field.queryset.exists():
        field.empty_label = prompt
    else:
        field.empty_label = NO_SITES_YET
    return field.queryset


def site_picker_data(customers, *, prompt, none_yet_help=NO_SITES_HELP):
    """
    Every listed customer's sites, keyed by customer, and the words the
    picker uses — so a page can fill its site list the moment a customer is
    chosen. Without it the list can only fill on submit, and on a create
    form submitting is what creates the record.

    What the page does with this is a convenience. The field's own queryset
    is what a posted site is checked against, so a site belonging to another
    customer is refused however the list was filled.
    """
    sites = {}
    for site in Site.objects.filter(is_active=True, customer__in=customers).order_by("name"):
        sites.setdefault(site.customer_id, []).append(
            {"id": site.pk, "label": site_label(site)}
        )
    return {
        "sites": sites,
        "prompt": prompt,
        "no_customer": NO_CUSTOMER_YET,
        "none_yet": NO_SITES_YET,
        "none_yet_help": none_yet_help,
    }


class TicketAssignForm(forms.Form):
    """
    One action from the ticket list, as the brief requires — and the days it
    is expected to take.

    Handing a ticket over without saying when says only that it is somebody
    else's problem now. The dates are what a technician plans their week
    around and what anybody scheduling around them needs to see, so they are
    asked for here rather than left to a phone call.
    """

    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=True,
        label="Technician",
        empty_label="Assign to…",
    )
    start_date = forms.DateField(
        required=False, label="From",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    due_date = forms.DateField(
        required=False, label="Due by",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        start, due = cleaned.get("start_date"), cleaned.get("due_date")
        if start and due and start > due:
            self.add_error("due_date", "The due date cannot be before the start date.")
        return cleaned

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
