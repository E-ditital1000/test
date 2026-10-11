"""
The one approval mechanism, shared by assessments, requisitions and
invoices. Decisions are append-only rows carrying actor, decision, comment
and timestamp — the current state of a thing is the latest decision on it,
never a mutable status field somebody can set directly.

No approval chains beyond supervisor then executive.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from config.mixins import TimeStampedModel


class Approval(TimeStampedModel):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    RETURNED = "returned"
    ESCALATED = "escalated"
    # Added for leave, and meaningful for anything else approved here.
    # "Returned" means fix it and send it back; "declined" is an answer, and
    # the two cannot be collapsed without lying to the person waiting.
    # "Cancelled" is the subject withdrawing their own request before it is
    # decided, which is not a decision anybody made about them.
    DECLINED = "declined"
    CANCELLED = "cancelled"
    DECISIONS = [
        (SUBMITTED, "Submitted"),
        (APPROVED, "Approved"),
        (RETURNED, "Returned"),
        (ESCALATED, "Escalated"),
        (DECLINED, "Declined"),
        (CANCELLED, "Cancelled"),
    ]

    # Saying no, or sending something back, has to say why. The person
    # waiting gets this comment and nothing else.
    MUST_EXPLAIN = (RETURNED, DECLINED)

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    decision = models.CharField(max_length=20, choices=DECISIONS)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_decisions"
    )
    # A return must say why; the technician sees this comment.
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["content_type", "object_id", "created_at"])]

    def __str__(self):
        return f"{self.get_decision_display()} by {self.actor} on {self.content_type}:{self.object_id}"


def latest_decisions(model, object_ids):
    """
    The current decision for many objects in one query.

    `approval_state` on a single object costs a query, which is right when
    you are looking at one record and wrong when you are listing forty: a
    queue of pending requisitions was doing one round trip per row. This
    reads the whole trail for the given ids once and keeps the newest per
    object.

    Returns {object_id: Approval}.
    """
    object_ids = list(object_ids)
    if not object_ids:
        return {}

    content_type = ContentType.objects.get_for_model(model)
    latest = {}
    # Ordered oldest first, so the last write per id wins — the same rule
    # `approval_state` applies one object at a time.
    for approval in Approval.objects.filter(
        content_type=content_type, object_id__in=object_ids
    ).select_related("actor").order_by("created_at"):
        latest[approval.object_id] = approval
    return latest


class ApprovalTrailMixin:
    """
    Mix into any model that is approved. Gives it a trail and a current
    state derived from the trail, so no module invents its own.
    """

    @property
    def approval_trail(self):
        return Approval.objects.filter(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.pk,
        )

    @property
    def latest_decision(self):
        return self.approval_trail.order_by("-created_at").first()

    @property
    def approval_state(self):
        latest = self.latest_decision
        return latest.decision if latest else None

    @property
    def is_approved(self):
        return self.approval_state == Approval.APPROVED

    def record_decision(self, *, decision, actor, comment=""):
        if decision in Approval.MUST_EXPLAIN and not comment.strip():
            raise ValueError(
                "Returning requires a comment."
                if decision == Approval.RETURNED
                else "Declining requires a reason — the person waiting sees it."
            )
        return Approval.objects.create(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.pk,
            decision=decision,
            actor=actor,
            comment=comment,
        )
