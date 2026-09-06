"""
Tests for the shared approval mechanism. It is built once here and reused
by assessments, requisitions and invoices, so the append-only rule and the
"a return must say why" rule are proven in one place.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from config.models import ServiceType, StatusOption
from crm.models import Customer
from projects.models import Project, Requisition

from .models import Approval

User = get_user_model()


class ApprovalMechanismTests(TestCase):
    def setUp(self):
        self.raiser = User.objects.create_user(username="pm", email="pm@test.local")
        self.supervisor = User.objects.create_user(username="sup", email="sup@test.local")
        self.executive = User.objects.create_user(username="exec", email="exec@test.local")

        customer = Customer.objects.create(name="Acme")
        project = Project.objects.create(
            reference="PRJ-0001",
            name="Acme fitout",
            customer=customer,
            service_type=ServiceType.objects.create(code="elec", name="Electrical"),
            status=StatusOption.objects.create(
                kind=StatusOption.PROJECT, code="active", label="Active"
            ),
        )
        self.requisition = Requisition.objects.create(
            project=project,
            reference="REQ-0001",
            description="Switchgear",
            amount=Decimal("250000"),
            raised_by=self.raiser,
        )

    def test_state_is_derived_from_the_latest_decision(self):
        self.assertIsNone(self.requisition.approval_state)

        self.requisition.record_decision(decision=Approval.SUBMITTED, actor=self.raiser)
        self.assertEqual(self.requisition.approval_state, Approval.SUBMITTED)

        self.requisition.record_decision(decision=Approval.APPROVED, actor=self.executive)
        self.assertEqual(self.requisition.approval_state, Approval.APPROVED)
        self.assertTrue(self.requisition.is_approved)

    def test_the_trail_is_append_only_and_keeps_every_decision(self):
        self.requisition.record_decision(decision=Approval.SUBMITTED, actor=self.raiser)
        self.requisition.record_decision(
            decision=Approval.RETURNED, actor=self.supervisor, comment="Quote missing"
        )
        self.requisition.record_decision(decision=Approval.SUBMITTED, actor=self.raiser)
        self.requisition.record_decision(decision=Approval.APPROVED, actor=self.executive)

        trail = list(self.requisition.approval_trail.order_by("created_at"))
        self.assertEqual(
            [entry.decision for entry in trail],
            [Approval.SUBMITTED, Approval.RETURNED, Approval.SUBMITTED, Approval.APPROVED],
        )
        # The rejection is still readable after the later approval.
        self.assertEqual(trail[1].comment, "Quote missing")

    def test_returning_without_a_comment_is_refused(self):
        with self.assertRaises(ValueError):
            self.requisition.record_decision(
                decision=Approval.RETURNED, actor=self.supervisor, comment="   "
            )
        self.assertEqual(self.requisition.approval_trail.count(), 0)

    def test_every_decision_carries_actor_and_timestamp(self):
        self.requisition.record_decision(
            decision=Approval.APPROVED, actor=self.executive, comment="Within budget"
        )
        decision = self.requisition.latest_decision

        self.assertEqual(decision.actor, self.executive)
        self.assertIsNotNone(decision.created_at)
        self.assertEqual(decision.target, self.requisition)
