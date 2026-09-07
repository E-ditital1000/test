from django import forms
from django.contrib.auth import get_user_model

from config.models import ServiceType
from crm.models import Customer, Site

from hr.models import Employee

from .models import FieldJob

User = get_user_model()


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
            self.fields["site"].queryset = Site.objects.filter(
                customer=project.customer, is_active=True
            )
            self.fields["site"].initial = project.site_id
        else:
            self.fields["site"].queryset = Site.objects.filter(is_active=True)
        self.fields["site"].required = False
