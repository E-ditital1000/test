"""
The leave forms.

The request form does the arithmetic the person would otherwise do wrong:
it counts the working days the dates actually consume, refuses a span that
is all rest days, catches a request that overlaps one already live, and asks
for a sick note at the point the statute says one is needed rather than
after the fact.

Everything it refuses, it refuses with something the person can act on. A
form that says "invalid" about a date range teaches people to stop using the
form.
"""
from django import forms
from django.utils import timezone

from . import leave
from .models import (
    Employee,
    LeaveAdjustment,
    LeaveEntitlementRule,
    LeaveRequest,
    LeaveType,
    PublicHoliday,
)


class LeaveRequestForm(forms.ModelForm):
    """
    Putting in for leave. `for_employee` is set when somebody is asking for
    themselves; HR raising one on a person's behalf gets the picker instead.
    """

    class Meta:
        model = LeaveRequest
        fields = [
            "employee",
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "document",
            "handover_note",
            "contact_while_away",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
            "handover_note": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "start_date": "First day away",
            "end_date": "Last day away",
            "reason": "Why the time is needed",
            "document": "Supporting document",
            "handover_note": "Handover",
            "contact_while_away": "Reachable on",
        }
        help_texts = {
            "handover_note": "What is outstanding, and who is picking it up. "
                             "This is what stops work stalling while you are away.",
            "contact_while_away": "A number, if you can be reached. Leave blank if not.",
        }

    def __init__(self, *args, for_employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.for_employee = for_employee

        types = LeaveType.objects.filter(is_active=True).prefetch_related(
            "entitlement_rules"
        )
        self.fields["leave_type"].queryset = types
        self.fields["leave_type"].empty_label = "Choose a kind of leave…"
        self.fields["leave_type"].help_text = (
            "Kept in HR → Leave settings." if types.exists()
            else "No leave types yet — add them in HR → Leave settings."
        )

        if for_employee is not None:
            # Asking for themselves: the register row is not theirs to pick.
            del self.fields["employee"]
        else:
            self.fields["employee"].queryset = Employee.objects.filter(
                is_active=True
            ).select_related("user")
            self.fields["employee"].empty_label = "Choose an employee…"
            self.fields["employee"].help_text = (
                "Whose leave this is. The request stays theirs, and they are "
                "told the outcome."
            )
            self.fields["employee"].label_from_instance = (
                lambda employee: f"{employee.staff_id} · {employee.full_name}"
            )

    @property
    def employee_in_question(self):
        return self.for_employee or self.cleaned_data.get("employee")

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        leave_type = cleaned.get("leave_type")
        employee = self.for_employee or cleaned.get("employee")

        if not (start and end):
            return cleaned

        if end < start:
            self.add_error(
                "end_date", "The last day cannot be before the first day away."
            )
            return cleaned

        counted = leave.count_working_days(start, end)
        if counted == 0:
            self.add_error(
                "start_date",
                "Every day in that span is a rest day or a public holiday, so "
                "there is no leave to ask for.",
            )
            return cleaned
        cleaned["working_days"] = counted

        if employee is None or leave_type is None:
            return cleaned

        clashes = leave.overlapping_requests(
            employee, start, end, exclude=self.instance if self.instance.pk else None
        )
        if clashes:
            clash = clashes[0]
            self.add_error(
                "start_date",
                f"This overlaps {clash.leave_type.label} leave already "
                f"{'approved' if clash.is_approved else 'waiting'} for "
                f"{clash.start_date:%d %b} to {clash.end_date:%d %b %Y}. "
                "Withdraw that one first, or pick dates outside it.",
            )
            return cleaned

        self._check_document(cleaned, employee, leave_type, counted)
        return cleaned

    def _check_document(self, cleaned, employee, leave_type, counted):
        """
        Ask for the document at the point policy requires it.

        Sick leave is the case: the statute allows three undocumented days in
        a year, so the fourth is where the note is asked for — not the first,
        which would be stricter than the law, and not never, which would make
        the record indefensible.
        """
        threshold = leave_type.document_required_after_days
        if threshold is None or cleaned.get("document"):
            return

        already = leave.days_taken(employee, leave_type)
        if already + counted <= threshold:
            return

        left = max(threshold - already, 0)
        self.add_error(
            "document",
            f"{leave_type.label} leave needs a supporting document beyond "
            f"{threshold} day{'' if threshold == 1 else 's'} a year. "
            + (
                f"You have used {already} and can take {left} more without "
                f"one, but this request is for {counted}."
                if left
                else f"You have already used {already} this year, so this "
                     "request needs one."
            ),
        )


class LeaveTypeForm(forms.ModelForm):
    """The HR-owned list behind the leave type dropdown."""

    class Meta:
        model = LeaveType
        fields = [
            "label",
            "code",
            "description",
            "is_paid",
            "document_required_after_days",
            "order",
            "is_active",
        ]
        labels = {
            "code": "Short code",
            "is_paid": "Paid leave",
            "document_required_after_days": "Document needed after",
            "order": "Position in the list",
            "is_active": "Still offered",
        }
        help_texts = {
            "code": "Used in exports and reports. Not shown to staff.",
            "document_required_after_days": "Days a year allowed without a "
                                            "supporting document. Leave empty "
                                            "if one is never needed.",
            "is_active": "Turning this off stops it being offered on new "
                         "requests and leaves every recorded request alone.",
        }

    def clean_label(self):
        label = " ".join(self.cleaned_data["label"].split())
        existing = LeaveType.objects.filter(label__iexact=label)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError(
                "There is already a leave type with that name."
            )
        return label


class LeaveEntitlementRuleForm(forms.ModelForm):
    """One band of an allowance: after this long, this many days."""

    class Meta:
        model = LeaveEntitlementRule
        fields = ["leave_type", "after_months", "days"]
        labels = {
            "after_months": "After this many months of service",
            "days": "Working days a year",
        }
        help_texts = {
            "after_months": "0 applies from somebody's first day.",
            "days": "A-1 works a six-day week, so a statutory week is six days here.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["leave_type"].queryset = LeaveType.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        leave_type = cleaned.get("leave_type")
        months = cleaned.get("after_months")
        if leave_type is None or months is None:
            return cleaned
        existing = LeaveEntitlementRule.objects.filter(
            leave_type=leave_type, after_months=months
        )
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            self.add_error(
                "after_months",
                f"{leave_type.label} already has a band at {months} months. "
                "Edit that one instead of adding a second.",
            )
        return cleaned


class PublicHolidayForm(forms.ModelForm):
    """
    One day nobody is expected in. Entered per year because several of
    Liberia's move, and any of them can be shifted by proclamation.
    """

    class Meta:
        model = PublicHoliday
        fields = ["date", "name"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
        labels = {"name": "What it is"}

    def clean_date(self):
        day = self.cleaned_data["date"]
        existing = PublicHoliday.objects.filter(date=day)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError(
                f"{day:%d %b %Y} is already on the list as "
                f"{existing.first().name}."
            )
        return day


class LeaveAdjustmentForm(forms.ModelForm):
    """
    Correcting a balance by hand, with a reason that goes on the record.

    This is how go-live works: A-1's leave records before the system are not
    reconstructable, so HR states the position instead of the system
    inventing history for it.
    """

    class Meta:
        model = LeaveAdjustment
        fields = ["employee", "leave_type", "leave_year", "days", "reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}
        labels = {
            "leave_year": "Leave year",
            "days": "Days",
            "reason": "Why",
        }
        help_texts = {
            "days": "Positive gives days, negative takes them away. "
                    "-4 records four days already taken before go-live.",
            "reason": "Shown on the balance screen beside the figure. "
                      "Say where it came from.",
        }

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(
            is_active=True
        ).select_related("user")
        self.fields["employee"].label_from_instance = (
            lambda row: f"{row.staff_id} · {row.full_name}"
        )
        self.fields["leave_type"].queryset = LeaveType.objects.filter(is_active=True)
        self.fields["leave_year"].initial = timezone.localdate().year
        if employee is not None:
            self.fields["employee"].initial = employee.pk

    def clean_days(self):
        days = self.cleaned_data["days"]
        if days == 0:
            raise forms.ValidationError(
                "An adjustment of zero days changes nothing. Leave it out."
            )
        return days

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError(
                "An adjustment without a reason is a number nobody can defend "
                "later. Say where it came from."
            )
        return reason
