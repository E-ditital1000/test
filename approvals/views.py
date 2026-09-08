"""
Assessment review, and the shared approval mechanism.

A submitted assessment is shown read-only in three columns — structured
answers, the client's own responses with the photos, and the approval
sidebar. The supervisor can approve, or return with a comment that is
mandatory: returning without saying why gives the technician nothing to act
on, so the comment is the gate rather than a courtesy.

Decisions are append-only rows carrying actor, decision, comment and
timestamp. The current state of anything is the latest decision on it, never
a mutable status field somebody can set directly.
"""
from django.contrib import messages
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render

from config.pagination import paginate
from accounts.decorators import require_permission, user_has_permission
from accounts.scoping import apply_scope
from fieldjobs.models import Assessment

from .models import Approval, latest_decisions


def _reviewable(user, code="review_assessment"):
    """
    Scope qualifies permission: a Supervisor reviews their own team's work.
    Applied here so no screen in this module can forget it.
    """
    return apply_scope(
        Assessment.objects.select_related(
            "field_job__customer", "field_job__site", "service_type", "technician"
        ),
        user,
        code,
        own_team_filter=Q(technician__employee__supervisor__user=user),
    )


@require_permission("review_assessment")
def queue(request):
    """
    Assessments waiting on this reviewer, oldest first — the thing that has
    been blocked longest is always at the top.
    """
    submitted = _reviewable(request.user).filter(state=Assessment.SUBMITTED)

    waiting, decided = [], []
    # Oldest first. submitted_at is nullable, and NULL placement is not
    # portable — an assessment missing one is anomalous and should trail the
    # queue, not head it because the database happens to sort NULLs first.
    for assessment in submitted.order_by(F("submitted_at").asc(nulls_last=True)):
        (decided if assessment.approval_state in ("approved", "returned") else waiting).append(
            assessment
        )

    return render(
        request,
        "approvals/queue.html",
        {
            "waiting": paginate(request, waiting),
            # Recently decided is a glance, not a list to work through, so it
            # stays capped rather than paged -- and says so on the screen.
            "decided": decided[:20],
            "can_decide": user_has_permission(request.user, "approve_assessment"),
        },
    )


@require_permission("review_assessment")
def assessment_review(request, pk):
    assessment = get_object_or_404(
        _reviewable(request.user).prefetch_related(
            "answers__question",
            "photos",
            "field_job__check_ins__technician",
            "field_job__crew__employee__user",
        ),
        pk=pk,
    )

    answers = list(assessment.answers.all())
    answered, required = assessment.completeness

    return render(
        request,
        "approvals/assessment_review.html",
        {
            "assessment": assessment,
            # Two labelled groups, never mixed: what the technician recorded,
            # and what the client themselves said.
            "technician_answers": [a for a in answers if a.respondent == "technician"],
            "client_answers": [a for a in answers if a.respondent == "client"],
            "photos": assessment.photos.all(),
            "check_in": assessment.field_job.check_ins.order_by("-device_timestamp").first(),
            "crew": assessment.field_job.crew.all(),
            "check_ins": assessment.field_job.check_ins.order_by("device_timestamp"),
            "answered": answered,
            "required": required,
            "trail": assessment.approval_trail.select_related("actor"),
            "can_decide": user_has_permission(request.user, "approve_assessment"),
        },
    )


@require_permission("approve_assessment")
@transaction.atomic
def assessment_decide(request, pk):
    assessment = get_object_or_404(_reviewable(request.user, "approve_assessment"), pk=pk)
    if request.method != "POST":
        return redirect("approvals-assessment-review", pk=assessment.pk)

    decision = request.POST.get("decision")
    comment = (request.POST.get("comment") or "").strip()

    if decision not in (Approval.APPROVED, Approval.RETURNED):
        messages.error(request, "That is not a decision this screen can record.")
        return redirect("approvals-assessment-review", pk=assessment.pk)

    try:
        assessment.record_decision(decision=decision, actor=request.user, comment=comment)
    except ValueError as error:
        # Returning without a comment. The model refuses it, not the template.
        messages.error(request, str(error))
        return redirect("approvals-assessment-review", pk=assessment.pk)

    if decision == Approval.APPROVED:
        messages.success(request, f"Assessment for {assessment.field_job.reference} approved.")
    else:
        messages.success(
            request,
            f"Returned to {assessment.technician.get_full_name() or assessment.technician.email}. "
            "They see your comment on the job.",
        )
    return redirect("approvals-queue")
