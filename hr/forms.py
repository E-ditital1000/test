from datetime import timedelta

from django import forms
from django.utils import timezone

from config.models import CorrectionReason

from .models import AttendanceCode, AttendanceCorrection, Employee


class EmployeeForm(forms.ModelForm):
    """
    The register deliberately carries no pay, rate or salary field. Payroll
    is Phase Two and will consume the attendance column contract; it does
    not need a placeholder here.
    """

    class Meta:
        model = Employee
        fields = [
            "user",
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
            "user": "Sign-in account",
        }
        help_texts = {
            "user": "The account this person signs in with.",
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
