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


class CompanyDetailForm(forms.ModelForm):
    class Meta:
        model = CompanyDetail
        fields = ["name", "address", "phone", "email", "tax_number", "logo"]


class PolicySettingForm(forms.ModelForm):
    class Meta:
        model = PolicySetting
        fields = ["key", "value", "description"]
