from django import forms
from django.contrib.auth import get_user_model

from hr.models import Employee

from .models import ProjectCrew, ProjectDocument, Requisition, Task

User = get_user_model()


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "assignee", "due_date"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].required = False
        self.fields["assignee"].empty_label = "Unassigned"
        # Offer the crew first: a task on this project is almost always for
        # somebody already on it.
        self.fields["assignee"].queryset = User.objects.filter(
            is_active=True, employee__is_active=True
        ).order_by("first_name", "last_name")


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
