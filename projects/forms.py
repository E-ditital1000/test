from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from accounts.templatetags.a1 import a1datetime, a1name
from fieldjobs.models import FieldJob
from hr import leave
from hr.models import Employee

from .models import Project, ProjectCrew, ProjectDocument, Requisition, RequisitionItem, Task

User = get_user_model()


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            "title", "assignee", "field_job",
            "start_date", "start_time", "due_date", "due_time",
            "description",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            # A native time input: a phone offers its own picker, and nobody
            # has to be taught a format.
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "due_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "description": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "description": "What needs doing",
            "field_job": "On which visit",
            "start_date": "Starts",
            "start_time": "Start time",
            "due_date": "Due",
            "due_time": "Due time",
        }
        help_texts = {
            "start_date": "The day it can begin. Leave empty if it can start now.",
            "due_date": "The day it must be finished by. Overdue is counted from here.",
            # The times are the optional half of the answer. Work here runs
            # from twenty minutes to a fortnight, and a form that only holds
            # days makes "today to today" the answer for something needed
            # before a ten o'clock visit.
            "start_time": "Optional. Leave empty if the day is enough.",
            "due_time": "Optional. Given one, overdue is counted to the minute "
                        "rather than at midnight.",
            "description": "Read on a phone by whoever picks it up. Which client, "
                           "what to bring, what finished looks like.",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["assignee"].required = False
        self.fields["assignee"].empty_label = "Unassigned"

        # Who can be given work is who can sign in and see it. It used to be
        # who had a record in the HR register as well, which reads as the
        # same thing and is not: on a system whose register has not been
        # filled in yet — a new one, or the morning after go-live — the list
        # was empty and no task could be assigned to anybody at all. Nothing
        # said why, because an empty dropdown looks like a dropdown.
        #
        # The register is HR's record of employment. Assignment is about who
        # holds an account, which is also what every screen that shows a
        # person their own work reads from.
        people = User.objects.filter(is_active=True)
        if project is not None:
            on_project = Q(employee__project_assignments__project=project)
            if project.manager_id:
                on_project |= Q(pk=project.manager_id)
            crewed = people.filter(on_project).distinct()
            # Narrowed to the crew where there is one, because offering forty
            # names buries the four who are on the job. Never narrowed to
            # nothing.
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

        # Marked, not filtered: somebody on leave can still be given a task
        # that starts after they are back, and a scheduler who cannot see the
        # person they need will work outside the system. See
        # hr.leave.away_labels.
        away = leave.away_labels()
        self.fields["assignee"].label_from_instance = lambda user: leave.mark_away(
            a1name(user) or user.email, user.pk, away
        )

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

        The times are checked in the same place, because they are the same
        question asked more precisely.
        """
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        start_time = cleaned.get("start_time")
        due_date = cleaned.get("due_date")
        due_time = cleaned.get("due_time")

        # A time with no day is a time on no particular day, which the
        # system cannot act on and nobody can read.
        if start_time and not start_date:
            self.add_error("start_date", "Give the day this starts, or clear the start time.")
        if due_time and not due_date:
            self.add_error("due_date", "Give the day this is due, or clear the due time.")

        if start_date and due_date:
            if due_date < start_date:
                self.add_error("due_date", "The due date cannot be before the start date.")
            elif (
                due_date == start_date
                and start_time and due_time and due_time < start_time
            ):
                self.add_error(
                    "due_time",
                    "On the same day, the due time cannot be before the start time.",
                )
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


class ProjectForm(forms.ModelForm):
    """
    A project that did not come from a ticket.

    A ticket is a small job — a customer rings, or it comes off the day's
    assignments — and most projects do grow out of one. A contract does not:
    fifty kilowatts of solar across twelve health facilities over six months,
    signed with an institution, never was a service call. Routing it through
    a ticket would have put a fiction at the head of the job, and the first
    thing anybody read about a six-month contract would have been an invented
    phone call.

    So this mints its own job reference and stands on its own. Everything
    downstream — field jobs, requisitions, invoices — joins it exactly as it
    joins a project that was converted, because they carry the project's
    reference either way.
    """

    class Meta:
        model = Project
        fields = [
            "name", "customer", "site", "service_type", "status", "manager",
            "start_date", "target_end_date", "description",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "target_end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "name": "What is the contract",
            "site": "Main site",
            "service_type": "Service type",
            "start_date": "Starts",
            "target_end_date": "Due to finish",
            "description": "What was agreed",
        }
        help_texts = {
            "name": "As somebody would say it out loud — "
                    "“CRS: 50 kW solar at 12 health facilities”.",
            "site": "Where the work is centred, if anywhere. Work that spans "
                    "several sites records each one on its own field job.",
            "manager": "Who owns it. They see it on their own project list.",
            "target_end_date": "What was agreed with the customer, not a guess "
                               "at when it will actually finish.",
        }

    # Who is on it, asked on the page where the project is made.
    #
    # A-1's fifth item: "Creating project you should add the crew on the same
    # page." Crew was a second screen you reached after saving, and the cost
    # of that was not only the extra click — the task form narrows its
    # assignee list to the crew, so a project created with nobody on it
    # offered every account in the company and the four people actually on
    # the job were buried.
    crew = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Crew on this project",
        help_text="Who is on the job. Tasks are assigned from this list, and "
                  "they see the project on their own. Leave empty and add "
                  "people later from the project itself.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from config.models import ServiceType, StatusOption
        from crm.models import Customer

        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["crew"].queryset = (
            Employee.objects.filter(is_active=True)
            .select_related("user")
            .order_by("staff_id")
        )
        # Editing an existing project shows who is already on it, so the box
        # is the current crew rather than an empty list that would read as
        # "nobody" and strip them on the next save.
        if self.instance.pk:
            self.fields["crew"].initial = list(
                self.instance.crew.values_list("employee_id", flat=True)
            )
        # Marked, not filtered — the rule every other picker here follows.
        # Somebody on leave next week can still be crewed on a six-month
        # contract, and a form that refuses gets worked around.
        away = leave.away_labels()
        self.fields["crew"].label_from_instance = lambda employee: leave.mark_away(
            f"{employee.staff_id} · {employee.full_name}", employee.user_id, away
        )
        self.fields["service_type"].queryset = ServiceType.objects.filter(is_active=True)

        statuses = StatusOption.objects.filter(kind=StatusOption.PROJECT, is_active=True)
        self.fields["status"].queryset = statuses
        default = statuses.filter(is_default=True).first()
        if default is not None and not self.instance.pk:
            self.fields["status"].initial = default.pk

        self.fields["manager"].queryset = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name"
        )
        self.fields["manager"].empty_label = "Nobody yet"
        self.fields["manager"].required = False

        # The site is chosen from this customer's own, the same rule every
        # other screen follows. Nothing is offered until a customer is.
        from crm.forms import limit_sites_to_customer

        customer_id = (
            self.data.get("customer")
            or getattr(self.instance, "customer_id", None)
        )
        limit_sites_to_customer(
            self.fields["site"], customer_id, prompt="Which site?"
        )

    def sync_crew(self, project, *, actor):
        """
        Make the project's crew match what the form says, and report the
        change so the screen can say what happened.

        A row removed here is an assignment, not a record of work: the tasks
        somebody was given stay on them, and the requisitions they raised
        stay on the project. Taking somebody off the crew stops them seeing
        a project they are no longer on, and nothing else.
        """
        chosen = list(self.cleaned_data.get("crew") or [])
        chosen_ids = {employee.pk for employee in chosen}
        existing = {row.employee_id: row for row in project.crew.all()}

        removed = 0
        for employee_id, row in existing.items():
            if employee_id not in chosen_ids:
                row.delete()
                removed += 1

        added = 0
        for employee in chosen:
            if employee.pk not in existing:
                ProjectCrew.objects.create(
                    project=project, employee=employee, assigned_by=actor
                )
                added += 1
        return added, removed

    def site_picker(self):
        from crm.forms import site_picker_data

        return site_picker_data(
            self.fields["customer"].queryset, prompt="Which site?"
        )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("target_end_date")
        if start and end and start > end:
            self.add_error(
                "target_end_date",
                "A contract cannot be due to finish before it starts.",
            )
        return cleaned
