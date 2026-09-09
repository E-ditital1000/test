from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from accounts.services import assignable_roles
from config.models import CorrectionReason

from .models import AttendanceCode, AttendanceCorrection, Employee

User = get_user_model()


class EmployeeForm(forms.ModelForm):
    """
    The register deliberately carries no pay, rate or salary field. Payroll
    is Phase Two and will consume the attendance column contract; it does
    not need a placeholder here.
    """

    class Meta:
        model = Employee
        # No "user": the sign-in account is bound when the person is taken on
        # and never repointed. Moving a record to a different account would
        # silently move somebody's whole attendance history with it.
        fields = [
            "staff_id",
            "job_title",
            "department",
            "phone",
            "supervisor",
            "start_date",
            "is_active",
        ]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "staff_id": "Staff ID",
            "is_active": "Currently employed",
        }
        help_texts = {
            "supervisor": "Whose roll-call they appear on.",
        }

    def clean_supervisor(self):
        supervisor = self.cleaned_data.get("supervisor")
        if supervisor and self.instance.pk and supervisor.pk == self.instance.pk:
            raise forms.ValidationError("An employee cannot be their own supervisor.")
        return supervisor


class AttendanceCorrectionForm(forms.Form):
    """
    A correction is a new record, not an edit. The reason comes from the
    Settings-owned fixed list and cannot be blank — that is what makes the
    attendance record defensible when somebody disputes a day months later.
    """

    action = forms.ChoiceField(
        choices=AttendanceCorrection.ACTIONS,
        initial=AttendanceCorrection.AMEND_TIME,
        label="What is wrong",
    )
    corrected_timestamp = forms.DateTimeField(
        required=False,
        label="Corrected time",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="The time it should have been. Required when amending a time.",
    )
    reason = forms.ModelChoiceField(
        queryset=CorrectionReason.objects.none(),
        label="Reason",
        empty_label="Choose a reason…",
        help_text="Recorded against your name. A correction cannot be saved without one.",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Note (optional)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].queryset = CorrectionReason.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("action") == AttendanceCorrection.AMEND_TIME
            and not cleaned.get("corrected_timestamp")
        ):
            self.add_error("corrected_timestamp", "Amending a time needs the corrected time.")
        return cleaned


class AttendanceCodeForm(forms.ModelForm):
    """
    A code HR prints and posts. The expiry is mandatory and defaulted rather
    than left open: a printed code is a shared secret, and the expiry is the
    only thing that limits one that has been photographed.
    """

    class Meta:
        model = AttendanceCode
        fields = ["description", "location_name", "expires_at"]
        widgets = {"expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}
        labels = {
            "description": "What this code is for",
            "location_name": "Where it will be posted",
            "expires_at": "Stops working at",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Four weeks is long enough not to be a chore and short enough that a
        # photographed code stops being useful within a payroll cycle.
        default = timezone.localtime() + timedelta(days=28)
        self.fields["expires_at"].initial = default.strftime("%Y-%m-%dT%H:%M")
        self.fields["expires_at"].help_text = (
            "After this moment the code stops working and you print a new one. "
            "Keep it short: a photograph of a printed code works as well as the paper."
        )

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at <= timezone.now():
            raise forms.ValidationError(
                "That moment has already passed, so the code would never work."
            )
        return expires_at


class EmployeeOnboardingForm(forms.Form):
    """
    Taking somebody on is one act, so it is one form.

    Before this it was three, spread across two modules: create the sign-in
    account in Settings, create the employee record in HR, then go back and
    grant a role. Worse, the middle step demanded an account that only
    Settings could make -- so the HR person whose job this is could not
    finish it, and it fell to an Admin every time.

    The three sections below are the three questions actually being asked:
    who is this, what do they do, and what may they reach.
    """

    # -- who they are -----------------------------------------------------
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Surname")
    email = forms.EmailField(
        label="Work email",
        help_text="This is what they sign in with. A temporary password is "
        "issued on save and they must replace it at first sign-in.",
    )
    phone = forms.CharField(max_length=40, required=False, label="Phone")

    # -- what they do -----------------------------------------------------
    staff_id = forms.CharField(max_length=30, label="Staff ID")
    job_title = forms.CharField(max_length=80, required=False, label="Job title")
    department = forms.CharField(max_length=80, required=False)
    supervisor = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        label="Supervisor",
        empty_label="Nobody yet",
        help_text="Whose roll-call they appear on.",
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Start date",
    )

    # -- what they may reach ----------------------------------------------
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Access",
        help_text="Everything this person can do comes from the roles ticked "
        "here. Most field staff need only Technician; office staff who just "
        "clock in and out need only Employee.",
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["supervisor"].queryset = Employee.objects.filter(
            is_active=True
        ).select_related("user")
        # You cannot confer power you do not hold, so the list only offers
        # roles this person could grant. An HR user sees Technician and
        # Employee; they do not see Admin, and posting it would be refused
        # server-side anyway.
        self.fields["roles"].queryset = Role.objects.filter(
            pk__in=[r.pk for r in assignable_roles(actor)]
        ) if actor is not None else Role.objects.none()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Somebody already signs in with that address. If they already "
                "work here, edit their record instead of creating a second one."
            )
        return email

    def clean_staff_id(self):
        staff_id = self.cleaned_data["staff_id"].strip()
        if Employee.objects.filter(staff_id__iexact=staff_id).exists():
            raise forms.ValidationError("That staff ID is already on the register.")
        return staff_id
