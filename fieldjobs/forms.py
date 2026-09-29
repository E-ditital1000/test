from django import forms
from django.contrib.auth import get_user_model

from config.models import ServiceType
from crm.forms import limit_sites_to_customer, site_picker_data
from crm.models import Customer

from hr.models import Employee

from .models import FieldJob

User = get_user_model()

# A job with no site is worse here than on a ticket: it is a crew driving to
# an address nobody wrote down. Where the customer has no site on record the
# form says so, and says what to do instead.
NO_SITES_HELP_HERE = (
    "This customer has no site yet. Add one on the customer record, or leave "
    "it empty and put the location in the instructions."
)


class FieldJobForm(forms.ModelForm):
    """
    Putting a job on a technician's phone. Service type is what decides the
    assessment questions they will answer on site, so it is carried from the
    project rather than chosen again where possible.
    """

    class Meta:
        model = FieldJob
        fields = [
            "customer",
            "site",
            "service_type",
            "assigned_to",
            "scheduled_for",
            "instructions",
        ]
        widgets = {
            "scheduled_for": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "instructions": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"assigned_to": "Lead technician"}
        help_texts = {
            "service_type": "Decides which assessment questions appear on the phone.",
            "assigned_to": "Accountable for the visit, and the only one who submits its assessment.",
        }

    crew = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Others on site",
        help_text="They see the job on their own phone and can check in. "
                  "The assessment stays with the lead. Leave empty for a one-person visit.",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["crew"].queryset = Employee.objects.filter(
            is_active=True
        ).select_related("user").order_by("staff_id")
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)

        # Where the visit is. Only ever this customer's sites: scheduled
        # against a project the customer comes with it, and on a job of its
        # own the customer is chosen on this form and the page follows it.
        # Offering every site in the company let a job be booked at another
        # customer's address, which is a crew sent to the wrong town.
        if project is not None:
            self.customer_id = project.customer_id
        else:
            self.customer_id = (
                self.data.get("customer") or getattr(self.instance, "customer_id", None)
            )
        sites = limit_sites_to_customer(
            self.fields["site"], self.customer_id, prompt="Where is this visit?"
        )
        # Said on the page as well as by the empty option, because a job
        # scheduled from a project never picks a customer and so never gets
        # the version the page swaps in.
        self.fields["site"].help_text = (
            NO_SITES_HELP_HERE if self.customer_id and not sites.exists()
            else "Where the visit is. Sites come from the customer's record."
        )
        # Most customers have exactly one place work happens. Choosing it
        # from a list of one is a step that only makes it likelier a job
        # goes out with no location on it.
        if not self.is_bound and sites.count() == 1:
            self.fields["site"].initial = sites[0].pk
        self.fields["service_type"].queryset = ServiceType.objects.filter(is_active=True)
        # Who can be sent to a job is a permission, not a job title.
        self.fields["assigned_to"].queryset = (
            User.objects.filter(
                is_active=True,
                user_roles__role__permissions__code="view_own_job_list",
            )
            .distinct()
            .order_by("first_name", "last_name")
        )
        self.fields["assigned_to"].empty_label = "Choose a technician…"

        if project is not None:
            # Mark the people already on this project, rather than filtering
            # the rest out. A supervisor sending an extra body to a site is
            # ordinary, and a form that refuses it would be worked around by
            # not using the form.
            on_project = set(
                project.crew.values_list("employee__user_id", flat=True)
            )
            if project.manager_id:
                on_project.add(project.manager_id)

            def mark_user(user):
                name = user.get_full_name() or user.email
                return f"{name} — on this project" if user.pk in on_project else name

            def mark_employee(employee):
                label = f"{employee.staff_id} · {employee.full_name}"
                return f"{label} — on this project" if employee.user_id in on_project else label

            self.fields["assigned_to"].label_from_instance = mark_user
            self.fields["crew"].label_from_instance = mark_employee

            # Scheduled against a project: the customer, site and service type
            # come with it, so they are shown but not re-asked.
            for name in ("customer", "service_type"):
                self.fields[name].initial = getattr(project, f"{name}_id")
                self.fields[name].disabled = True
            # Where the project says the work is, if it says. Where it does
            # not, the single site picked above still stands.
            if project.site_id:
                self.fields["site"].initial = project.site_id

    def site_picker(self):
        """
        The sites the page may offer. Against a project that is one
        customer's; on a job of its own it is every customer's, because the
        customer is still being chosen.
        """
        customers = (
            [self.project.customer] if self.project is not None
            else self.fields["customer"].queryset
        )
        return site_picker_data(
            customers, prompt="Where is this visit?", none_yet_help=NO_SITES_HELP_HERE
        )
