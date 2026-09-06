from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm

from .models import Role
from .permission_registry import PERMISSIONS

User = get_user_model()


class EmailLoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )


class ForcedPasswordResetForm(SetPasswordForm):
    """Used on the forced reset at first login; identical rules thereafter."""


class UserForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("An account already uses this email address.")
        return email


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        existing = Role.objects.filter(name__iexact=name)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("A role with this name already exists.")
        return name


def grouped_permissions(current=None):
    """
    The permission list grouped by module for the role editor, with each
    row already carrying whether this role grants it and at what scope.
    Built here rather than in the template so the editor cannot drift from
    the frozen registry.
    """
    current = current or {}
    groups = {}
    for code, module, description in PERMISSIONS:
        groups.setdefault(module, []).append(
            {
                "code": code,
                "description": description,
                "checked": code in current,
                "scope": current.get(code, "all"),
            }
        )
    return sorted(groups.items())
