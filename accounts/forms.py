import io
import re
from datetime import date
from urllib.parse import urlparse

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.files.base import ContentFile

from .models import Profile, Role
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
    """
    An account, and — where the person works here — their place on the staff
    register in the same act.

    These were two screens, and the gap between them made half a person: an
    account carrying the Employee role but no employment record, which cannot
    clock in, does not appear on a roll-call and is not on any report, while
    looking perfectly complete on this list. Whoever filled in the first
    screen had no reason to know a second one existed.
    """

    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    # -- and are they on the staff register --------------------------------
    is_employee = forms.BooleanField(
        required=False,
        label="This person works here",
        help_text="Puts them on the staff register so they can clock in and "
                  "appear on a roll-call. Leave it off for an account that is "
                  "not a member of staff.",
    )
    staff_id = forms.CharField(max_length=30, required=False, label="Staff ID")
    job_title = forms.CharField(max_length=80, required=False, label="Job title")
    department = forms.ModelChoiceField(
        queryset=None, required=False, label="Department", empty_label="Not set"
    )
    phone = forms.CharField(max_length=40, required=False, label="Phone")
    supervisor = forms.ModelChoiceField(
        queryset=None, required=False, label="Supervisor",
        empty_label="Nobody yet", help_text="Whose roll-call they appear on.",
    )
    start_date = forms.DateField(
        required=False, label="Start date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    EMPLOYEE_FIELDS = [
        "is_employee", "staff_id", "job_title", "department", "phone",
        "supervisor", "start_date",
    ]

    class Meta:
        model = User
        fields = ["first_name", "middle_name", "last_name", "email", "is_active"]
        labels = {"middle_name": "Middle name"}
        help_texts = {"middle_name": "Optional. Shown in full on the employee register."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from hr.models import Department, Employee

        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["supervisor"].queryset = Employee.objects.filter(
            is_active=True
        ).select_related("user")

        # Somebody already on the register is managed in HR, where the rest
        # of their employment lives. Asking again here would be two places to
        # change one thing.
        self.already_on_register = bool(
            self.instance.pk and Employee.objects.filter(user=self.instance).exists()
        )
        if self.already_on_register:
            for name in self.EMPLOYEE_FIELDS:
                del self.fields[name]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("An account already uses this email address.")
        return email

    def clean_staff_id(self):
        from hr.models import Employee

        staff_id = (self.cleaned_data.get("staff_id") or "").strip()
        if staff_id and Employee.objects.filter(staff_id__iexact=staff_id).exists():
            raise forms.ValidationError("That staff ID already belongs to somebody.")
        return staff_id

    def clean(self):
        cleaned = super().clean()
        if "is_employee" not in self.fields:
            return cleaned
        if cleaned.get("is_employee") and not cleaned.get("staff_id"):
            self.add_error(
                "staff_id",
                "A staff ID is what the register is keyed on, so somebody on "
                "it needs one.",
            )
        return cleaned

    def employee_fields(self):
        """The register half, rendered as its own block."""
        return [self[name] for name in self.EMPLOYEE_FIELDS if name in self.fields]

    def account_fields(self):
        return [self[name] for name in
                ("first_name", "middle_name", "last_name", "email", "is_active")]


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


# --------------------------------------------------------------------------
# My profile
# --------------------------------------------------------------------------

PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,24}$")

# The host a link must be on, per network. Checked so a mistyped or
# misleading link cannot sit under a trusted label.
SOCIAL_HOSTS = {
    "linkedin_url": ("linkedin.com",),
    "x_url": ("x.com", "twitter.com"),
    "facebook_url": ("facebook.com", "fb.com"),
}


def _on_host(url, hosts):
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def _age_on(born, today):
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


class ProfileForm(forms.ModelForm):
    GROUPS = {
        "personal": ["date_of_birth", "show_birthday", "personal_phone", "address", "bio"],
        "emergency": [
            "emergency_contact_name", "emergency_contact_relationship",
            "emergency_contact_phone",
        ],
        "social": ["linkedin_url", "x_url", "facebook_url", "website_url"],
    }

    class Meta:
        model = Profile
        fields = [
            "date_of_birth", "show_birthday", "personal_phone", "address", "bio",
            "emergency_contact_name", "emergency_contact_relationship",
            "emergency_contact_phone",
            "linkedin_url", "x_url", "facebook_url", "website_url",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "personal_phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
            "emergency_contact_phone": forms.TextInput(attrs={"type": "tel"}),
            "address": forms.TextInput(attrs={"autocomplete": "street-address"}),
            "bio": forms.Textarea(attrs={"rows": 3, "maxlength": 280}),
        }
        help_texts = {
            "bio": "A line or two colleagues would find useful. 280 characters.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.GROUPS["social"]:
            field = self.fields[name]
            # People paste "linkedin.com/in/someone" without the scheme.
            field.assume_scheme = "https"
            field.widget.attrs.update({"inputmode": "url", "placeholder": "https://"})
        self.fields["date_of_birth"].widget.attrs["max"] = date.today().isoformat()

    def personal_fields(self):
        return [self[name] for name in self.GROUPS["personal"]]

    def emergency_fields(self):
        return [self[name] for name in self.GROUPS["emergency"]]

    def social_fields(self):
        return [self[name] for name in self.GROUPS["social"]]

    def clean_date_of_birth(self):
        born = self.cleaned_data.get("date_of_birth")
        if born is None:
            return born
        today = date.today()
        if born >= today:
            raise forms.ValidationError("A date of birth has to be in the past.")
        age = _age_on(born, today)
        if age < 16 or age > 100:
            raise forms.ValidationError(
                "Check the year: that would make you {} years old.".format(age)
            )
        return born

    def _clean_phone(self, name):
        value = " ".join(self.cleaned_data.get(name, "").split())
        if value and not PHONE.match(value):
            raise forms.ValidationError("Use digits and spaces, with an optional + at the start.")
        return value

    def clean_personal_phone(self):
        return self._clean_phone("personal_phone")

    def clean_emergency_contact_phone(self):
        return self._clean_phone("emergency_contact_phone")

    def clean(self):
        cleaned = super().clean()
        for name in self.GROUPS["social"]:
            url = cleaned.get(name)
            if url and urlparse(url).scheme not in ("http", "https"):
                self.add_error(name, "Links must start with https://")
            elif url and name in SOCIAL_HOSTS and not _on_host(url, SOCIAL_HOSTS[name]):
                self.add_error(name, "That is not a {} address.".format(SOCIAL_HOSTS[name][0]))

        # Half an emergency contact is worse than none: a name nobody can
        # ring, or a number nobody can put a name to.
        contact = cleaned.get("emergency_contact_name")
        phone = cleaned.get("emergency_contact_phone")
        if contact and not phone and "emergency_contact_phone" not in self.errors:
            self.add_error("emergency_contact_phone", "Add a number for {}.".format(contact))
        if phone and not contact:
            self.add_error("emergency_contact_name", "Whose number is this?")
        return cleaned


class ProfilePhotoForm(forms.Form):
    """
    The photo is re-encoded rather than stored as uploaded: a square 400px
    JPEG of a few tens of kilobytes, which a technician's phone can load over
    a weak signal. Re-encoding also drops the EXIF block, and with it the GPS
    position a phone camera writes into every picture. A photo taken at home
    would otherwise tell anyone who downloads it where the person lives.
    """

    MAX_BYTES = 8 * 1024 * 1024
    MAX_PIXELS = 60_000_000
    SIZE = 400
    ACCEPT = "image/jpeg,image/png,image/webp"

    photo = forms.ImageField(
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
        help_text="JPEG, PNG or WebP, up to 8 MB. It is cropped to a square.",
    )

    def clean_photo(self):
        from PIL import Image, ImageOps

        upload = self.cleaned_data["photo"]
        if upload.size > self.MAX_BYTES:
            raise forms.ValidationError("That photo is over 8 MB. Try a smaller one.")
        try:
            upload.seek(0)
            image = Image.open(upload)
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise forms.ValidationError("Use a JPEG, PNG or WebP photo.")
            # Checked before decoding: a tiny file can declare a huge canvas.
            if image.width * image.height > self.MAX_PIXELS:
                raise forms.ValidationError("That image is too large to process.")
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA", "P"):
                # A transparent PNG goes onto white, not black.
                image = image.convert("RGBA")
                ground = Image.new("RGB", image.size, (255, 255, 255))
                ground.paste(image, mask=image.split()[-1])
                image = ground
            else:
                image = image.convert("RGB")
            image = ImageOps.fit(image, (self.SIZE, self.SIZE), Image.Resampling.LANCZOS)
        except forms.ValidationError:
            raise
        except Exception:
            raise forms.ValidationError("That file could not be read as a photo.")

        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85, optimize=True)
        return ContentFile(out.getvalue(), name="photo.jpg")


class OwnPasswordChangeForm(PasswordChangeForm):
    """
    Asks for the current password, unlike the forced first-sign-in reset.
    Somebody who finds a colleague's unlocked screen must not be able to take
    the account over by choosing a new password on it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        old = self.fields["old_password"].widget.attrs
        old["autocomplete"] = "current-password"
        old.pop("autofocus", None)
        for name in ("new_password1", "new_password2"):
            self.fields[name].widget.attrs["autocomplete"] = "new-password"
