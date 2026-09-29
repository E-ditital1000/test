from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from accounts.templatetags.a1 import a1datetime, a1name
from fieldjobs.models import FieldJob
from hr.models import Employee

from .models import ProjectCrew, ProjectDocument, Requisition, RequisitionItem, Task

User = get_user_model()


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "assignee", "field_job", "start_date", "due_date", "description"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "description": "What needs doing",
            "field_job": "On which visit",
            "start_date": "Starts",
            "due_date": "Due",
        }
        help_texts = {
            "start_date": "The day it can begin. Leave empty if it can start now.",
            "due_date": "The day it must be finished by. Overdue is counted from here.",
            "description": "Read on a phone by whoever picks it up. Which client, "
                           "what to bring, what finished looks like.",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["assignee"].required = False
        self.fields["assignee"].empty_label = "Unassigned"

        # A task on this project goes to somebody on this project. Offering
        # all 38 employees invites picking a name that has nothing to do with
        # the job, and buries the four people who do.
        people = User.objects.filter(is_active=True, employee__is_active=True)
        if project is not None:
            on_project = Q(employee__project_assignments__project=project)
            if project.manager_id:
                on_project |= Q(pk=project.manager_id)
            crewed = people.filter(on_project).distinct()
            # Before a crew is assembled there is nobody to choose, and a
            # form that cannot be used is worse than a long list — so fall
            # back to everyone and say why.
            if crewed.exists():
                people = crewed
                self.fields["assignee"].help_text = (
                    "The crew on this project. Add someone to the crew first "
                    "to assign them work here."
                )
            else:
                self.fields["assignee"].help_text = (
                    "No crew on this project yet, so everyone is listed. "
                    "Assign a crew and this narrows to them."
                )

        self.fields["assignee"].queryset = people.order_by("first_name", "last_name")

        # Which visit this is part of, where it is part of one. Only this
        # project's visits: a checklist for somebody else's day on somebody
        # else's site is not work on this project.
        visits = FieldJob.objects.none() if project is None else (
            FieldJob.objects.filter(project=project)
            .select_related("site", "assigned_to")
            .order_by("scheduled_for")
        )
        self.fields["field_job"].queryset = visits
        self.fields["field_job"].required = False
        # Dates have one form across the system, and a1.py is where it is
        # decided — `%-d` is not even portable off Linux.
        self.fields["field_job"].label_from_instance = lambda job: (
            f"{job.reference} · {a1datetime(job.scheduled_for)}"
            f"{f' · {job.site.name}' if job.site else ''}"
            f" · {a1name(job.assigned_to)}"
        )
        if visits.exists():
            self.fields["field_job"].empty_label = "Not tied to a visit"
            self.fields["field_job"].help_text = (
                "Whoever is on that visit sees it on the job itself, not "
                "buried in a list of everything on the project."
            )
        else:
            self.fields["field_job"].empty_label = "No visit scheduled yet"
            self.fields["field_job"].help_text = (
                "Schedule a field job on this project and you can pin work to it."
            )

    def clean(self):
        """
        A range that ends before it starts is a typo, and saved it would show
        as "12–3 Sept" and count overdue from a day the work could not have
        begun. Caught here rather than left to whoever reads it later.
        """
        cleaned = super().clean()
        start, due = cleaned.get("start_date"), cleaned.get("due_date")
        if start and due and start > due:
            self.add_error("due_date", "The due date cannot be before the start date.")
        return cleaned


class ProjectCrewForm(forms.ModelForm):
    """Crew is drawn from the HR employee register — the single source of
    who works here."""

    class Meta:
        model = ProjectCrew
        fields = ["employee"]

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        available = Employee.objects.filter(is_active=True).select_related("user")
        if project is not None:
            available = available.exclude(project_assignments__project=project)
        self.fields["employee"].queryset = available
        self.fields["employee"].empty_label = "Add someone to the crew…"


class ProjectDocumentForm(forms.ModelForm):
    class Meta:
        model = ProjectDocument
        fields = ["label", "file"]


class RequisitionForm(forms.ModelForm):
    """
    Raised against a project. Whether it needs an Executive is decided by the
    Settings-owned threshold at approval time, not chosen here.
    """

    class Meta:
        model = Requisition
        fields = ["description", "amount", "needed_by"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "needed_by": forms.DateInput(attrs={"type": "date"}),
        }


class TaskCompletionForm(forms.Form):
    """
    Marking a task done. The note is optional but asked for, because "done"
    on its own answers nothing when somebody reads the project back later.
    """

    completion_note = forms.CharField(
        required=False,
        label="What was done (optional)",
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class RequisitionItemForm(forms.ModelForm):
    """
    One row of what the job needs. Every field but the description is
    optional: a technician on a site is asked what is needed, not made to
    price it.
    """

    class Meta:
        model = RequisitionItem
        fields = ["description", "quantity", "unit", "estimated_unit_cost"]
        labels = {
            "description": "What is needed",
            "quantity": "How many",
            "estimated_unit_cost": "Rough cost each",
        }
        widgets = {
            "unit": forms.TextInput(attrs={"placeholder": "lengths, bags, metres"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        self.fields["quantity"].initial = None

    def clean(self):
        cleaned = super().clean()
        description = (cleaned.get("description") or "").strip()
        quantity = cleaned.get("quantity")
        cost = cleaned.get("estimated_unit_cost")

        # A blank row is not an error: the form offers more rows than most
        # requisitions need, and the empty ones are simply dropped.
        if not description:
            if quantity or cost:
                self.add_error("description", "Say what this is.")
            cleaned["DELETE_EMPTY"] = True
            return cleaned

        if quantity is None:
            cleaned["quantity"] = Decimal("1")
        elif quantity <= 0:
            self.add_error("quantity", "A quantity has to be more than nothing.")
        if cost is not None and cost < 0:
            self.add_error("estimated_unit_cost", "A cost cannot be negative.")
        return cleaned


class BaseRequisitionItemFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        filled = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE_EMPTY")
        ]
        if not filled:
            raise forms.ValidationError("List at least one thing the job needs.")
        self.filled = filled


RequisitionItemFormSet = forms.formset_factory(
    RequisitionItemForm, formset=BaseRequisitionItemFormSet, extra=3, max_num=40, validate_max=True
)


class RequisitionRequestForm(forms.ModelForm):
    """
    The header of a request for materials. What it is for and when it is
    needed; the amount is not typed, it is the total of the items.
    """

    class Meta:
        model = Requisition
        fields = ["description", "needed_by"]
        labels = {
            "description": "What is this for",
            "needed_by": "Needed by",
        }
        help_texts = {
            "description": "The work these are for, so whoever approves it knows what they are paying for.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "needed_by": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_needed_by(self):
        needed_by = self.cleaned_data.get("needed_by")
        if needed_by and needed_by < timezone.localdate():
            raise forms.ValidationError("That date has passed.")
        return needed_by
