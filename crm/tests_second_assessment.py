"""
The second sheet A-1 sent back, item by item.

    1. Technicians can't click complete task in the system. Both field
       jobs, project or ticket.
    2. Login: the location of staff can't be shown where they login. If
       they sign in in the field the GPS coordinate should indicate where
       they sign in.
    3. Super admin can't delete or edit ticket, field job.
    4. Field job doesn't have duration range. e.g. from October 15 —
       October 17.
    5. Creating project you should add the crew on the same page. And when
       creating task we should assign a task to a crew member. The crew
       member should have a timeline for completion.

Three of the five are the same fault as the first sheet, in a new place: a
screen that lists somebody's work and offers them nothing to do about it.
The ticket detail went further and rendered an Update button whose route
answers 403, which is worse than offering nothing — it tells a person their
account is broken.

"Super admin" is the Admin role. A-1 have no CEO role and did not want one;
Daniel Padmore holds Admin, and Admin is what carries full access.
"""
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site, Ticket
from fieldjobs.models import Assessment, CheckIn, FieldJob, FieldJobCrew
from hr.models import Employee
from projects.models import Project, ProjectCrew, Task

User = get_user_model()


def make_user(email, role_names=(), **extra):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345", **extra
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class Fixture(TestCase):
    """One company, enough of it to send somebody to a site."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.open_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.closed_status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="closed", label="Closed",
            is_terminal=True, order=9,
        )
        cls.project_status = StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="live", label="Live", is_default=True
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.site = Site.objects.create(customer=cls.customer, name="Main building")

        cls.admin = make_user("admin.sa@test.local", ["Admin"], first_name="Daniel")
        cls.supervisor = make_user("sup.sa@test.local", ["Supervisor"], first_name="Grace")
        cls.tech = make_user("tech.sa@test.local", ["Employee"], first_name="Moses")
        cls.mate = make_user("mate.sa@test.local", ["Employee"], first_name="Sam")
        cls.tech_employee = Employee.objects.create(user=cls.tech, staff_id="A1TES-101")
        cls.mate_employee = Employee.objects.create(user=cls.mate, staff_id="A1TES-102")

    def a_visit(self, **extra):
        fields = {
            "reference": extra.pop("reference", "FJ-9001"),
            "customer": self.customer,
            "site": self.site,
            "service_type": self.service,
            "assigned_to": self.tech,
            "scheduled_for": timezone.now(),
        }
        fields.update(extra)
        return FieldJob.objects.create(**fields)

    def a_ticket(self, **extra):
        fields = {
            "reference": extra.pop("reference", "TKT-9001"),
            "customer": self.customer,
            "site": self.site,
            "service_type": self.service,
            "status": self.open_status,
            "description": "Inverter fault light since Tuesday.",
        }
        fields.update(extra)
        return Ticket.objects.create(**fields)

    def a_project(self, **extra):
        fields = {
            "reference": extra.pop("reference", "PRJ-9001"),
            "name": "Clinic solar",
            "customer": self.customer,
            "service_type": self.service,
            "status": self.project_status,
        }
        fields.update(extra)
        return Project.objects.create(**fields)


# --------------------------------------------------------------------------
# 1. "Technicians can't click complete task"
# --------------------------------------------------------------------------

class FinishingAVisit(Fixture):
    """
    A field job only ever reached Completed because an assessment arrived.
    Work that leaves no assessment — a delivery, a part fitted, a return
    trip — stayed In progress for ever, and the board's "Not finished"
    count only grew.
    """

    @tag("acceptance")
    def test_the_lead_can_mark_a_visit_finished(self):
        job = self.a_visit()
        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("fieldjobs-job-complete", args=[job.pk]),
            {"reason": "Fuse replaced, nothing else needed"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.COMPLETED)
        self.assertIsNotNone(job.completed_at)
        self.assertEqual(job.completed_by, self.tech)

    def test_the_button_is_on_the_screen_the_technician_works_from(self):
        """
        A route nothing offers is a route nobody uses. This is the whole of
        A-1's complaint: the system could do it and the screen never said so.
        """
        job = self.a_visit()
        self.client.force_login(self.tech)
        body = self.client.get(reverse("fieldjobs-job-detail", args=[job.pk])).content.decode()
        self.assertIn(reverse("fieldjobs-job-complete", args=[job.pk]), body)
        self.assertIn("Mark this visit finished", body)

    def test_crew_are_told_whose_job_it_is_to_close_it(self):
        """
        One person is accountable for the visit, the same rule the
        assessment follows. A refusal that does not say who is a refusal
        the crew member cannot act on.
        """
        job = self.a_visit()
        FieldJobCrew.objects.create(field_job=job, employee=self.mate_employee)
        self.client.force_login(self.mate)
        response = self.client.post(
            reverse("fieldjobs-job-complete", args=[job.pk]), follow=True
        )
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.SCHEDULED)
        self.assertContains(response, "leads this visit and closes it")

    def test_the_office_can_close_a_visit_the_lead_left_open(self):
        job = self.a_visit()
        self.client.force_login(self.supervisor)
        self.client.post(reverse("fieldjobs-job-complete", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.COMPLETED)

    def test_a_visit_closed_by_mistake_can_be_reopened(self):
        job = self.a_visit(state=FieldJob.COMPLETED, completed_at=timezone.now())
        self.client.force_login(self.tech)
        self.client.post(reverse("fieldjobs-job-complete", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.SCHEDULED)
        self.assertIsNone(job.completed_at)

    @tag("acceptance")
    def test_a_visit_closed_by_its_assessment_is_not_reopened_here(self):
        """
        The assessment is the record of what happened on site. Reopening
        the visit would invite a second one; returning the assessment from
        the review queue is how that is undone.
        """
        job = self.a_visit()
        Assessment.objects.create(
            field_job=job, service_type=self.service, technician=self.tech,
            client_uuid="11111111-1111-4111-8111-111111111111",
            device_timestamp=timezone.now(), state=Assessment.SUBMITTED,
            submitted_at=timezone.now(),
        )
        job.state, job.completed_at = FieldJob.COMPLETED, timezone.now()
        job.save(update_fields=["state", "completed_at"])

        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("fieldjobs-job-complete", args=[job.pk]), follow=True
        )
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.COMPLETED)
        self.assertContains(response, "Return the assessment")

    def test_reopening_a_visit_somebody_arrived_at_leaves_it_in_progress(self):
        """Somebody checked in, so Scheduled would be a lie."""
        job = self.a_visit(state=FieldJob.COMPLETED, completed_at=timezone.now())
        CheckIn.objects.create(
            field_job=job, technician=self.tech,
            client_uuid="22222222-2222-4222-8222-222222222222",
            device_timestamp=timezone.now(),
        )
        self.client.force_login(self.tech)
        self.client.post(reverse("fieldjobs-job-complete", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.state, FieldJob.IN_PROGRESS)


class FinishingATask(Fixture):
    """
    The toggle existed and worked. It was on the project page and on the
    visit, and absent from both screens a technician actually works from —
    "My work", which claims to hold everything handed to them, and "Today's
    jobs".
    """

    def setUp(self):
        self.project = self.a_project()
        self.task = Task.objects.create(
            project=self.project, title="Fit the array rails", assignee=self.tech
        )

    @tag("acceptance")
    def test_my_work_offers_a_way_to_finish_a_task(self):
        self.client.force_login(self.tech)
        body = self.client.get(reverse("dashboard-my-tasks")).content.decode()
        self.assertIn(reverse("projects-task-toggle", args=[self.task.pk]), body)
        self.assertIn("Mark done", body)

    def test_finishing_from_my_work_comes_back_to_my_work(self):
        """
        Sent to the project page instead, a technician lands on a screen
        they may not be able to open at all — view_projects is scoped to
        own projects and they are crewed on none of them.
        """
        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("projects-task-toggle", args=[self.task.pk]),
            {"next": reverse("dashboard-my-tasks"), "completion_note": "Rails up"},
        )
        self.assertRedirects(response, reverse("dashboard-my-tasks"))
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_complete)
        self.assertEqual(self.task.completion_note, "Rails up")

    def test_todays_jobs_offers_a_way_to_finish_a_loose_task(self):
        self.client.force_login(self.tech)
        body = self.client.get(reverse("fieldjobs-my-jobs")).content.decode()
        self.assertIn(reverse("projects-task-toggle", args=[self.task.pk]), body)

    def test_the_row_does_not_nest_a_form_inside_a_link(self):
        """
        The row used to be one anchor. A form inside an anchor is invalid
        and browsers recover from it differently, so the button either does
        nothing or follows the link instead of submitting.
        """
        self.client.force_login(self.tech)
        body = self.client.get(reverse("dashboard-my-tasks")).content.decode()
        start = body.index(self.task.title)
        # Walk back to the opening tag of the row this title sits in.
        row = body.rindex("<div class=\"row-i\"", 0, start)
        self.assertNotIn("<a class=\"row-i\"", body[row:start])


class FinishingATicket(Fixture):
    """
    A ticket handed to a technician had a screen by the first sheet, and
    nothing on it they could press. Worse, the status card rendered for
    everybody and its route asks for view_ticket_status — so the one
    control they were offered answered 403.
    """

    def setUp(self):
        self.ticket = self.a_ticket(assigned_to=self.tech)

    @tag("acceptance")
    def test_the_assignee_can_mark_their_own_ticket_finished(self):
        self.client.force_login(self.tech)
        self.client.post(
            reverse("crm-ticket-close", args=[self.ticket.pk]),
            {"reason": "Replaced the inverter fuse"},
        )
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.closed_at)
        self.assertEqual(self.ticket.status, self.closed_status)

    def test_what_they_said_was_done_goes_into_the_activity_log(self):
        self.client.force_login(self.tech)
        self.client.post(
            reverse("crm-ticket-close", args=[self.ticket.pk]),
            {"reason": "Replaced the inverter fuse"},
        )
        event = self.ticket.events.filter(action="Ticket closed").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.tech)
        self.assertEqual(event.detail, "Replaced the inverter fuse")

    def test_somebody_elses_ticket_is_not_theirs_to_finish(self):
        other = self.a_ticket(reference="TKT-9002", assigned_to=self.mate)
        self.client.force_login(self.tech)
        response = self.client.post(reverse("crm-ticket-close", args=[other.pk]))
        self.assertEqual(response.status_code, 403)
        other.refresh_from_db()
        self.assertIsNone(other.closed_at)

    @tag("acceptance")
    def test_the_technician_is_not_shown_a_control_that_would_be_refused(self):
        """
        The status form posts to a route asking for view_ticket_status. A
        technician holds none, so the button they were given answered 403.
        """
        self.client.force_login(self.tech)
        body = self.client.get(
            reverse("crm-ticket-detail", args=[self.ticket.pk])
        ).content.decode()
        self.assertNotIn(reverse("crm-ticket-update", args=[self.ticket.pk]), body)
        self.assertIn(reverse("crm-ticket-close", args=[self.ticket.pk]), body)

    def test_the_office_still_gets_the_status_form(self):
        self.client.force_login(self.supervisor)
        body = self.client.get(
            reverse("crm-ticket-detail", args=[self.ticket.pk])
        ).content.decode()
        self.assertIn(reverse("crm-ticket-update", args=[self.ticket.pk]), body)

    def test_a_closed_ticket_can_be_reopened_by_the_office(self):
        """
        Needed the moment the person doing the work could close one. A
        customer rings back; a technician closes the wrong row.
        """
        self.client.force_login(self.tech)
        self.client.post(reverse("crm-ticket-close", args=[self.ticket.pk]))

        self.client.force_login(self.supervisor)
        self.client.post(reverse("crm-ticket-reopen", args=[self.ticket.pk]))
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closed_at)
        self.assertEqual(self.ticket.status, self.open_status)
        self.assertTrue(self.ticket.events.filter(action="Ticket reopened").exists())

    def test_a_technician_cannot_reopen(self):
        self.ticket.closed_at = timezone.now()
        self.ticket.status = self.closed_status
        self.ticket.save(update_fields=["closed_at", "status"])
        self.client.force_login(self.tech)
        response = self.client.post(reverse("crm-ticket-reopen", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 403)

    def test_my_work_carries_the_action_for_a_ticket_too(self):
        self.client.force_login(self.tech)
        body = self.client.get(reverse("dashboard-my-tasks")).content.decode()
        self.assertIn(reverse("crm-ticket-close", args=[self.ticket.pk]), body)


class ComingBackToWhereYouPressed(Fixture):
    """
    These screens list work that lives somewhere else, so the action
    carries a `next` saying where to return — otherwise somebody ticking a
    task off their dashboard lands on a project page they may not be
    allowed to open.

    `next` arrives in the request body, which is to say from outside. Given
    straight to `redirect()` it will send a signed-in person to another
    site wearing our address bar, which is how a convincing sign-in page
    gets in front of somebody.
    """

    @tag("acceptance")
    def test_a_next_pointing_off_the_site_is_ignored(self):
        project = self.a_project()
        task = Task.objects.create(
            project=project, title="Fit the rails", assignee=self.tech
        )
        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("projects-task-toggle", args=[task.pk]),
            {"next": "https://not-a1.example/sign-in"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("not-a1.example", response["Location"])
        # The work still happened; only the destination was refused.
        task.refresh_from_db()
        self.assertTrue(task.is_complete)

    def test_a_next_on_this_site_is_honoured(self):
        project = self.a_project()
        task = Task.objects.create(
            project=project, title="Fit the rails", assignee=self.tech
        )
        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("projects-task-toggle", args=[task.pk]),
            {"next": reverse("dashboard-my-tasks")},
        )
        self.assertRedirects(response, reverse("dashboard-my-tasks"))

    def test_the_same_guard_covers_finishing_a_ticket(self):
        ticket = self.a_ticket(assigned_to=self.tech)
        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("crm-ticket-close", args=[ticket.pk]),
            {"next": "//not-a1.example/sign-in"},
        )
        self.assertNotIn("not-a1.example", response["Location"])

    def test_the_same_guard_covers_finishing_a_visit(self):
        job = self.a_visit()
        self.client.force_login(self.tech)
        response = self.client.post(
            reverse("fieldjobs-job-complete", args=[job.pk]),
            {"next": "https://not-a1.example/"},
        )
        self.assertNotIn("not-a1.example", response["Location"])


# --------------------------------------------------------------------------
# 3. "Super admin can't delete or edit ticket, field job"
# --------------------------------------------------------------------------

class AdminCorrectsATicket(Fixture):
    def setUp(self):
        self.ticket = self.a_ticket()

    @tag("acceptance")
    def test_an_admin_can_fix_the_customer_on_a_ticket(self):
        other = Customer.objects.create(name="Ganta Health Centre")
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("crm-ticket-edit", args=[self.ticket.pk]),
            {
                "customer": other.pk,
                "site": "",
                "service_type": self.service.pk,
                "priority": Ticket.HIGH,
                "description": "Inverter fault light since Tuesday.",
            },
        )
        self.assertRedirects(
            response, reverse("crm-ticket-detail", args=[self.ticket.pk])
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.customer, other)
        self.assertEqual(self.ticket.priority, Ticket.HIGH)

    def test_the_correction_shows_in_the_activity_log(self):
        """
        An audit row in Settings is not where anybody reading the ticket
        will look for what changed.
        """
        other = Customer.objects.create(name="Ganta Health Centre")
        self.client.force_login(self.admin)
        self.client.post(
            reverse("crm-ticket-edit", args=[self.ticket.pk]),
            {
                "customer": other.pk, "site": "", "service_type": self.service.pk,
                "priority": Ticket.NORMAL,
                "description": "Inverter fault light since Tuesday.",
            },
        )
        event = self.ticket.events.filter(action="Ticket corrected").first()
        self.assertIsNotNone(event)
        self.assertIn("customer", event.detail)

    def test_the_edit_screen_leaves_assignment_alone(self):
        """
        Assignment has its own screen, its own service and its own trail
        entry. Two doors to the same act means two behaviours.
        """
        self.client.force_login(self.admin)
        body = self.client.get(
            reverse("crm-ticket-edit", args=[self.ticket.pk])
        ).content.decode()
        self.assertNotIn('name="assigned_to"', body)

    def test_a_supervisor_cannot_edit_a_ticket(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("crm-ticket-edit", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 403)

    @tag("acceptance")
    def test_an_admin_can_remove_a_ticket_logged_twice(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("crm-ticket-remove", args=[self.ticket.pk]),
            {"reason": "Logged twice for the same call"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_a_ticket_a_project_grew_out_of_will_not_go(self):
        """
        Erase the plan, never the history. The refusal names what is in the
        way so the person knows whether to deal with it or leave this alone.
        """
        self.a_project(ticket=self.ticket)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("crm-ticket-remove", args=[self.ticket.pk]),
            {"reason": "Tidying up"},
            follow=True,
        )
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())
        self.assertContains(response, "cannot be removed")
        self.assertContains(response, "project")

    def test_the_ticket_offers_the_admin_its_own_edit_link(self):
        self.client.force_login(self.admin)
        body = self.client.get(
            reverse("crm-ticket-detail", args=[self.ticket.pk])
        ).content.decode()
        self.assertIn(reverse("crm-ticket-edit", args=[self.ticket.pk]), body)


class AdminCorrectsAVisit(Fixture):
    """
    The field job half of item 3 shipped with the first sheet. These hold
    it, and cover the bug that made the edit screen unusable in practice.
    """

    @tag("acceptance")
    def test_the_edit_screen_shows_the_time_the_visit_is_already_booked_for(self):
        """
        `datetime-local` will not display Django's localised rendering of a
        datetime, so the field came up blank. An Admin opening the screen to
        change the technician would have cleared the schedule by saving.
        """
        when = timezone.localtime(timezone.now()).replace(
            hour=9, minute=30, second=0, microsecond=0
        )
        job = self.a_visit(scheduled_for=when)
        self.client.force_login(self.admin)
        body = self.client.get(reverse("fieldjobs-job-edit", args=[job.pk])).content.decode()
        self.assertIn(when.strftime("%Y-%m-%dT%H:%M"), body)

    def test_an_admin_can_remove_a_visit_booked_twice(self):
        job = self.a_visit()
        self.client.force_login(self.admin)
        self.client.post(
            reverse("fieldjobs-job-remove", args=[job.pk]),
            {"reason": "Booked twice for the same day"},
        )
        self.assertFalse(FieldJob.objects.filter(pk=job.pk).exists())

    def test_the_instructions_are_audited_when_they_change(self):
        """
        The audit list named a field the model does not have, so
        `model_to_dict` dropped it and every rescheduling was recorded with
        the instructions missing from both sides.
        """
        from accounts.models import AuditEntry

        job = self.a_visit(instructions="Bring the 10mm")
        self.client.force_login(self.admin)
        self.client.post(
            reverse("fieldjobs-job-edit", args=[job.pk]),
            {
                "customer": self.customer.pk,
                "site": self.site.pk,
                "service_type": self.service.pk,
                "assigned_to": self.tech.pk,
                "scheduled_for": timezone.localtime(job.scheduled_for).strftime("%Y-%m-%dT%H:%M"),
                "expected_end": "",
                "instructions": "Bring the 10mm and a spare fuse",
            },
        )
        entry = AuditEntry.objects.filter(action="fieldjob.corrected").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.before["instructions"], "Bring the 10mm")
        self.assertEqual(entry.after["instructions"], "Bring the 10mm and a spare fuse")


# --------------------------------------------------------------------------
# 4. "Field job doesn't have duration range"
# --------------------------------------------------------------------------

class AVisitThatRunsForDays(Fixture):
    def setUp(self):
        self.starts = timezone.make_aware(
            timezone.datetime.combine(date(2026, 10, 15), time(9, 0))
        )
        self.ends = timezone.make_aware(
            timezone.datetime.combine(date(2026, 10, 17), time(17, 0))
        )

    @tag("acceptance")
    def test_a_visit_can_be_booked_across_several_days(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("fieldjobs-schedule"),
            {
                "customer": self.customer.pk,
                "site": self.site.pk,
                "service_type": self.service.pk,
                "assigned_to": self.tech.pk,
                "scheduled_for": "2026-10-15T09:00",
                "expected_end": "2026-10-17T17:00",
                "instructions": "",
            },
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        job = FieldJob.objects.get()
        self.assertEqual(timezone.localtime(job.expected_end).date(), date(2026, 10, 17))
        self.assertTrue(job.runs_past_its_day)

    def test_a_visit_that_ends_before_it_starts_is_refused(self):
        from fieldjobs.forms import FieldJobForm

        form = FieldJobForm({
            "customer": self.customer.pk,
            "site": self.site.pk,
            "service_type": self.service.pk,
            "assigned_to": self.tech.pk,
            "scheduled_for": "2026-10-17T09:00",
            "expected_end": "2026-10-15T17:00",
            "instructions": "",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("expected_end", form.errors)

    def test_a_single_day_visit_needs_no_end(self):
        from fieldjobs.forms import FieldJobForm

        form = FieldJobForm({
            "customer": self.customer.pk,
            "site": self.site.pk,
            "service_type": self.service.pk,
            "assigned_to": self.tech.pk,
            "scheduled_for": "2026-10-15T09:00",
            "expected_end": "",
            "instructions": "",
        })
        self.assertTrue(form.is_valid(), form.errors)

    @tag("acceptance")
    def test_the_middle_day_of_a_three_day_visit_is_still_the_crews_day(self):
        """
        The whole point of the range. Filtering on the start date alone
        left a crew's own screen empty on day two of their own job, and the
        office board showing nothing booked.
        """
        job = self.a_visit(scheduled_for=self.starts, expected_end=self.ends)
        middle = date(2026, 10, 16)
        self.assertIn(job, FieldJob.objects.on_day(middle))
        self.assertNotIn(job, FieldJob.objects.on_day(date(2026, 10, 18)))

    def test_a_one_day_visit_is_only_on_its_own_day(self):
        job = self.a_visit(scheduled_for=self.starts)
        self.assertIn(job, FieldJob.objects.on_day(date(2026, 10, 15)))
        self.assertNotIn(job, FieldJob.objects.on_day(date(2026, 10, 16)))

    def test_the_board_says_how_long_a_visit_runs(self):
        self.a_visit(scheduled_for=self.starts, expected_end=self.ends)
        self.client.force_login(self.supervisor)
        body = self.client.get(
            reverse("fieldjobs-board") + "?filter=all"
        ).content.decode()
        self.assertIn("15 Oct 09:00 – 17 Oct 17:00", body)


# --------------------------------------------------------------------------
# 5. "Creating project you should add the crew on the same page"
# --------------------------------------------------------------------------

class CrewOnTheProjectPage(Fixture):
    @tag("acceptance")
    def test_a_project_is_created_with_its_crew(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("projects-create"),
            {
                "name": "Clinic solar",
                "customer": self.customer.pk,
                "site": self.site.pk,
                "service_type": self.service.pk,
                "status": self.project_status.pk,
                "manager": "",
                "start_date": "",
                "target_end_date": "",
                "description": "",
                "crew": [self.tech_employee.pk, self.mate_employee.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get()
        self.assertEqual(project.crew.count(), 2)
        self.assertEqual(project.crew.first().assigned_by, self.admin)

    def test_the_crew_box_is_on_the_create_screen(self):
        self.client.force_login(self.admin)
        body = self.client.get(reverse("projects-create")).content.decode()
        self.assertIn('name="crew"', body)
        self.assertIn("A1TES-101", body)

    @tag("acceptance")
    def test_the_crew_decides_who_a_task_can_be_given_to(self):
        """
        Why the second screen mattered. The task form narrows its assignee
        list to the crew, so a project created with nobody on it offered
        every account in the company and the people actually on the job
        were buried in it.
        """
        from projects.forms import TaskForm

        project = self.a_project()
        ProjectCrew.objects.create(project=project, employee=self.tech_employee)
        form = TaskForm(project=project)
        self.assertEqual(list(form.fields["assignee"].queryset), [self.tech])

    def test_editing_a_project_shows_the_crew_it_already_has(self):
        """
        An empty box on the edit screen would read as "nobody" and strip
        the crew on the next save.
        """
        import re

        project = self.a_project()
        ProjectCrew.objects.create(project=project, employee=self.tech_employee)
        self.client.force_login(self.admin)
        body = self.client.get(reverse("projects-edit", args=[project.pk])).content.decode()

        boxes = {
            int(match.group(1)): "checked" in match.group(0)
            for match in re.finditer(
                r'<input type="checkbox" name="crew" value="(\d+)"[^>]*>', body
            )
        }
        self.assertTrue(boxes.get(self.tech_employee.pk), "the crew they have must be ticked")
        self.assertFalse(boxes.get(self.mate_employee.pk), "and nobody else")

    def test_editing_can_add_and_remove_crew(self):
        project = self.a_project()
        ProjectCrew.objects.create(project=project, employee=self.tech_employee)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("projects-edit", args=[project.pk]),
            {
                "name": project.name,
                "customer": self.customer.pk,
                "site": "",
                "service_type": self.service.pk,
                "status": self.project_status.pk,
                "manager": "",
                "start_date": "",
                "target_end_date": "",
                "description": "",
                "crew": [self.mate_employee.pk],
            },
        )
        self.assertEqual(
            list(project.crew.values_list("employee_id", flat=True)),
            [self.mate_employee.pk],
        )

    def test_taking_somebody_off_the_crew_leaves_their_tasks_alone(self):
        """
        A crew row is an assignment, not a record of work. The task they
        were given stays on them.
        """
        project = self.a_project()
        ProjectCrew.objects.create(project=project, employee=self.tech_employee)
        task = Task.objects.create(project=project, title="Fit the rails", assignee=self.tech)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("projects-edit", args=[project.pk]),
            {
                "name": project.name, "customer": self.customer.pk, "site": "",
                "service_type": self.service.pk, "status": self.project_status.pk,
                "manager": "", "start_date": "", "target_end_date": "",
                "description": "", "crew": [],
            },
        )
        task.refresh_from_db()
        self.assertEqual(task.assignee, self.tech)
        self.assertEqual(project.crew.count(), 0)

    def test_a_crew_member_gets_a_timeline_on_the_task(self):
        """
        "The crew member should have a timeline for completion." The dates
        and their optional times already existed; what was missing was a
        crew to assign them to at the point the project was made.
        """
        project = self.a_project()
        ProjectCrew.objects.create(project=project, employee=self.tech_employee)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("projects-task-create", args=[project.pk]),
            {
                "title": "Fit the array rails",
                "assignee": self.tech.pk,
                "field_job": "",
                "start_date": "2026-10-15",
                "start_time": "09:00",
                "due_date": "2026-10-17",
                "due_time": "17:00",
                "description": "",
            },
        )
        task = Task.objects.get()
        self.assertEqual(task.assignee, self.tech)
        self.assertEqual(task.start_date, date(2026, 10, 15))
        self.assertEqual(task.due_date, date(2026, 10, 17))
        self.assertEqual(task.due_time, time(17, 0))
