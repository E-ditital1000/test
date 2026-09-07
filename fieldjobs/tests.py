"""
Field Jobs acceptance tests, including the end-to-end scenario the release
gate names: one job traced from first call to paid invoice, and an
assessment completed offline that syncs exactly once.
"""
import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from approvals.models import Approval
from config.models import AssessmentQuestion, ServiceType, StatusOption
from crm.models import Customer, Site, Ticket
from finance.models import Invoice, InvoiceLine
from hr.models import Employee
from projects.models import Project

from .models import Assessment, AssessmentPhoto, CheckIn, FieldJob, FieldJobCrew

User = get_user_model()

# A 1x1 PNG standing in for a site photo captured on the device.
TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class FieldWorkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)

        cls.service_type = ServiceType.objects.create(code="solar", name="Solar Installation")
        cls.q_required = AssessmentQuestion.objects.create(
            service_type=cls.service_type, text="Roof type", answer_type="text",
            respondent="technician", is_required=True, order=1,
        )
        cls.q_client = AssessmentQuestion.objects.create(
            service_type=cls.service_type, text="Budget indication?", answer_type="text",
            respondent="client", is_required=False, order=2,
        )
        StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True, order=1
        )
        StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True, order=1
        )

        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.site = Site.objects.create(
            customer=cls.customer, name="Duport Road", latitude="6.290000", longitude="-10.760000"
        )

        cls.receptionist = make_user("recep@test.local", ["Receptionist"])
        cls.manager = make_user("pm@test.local", ["Project Manager"])
        cls.supervisor = make_user("sup@test.local", ["Supervisor"])
        cls.finance = make_user("fin@test.local", ["Finance"])
        cls.technician = make_user("tech@test.local", ["Technician"])
        cls.other_tech = make_user("tech2@test.local", ["Technician"])

        # The supervisor's own team, for the scoping test.
        sup_employee = Employee.objects.create(user=cls.supervisor, staff_id="A1-001")
        Employee.objects.create(user=cls.technician, staff_id="A1-002", supervisor=sup_employee)
        Employee.objects.create(user=cls.other_tech, staff_id="A1-003")

    # -- helpers ----------------------------------------------------------

    def _job(self, technician=None):
        project = Project.objects.create(
            reference="PRJ-0001", name="Solar array", customer=self.customer, site=self.site,
            service_type=self.service_type,
            status=StatusOption.objects.get(kind=StatusOption.PROJECT, code="active"),
        )
        return FieldJob.objects.create(
            reference="FJ-0001", project=project, customer=self.customer, site=self.site,
            service_type=self.service_type, assigned_to=technician or self.technician,
            scheduled_for=timezone.now(), job_ref=project.job_ref,
        )

    def _payload(self, **overrides):
        payload = {
            "client_uuid": str(uuid.uuid4()),
            "device_timestamp": timezone.now().isoformat(),
            "location": {"latitude": "6.290700", "longitude": "-10.760500", "accuracy_m": 9},
            "notes": "Filled in with no signal.",
            "answers": [
                {"question_id": self.q_required.pk, "value": "Corrugated zinc"},
                {"question_id": self.q_client.pk, "value": "Under $20,000"},
            ],
            "photos": [{"client_uuid": str(uuid.uuid4()), "data_url": TINY_PNG, "label": "Panel"}],
        }
        payload.update(overrides)
        return payload

    def _submit(self, job, payload):
        return self.client.post(
            reverse("fieldjobs-assessment-submit", args=[job.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # -- the technician only ever sees their own work ---------------------

    def test_a_technician_cannot_open_another_technicians_job(self):
        job = self._job(technician=self.other_tech)
        self.client.force_login(self.technician)
        self.assertEqual(
            self.client.get(reverse("fieldjobs-job-detail", args=[job.pk])).status_code, 404
        )

    # -- GPS never blocks the event ---------------------------------------

    @tag("acceptance")
    def test_check_in_records_without_a_gps_fix_and_is_idempotent(self):
        job = self._job()
        self.client.force_login(self.technician)
        client_uuid = str(uuid.uuid4())
        for _ in range(2):
            self.client.post(
                reverse("fieldjobs-check-in", args=[job.pk]),
                {"client_uuid": client_uuid, "location_unavailable": "1"},
            )
        check_ins = CheckIn.objects.filter(field_job=job)
        self.assertEqual(check_ins.count(), 1, "replaying a check-in must not duplicate it")
        self.assertTrue(check_ins.first().location_unavailable)
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.IN_PROGRESS)

    # -- the form is configuration, not code ------------------------------

    @tag("acceptance")
    def test_the_assessment_form_is_generated_from_the_service_types_questions(self):
        job = self._job()
        self.client.force_login(self.technician)
        body = self.client.get(reverse("fieldjobs-assessment", args=[job.pk])).content.decode()
        self.assertIn(self.q_required.text, body)
        self.assertIn(self.q_client.text, body)
        self.assertIn(f'data-q="{self.q_required.pk}"', body)
        # A running assessment offers no one-tap exit.
        self.assertNotIn("a1-tabs", body)

    # -- the offline contract ---------------------------------------------

    @tag("acceptance")
    def test_an_assessment_held_offline_then_synced_creates_one_record(self):
        job = self._job()
        self.client.force_login(self.technician)
        payload = self._payload()

        first = self._submit(job, payload)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(json.loads(first.content)["ok"])

        # The phone retries the identical payload after a dropped response.
        second = self._submit(job, payload)
        self.assertTrue(json.loads(second.content)["duplicate"])

        self.assertEqual(Assessment.objects.filter(field_job=job).count(), 1)
        self.assertEqual(AssessmentPhoto.objects.filter(assessment__field_job=job).count(), 1)

    @tag("acceptance")
    def test_an_assessment_held_for_days_syncs_once_and_keeps_its_field_time(self):
        """
        The scenario the release gate names: filled in on site with no signal,
        carried for three days, then synced. The record must keep the time it
        was actually taken, not the time it happened to arrive.
        """
        job = self._job()
        self.client.force_login(self.technician)

        taken_at = timezone.now() - timedelta(days=3)
        payload = self._payload(device_timestamp=taken_at.isoformat())
        self._submit(job, payload)
        self._submit(job, payload)   # the phone retries on reconnect

        assessments = Assessment.objects.filter(field_job=job)
        self.assertEqual(assessments.count(), 1, "three days in a pocket is still one assessment")

        assessment = assessments.get()
        # What happened on site, and when the server heard about it, are two
        # different facts and both are kept.
        self.assertEqual(assessment.device_timestamp.date(), taken_at.date())
        self.assertGreater(assessment.server_received_at, assessment.device_timestamp)
        self.assertEqual(
            (assessment.server_received_at - assessment.device_timestamp).days, 3
        )

    @tag("acceptance")
    def test_the_same_client_uuid_from_two_devices_creates_one_record(self):
        job = self._job()
        payload = self._payload()

        self.client.force_login(self.technician)
        self._submit(job, payload)
        # A second device, signed in as the same technician, replays it.
        other = self.client_class()
        other.force_login(self.technician)
        other.post(
            reverse("fieldjobs-assessment-submit", args=[job.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(Assessment.objects.filter(client_uuid=payload["client_uuid"]).count(), 1)

    def test_submitting_records_gps_answers_and_starts_the_approval_trail(self):
        job = self._job()
        self.client.force_login(self.technician)
        payload = self._payload()
        self._submit(job, payload)

        assessment = Assessment.objects.get(client_uuid=payload["client_uuid"])
        self.assertEqual(assessment.answers.count(), 2)
        self.assertEqual(assessment.approval_state, Approval.SUBMITTED)
        # The distance is computed from the site on record.
        self.assertIsNotNone(assessment.gps_match_distance_m)
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.COMPLETED)

    @tag("acceptance")
    def test_retiring_a_question_does_not_alter_a_recorded_answer(self):
        job = self._job()
        self.client.force_login(self.technician)
        payload = self._payload()
        self._submit(job, payload)

        assessment = Assessment.objects.get(client_uuid=payload["client_uuid"])
        answer = assessment.answers.get(question=self.q_required)
        self.q_required.retire(actor=self.manager)

        answer.refresh_from_db()
        self.assertEqual(answer.question_text, "Roof type")
        self.assertEqual(answer.value_text, "Corrugated zinc")

    # -- review, and its scope --------------------------------------------

    @tag("acceptance")
    def test_returning_an_assessment_requires_a_comment(self):
        job = self._job()
        self.client.force_login(self.technician)
        payload = self._payload()
        self._submit(job, payload)
        assessment = Assessment.objects.get(client_uuid=payload["client_uuid"])

        self.client.force_login(self.supervisor)
        self.client.post(
            reverse("approvals-assessment-decide", args=[assessment.pk]),
            {"decision": Approval.RETURNED, "comment": "   "},
        )
        self.assertEqual(assessment.approval_state, Approval.SUBMITTED, "a blank comment must not return")

        self.client.post(
            reverse("approvals-assessment-decide", args=[assessment.pk]),
            {"decision": Approval.RETURNED, "comment": "Add the earth pit measurement."},
        )
        self.assertEqual(assessment.approval_state, Approval.RETURNED)

    @tag("acceptance")
    def test_a_supervisor_cannot_reach_another_teams_assessment(self):
        """Scope qualifies permission: their own team, not every team."""
        job = self._job(technician=self.other_tech)
        self.client.force_login(self.other_tech)
        payload = self._payload()
        self._submit(job, payload)
        assessment = Assessment.objects.get(client_uuid=payload["client_uuid"])

        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("approvals-assessment-review", args=[assessment.pk]))
        self.assertEqual(response.status_code, 404)

    # -- a crew on one visit ----------------------------------------------

    def _crewed_job(self):
        """One visit: technician leads, other_tech is on the crew."""
        job = self._job()
        FieldJobCrew.objects.create(
            field_job=job, employee=Employee.objects.get(user=self.other_tech)
        )
        return job

    def test_a_crew_member_sees_the_job_on_their_own_phone(self):
        job = self._crewed_job()
        self.client.force_login(self.other_tech)

        listing = self.client.get(reverse("fieldjobs-my-jobs"))
        self.assertContains(listing, job.reference)
        self.assertEqual(
            self.client.get(reverse("fieldjobs-job-detail", args=[job.pk])).status_code, 200
        )

    def test_a_crew_member_checks_in_for_themselves(self):
        job = self._crewed_job()
        for who in (self.technician, self.other_tech):
            self.client.force_login(who)
            self.client.post(
                reverse("fieldjobs-check-in", args=[job.pk]),
                {"client_uuid": str(uuid.uuid4()), "location_unavailable": "1"},
            )
        # Each person on site is recorded arriving, not just the lead.
        self.assertEqual(job.check_ins.count(), 2)
        self.assertEqual(
            set(job.check_ins.values_list("technician", flat=True)),
            {self.technician.pk, self.other_tech.pk},
        )

    def test_checking_in_twice_records_once(self):
        job = self._crewed_job()
        self.client.force_login(self.other_tech)
        for _ in range(2):
            self.client.post(
                reverse("fieldjobs-check-in", args=[job.pk]),
                {"client_uuid": str(uuid.uuid4()), "location_unavailable": "1"},
            )
        self.assertEqual(job.check_ins.filter(technician=self.other_tech).count(), 1)

    @tag("acceptance")
    def test_only_the_lead_submits_the_assessment(self):
        """
        An assessment with three possible authors is one nobody owns. Crew
        are on the visit; the report stays with the lead.
        """
        job = self._crewed_job()
        self.client.force_login(self.other_tech)

        # The form redirects them back with an explanation.
        response = self.client.get(reverse("fieldjobs-assessment", args=[job.pk]))
        self.assertEqual(response.status_code, 302)

        # And the endpoint refuses a direct post from their device.
        refused = self._submit(job, self._payload())
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(Assessment.objects.filter(field_job=job).count(), 0)

        # The lead submits normally.
        self.client.force_login(self.technician)
        self._submit(job, self._payload())
        self.assertEqual(Assessment.objects.filter(field_job=job).count(), 1)

    def test_someone_on_neither_the_lead_nor_the_crew_cannot_see_it(self):
        job = self._job()          # no crew
        self.client.force_login(self.other_tech)
        self.assertEqual(
            self.client.get(reverse("fieldjobs-job-detail", args=[job.pk])).status_code, 404
        )

    def test_scheduling_with_a_crew_records_everyone_once(self):
        project = Project.objects.create(
            reference="PRJ-0009", name="Array", customer=self.customer, site=self.site,
            service_type=self.service_type,
            status=StatusOption.objects.get(kind=StatusOption.PROJECT, code="active"),
        )
        self.client.force_login(self.manager)
        self.client.post(
            reverse("fieldjobs-schedule-for-project", args=[project.pk]),
            {
                "customer": self.customer.pk, "site": self.site.pk,
                "service_type": self.service_type.pk,
                "assigned_to": self.technician.pk,
                "scheduled_for": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "instructions": "",
                # The lead is deliberately ticked as crew too; they must not
                # appear on their own job twice.
                "crew": [
                    Employee.objects.get(user=self.technician).pk,
                    Employee.objects.get(user=self.other_tech).pk,
                ],
            },
        )
        job = FieldJob.objects.latest("id")
        self.assertEqual(job.crew.count(), 1, "the lead must not be listed as their own crew")
        self.assertEqual(job.crew.first().employee.user, self.other_tech)
        self.assertEqual(job.crew_size, 2)

    # -- the whole job, end to end ----------------------------------------

    @tag("acceptance")
    def test_one_job_traced_from_first_call_to_paid_invoice(self):
        """
        The acceptance scenario: a call becomes a ticket, converts to a
        project, is scheduled onto a phone, assessed, approved, and invoiced —
        and every record shares one job reference the whole way.
        """
        self.client.force_login(self.receptionist)
        self.client.post(
            reverse("crm-ticket-create"),
            {
                "customer": self.customer.pk, "site": "",
                "service_type": self.service_type.pk, "priority": "high",
                "description": "Array not charging.",
            },
        )
        ticket = Ticket.objects.latest("id")

        self.client.force_login(self.manager)
        self.client.post(reverse("crm-ticket-convert", args=[ticket.pk]))
        project = Project.objects.get(ticket=ticket)

        self.client.post(
            reverse("fieldjobs-schedule-for-project", args=[project.pk]),
            {
                "customer": self.customer.pk, "site": self.site.pk,
                "service_type": self.service_type.pk, "assigned_to": self.technician.pk,
                "scheduled_for": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "instructions": "",
            },
        )
        job = FieldJob.objects.latest("id")

        self.client.force_login(self.technician)
        payload = self._payload()
        self._submit(job, payload)
        assessment = Assessment.objects.get(client_uuid=payload["client_uuid"])

        self.client.force_login(self.supervisor)
        self.client.post(
            reverse("approvals-assessment-decide", args=[assessment.pk]),
            {"decision": Approval.APPROVED, "comment": ""},
        )

        self.client.force_login(self.finance)
        self.client.post(
            reverse("finance-invoice-create"),
            {"project": project.pk, "due_on": "", "notes": ""},
        )
        invoice = Invoice.objects.latest("id")
        InvoiceLine.objects.create(
            invoice=invoice, description="Solar array", quantity=1, unit_price=18400
        )
        self.client.post(reverse("finance-invoice-issue", args=[invoice.pk]))
        self.client.post(
            reverse("finance-payment-record", args=[invoice.pk]),
            {"amount": "18400", "paid_on": timezone.localdate().isoformat(), "method": "Transfer", "reference": ""},
        )

        invoice.refresh_from_db()
        assessment.refresh_from_db()

        # One identity, carried the whole way.
        lineage = {ticket.job_ref, project.job_ref, job.job_ref, invoice.job_ref}
        self.assertEqual(len(lineage), 1, "the job reference must survive every hop")
        self.assertEqual(assessment.approval_state, Approval.APPROVED)
        self.assertEqual(invoice.state, Invoice.PAID)
        self.assertEqual(invoice.outstanding, 0)
