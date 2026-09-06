"""
Configuration-not-code tests. The rule the brief is strictest about is that
retiring a question must not alter historical answers — these prove it at
the schema level.
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crm.models import Customer, Site
from fieldjobs.models import Assessment, AssessmentAnswer, FieldJob

from .models import AssessmentQuestion, PolicySetting, ServiceType, StatusOption

User = get_user_model()


class QuestionSetTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(code="solar", name="Solar")
        self.question = AssessmentQuestion.objects.create(
            service_type=self.service_type, text="Roof type?", order=1
        )
        self.technician = User.objects.create_user(username="t", email="t@test.local")
        customer = Customer.objects.create(name="Acme")
        site = Site.objects.create(customer=customer, name="HQ")
        job = FieldJob.objects.create(
            reference="FJ-0001",
            customer=customer,
            site=site,
            service_type=self.service_type,
            assigned_to=self.technician,
            scheduled_for=timezone.now(),
        )
        self.assessment = Assessment.objects.create(
            field_job=job,
            service_type=self.service_type,
            technician=self.technician,
            client_uuid=uuid.uuid4(),
            device_timestamp=timezone.now(),
        )
        self.answer = AssessmentAnswer.objects.create(
            assessment=self.assessment,
            question=self.question,
            question_text=self.question.text,
            respondent=self.question.respondent,
            value_text="Corrugated iron",
        )

    def test_retiring_a_question_leaves_historical_answers_intact(self):
        self.question.retire()

        self.answer.refresh_from_db()
        self.assertEqual(self.answer.value_text, "Corrugated iron")
        self.assertEqual(self.answer.question_text, "Roof type?")
        self.assertTrue(self.question.is_retired)

    def test_a_retired_question_drops_off_new_assessments_only(self):
        second = AssessmentQuestion.objects.create(
            service_type=self.service_type, text="Usable area?", order=2
        )
        self.question.retire()

        live = list(self.service_type.active_questions())
        self.assertEqual(live, [second])
        self.assertEqual(self.service_type.questions.count(), 2)

    def test_reworded_question_does_not_rewrite_the_recorded_answer(self):
        self.question.text = "What is the roof construction?"
        self.question.save(update_fields=["text"])

        self.answer.refresh_from_db()
        self.assertEqual(self.answer.question_text, "Roof type?")

    def test_a_new_service_type_needs_no_code_change(self):
        """Adding a service and its questions is data, end to end."""
        borehole = ServiceType.objects.create(code="borehole", name="Borehole")
        AssessmentQuestion.objects.create(service_type=borehole, text="Depth?", order=1)
        AssessmentQuestion.objects.create(
            service_type=borehole,
            text="Client confirms water table survey",
            respondent=AssessmentQuestion.CLIENT,
            order=2,
        )

        self.assertEqual(borehole.active_questions().count(), 2)
        client_questions = borehole.active_questions().filter(
            respondent=AssessmentQuestion.CLIENT
        )
        self.assertEqual(client_questions.count(), 1)


class StatusListTests(TestCase):
    def test_default_status_is_configuration(self):
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", order=1, is_default=True
        )
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="closed", label="Closed", order=2, is_terminal=True
        )

        self.assertEqual(StatusOption.default_for(StatusOption.TICKET).code, "new")

    def test_retired_status_is_not_offered_but_still_exists(self):
        status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="paused", label="Paused", order=1
        )
        status.deactivate()

        self.assertEqual(StatusOption.default_for(StatusOption.PROJECT), None)
        self.assertTrue(StatusOption.objects.filter(code="paused").exists())


class PolicyTests(TestCase):
    def test_missing_policy_value_falls_back_rather_than_failing(self):
        self.assertEqual(PolicySetting.get_value("nothing_here", "fallback"), "fallback")

    def test_threshold_is_read_from_settings_not_a_constant(self):
        PolicySetting.objects.create(key=PolicySetting.REQUISITION_THRESHOLD, value="50000")
        self.assertEqual(
            PolicySetting.get_value(PolicySetting.REQUISITION_THRESHOLD), "50000"
        )
