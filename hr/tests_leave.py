"""
Leave: the figures, the flow, and who hears about it.

The guarantees worth defending, in the order they would hurt if they broke:

A leave day costs what the working week says it costs. A-1 works Monday to
Saturday, so a Thursday-to-Tuesday request is five days and not four, and a
public holiday inside a span is not consumed.

Entitlement that cannot be worked out is never rendered as zero. An employee
with no start date has an unknown allowance, and telling them they have none
left would be a lie the screens would repeat every day.

A balance is computed, never stored, so it cannot drift from the requests
behind it.

And somebody's leave is between them and HR. The decision email goes to the
person and nobody else — copying the office on a sick note is not a
notification, it is a disclosure.
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from accounts.models import Role, UserRole
from approvals.models import Approval
from config.models import PolicySetting

from . import leave, services
from .services import rebuild_attendance_days
from .models import (
    AttendanceDay,
    Employee,
    LeaveAdjustment,
    LeaveEntitlementRule,
    LeaveRequest,
    LeaveType,
    PublicHoliday,
)

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


class WorkingDayCountingTests(TestCase):
    """
    What a leave day costs. Every other figure in the module is built on
    this one, so it is tested on its own before anything else.
    """

    @classmethod
    def setUpTestData(cls):
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")

    @tag("acceptance")
    def test_a_six_day_week_counts_saturday(self):
        # Thursday 15 Oct 2026 to Tuesday 20 Oct: Thu Fri Sat Mon Tue.
        # Sunday the 18th drops out; Saturday does not.
        self.assertEqual(
            leave.count_working_days(date(2026, 10, 15), date(2026, 10, 20)), 5
        )

    def test_sunday_is_never_counted(self):
        sunday = date(2026, 10, 18)
        self.assertEqual(sunday.weekday(), 6, "fixture assumption")
        self.assertEqual(leave.count_working_days(sunday, sunday), 0)

    def test_a_public_holiday_inside_the_span_is_not_consumed(self):
        PublicHoliday.objects.create(date=date(2026, 10, 16), name="A day off")
        self.assertEqual(
            leave.count_working_days(date(2026, 10, 15), date(2026, 10, 20)),
            4,
            "the holiday should not come out of anybody's leave",
        )

    def test_one_day_is_one_day(self):
        self.assertEqual(
            leave.count_working_days(date(2026, 10, 15), date(2026, 10, 15)), 1
        )

    def test_a_backwards_span_counts_nothing_rather_than_raising(self):
        self.assertEqual(
            leave.count_working_days(date(2026, 10, 20), date(2026, 10, 15)), 0
        )

    def test_the_days_are_returned_and_not_just_the_total(self):
        """The decision screen lists which days a request actually uses."""
        days = leave.working_days(date(2026, 10, 15), date(2026, 10, 20))
        self.assertNotIn(date(2026, 10, 18), days)
        self.assertIn(date(2026, 10, 17), days)


class RestDaySettingTests(TestCase):
    """
    The working week is a setting, and a mistyped setting must not take the
    leave screens down or silently make every request free.
    """

    def test_it_defaults_to_sunday_when_nothing_is_set(self):
        self.assertEqual(leave.rest_days(), frozenset({6}))

    def test_a_five_day_week_is_respected(self):
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="5,6")
        self.assertEqual(
            leave.count_working_days(date(2026, 10, 15), date(2026, 10, 20)),
            4,
            "Saturday should stop counting on a five-day week",
        )

    def test_nonsense_falls_back_rather_than_raising(self):
        PolicySetting.objects.create(
            key=PolicySetting.LEAVE_REST_DAYS, value="whenever"
        )
        self.assertEqual(leave.rest_days(), frozenset({6}))

    def test_a_week_of_rest_days_is_treated_as_a_mistake(self):
        """
        Seven rest days would make every request zero days long. That is a
        configuration error, not a policy anybody means.
        """
        PolicySetting.objects.create(
            key=PolicySetting.LEAVE_REST_DAYS, value="0,1,2,3,4,5,6"
        )
        self.assertEqual(leave.rest_days(), frozenset({6}))


class EntitlementTests(TestCase):
    """
    The statutory bands: one week in the first year rising to four after
    five, as working days on a six-day week.
    """

    @classmethod
    def setUpTestData(cls):
        cls.annual = LeaveType.objects.create(code="annual", label="Annual leave")
        for months, days in ((0, 6), (12, 12), (36, 18), (60, 24)):
            LeaveEntitlementRule.objects.create(
                leave_type=cls.annual, after_months=months, days=days
            )
        cls.unpaid = LeaveType.objects.create(
            code="unpaid", label="Unpaid leave", is_paid=False
        )
        cls.user = make_user("bands@test.local")

    def _employee_starting(self, months_ago, staff_id="A1-900"):
        today = date(2026, 10, 11)
        start = today - timedelta(days=int(months_ago * 30.5))
        return Employee.objects.create(
            user=make_user(f"{staff_id}@test.local"),
            staff_id=staff_id,
            start_date=start,
        )

    def test_each_band_pays_out_at_its_own_threshold(self):
        for months, expected in ((0, 6), (6, 6), (12, 12), (40, 18), (70, 24)):
            with self.subTest(months=months):
                self.assertEqual(self.annual.entitlement_after(months), expected)

    def test_service_below_the_lowest_band_still_earns_the_first_one(self):
        """The Act gives a week in the first twelve months, not nothing."""
        self.assertEqual(self.annual.entitlement_after(0), 6)

    def test_no_start_date_means_unknown_and_not_zero(self):
        employee = Employee.objects.create(
            user=self.user, staff_id="A1-901", start_date=None
        )
        self.assertIsNone(leave.tenure_months(employee))
        self.assertIsNone(leave.entitlement_for(employee, self.annual))

    def test_a_type_with_no_bands_is_untracked_rather_than_zero(self):
        employee = self._employee_starting(24, "A1-902")
        self.assertFalse(self.unpaid.is_tracked)
        self.assertIsNone(leave.entitlement_for(employee, self.unpaid))

    def test_tenure_is_whole_months_completed(self):
        employee = Employee.objects.create(
            user=make_user("tenure@test.local"),
            staff_id="A1-903",
            start_date=date(2025, 10, 20),
        )
        # On 11 Oct 2026 they are eleven months in, not twelve: the day of
        # the month has not come round yet.
        self.assertEqual(leave.tenure_months(employee, date(2026, 10, 11)), 11)
        self.assertEqual(leave.tenure_months(employee, date(2026, 10, 20)), 12)

    def test_service_cannot_be_negative_before_somebody_starts(self):
        employee = Employee.objects.create(
            user=make_user("future@test.local"),
            staff_id="A1-904",
            start_date=date(2027, 1, 1),
        )
        self.assertEqual(leave.tenure_months(employee, date(2026, 10, 11)), 0)


class LeaveFlowTests(TestCase):
    """Asking, deciding, and what each step does to the balance."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")

        cls.annual = LeaveType.objects.create(
            code="annual", label="Annual leave", order=1
        )
        LeaveEntitlementRule.objects.create(
            leave_type=cls.annual, after_months=0, days=12
        )
        cls.sick = LeaveType.objects.create(
            code="sick",
            label="Sick leave",
            order=2,
            document_required_after_days=3,
        )
        LeaveEntitlementRule.objects.create(
            leave_type=cls.sick, after_months=0, days=10
        )

        cls.hr = make_user("hr.leave@test.local", ["HR"], first_name="Grace")
        cls.worker = make_user("worker.leave@test.local", ["Technician"], first_name="Moses")
        cls.other = make_user("other.leave@test.local", ["Technician"], first_name="Sam")
        cls.supervisor = make_user("sup.leave@test.local", ["Supervisor"])

        cls.sup_employee = Employee.objects.create(
            user=cls.supervisor, staff_id="A1-800", start_date=date(2020, 1, 6)
        )
        cls.employee = Employee.objects.create(
            user=cls.worker,
            staff_id="A1-801",
            start_date=date(2024, 1, 8),
            supervisor=cls.sup_employee,
        )
        cls.stranger = Employee.objects.create(
            user=cls.other, staff_id="A1-802", start_date=date(2024, 1, 8)
        )

    def setUp(self):
        mail.outbox = []

    def _ask(self, employee=None, leave_type=None, start=None, end=None, actor=None):
        return leave.submit_request(
            employee=employee or self.employee,
            leave_type=leave_type or self.annual,
            start_date=start or date(2026, 11, 2),
            end_date=end or date(2026, 11, 4),
            reason="Family matter in Ganta.",
            actor=actor or self.worker,
        )

    # ---- asking ---------------------------------------------------------

    def test_a_request_starts_as_submitted_on_the_shared_trail(self):
        request = self._ask()
        self.assertEqual(request.approval_state, Approval.SUBMITTED)
        self.assertTrue(request.is_waiting)
        self.assertFalse(request.is_settled)

    def test_the_days_are_counted_and_stored_at_the_time(self):
        request = self._ask()
        self.assertEqual(request.working_days, 3)

    def test_a_span_of_only_rest_days_is_refused_with_a_reason(self):
        sunday = date(2026, 11, 1)
        with self.assertRaises(leave.LeaveError) as caught:
            self._ask(start=sunday, end=sunday)
        self.assertIn("rest days", str(caught.exception))

    def test_a_backwards_span_is_refused(self):
        with self.assertRaises(leave.LeaveError):
            self._ask(start=date(2026, 11, 4), end=date(2026, 11, 2))

    # ---- deciding -------------------------------------------------------

    def test_approving_spends_the_days(self):
        request = self._ask()
        before = leave.balance_for(self.employee, self.annual, 2026)
        self.assertEqual(before["taken"], 0)
        self.assertEqual(before["pending"], 3)
        self.assertEqual(before["remaining"], 12, "pending is not spent yet")

        leave.decide_request(
            request=request, decision=Approval.APPROVED, actor=self.hr
        )
        after = leave.balance_for(self.employee, self.annual, 2026)
        self.assertEqual(after["taken"], 3)
        self.assertEqual(after["remaining"], 9)

    def test_declining_spends_nothing(self):
        request = self._ask()
        leave.decide_request(
            request=request,
            decision=Approval.DECLINED,
            actor=self.hr,
            comment="The Zwedru install is that week.",
        )
        balance = leave.balance_for(self.employee, self.annual, 2026)
        self.assertEqual(balance["taken"], 0)
        self.assertEqual(balance["remaining"], 12)
        self.assertTrue(request.is_declined)

    def test_declining_without_a_reason_is_refused(self):
        """The person waiting sees this comment and nothing else."""
        request = self._ask()
        with self.assertRaises(ValueError):
            leave.decide_request(
                request=request, decision=Approval.DECLINED, actor=self.hr, comment="   "
            )
        self.assertEqual(request.approval_state, Approval.SUBMITTED)

    def test_a_settled_request_is_not_decided_twice(self):
        request = self._ask()
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        with self.assertRaises(leave.LeaveError):
            leave.decide_request(
                request=request,
                decision=Approval.DECLINED,
                actor=self.hr,
                comment="changed my mind",
            )

    def test_withdrawing_gives_the_days_back(self):
        request = self._ask()
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        self.assertEqual(leave.balance_for(self.employee, self.annual, 2026)["taken"], 3)

        leave.cancel_request(request=request, actor=self.hr, comment="Install moved.")
        self.assertEqual(leave.balance_for(self.employee, self.annual, 2026)["taken"], 0)

    # ---- balances -------------------------------------------------------

    def test_an_adjustment_is_applied_to_the_balance(self):
        LeaveAdjustment.objects.create(
            employee=self.employee,
            leave_type=self.annual,
            leave_year=2026,
            days=-4,
            reason="Four days taken before go-live, per the paper register.",
            recorded_by=self.hr,
        )
        balance = leave.balance_for(self.employee, self.annual, 2026)
        self.assertEqual(balance["adjustment"], -4)
        self.assertEqual(balance["remaining"], 8)

    def test_a_balance_is_never_stored_on_the_request(self):
        """
        Wiping nothing and recomputing gives the same answer, because there
        is nothing to wipe: the figure is derived every time it is asked for.
        """
        request = self._ask()
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        first = leave.balance_for(self.employee, self.annual, 2026)
        second = leave.balance_for(self.employee, self.annual, 2026)
        self.assertEqual(first, second)
        self.assertFalse(
            [f for f in LeaveRequest._meta.get_fields() if f.name == "balance"],
            "a stored balance column is the thing this module exists without",
        )

    def test_another_employees_leave_does_not_touch_this_ones_balance(self):
        request = self._ask(employee=self.stranger, actor=self.other)
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        self.assertEqual(leave.balance_for(self.employee, self.annual, 2026)["taken"], 0)

    def test_leave_spanning_new_year_is_counted_in_both_years(self):
        request = leave.submit_request(
            employee=self.employee,
            leave_type=self.annual,
            start_date=date(2026, 12, 28),
            end_date=date(2027, 1, 5),
            reason="Travelling.",
            actor=self.worker,
        )
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        in_2026 = leave.days_taken(self.employee, self.annual, 2026)
        in_2027 = leave.days_taken(self.employee, self.annual, 2027)
        self.assertGreater(in_2026, 0)
        self.assertGreater(in_2027, 0)
        self.assertEqual(in_2026 + in_2027, request.working_days)


    def test_the_bulk_balance_path_agrees_with_the_single_one(self):
        """
        The register screen computes balances in bulk because doing it one
        at a time was six hundred round trips over a public database proxy.
        Two code paths for one figure is only safe if they agree, so this
        asserts it rather than assuming it.
        """
        request = self._ask()
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        LeaveAdjustment.objects.create(
            employee=self.employee, leave_type=self.annual, leave_year=2026,
            days=-2, reason="Taken before go-live.", recorded_by=self.hr,
        )
        pending = leave.submit_request(
            employee=self.employee, leave_type=self.sick,
            start_date=date(2026, 12, 1), end_date=date(2026, 12, 2),
            reason="Unwell.", actor=self.worker,
        )
        self.assertTrue(pending.is_waiting)

        types = list(
            LeaveType.objects.filter(is_active=True).prefetch_related("entitlement_rules")
        )
        bulk = leave.balances_for_many([self.employee, self.stranger], year=2026,
                                       leave_types=types)
        for employee in (self.employee, self.stranger):
            for leave_type in types:
                with self.subTest(employee=employee.staff_id, type=leave_type.code):
                    self.assertEqual(
                        bulk[employee.pk][types.index(leave_type)],
                        leave.balance_for(employee, leave_type, 2026),
                    )

    def test_the_register_does_not_cost_a_query_per_employee(self):
        from django.db import connection, reset_queries
        from django.test.utils import override_settings

        def queries_for(headcount):
            while Employee.objects.filter(staff_id__startswith="A1-95").count() < headcount:
                n = Employee.objects.filter(staff_id__startswith="A1-95").count()
                Employee.objects.create(
                    user=make_user(f"reg{n}@test.local"),
                    staff_id=f"A1-95{n}",
                    start_date=date(2024, 1, 8),
                )
            people = list(Employee.objects.filter(is_active=True))
            types = list(
                LeaveType.objects.filter(is_active=True).prefetch_related(
                    "entitlement_rules"
                )
            )
            with override_settings(DEBUG=True):
                reset_queries()
                leave.balances_for_many(people, year=2026, leave_types=types)
                return len(connection.queries)

        self.assertEqual(queries_for(2), queries_for(10))

    # ---- who is away ----------------------------------------------------

    def test_only_approved_leave_makes_somebody_away(self):
        request = self._ask()
        self.assertEqual(
            leave.on_leave_between(date(2026, 11, 2), date(2026, 11, 4)),
            {},
            "a request still waiting is not a fact about the week",
        )
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        away = leave.on_leave_between(date(2026, 11, 2), date(2026, 11, 4))
        self.assertIn(self.worker.pk, away)
        self.assertEqual(away[self.worker.pk].pk, request.pk)

    def test_declined_leave_never_makes_somebody_away(self):
        request = self._ask()
        leave.decide_request(
            request=request, decision=Approval.DECLINED, actor=self.hr, comment="No."
        )
        self.assertEqual(leave.on_leave_between(date(2026, 11, 2), date(2026, 11, 4)), {})

    def test_leave_outside_the_span_is_not_returned(self):
        request = self._ask()
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        self.assertEqual(
            leave.on_leave_between(date(2026, 12, 1), date(2026, 12, 5)), {}
        )

    def test_an_overlapping_request_is_found(self):
        self._ask()
        clashes = leave.overlapping_requests(
            self.employee, date(2026, 11, 3), date(2026, 11, 6)
        )
        self.assertEqual(len(clashes), 1)

    def test_a_withdrawn_request_is_not_a_clash(self):
        request = self._ask()
        leave.cancel_request(request=request, actor=self.worker)
        self.assertEqual(
            leave.overlapping_requests(
                self.employee, date(2026, 11, 3), date(2026, 11, 6)
            ),
            [],
        )


class LeaveNotificationTests(TestCase):
    """
    Who hears what. The privacy rule is the one that matters: a decision
    about somebody's leave goes to them, not to the office.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        cls.annual = LeaveType.objects.create(code="annual", label="Annual leave")
        LeaveEntitlementRule.objects.create(
            leave_type=cls.annual, after_months=0, days=12
        )

        cls.hr = make_user("hr.mail@test.local", ["HR"], first_name="Grace")
        cls.admin = make_user("admin.mail@test.local", ["Admin"])
        cls.worker = make_user("worker.mail@test.local", ["Technician"], first_name="Moses")
        cls.supervisor = make_user("sup.mail@test.local", ["Supervisor"])
        cls.finance = make_user("finance.mail@test.local", ["Finance"])

        cls.sup_employee = Employee.objects.create(
            user=cls.supervisor, staff_id="A1-810", start_date=date(2020, 1, 6)
        )
        cls.employee = Employee.objects.create(
            user=cls.worker,
            staff_id="A1-811",
            start_date=date(2024, 1, 8),
            supervisor=cls.sup_employee,
        )

    def setUp(self):
        mail.outbox = []

    def _ask(self, actor=None):
        return leave.submit_request(
            employee=self.employee,
            leave_type=self.annual,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 4),
            reason="Family matter in Ganta.",
            actor=actor or self.worker,
        )

    def test_a_request_tells_whoever_may_decide_it(self):
        self._ask()
        self.assertEqual(len(mail.outbox), 1)
        recipients = set(mail.outbox[0].to)
        self.assertIn("hr.mail@test.local", recipients)
        self.assertIn("admin.mail@test.local", recipients)

    def test_a_request_does_not_tell_people_who_cannot_decide_it(self):
        self._ask()
        recipients = set(mail.outbox[0].to)
        self.assertNotIn("finance.mail@test.local", recipients)
        self.assertNotIn("sup.mail@test.local", recipients)
        self.assertNotIn("worker.mail@test.local", recipients)

    def test_hr_raising_one_is_not_told_about_their_own_typing(self):
        self._ask(actor=self.hr)
        recipients = set(mail.outbox[0].to) if mail.outbox else set()
        self.assertNotIn("hr.mail@test.local", recipients)

    def test_the_decision_goes_to_the_employee_and_nobody_else(self):
        """
        The privacy guarantee. Somebody's sick leave is between them and HR;
        copying the office on it is a disclosure, not a notification.
        """
        request = self._ask()
        mail.outbox = []
        leave.decide_request(
            request=request, decision=Approval.APPROVED, actor=self.hr
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["worker.mail@test.local"])

    def test_a_declined_request_carries_the_reason_to_the_person(self):
        request = self._ask()
        mail.outbox = []
        leave.decide_request(
            request=request,
            decision=Approval.DECLINED,
            actor=self.hr,
            comment="The Zwedru install runs that week.",
        )
        body = mail.outbox[0].body
        self.assertIn("declined", mail.outbox[0].subject)
        self.assertIn("Zwedru install", body)

    def test_withdrawing_tells_hr_rather_than_the_person_who_did_it(self):
        request = self._ask()
        mail.outbox = []
        leave.cancel_request(request=request, actor=self.worker)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("hr.mail@test.local", mail.outbox[0].to)
        self.assertNotIn("worker.mail@test.local", mail.outbox[0].to)

    def test_hr_cancelling_approved_leave_tells_the_person(self):
        request = self._ask()
        leave.decide_request(request=request, decision=Approval.APPROVED, actor=self.hr)
        mail.outbox = []
        leave.cancel_request(
            request=request, actor=self.hr, comment="The install moved."
        )
        self.assertEqual(mail.outbox[0].to, ["worker.mail@test.local"])

    def test_no_message_carries_a_password_or_a_sign_in_link(self):
        self._ask()
        body = mail.outbox[0].body.lower()
        for word in ("password", "sign-in link", "log in with"):
            self.assertNotIn(word, body)

    def test_a_mail_failure_does_not_lose_the_request(self):
        """
        The guarantee that matters most: somebody has asked for time off and
        whether the mail server agreed is not their problem.
        """
        with patch(
            "django.core.mail.EmailMessage.send", side_effect=OSError("refused")
        ):
            request = self._ask()
        self.assertTrue(LeaveRequest.objects.filter(pk=request.pk).exists())
        self.assertEqual(request.approval_state, Approval.SUBMITTED)

    def test_a_mail_failure_does_not_undo_a_decision(self):
        request = self._ask()
        with patch(
            "django.core.mail.EmailMessage.send", side_effect=OSError("refused")
        ):
            leave.decide_request(
                request=request, decision=Approval.APPROVED, actor=self.hr
            )
        request.refresh_from_db()
        self.assertTrue(request.is_approved)


class LeaveScreenTests(TestCase):
    """
    What each person can reach. Scope qualifies permission here exactly as
    it does for attendance.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        cls.annual = LeaveType.objects.create(code="annual", label="Annual leave")
        LeaveEntitlementRule.objects.create(
            leave_type=cls.annual, after_months=0, days=12
        )

        cls.hr = make_user("hr.screen@test.local", ["HR"])
        cls.worker = make_user("worker.screen@test.local", ["Technician"], first_name="Moses")
        cls.outsider = make_user("outsider.screen@test.local", ["Technician"])
        cls.supervisor = make_user("sup.screen@test.local", ["Supervisor"])

        cls.sup_employee = Employee.objects.create(
            user=cls.supervisor, staff_id="A1-820", start_date=date(2020, 1, 6)
        )
        cls.employee = Employee.objects.create(
            user=cls.worker,
            staff_id="A1-821",
            start_date=date(2024, 1, 8),
            supervisor=cls.sup_employee,
        )
        cls.other_team = Employee.objects.create(
            user=cls.outsider, staff_id="A1-822", start_date=date(2024, 1, 8)
        )

    def setUp(self):
        mail.outbox = []
        self.request = leave.submit_request(
            employee=self.employee,
            leave_type=self.annual,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 4),
            reason="Family matter in Ganta.",
            actor=self.worker,
        )
        self.foreign = leave.submit_request(
            employee=self.other_team,
            leave_type=self.annual,
            start_date=date(2026, 11, 9),
            end_date=date(2026, 11, 10),
            reason="Appointment.",
            actor=self.outsider,
        )
        mail.outbox = []

    def test_an_employee_sees_their_own_balance_on_their_own_screen(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse("hr-leave-mine"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Annual leave")

    def test_an_employee_cannot_reach_the_queue(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("hr-leave-queue")).status_code, 403)

    def test_a_supervisor_sees_their_own_crew_and_not_another(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("hr-leave-queue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Moses")
        self.assertNotContains(response, "A1-822")

    def test_a_supervisor_is_refused_another_teams_request(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(
            self.client.get(
                reverse("hr-leave-detail", args=[self.foreign.pk])
            ).status_code,
            404,
            "scope filters the queryset, so it is not found rather than forbidden",
        )

    def test_a_supervisor_cannot_decide(self):
        """Reading their crew's leave is not holding the pen on it."""
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("hr-leave-decide", args=[self.request.pk]),
            {"decision": "approved", "comment": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.request.refresh_from_db()
        self.assertEqual(self.request.approval_state, Approval.SUBMITTED)

    def test_hr_can_approve_and_the_person_is_told(self):
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("hr-leave-decide", args=[self.request.pk]),
            {"decision": "approved", "comment": "Enjoy it."},
        )
        self.assertEqual(response.status_code, 302)
        self.request.refresh_from_db()
        self.assertTrue(self.request.is_approved)
        self.assertEqual(mail.outbox[0].to, ["worker.screen@test.local"])

    def test_the_form_refuses_an_overlapping_request(self):
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse("hr-leave-create"),
            {
                "leave_type": self.annual.pk,
                "start_date": "2026-11-03",
                "end_date": "2026-11-05",
                "reason": "Same days again.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "overlaps")
        self.assertEqual(
            LeaveRequest.objects.filter(employee=self.employee).count(),
            1,
            "the second request must not have been created",
        )

    def test_an_account_with_no_register_row_is_told_why_rather_than_bounced(self):
        nobody = make_user("nobody.screen@test.local", ["Employee"])
        self.client.force_login(nobody)
        response = self.client.get(reverse("hr-leave-create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "employee register")



    @tag("acceptance")
    @override_settings(TESTING=False)
    def test_a_hanging_mail_server_neither_errors_nor_loses_the_request(self):
        """
        The production incident, end to end.

        A leave request submitted against a mail host that accepts the
        connection and then says nothing came back "Internal Server Error"
        and was gone afterwards: the send blocked on the request thread,
        gunicorn killed the worker at sixty seconds, and the atomic block
        rolled the request back.

        There is no worker killer in a test runner, so the observable here
        is time: before the fix this POST returned only after the hang, and
        in production that wait was fatal.
        """
        import threading
        import time

        started = threading.Event()
        release = threading.Event()

        def hang(*args, **kwargs):
            started.set()
            release.wait(timeout=10)

        self.client.force_login(self.worker)
        began = time.monotonic()
        try:
            with patch("django.core.mail.EmailMessage.send", side_effect=hang):
                response = self.client.post(
                    reverse("hr-leave-create"),
                    {
                        "leave_type": self.annual.pk,
                        "start_date": "2026-12-14",
                        "end_date": "2026-12-16",
                        "reason": "Travelling home for Christmas.",
                    },
                )
            elapsed = time.monotonic() - began
        finally:
            release.set()

        self.assertEqual(response.status_code, 302, "the person must not see an error")
        self.assertTrue(
            LeaveRequest.objects.filter(
                employee=self.employee, start_date=date(2026, 12, 14)
            ).exists(),
            "their request must survive a mail server that never answers",
        )
        self.assertLess(
            elapsed, 2.0,
            "the response waited on the mail server — in production that wait "
            "is what killed the worker and rolled the request back",
        )

    def test_the_withdraw_url_does_not_break_on_a_plain_visit(self):
        """
        Withdrawing changes something, so it is a POST. A GET — a bookmark,
        a hand-typed URL, a crawler — redirects instead of raising, and
        reverses a URL that actually takes no arguments.
        """
        self.client.force_login(self.worker)
        response = self.client.get(reverse("hr-leave-cancel", args=[self.request.pk]))
        self.assertEqual(response.status_code, 302)
        self.request.refresh_from_db()
        self.assertEqual(self.request.approval_state, Approval.SUBMITTED)

    def test_an_employee_cannot_withdraw_somebody_elses_request(self):
        self.client.force_login(self.worker)
        response = self.client.post(
            reverse("hr-leave-cancel", args=[self.foreign.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.approval_state, Approval.SUBMITTED)

    def test_an_employee_cannot_withdraw_their_own_approved_leave(self):
        """
        The days have to go back and the schedule has to be updated, so
        cancelling approved leave is HR's.
        """
        leave.decide_request(
            request=self.request, decision=Approval.APPROVED, actor=self.hr
        )
        self.client.force_login(self.worker)
        self.client.post(reverse("hr-leave-cancel", args=[self.request.pk]))
        self.request.refresh_from_db()
        self.assertTrue(self.request.is_approved)

    def test_the_calendar_shows_approved_leave_only(self):
        """
        A request still waiting is not a fact about the month. Showing it
        would have supervisors planning around days that may never be
        granted.
        """
        self.client.force_login(self.hr)
        response = self.client.get(reverse("hr-leave-calendar"), {"month": "2026-11-01"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nobody is away this month")
        self.assertNotContains(response, "A1-821")

        leave.decide_request(
            request=self.request, decision=Approval.APPROVED, actor=self.hr
        )
        response = self.client.get(reverse("hr-leave-calendar"), {"month": "2026-11-01"})
        self.assertNotContains(response, "Nobody is away this month")
        self.assertContains(response, "A1-821")
        self.assertContains(response, "1 person away this month")

    def test_the_balances_screen_names_who_has_no_start_date(self):
        Employee.objects.create(
            user=make_user("nodate.screen@test.local"),
            staff_id="A1-823",
            start_date=None,
        )
        self.client.force_login(self.hr)
        response = self.client.get(reverse("hr-leave-balances"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no start date")


class SickNoteTests(TestCase):
    """
    The statute allows three undocumented sick days in a year and asks for
    documentation beyond that. Asked for at the fourth day, not the first.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        cls.sick = LeaveType.objects.create(
            code="sick", label="Sick leave", document_required_after_days=3
        )
        LeaveEntitlementRule.objects.create(
            leave_type=cls.sick, after_months=0, days=10
        )
        cls.hr = make_user("hr.sick@test.local", ["HR"])
        cls.worker = make_user("worker.sick@test.local", ["Technician"])
        cls.employee = Employee.objects.create(
            user=cls.worker, staff_id="A1-830", start_date=date(2024, 1, 8)
        )

    def setUp(self):
        mail.outbox = []
        self.client.force_login(self.worker)

    def _post(self, start, end):
        return self.client.post(
            reverse("hr-leave-create"),
            {
                "leave_type": self.sick.pk,
                "start_date": start,
                "end_date": end,
                "reason": "Malaria.",
            },
        )

    def test_three_days_need_no_document(self):
        response = self._post("2026-11-02", "2026-11-04")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_a_fourth_day_asks_for_one(self):
        response = self._post("2026-11-02", "2026-11-05")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "supporting document")
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_the_count_is_across_the_year_and_not_per_request(self):
        first = leave.submit_request(
            employee=self.employee,
            leave_type=self.sick,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 4),
            reason="Fever.",
            actor=self.worker,
        )
        leave.decide_request(
            request=first, decision=Approval.APPROVED, actor=self.hr
        )
        response = self._post("2026-11-02", "2026-11-02")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already used 3")


class LeaveMeetsAttendanceTests(TestCase):
    """
    The integration that made leave worth building.

    Before it, an approved leave day had no derived attendance row, and the
    roll-call reads a missing row as absent — so somebody on approved annual
    leave sat in their supervisor's exceptions list next to the person who
    simply did not turn up. These tests pin the fix, and pin that payroll
    can still tell the two apart.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        PolicySetting.objects.create(
            key=PolicySetting.WORKDAY_START, value="08:00"
        )
        PolicySetting.objects.create(
            key=PolicySetting.LATE_AFTER_MINUTES, value="15"
        )

        cls.annual = LeaveType.objects.create(
            code="annual", label="Annual leave", is_paid=True, order=1
        )
        LeaveEntitlementRule.objects.create(
            leave_type=cls.annual, after_months=0, days=24
        )
        cls.unpaid = LeaveType.objects.create(
            code="unpaid", label="Unpaid leave", is_paid=False, order=2
        )

        cls.hr = make_user("hr.attend@test.local", ["HR"])
        cls.worker = make_user("worker.attend@test.local", ["Technician"], first_name="Moses")
        cls.employee = Employee.objects.create(
            user=cls.worker, staff_id="A1-840", start_date=date(2024, 1, 8)
        )

    def setUp(self):
        mail.outbox = []
        from django.utils import timezone as dj

        today = dj.localdate()
        # A Monday four weeks back, so the window is in the past (the stored
        # table only describes days that have happened) and holds no Sunday.
        self.monday = today - timedelta(days=today.weekday() + 28)
        self.wednesday = self.monday + timedelta(days=2)

    def _approve(self, start=None, end=None, leave_type=None):
        request = leave.submit_request(
            employee=self.employee,
            leave_type=leave_type or self.annual,
            start_date=start or self.monday,
            end_date=end or self.wednesday,
            reason="Family matter.",
            actor=self.worker,
        )
        return leave.decide_request(
            request=request, decision=Approval.APPROVED, actor=self.hr
        )

    # ---- the derived table ----------------------------------------------

    def test_approving_leave_writes_on_leave_days(self):
        self._approve()
        rows = AttendanceDay.objects.filter(
            employee=self.employee, date__gte=self.monday, date__lte=self.wednesday
        )
        self.assertEqual(rows.count(), 3)
        for row in rows:
            self.assertEqual(row.state, AttendanceDay.ON_LEAVE)
            self.assertTrue(row.on_leave)
            self.assertEqual(row.leave_type, self.annual)

    def test_leave_in_the_future_writes_no_rows(self):
        """
        AttendanceDay describes days that have happened. Leave granted for
        next month belongs on the leave calendar until the day arrives.
        """
        from django.utils import timezone as dj

        ahead = dj.localdate() + timedelta(days=30)
        ahead -= timedelta(days=ahead.weekday())  # a Monday
        self._approve(start=ahead, end=ahead + timedelta(days=2))
        self.assertFalse(
            AttendanceDay.objects.filter(date__gte=ahead).exists(),
            "no fictional history in the table every report reads",
        )

    def test_cancelling_approved_leave_restates_the_days(self):
        request = self._approve()
        self.assertTrue(
            AttendanceDay.objects.filter(
                employee=self.employee, state=AttendanceDay.ON_LEAVE
            ).exists()
        )
        leave.cancel_request(request=request, actor=self.hr, comment="Install moved.")
        self.assertFalse(
            AttendanceDay.objects.filter(
                employee=self.employee, state=AttendanceDay.ON_LEAVE
            ).exists(),
            "days that were accounted for must go back to being unexplained",
        )

    def test_the_rebuild_stays_idempotent_with_leave_in_it(self):
        """
        The promise AttendanceDay has always made: wipe it, rebuild it, and
        it comes back identical. Leave widened the inputs, not the promise.
        """
        self._approve()
        before = sorted(
            AttendanceDay.objects.values_list(
                "employee_id", "date", "state", "on_leave", "leave_type_id"
            )
        )
        AttendanceDay.objects.all().delete()
        rebuild_attendance_days()
        after = sorted(
            AttendanceDay.objects.values_list(
                "employee_id", "date", "state", "on_leave", "leave_type_id"
            )
        )
        self.assertEqual(before, after)

    # ---- the roll-call --------------------------------------------------

    @tag("acceptance")
    def test_somebody_on_leave_does_not_read_as_absent(self):
        """The bug this integration exists to fix."""
        self._approve()
        rows = services.roll_call(on_date=self.monday)
        row = next(r for r in rows if r["employee"] == self.employee)
        self.assertEqual(row["state"], AttendanceDay.ON_LEAVE)
        self.assertEqual(row["leave_type"], self.annual)

    def test_being_on_leave_is_not_sorted_as_an_exception(self):
        """
        Exceptions go to the top of the roll-call. Approved leave is
        accounted for, so it belongs at the bottom with the people who
        simply turned up — otherwise the exceptions list stops meaning
        anything.
        """
        absent = Employee.objects.create(
            user=make_user("absent.attend@test.local"),
            staff_id="A1-841",
            start_date=date(2024, 1, 8),
        )
        self._approve()
        rows = services.roll_call(on_date=self.monday)
        order = [r["employee"].staff_id for r in rows]
        self.assertLess(
            order.index(absent.staff_id),
            order.index(self.employee.staff_id),
            "the unexplained absence should sort above the approved one",
        )

    def test_future_leave_still_shows_on_the_roll_call_for_that_day(self):
        """
        The gap that stored rows alone could not close: nobody on leave
        files a clock event, so nothing triggers a rebuild for their day.
        The roll-call reads approved leave directly as well.
        """
        from django.utils import timezone as dj

        ahead = dj.localdate() + timedelta(days=30)
        ahead -= timedelta(days=ahead.weekday())
        self._approve(start=ahead, end=ahead + timedelta(days=2))
        self.assertFalse(AttendanceDay.objects.filter(date=ahead).exists())

        rows = services.roll_call(on_date=ahead)
        row = next(r for r in rows if r["employee"] == self.employee)
        self.assertEqual(
            row["state"],
            AttendanceDay.ON_LEAVE,
            "with no stored row, the roll-call must still know they are away",
        )

    def test_clocking_in_on_approved_leave_keeps_the_clock_event(self):
        """
        Somebody recalled from leave worked. The event log is the record and
        is never discarded; the day reads as worked and carries the leave
        flag, which is what makes it visible as an exception.
        """
        import uuid
        from datetime import datetime, time

        from django.utils import timezone as dj

        self._approve()
        moment = dj.make_aware(datetime.combine(self.monday, time(7, 55)))
        services.record_clock_event(
            employee=self.employee,
            kind="in",
            client_uuid=uuid.uuid4(),
            device_timestamp=moment,
            location_unavailable=True,
            require_code=False,
        )

        row = AttendanceDay.objects.get(employee=self.employee, date=self.monday)
        self.assertEqual(row.state, AttendanceDay.PRESENT)
        self.assertTrue(row.on_leave, "the day was still granted off")

        rolled = services.roll_call(on_date=self.monday)
        entry = next(r for r in rolled if r["employee"] == self.employee)
        self.assertTrue(entry["worked_while_on_leave"])

    # ---- the month and the export ---------------------------------------

    @tag("acceptance")
    def test_paid_leave_is_not_counted_as_a_day_worked(self):
        """
        `days_present` used to mean "not absent". With a fourth state that
        silently counts paid leave as attendance, which is money.
        """
        self._approve()
        summary = services.monthly_summary(self.monday.year, self.monday.month)
        row = next(r for r in summary["rows"] if r["employee"] == self.employee)
        self.assertEqual(row["days_present"], 0)
        self.assertEqual(row["days_on_leave_paid"], 3)
        self.assertEqual(row["days_on_leave_unpaid"], 0)

    def test_unpaid_leave_lands_in_its_own_column(self):
        self._approve(leave_type=self.unpaid)
        summary = services.monthly_summary(self.monday.year, self.monday.month)
        row = next(r for r in summary["rows"] if r["employee"] == self.employee)
        self.assertEqual(row["days_on_leave_unpaid"], 3)
        self.assertEqual(row["days_on_leave_paid"], 0)

    def test_an_explained_absence_is_not_counted_as_an_absence(self):
        self._approve()
        summary = services.monthly_summary(self.monday.year, self.monday.month)
        row = next(r for r in summary["rows"] if r["employee"] == self.employee)
        self.assertEqual(row["days_absent"], 0)

    def test_a_day_worked_while_on_leave_is_counted_once_and_flagged(self):
        import uuid
        from datetime import datetime, time

        from django.utils import timezone as dj

        self._approve()
        services.record_clock_event(
            employee=self.employee,
            kind="in",
            client_uuid=uuid.uuid4(),
            device_timestamp=dj.make_aware(datetime.combine(self.monday, time(7, 55))),
            location_unavailable=True,
            require_code=False,
        )
        summary = services.monthly_summary(self.monday.year, self.monday.month)
        row = next(r for r in summary["rows"] if r["employee"] == self.employee)
        self.assertEqual(row["days_present"], 1)
        self.assertEqual(row["worked_while_on_leave"], 1)
        self.assertEqual(
            row["days_on_leave_paid"], 2, "the worked day is not also a leave day"
        )


    def test_the_attendance_report_accounts_for_the_leave_days(self):
        """
        The month has to add up. Without a leave column, somebody away for a
        fortnight reads as neither present nor absent — two weeks simply
        missing from their row, with nothing on screen saying where they
        went. This was a real gap, found by reading rather than by a test.
        """
        from reports.reports import attendance_summary

        self._approve()
        rows = attendance_summary(self.hr, self.monday.year, self.monday.month)
        row = next(r for r in rows if r["staff_id"] == "A1-840")
        self.assertEqual(row["days_on_leave"], 3)
        self.assertEqual(row["days_present"], 0)
        self.assertEqual(row["days_absent"], 0)

    def test_the_export_carries_the_leave_columns(self):
        self._approve()
        self.client.force_login(self.hr)
        response = self.client.get(
            "/hr/report/", {"year": self.monday.year, "month": self.monday.month, "export": "csv"}
        )
        self.assertEqual(response.status_code, 200)
        lines = response.content.decode().splitlines()
        self.assertIn("days_on_leave_paid", lines[0])
        self.assertIn("days_on_leave_unpaid", lines[0])
        row = next(line for line in lines if "A1-840" in line)
        self.assertIn(",3,", row, "three paid leave days should appear in the row")


class SchedulingKnowsAboutLeaveTests(TestCase):
    """
    The highest-value integration: the assignment pickers say who is away.

    They mark rather than filter, deliberately. A technician can be recalled
    from leave, and a scheduler who cannot see the person they need will
    assign the work outside the system — which is worse than a clash
    somebody can see.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        cls.annual = LeaveType.objects.create(code="annual", label="Annual leave")
        LeaveEntitlementRule.objects.create(
            leave_type=cls.annual, after_months=0, days=24
        )
        cls.hr = make_user("hr.sched@test.local", ["HR"])
        # Assigning a ticket needs `assign_ticket`, which HR does not hold —
        # deciding leave and dispatching work are different desks.
        cls.dispatcher = make_user("admin.sched@test.local", ["Admin"])
        cls.away_user = make_user(
            "away.sched@test.local", ["Technician"], first_name="Moses", last_name="Toe"
        )
        cls.here_user = make_user(
            "here.sched@test.local", ["Technician"], first_name="Sam", last_name="Kollie"
        )
        cls.employee = Employee.objects.create(
            user=cls.away_user, staff_id="A1-850", start_date=date(2024, 1, 8)
        )
        Employee.objects.create(
            user=cls.here_user, staff_id="A1-851", start_date=date(2024, 1, 8)
        )

    def setUp(self):
        from django.utils import timezone as dj

        mail.outbox = []
        today = dj.localdate()
        self.start = today + timedelta(days=7 - today.weekday())  # next Monday
        self.end = self.start + timedelta(days=4)
        request = leave.submit_request(
            employee=self.employee,
            leave_type=self.annual,
            start_date=self.start,
            end_date=self.end,
            reason="Travelling.",
            actor=self.away_user,
        )
        leave.decide_request(
            request=request, decision=Approval.APPROVED, actor=self.hr
        )
        mail.outbox = []

    def test_the_away_label_names_the_dates(self):
        labels = leave.away_labels()
        self.assertIn(self.away_user.pk, labels)
        self.assertIn("on leave", labels[self.away_user.pk])
        self.assertNotIn(self.here_user.pk, labels)

    def test_a_clash_is_found_for_the_dates_and_not_outside_them(self):
        self.assertIsNotNone(leave.conflict_for(self.away_user, self.start, self.end))
        self.assertIsNone(
            leave.conflict_for(self.away_user, self.end + timedelta(days=14))
        )
        self.assertIsNone(leave.conflict_for(self.here_user, self.start, self.end))

    def test_nobody_is_removed_from_the_picker(self):
        """
        Marked, not filtered. A form that refuses a legitimate assignment
        gets worked around by not using the form.
        """
        from fieldjobs.forms import FieldJobForm

        form = FieldJobForm()
        choices = [str(label) for _value, label in form.fields["assigned_to"].choices]
        self.assertTrue(
            any("Moses" in label for label in choices),
            "the person on leave must still be assignable",
        )
        self.assertTrue(any("on leave" in label for label in choices))
        self.assertTrue(any("Sam" in label and "on leave" not in label for label in choices))

    def test_the_task_picker_marks_who_is_away(self):
        from projects.forms import TaskForm

        form = TaskForm()
        choices = [str(label) for _value, label in form.fields["assignee"].choices]
        self.assertTrue(any("on leave" in label for label in choices))


    def test_marking_who_is_away_does_not_cost_a_query_per_request(self):
        """
        `LeaveRequest.is_approved` reads the approval trail, so filtering a
        list of requests with it costs one round trip per row. Three
        assignment pickers render this on every page load, so the cost must
        not grow with how much leave the company has booked.
        """
        from django.db import connection, reset_queries
        from django.test.utils import override_settings

        def queries_for(count):
            while LeaveRequest.objects.count() > count:
                LeaveRequest.objects.order_by("-pk").first().delete()
            while LeaveRequest.objects.count() < count:
                extra = Employee.objects.create(
                    user=make_user(f"bulk{LeaveRequest.objects.count()}@test.local"),
                    staff_id=f"A1-9{LeaveRequest.objects.count():02d}",
                    start_date=date(2024, 1, 8),
                )
                request = leave.submit_request(
                    employee=extra,
                    leave_type=self.annual,
                    start_date=self.start,
                    end_date=self.end,
                    reason="Bulk.",
                    actor=self.hr,
                )
                leave.decide_request(
                    request=request, decision=Approval.APPROVED, actor=self.hr
                )
            with override_settings(DEBUG=True):
                reset_queries()
                leave.away_labels()
                return len(connection.queries)

        few = queries_for(2)
        many = queries_for(12)
        self.assertEqual(
            few,
            many,
            f"{few} queries for 2 requests but {many} for 12 — this is the "
            "N+1 that approvals.latest_decisions exists to prevent",
        )

    def test_assigning_a_ticket_into_somebodys_leave_warns_and_still_assigns(self):
        from config.models import ServiceType, StatusOption
        from crm.models import Customer, Ticket

        service = ServiceType.objects.create(code="solar", name="Solar")
        status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        customer = Customer.objects.create(name="Duport Road Clinic")
        ticket = Ticket.objects.create(
            reference="TKT-9001",
            customer=customer,
            service_type=service,
            status=status,
            description="Inverter fault.",
        )

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse("crm-ticket-assign", args=[ticket.pk]),
            {
                "assigned_to": self.away_user.pk,
                "start_date": self.start.isoformat(),
                "due_date": self.end.isoformat(),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(
            ticket.assigned_to,
            self.away_user,
            "the assignment stands — this is a warning, not a refusal",
        )
        warnings = [
            str(m) for m in response.context["messages"] if m.level_tag == "warning"
        ]
        self.assertTrue(warnings, "the clash must not happen silently")
        self.assertIn("approved annual leave", warnings[0])


class LeaveInTheAttentionQueueTests(TestCase):
    """
    A request that nobody notices is the failure mode the emails only half
    solve: an inbox is read once. The queue keeps it visible until it is
    cleared, and shows it only to somebody who can clear it.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        cls.annual = LeaveType.objects.create(code="annual", label="Annual leave")
        LeaveEntitlementRule.objects.create(
            leave_type=cls.annual, after_months=0, days=24
        )
        cls.hr = make_user("hr.queue@test.local", ["HR"])
        cls.worker = make_user("worker.queue@test.local", ["Technician"], first_name="Moses")
        cls.supervisor = make_user("sup.queue@test.local", ["Supervisor"])
        cls.employee = Employee.objects.create(
            user=cls.worker, staff_id="A1-860", start_date=date(2024, 1, 8)
        )

    def setUp(self):
        from django.utils import timezone as dj

        mail.outbox = []
        start = dj.localdate() + timedelta(days=21)
        self.request = leave.submit_request(
            employee=self.employee,
            leave_type=self.annual,
            start_date=start,
            end_date=start + timedelta(days=2),
            reason="Family matter.",
            actor=self.worker,
        )
        mail.outbox = []

    def test_a_waiting_request_is_in_hrs_queue(self):
        from dashboard.services import attention_count, attention_queue

        kinds = [item.kind for item in attention_queue(self.hr)]
        self.assertIn("Leave", kinds)
        self.assertGreaterEqual(attention_count(self.hr), 1)

    def test_it_is_not_in_the_queue_of_somebody_who_cannot_decide_it(self):
        """
        An item nobody can act on does not belong in a queue called "needs
        your attention" — and a supervisor reads leave without deciding it.
        """
        from dashboard.services import attention_queue

        self.assertNotIn("Leave", [i.kind for i in attention_queue(self.supervisor)])
        self.assertNotIn("Leave", [i.kind for i in attention_queue(self.worker)])

    def test_a_decided_request_leaves_the_queue(self):
        from dashboard.services import attention_count, attention_queue

        leave.decide_request(
            request=self.request, decision=Approval.APPROVED, actor=self.hr
        )
        self.assertNotIn("Leave", [i.kind for i in attention_queue(self.hr)])
        self.assertEqual(attention_count(self.hr), 0)

    def test_the_bell_count_agrees_with_the_queue(self):
        """
        The badge sends people to the list. If they disagree, the badge is
        sending somebody to look for something that is not there.
        """
        from dashboard.services import attention_count, attention_queue

        leave_items = [i for i in attention_queue(self.hr) if i.kind == "Leave"]
        self.assertEqual(len(leave_items), attention_count(self.hr))

    def test_leave_starting_within_days_is_marked_urgent(self):
        from django.utils import timezone as dj

        from dashboard.services import attention_queue

        soon = dj.localdate() + timedelta(days=1)
        leave.submit_request(
            employee=self.employee,
            leave_type=self.annual,
            start_date=soon,
            end_date=soon,
            reason="Tomorrow.",
            actor=self.worker,
        )
        tones = {
            i.label: i.tone for i in attention_queue(self.hr) if i.kind == "Leave"
        }
        self.assertIn("over", tones.values())
