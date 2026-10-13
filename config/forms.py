from django import forms

from .models import (
    AssessmentQuestion,
    CompanyDetail,
    PolicySetting,
    ServiceType,
    StatusOption,
)


class ServiceTypeForm(forms.ModelForm):
    class Meta:
        model = ServiceType
        fields = ["code", "name", "description", "order", "is_active"]
        labels = {
            "code": "Short code",
            "order": "Position in the list",
            "is_active": "Offered on new tickets",
        }
        help_texts = {
            "code": "Used in exports and never shown to a customer.",
            "is_active": "Turning this off retires the service without touching its history.",
        }


class AssessmentQuestionForm(forms.ModelForm):
    class Meta:
        model = AssessmentQuestion
        fields = [
            "text",
            "help_text",
            "answer_type",
            "respondent",
            "choices",
            "is_required",
        ]
        labels = {
            "text": "The question",
            "help_text": "Guidance for the technician",
            "answer_type": "Answer type",
            "respondent": "Who answers it",
            "choices": "Choices",
            "is_required": "Must be answered before submitting",
        }
        widgets = {
            "choices": forms.TextInput(
                attrs={"placeholder": '["Yes", "No", "Not applicable"]'}
            ),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("answer_type") == AssessmentQuestion.CHOICE and not cleaned.get("choices"):
            raise forms.ValidationError("A choice question needs at least one option.")
        return cleaned


class StatusOptionForm(forms.ModelForm):
    class Meta:
        model = StatusOption
        fields = ["kind", "code", "label", "order", "is_default", "is_terminal", "is_active"]
        labels = {
            "kind": "Applies to",
            "code": "Short code",
            "label": "What people see",
            "order": "Position in the list",
            "is_default": "Where new records start",
            "is_terminal": "Counts as closed",
            "is_active": "Still offered",
        }


class CompanyDetailForm(forms.ModelForm):
    class Meta:
        model = CompanyDetail
        fields = ["name", "address", "phone", "email", "tax_number", "logo"]
        labels = {"tax_number": "Tax number", "logo": "Logo for invoices"}


class PolicySettingForm(forms.ModelForm):
    """
    A policy value, checked against the shape the code that reads it expects.

    It used to take any string for any key, and the consequence was found on
    the live system: the lateness grace period — a number of minutes — had
    been given "8:30". `int("8:30")` raises, the reader treats an unparsable
    policy as "do not judge lateness", and lateness was silently switched
    off for the whole company. Everybody read as present whatever time they
    arrived, and nothing anywhere said so.

    Failing open is right: a missing policy must not mark a workforce late.
    Failing open *silently*, on a value somebody typed by hand into a free
    text box, is the part that was wrong.
    """

    class Meta:
        model = PolicySetting
        fields = ["key", "value", "description"]
        labels = {"key": "Setting", "value": "Value", "description": "What it controls"}

    def clean(self):
        cleaned = super().clean()
        key = cleaned.get("key")
        value = (cleaned.get("value") or "").strip()
        if not key or not value:
            return cleaned

        checker = getattr(self, f"_check_{key}", None)
        if checker is not None:
            try:
                checker(value)
            except forms.ValidationError as error:
                self.add_error("value", error)
        return cleaned

    # One checker per key, named for it, so adding a policy and forgetting to
    # validate it is visible rather than silent.

    def _check_attendance_workday_start(self, value):
        self._as_time(value, "The workday start is a time of day, like 08:00.")

    def _check_attendance_late_after_minutes(self, value):
        if ":" in value:
            raise forms.ValidationError(
                "This is a number of MINUTES, not a time. For a workday "
                "starting at 08:00 with half an hour's grace, enter 30 — "
                "not 08:30. A time here switches lateness off entirely."
            )
        self._as_int(value, 0, 600, "Minutes of grace, between 0 and 600.")

    def _check_attendance_geofence_radius_m(self, value):
        radius = self._as_int(value, 5, 5000, "Metres, between 5 and 5000.")
        if radius < 50:
            raise forms.ValidationError(
                f"{radius} m is tighter than most phones can place themselves. "
                "A fix is routinely 20–50 m out, so staff standing at the "
                "office will be refused. 150–200 m covers a yard and parking."
            )

    def _check_attendance_office_latitude(self, value):
        self._as_float(value, -90, 90, "A latitude between -90 and 90.")

    def _check_attendance_office_longitude(self, value):
        self._as_float(value, -180, 180, "A longitude between -180 and 180.")

    def _check_leave_rest_days(self, value):
        days = [part.strip() for part in value.split(",") if part.strip()]
        if not days or len(days) == 7:
            raise forms.ValidationError(
                "Weekday numbers, Monday 0 to Sunday 6. A-1 works Monday to "
                "Saturday, so this is 6. Every day a rest day would make "
                "every leave request zero days long."
            )
        for day in days:
            self._as_int(day, 0, 6, "Weekday numbers from 0 (Monday) to 6 (Sunday).")

    def _check_leave_tracked_from(self, value):
        from django.utils.dateparse import parse_date

        if parse_date(value) is None:
            raise forms.ValidationError("A date, written 2026-10-13.")

    def _check_requisition_approval_threshold(self, value):
        self._as_float(value, 0, 10 ** 12, "An amount, like 100000.")

    # -- the small checks the above are written in -------------------------

    def _as_int(self, value, low, high, message):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise forms.ValidationError(message)
        if not low <= number <= high:
            raise forms.ValidationError(message)
        return number

    def _as_float(self, value, low, high, message):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise forms.ValidationError(message)
        if not low <= number <= high:
            raise forms.ValidationError(message)
        return number

    def _as_time(self, value, message):
        parts = value.split(":")
        if len(parts) < 2:
            raise forms.ValidationError(message)
        hour = self._as_int(parts[0], 0, 23, message)
        minute = self._as_int(parts[1], 0, 59, message)
        return hour, minute
