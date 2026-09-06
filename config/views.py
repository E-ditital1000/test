"""
The configuration half of Settings: service types and their assessment
question sets, the ticket/project status lists, company details and the
policy values. Everything here exists so that adding a service or changing
the approval threshold is an afternoon in Settings, not a release.
"""
from django.contrib import messages
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts import audit
from accounts.decorators import require_permission

from .forms import (
    AssessmentQuestionForm,
    CompanyDetailForm,
    PolicySettingForm,
    ServiceTypeForm,
    StatusOptionForm,
)
from .models import (
    AssessmentQuestion,
    CompanyDetail,
    CorrectionReason,
    PolicySetting,
    ServiceType,
    StatusOption,
)


# --------------------------------------------------------------------------
# Service types and their question sets
# --------------------------------------------------------------------------

@require_permission("manage_service_types")
def service_types(request):
    return render(
        request,
        "config/service_types.html",
        {"service_types": ServiceType.objects.all()},
    )


@require_permission("manage_service_types")
def service_type_edit(request, pk=None):
    instance = get_object_or_404(ServiceType, pk=pk) if pk else None
    form = ServiceTypeForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        before = audit.snapshot(instance, fields=["code", "name", "is_active"])
        obj = form.save()
        audit.record_change(
            actor=request.user,
            action="service_type.saved",
            target=obj,
            before=before,
            after=audit.snapshot(obj, fields=["code", "name", "is_active"]),
        )
        messages.success(request, "Service type saved.")
        return redirect("settings-service-type-questions", pk=obj.pk)
    return render(request, "config/service_type_form.html", {"form": form, "instance": instance})


@require_permission("manage_service_types")
def service_type_questions(request, pk):
    """
    The assessment question set for one service type. Questions are added,
    reordered and retired here — never deleted, so historical answers keep
    resolving to the question that was asked.
    """
    service_type = get_object_or_404(ServiceType, pk=pk)
    form = AssessmentQuestionForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.service_type = service_type
        next_order = service_type.questions.aggregate(m=Max("order"))["m"]
        question.order = (next_order or 0) + 1
        question.save()
        audit.record_change(
            actor=request.user,
            action="assessment_question.added",
            target=question,
            after=audit.snapshot(question, fields=["text", "answer_type", "respondent"]),
        )
        messages.success(request, "Question added.")
        return redirect("settings-service-type-questions", pk=service_type.pk)

    return render(
        request,
        "config/question_set.html",
        {
            "service_type": service_type,
            "questions": service_type.questions.all(),
            "form": form,
        },
    )


@require_permission("manage_service_types")
def question_retire(request, pk):
    question = get_object_or_404(AssessmentQuestion, pk=pk)
    if request.method == "POST":
        before = audit.snapshot(question, fields=["text", "retired_at"])
        question.retire(actor=request.user)
        audit.record_change(
            actor=request.user,
            action="assessment_question.retired",
            target=question,
            before=before,
            after=audit.snapshot(question, fields=["text", "retired_at"]),
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, "Question retired. Existing answers are unchanged.")
    return redirect("settings-service-type-questions", pk=question.service_type_id)


@require_permission("manage_service_types")
def question_reorder(request, pk):
    """Move one question up or down within its set."""
    question = get_object_or_404(AssessmentQuestion, pk=pk)
    direction = request.POST.get("direction")
    siblings = list(question.service_type.questions.all())
    index = next(i for i, q in enumerate(siblings) if q.pk == question.pk)
    swap_with = index - 1 if direction == "up" else index + 1
    if 0 <= swap_with < len(siblings):
        other = siblings[swap_with]
        question.order, other.order = other.order, question.order
        AssessmentQuestion.objects.bulk_update([question, other], ["order"])
    return redirect("settings-service-type-questions", pk=question.service_type_id)


# --------------------------------------------------------------------------
# Status lists
# --------------------------------------------------------------------------

@require_permission("manage_status_lists")
def status_lists(request):
    return render(
        request,
        "config/status_lists.html",
        {
            # Grouped rather than two named lists: the two columns are the
            # same component, and a third kind would need no template change.
            "status_groups": [
                {
                    "kind": kind,
                    "label": "{} statuses".format(label),
                    "rows": list(StatusOption.objects.filter(kind=kind)),
                }
                for kind, label in StatusOption.KINDS
            ],
        },
    )


@require_permission("manage_status_lists")
def status_edit(request, pk=None):
    instance = get_object_or_404(StatusOption, pk=pk) if pk else None
    form = StatusOptionForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        before = audit.snapshot(instance, fields=["kind", "code", "label", "is_active"])
        obj = form.save()
        audit.record_change(
            actor=request.user,
            action="status_option.saved",
            target=obj,
            before=before,
            after=audit.snapshot(obj, fields=["kind", "code", "label", "is_active"]),
        )
        messages.success(request, "Status saved.")
        return redirect("settings-status-lists")
    return render(request, "config/status_form.html", {"form": form, "instance": instance})


# --------------------------------------------------------------------------
# Company details and policy
# --------------------------------------------------------------------------

@require_permission("manage_company_details")
def company_details(request):
    company = CompanyDetail.get()
    form = CompanyDetailForm(request.POST or None, request.FILES or None, instance=company)
    if request.method == "POST" and form.is_valid():
        before = audit.snapshot(company)
        obj = form.save()
        audit.record_change(
            actor=request.user,
            action="company_details.updated",
            target=obj,
            before=before,
            after=audit.snapshot(obj),
        )
        messages.success(request, "Company details updated.")
        return redirect("settings-company")
    return render(request, "config/company_details.html", {"form": form})


@require_permission("manage_company_details")
def policy_settings(request):
    """
    The business-owned numbers: the requisition approval threshold and the
    attendance policy behind "late".
    """
    if request.method == "POST":
        for setting in PolicySetting.objects.all():
            new_value = request.POST.get("value__" + setting.key)
            if new_value is not None and new_value != setting.value:
                before = {"key": setting.key, "value": setting.value}
                setting.value = new_value
                setting.save(update_fields=["value"])
                audit.record_change(
                    actor=request.user,
                    action="policy.changed",
                    target=setting,
                    before=before,
                    after={"key": setting.key, "value": new_value},
                    reason=request.POST.get("reason", ""),
                )
        messages.success(request, "Policy settings updated.")
        return redirect("settings-policy")

    return render(
        request,
        "config/policy_settings.html",
        {
            "settings_rows": PolicySetting.objects.all(),
            "correction_reasons": CorrectionReason.objects.filter(is_active=True),
            "now": timezone.now(),
            "form": PolicySettingForm(),
        },
    )
