from django import forms

from config.models import CorrectionReason

from .models import AttendanceCorrection, Employee


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
