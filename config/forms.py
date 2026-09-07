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
    class Meta:
        model = PolicySetting
        fields = ["key", "value", "description"]
        labels = {"key": "Setting", "value": "Value", "description": "What it controls"}
