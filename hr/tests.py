"""
Attendance derivation tests.

The one the brief calls for by name: wiping the derived table and rebuilding
it from the append-only event log reconstructs it identically. The rest
cover the correction rules — corrections supersede rather than overwrite,
and a blank reason cannot be submitted.
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from config.models import CorrectionReason, PolicySetting

from .models import (
    AttendanceCode,
    AttendanceCorrection,
    AttendanceDay,
    AttendanceEvent,
    Employee,
)
from .services import rebuild_attendance_days

User = get_user_model()


class AttendanceRebuildTests(TestCase):
    def setUp(self):
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")
        self.reason = CorrectionReason.objects.create(
            code="forgot_to_clock", label="Forgot to clock in/out"
        )

        self.supervisor = User.objects.create_user(username="sup", email="sup@test.local")
        user = User.objects.create_user(username="tech", email="tech@test.local")
        self.employee = Employee.objects.create(user=user, staff_id="A1-001")

        # Three days of ordinary work.
        base = timezone.now().replace(hour=7, minute=55, second=0, microsecond=0)
        self.base = base - timedelta(days=3)
        for day in range(3):
            start = self.base + timedelta(days=day)
            self._event(AttendanceEvent.CLOCK_IN, start)
            self._event(AttendanceEvent.CLOCK_OUT, start + timedelta(hours=8))

    def _event(self, kind, when):
        return AttendanceEvent.objects.create(
            employee=self.employee,
            kind=kind,
            client_uuid=uuid.uuid4(),
            device_timestamp=when,
        )

    @staticmethod
    def _fingerprint():
        """Everything the derived table claims, in a comparable form."""
        return sorted(
            (
                row.employee_id,
                row.date,
                row.first_in,
                row.last_out,
                row.total_minutes,
                row.state,
                row.still_clocked_in,
                row.correction_count,
            )
            for row in AttendanceDay.objects.all()
        )

    def test_wiped_derived_table_reconstructs_identically(self):
        rebuild_attendance_days()
        original = self._fingerprint()
        self.assertEqual(len(original), 3)

        AttendanceDay.objects.all().delete()
        self.assertEqual(AttendanceDay.objects.count(), 0)

        rebuild_attendance_days()
        self.assertEqual(self._fingerprint(), original)

    def test_rebuild_is_idempotent(self):
        rebuild_attendance_days()
        first = self._fingerprint()
        rebuild_attendance_days()
        rebuild_attendance_days()
        self.assertEqual(self._fingerprint(), first)

    def test_hours_are_derived_from_the_paired_events(self):
        rebuild_attendance_days()
        day = AttendanceDay.objects.order_by("date").first()
        self.assertEqual(day.total_minutes, 8 * 60)
        self.assertEqual(day.state, AttendanceDay.PRESENT)
        self.assertFalse(day.still_clocked_in)

    def test_late_arrival_is_judged_against_the_settings_policy(self):
        late_day = self.base + timedelta(days=5, minutes=40)  # 08:35, past the grace
        self._event(AttendanceEvent.CLOCK_IN, late_day)
        self._event(AttendanceEvent.CLOCK_OUT, late_day + timedelta(hours=6))

        rebuild_attendance_days()
        day = AttendanceDay.objects.get(date=timezone.localtime(late_day).date())
        self.assertEqual(day.state, AttendanceDay.LATE)

    def test_correction_supersedes_without_touching_the_original_event(self):
        event = AttendanceEvent.objects.filter(kind=AttendanceEvent.CLOCK_IN).first()
        original = event.device_timestamp

        AttendanceCorrection.objects.create(
            event=event,
            action=AttendanceCorrection.AMEND_TIME,
            original_timestamp=original,
            corrected_timestamp=original - timedelta(hours=1),
            reason=self.reason,
            note="Phone was flat at the start of the shift",
            corrected_by=self.supervisor,
        )

        event.refresh_from_db()
        self.assertEqual(event.device_timestamp, original, "the original row must be untouched")
        self.assertEqual(event.effective_timestamp, original - timedelta(hours=1))

        rebuild_attendance_days()
        day = AttendanceDay.objects.get(date=timezone.localtime(original).date())
        self.assertEqual(day.total_minutes, 9 * 60)
        self.assertEqual(day.correction_count, 1)

    def test_voiding_an_event_removes_it_from_the_derived_figures(self):
        event = AttendanceEvent.objects.filter(kind=AttendanceEvent.CLOCK_OUT).first()
        AttendanceCorrection.objects.create(
            event=event,
            action=AttendanceCorrection.VOID,
            original_timestamp=event.device_timestamp,
            reason=self.reason,
            corrected_by=self.supervisor,
        )

        rebuild_attendance_days()
        day = AttendanceDay.objects.get(date=timezone.localtime(event.device_timestamp).date())
        self.assertTrue(day.still_clocked_in)
        self.assertEqual(day.total_minutes, 0)
        self.assertEqual(AttendanceEvent.objects.filter(pk=event.pk).count(), 1)

    def test_the_same_client_uuid_cannot_create_a_second_row(self):
        """Resubmission from a second device is idempotent, not a duplicate."""
        from django.db.utils import IntegrityError

        shared = uuid.uuid4()
        AttendanceEvent.objects.create(
            employee=self.employee,
            kind=AttendanceEvent.CLOCK_IN,
            client_uuid=shared,
            device_timestamp=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            AttendanceEvent.objects.create(
                employee=self.employee,
                kind=AttendanceEvent.CLOCK_IN,
                client_uuid=shared,
                device_timestamp=timezone.now(),
            )


class EmployeeRegisterTests(TestCase):
    def test_deactivation_preserves_history(self):
        user = User.objects.create_user(username="gone", email="gone@test.local")
        employee = Employee.objects.create(user=user, staff_id="A1-999")
        AttendanceEvent.objects.create(
            employee=employee,
            kind=AttendanceEvent.CLOCK_IN,
            client_uuid=uuid.uuid4(),
            device_timestamp=timezone.now(),
        )

        employee.deactivate()
        employee.refresh_from_db()

        self.assertFalse(employee.is_active)
        self.assertIsNotNone(employee.deactivated_at)
        self.assertEqual(employee.attendance_events.count(), 1)

    def test_register_holds_no_pay_or_salary_field(self):
        """Payroll is Phase Two; no field anticipates it, not even 'for later'."""
        field_names = {field.name.lower() for field in Employee._meta.get_fields()}
        for forbidden in ("salary", "pay", "wage", "rate", "bank", "account_number"):
            self.assertNotIn(forbidden, field_names)


class ClockScreenTests(TestCase):
    """
    The mobile clock, through the view a phone actually posts to. These
    cover the offline contract and the two refusals the release gate names.
    """

    def setUp(self):
        from django.core.management import call_command

        from accounts.models import Role, UserRole

        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")
        self.reason = CorrectionReason.objects.create(code="forgot", label="Forgot to clock in")

        self.supervisor = User.objects.create_user(
            username="sup@test.local", email="sup@test.local", password="Testing!12345"
        )
        self.worker = User.objects.create_user(
            username="emp@test.local", email="emp@test.local", password="Testing!12345"
        )
        for user, role in ((self.supervisor, "Supervisor"), (self.worker, "Employee")):
            user.must_reset_password = False
            user.save(update_fields=["must_reset_password"])
            UserRole.objects.create(user=user, role=Role.objects.get(name=role))

        self.sup_employee = Employee.objects.create(user=self.supervisor, staff_id="A1-001")
        self.employee = Employee.objects.create(
            user=self.worker, staff_id="A1-002", supervisor=self.sup_employee
        )
        # Clocking needs the code posted where the crew works. These tests are
        # about the clock rules, so they supply a good one and vary the rest.
        self.code = AttendanceCode.objects.create(
            description="Main office",
            location_name="Ganta yard",
            expires_at=timezone.now() + timedelta(days=28),
        )

    def _post(self, kind, client_uuid, **extra):
        payload = {"kind": kind, "client_uuid": client_uuid, "code": self.code.short_code}
        payload.update(extra)
        return self.client.post(
            "/hr/clock/event/", payload, headers={"x-requested-with": "XMLHttpRequest"}
        )

    @tag("acceptance")
    def test_clocking_out_with_nothing_open_is_refused_with_an_actionable_error(self):
        self.client.force_login(self.worker)
        response = self._post("out", str(uuid.uuid4()), location_unavailable="1")
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertFalse(body["ok"])
        # It says what to do about it, not just that it failed.
        self.assertIn("correction", body["message"])
        self.assertEqual(AttendanceEvent.objects.count(), 0)

    @tag("acceptance")
    def test_the_same_client_uuid_records_one_event(self):
        self.client.force_login(self.worker)
        client_uuid = str(uuid.uuid4())
        first = self._post("in", client_uuid, location_unavailable="1")
        second = self._post("in", client_uuid, location_unavailable="1")

        self.assertEqual(first.status_code, 200)
        self.assertTrue(second.json()["ok"])
        self.assertEqual(AttendanceEvent.objects.filter(employee=self.employee).count(), 1)

    @tag("acceptance")
    def test_a_missing_gps_fix_never_blocks_the_clock(self):
        self.client.force_login(self.worker)
        self._post("in", str(uuid.uuid4()), location_unavailable="1")
        event = AttendanceEvent.objects.get()
        self.assertTrue(event.location_unavailable)
        self.assertIsNone(event.latitude)

    def test_clocking_in_twice_is_refused(self):
        self.client.force_login(self.worker)
        self._post("in", str(uuid.uuid4()), location_unavailable="1")
        response = self._post("in", str(uuid.uuid4()), location_unavailable="1")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AttendanceEvent.objects.count(), 1)

    def test_a_clock_event_rebuilds_the_derived_day(self):
        self.client.force_login(self.worker)
        self._post("in", str(uuid.uuid4()), location_unavailable="1")
        day = AttendanceDay.objects.get(employee=self.employee)
        self.assertTrue(day.still_clocked_in)
        self.assertNotEqual(day.state, AttendanceDay.ABSENT)

    @tag("acceptance")
    def test_a_supervisor_sees_only_their_own_team_on_the_roll_call(self):
        stranger = User.objects.create_user(
            username="other@test.local", email="other@test.local", password="Testing!12345"
        )
        Employee.objects.create(user=stranger, staff_id="A1-999")

        self.client.force_login(self.supervisor)
        body = self.client.get("/hr/roll-call/").content.decode()
        self.assertIn("A1-002", body, "their own team member must appear")
        self.assertNotIn("A1-999", body, "another team's employee must not")

    @tag("acceptance")
    def test_a_correction_preserves_the_original_and_needs_a_reason(self):
        self.client.force_login(self.worker)
        self._post("in", str(uuid.uuid4()), location_unavailable="1")
        event = AttendanceEvent.objects.get()
        original = event.device_timestamp

        self.client.force_login(self.supervisor)
        # A blank reason cannot submit.
        self.client.post(f"/hr/events/{event.pk}/correct/", {"action": "amend_time", "reason": ""})
        self.assertEqual(AttendanceCorrection.objects.count(), 0)

        corrected = (original - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(
            f"/hr/events/{event.pk}/correct/",
            {"action": "amend_time", "corrected_timestamp": corrected, "reason": self.reason.pk},
        )
        correction = AttendanceCorrection.objects.get()
        event.refresh_from_db()

        self.assertEqual(correction.corrected_by, self.supervisor)
        # The original row is untouched; the correction supersedes it.
        self.assertEqual(event.device_timestamp, original)
        self.assertEqual(correction.original_timestamp, original)
        self.assertNotEqual(event.effective_timestamp, original)

    @tag("acceptance")
    def test_the_monthly_export_keeps_its_column_contract(self):
        """
        These headers are what a Phase Two payroll engine reads. The report
        belongs to HR — a Supervisor runs the roll-call, not the month.
        """
        from accounts.models import Role, UserRole

        hr_user = User.objects.create_user(
            username="hr@test.local", email="hr@test.local", password="Testing!12345"
        )
        hr_user.must_reset_password = False
        hr_user.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=hr_user, role=Role.objects.get(name="HR"))

        self.assertEqual(
            self.client.get("/hr/report/?export=csv").status_code, 302,
            "signed out, the report redirects to sign in",
        )
        self.client.force_login(self.supervisor)
        self.assertEqual(
            self.client.get("/hr/report/").status_code, 403,
            "a Supervisor runs the roll-call, not the monthly report",
        )

        self.client.force_login(hr_user)
        response = self.client.get("/hr/report/?export=csv")
        self.assertEqual(response.status_code, 200)
        header = response.content.decode().splitlines()[0]
        self.assertEqual(
            header,
            "staff_id,surname,first_name,department,days_present,days_absent,"
            "late_arrivals,total_hours,corrections",
        )
        # No pay, rate or salary column exists anywhere in it.
        for banned in ("salary", "rate", "pay", "wage"):
            self.assertNotIn(banned, header)


class AttendanceReachabilityTests(TestCase):
    """
    HR could create an employee and never see a day of their attendance.

    Every attendance screen existed, worked, and was correctly gated -- and
    nothing anywhere linked to any of them. The sidebar's one HR entry goes
    to the register, and the register was a dead end. A permission that
    cannot be reached is not a permission anybody has.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")

        from accounts.models import Role, UserRole

        def person(email, role):
            user = User.objects.create_user(
                username=email, email=email, password="Testing!12345",
            )
            user.must_reset_password = False
            user.save(update_fields=["must_reset_password"])
            UserRole.objects.create(user=user, role=Role.objects.get(name=role))
            return user

        cls.hr = person("hr@test.local", "HR")
        cls.executive = person("exec@test.local", "Executive")
        cls.worker = person("worker@test.local", "Technician")
        cls.employee = Employee.objects.create(user=cls.worker, staff_id="A1-100")

    def _as(self, user):
        self.client.force_login(user)

    # -- the complaint ----------------------------------------------------

    def test_hr_can_open_every_attendance_screen(self):
        self._as(self.hr)
        for name, args in (
            ("hr-roll-call", []),
            ("hr-monthly-report", []),
            ("hr-employee-attendance", [self.employee.pk]),
        ):
            with self.subTest(screen=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(
                    response.status_code, 200,
                    f"HR holds the permission for {name} and must be able to load it",
                )

    def test_the_register_links_hr_to_the_attendance_screens(self):
        """
        Gated, working and unreachable is the same as absent to the person
        using it, so the links are worth asserting and not just the routes.
        """
        self._as(self.hr)
        body = self.client.get(reverse("hr-employees")).content.decode()

        self.assertIn(reverse("hr-roll-call"), body, "no way through to roll-call")
        self.assertIn(reverse("hr-monthly-report"), body, "no way through to the report")
        self.assertIn(
            reverse("hr-employee-attendance", args=[self.employee.pk]), body,
            "no way to one employee's own record from the register",
        )

    # -- reading a record is not changing one -----------------------------

    def _an_event(self):
        return AttendanceEvent.objects.create(
            employee=self.employee, kind=AttendanceEvent.CLOCK_IN,
            client_uuid=uuid.uuid4(), device_timestamp=timezone.now(),
            location_unavailable=True,
        )

    def test_an_executive_can_read_a_record_without_the_right_to_change_it(self):
        """
        Executive holds view_attendance_records over everyone and
        correct_attendance over nobody. The record screen used to demand the
        second, so they could see a roll-call and never open a row of it.
        """
        event = self._an_event()
        correct_url = reverse("hr-correction-create", args=[event.pk])

        self._as(self.executive)
        response = self.client.get(
            reverse("hr-employee-attendance", args=[self.employee.pk])
        )
        self.assertEqual(response.status_code, 200, "an Executive must be able to read it")
        self.assertNotIn(
            correct_url, response.content.decode(),
            "an Executive must not be offered a correction they cannot make",
        )

        # ...and the same screen does offer it to someone who can, which is
        # what makes the assertion above mean anything.
        self._as(self.hr)
        body = self.client.get(
            reverse("hr-employee-attendance", args=[self.employee.pk])
        ).content.decode()
        self.assertIn(correct_url, body, "HR must still be offered the correction")

    def test_correcting_is_still_refused_without_the_permission(self):
        event = self._an_event()
        self._as(self.executive)
        response = self.client.get(reverse("hr-correction-create", args=[event.pk]))
        self.assertEqual(
            response.status_code, 403,
            "reading a record must not carry the right to change it",
        )


class AttendanceCodeTests(TestCase):
    """
    The codes HR prints and posts, and what a clock event does with one.

    The claim being defended is narrow: a code proves whoever clocked had it
    in front of them. These tests hold the edges of that claim — the expiry
    is judged at the scan, withdrawal is immediate, and a used code cannot be
    deleted out from under its events.
    """

    def setUp(self):
        from django.core.management import call_command

        from accounts.models import Role, UserRole

        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")

        self.hr_user = User.objects.create_user(
            username="hr@test.local", email="hr@test.local", password="Testing!12345"
        )
        self.worker = User.objects.create_user(
            username="emp@test.local", email="emp@test.local", password="Testing!12345"
        )
        for user, role in ((self.hr_user, "HR"), (self.worker, "Employee")):
            user.must_reset_password = False
            user.save(update_fields=["must_reset_password"])
            UserRole.objects.create(user=user, role=Role.objects.get(name=role))

        self.employee = Employee.objects.create(user=self.worker, staff_id="A1-100")
        self.code = AttendanceCode.objects.create(
            description="Main office",
            location_name="Ganta yard",
            expires_at=timezone.now() + timedelta(days=28),
        )

    def _clock(self, kind="in", code=None, when=None, client_uuid=None):
        payload = {
            "kind": kind,
            "client_uuid": client_uuid or str(uuid.uuid4()),
            "location_unavailable": "1",
        }
        if code is not None:
            payload["code"] = code
        if when is not None:
            payload["device_timestamp"] = when.isoformat()
        return self.client.post(
            "/hr/clock/event/", payload, headers={"x-requested-with": "XMLHttpRequest"}
        )

    # -- the code itself --------------------------------------------------

    def test_a_short_code_avoids_characters_people_misread(self):
        """It is typed off a wall, in the rain, on a phone keyboard."""
        for _ in range(40):
            short = AttendanceCode.new_short_code()
            self.assertNotRegex(short, r"[O0I1]", f"{short} contains a look-alike")
            self.assertRegex(short, r"^[A-Z2-9]{3}-[A-Z2-9]{3}$")

    def test_short_codes_are_unique(self):
        codes = {
            AttendanceCode.objects.create(
                description="d", location_name="l",
                expires_at=timezone.now() + timedelta(days=1),
            ).short_code
            for _ in range(25)
        }
        self.assertEqual(len(codes), 25)

    def test_withdrawal_beats_expiry(self):
        """A code HR pulled is pulled, whatever the clock says."""
        code = AttendanceCode.objects.create(
            description="d", location_name="l",
            expires_at=timezone.now() - timedelta(days=1),
        )
        code.revoked_at = timezone.now()
        code.save(update_fields=["revoked_at"])
        self.assertEqual(code.status, AttendanceCode.REVOKED)

    # -- clocking ---------------------------------------------------------

    @tag("acceptance")
    def test_a_clock_event_without_a_code_is_refused(self):
        self.client.force_login(self.worker)
        response = self._clock()
        self.assertEqual(response.status_code, 409)
        self.assertIn("Scan the attendance code", response.json()["message"])
        self.assertEqual(AttendanceEvent.objects.count(), 0)

    def test_an_unknown_code_is_refused_and_says_what_to_do(self):
        self.client.force_login(self.worker)
        response = self._clock(code="ZZZ-999")
        self.assertEqual(response.status_code, 409)
        self.assertIn("not recognised", response.json()["message"])
        self.assertEqual(AttendanceEvent.objects.count(), 0)

    @tag("acceptance")
    def test_the_event_records_which_code_it_came_from(self):
        self.client.force_login(self.worker)
        self._clock(code=self.code.short_code)
        event = AttendanceEvent.objects.get()
        self.assertEqual(event.attendance_code, self.code)

    def test_the_token_and_the_short_code_both_work_and_case_does_not_matter(self):
        self.client.force_login(self.worker)
        self.assertEqual(self._clock(code=self.code.short_code.lower()).status_code, 200)
        self.assertEqual(
            self._clock(kind="out", code=str(self.code.token)).status_code, 200
        )
        self.assertEqual(AttendanceEvent.objects.count(), 2)

    # -- the offline edge, which is the point of judging at the scan ------

    @tag("acceptance")
    def test_an_event_scanned_before_expiry_is_accepted_when_it_syncs_after(self):
        """
        A phone with no signal reaches the server later. Refusing the event
        because the code expired during the drive back would throw away a
        real event that a real person really recorded.
        """
        code = AttendanceCode.objects.create(
            description="Temporary", location_name="Site gate",
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.worker)
        scanned_at = timezone.now() - timedelta(hours=6)   # while it was live

        response = self._clock(code=code.short_code, when=scanned_at)
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(AttendanceEvent.objects.count(), 1)

    @tag("acceptance")
    def test_an_event_scanned_after_expiry_is_refused(self):
        code = AttendanceCode.objects.create(
            description="Temporary", location_name="Site gate",
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.worker)
        response = self._clock(code=code.short_code)
        self.assertEqual(response.status_code, 409)
        self.assertIn("expired", response.json()["message"])
        self.assertEqual(AttendanceEvent.objects.count(), 0)

    @tag("acceptance")
    def test_a_withdrawn_code_stops_working_immediately(self):
        self.client.force_login(self.hr_user)
        self.client.post(f"/hr/codes/{self.code.pk}/revoke/", {"reason": "Photographed"})

        self.client.force_login(self.worker)
        response = self._clock(code=self.code.short_code)
        self.assertEqual(response.status_code, 409)
        self.assertIn("withdrawn", response.json()["message"])
        self.assertEqual(AttendanceEvent.objects.count(), 0)

    def test_revoking_is_written_to_the_audit_log(self):
        from accounts.models import AuditEntry

        self.client.force_login(self.hr_user)
        self.client.post(f"/hr/codes/{self.code.pk}/revoke/", {"reason": "Photographed"})
        entry = AuditEntry.objects.filter(action="attendance_code.revoked").latest("id")
        self.assertEqual(entry.actor, self.hr_user)
        self.assertEqual(entry.reason, "Photographed")

    def test_a_used_code_cannot_be_deleted_out_from_under_its_events(self):
        from django.db.models import ProtectedError

        self.client.force_login(self.worker)
        self._clock(code=self.code.short_code)
        with self.assertRaises(ProtectedError):
            self.code.delete()

    # -- the screens ------------------------------------------------------

    def test_only_the_code_permission_reaches_the_code_screens(self):
        self.client.force_login(self.worker)
        for path in ("/hr/codes/", "/hr/codes/new/", f"/hr/codes/{self.code.pk}/print/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)

        self.client.force_login(self.hr_user)
        for path in ("/hr/codes/", "/hr/codes/new/", f"/hr/codes/{self.code.pk}/print/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_the_printed_sheet_carries_the_qr_and_the_typed_code(self):
        self.client.force_login(self.hr_user)
        body = self.client.get(f"/hr/codes/{self.code.pk}/print/").content.decode()
        self.assertIn(self.code.short_code, body)
        self.assertIn(f"/hr/codes/{self.code.pk}/qr.png", body)
        self.assertIn(self.code.location_name, body)

    def test_the_qr_encodes_a_link_a_phone_camera_can_open(self):
        self.client.force_login(self.hr_user)
        response = self.client.get(f"/hr/codes/{self.code.pk}/qr.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        # A revoked code must not sit in a proxy.
        self.assertIn("no-store", response["Cache-Control"])

    def test_scanning_opens_the_clock_with_the_location_confirmed(self):
        self.client.force_login(self.worker)
        body = self.client.get(f"/hr/clock/?code={self.code.token}").content.decode()
        self.assertIn(self.code.location_name, body)
        self.assertIn(self.code.short_code, body)

    def test_an_expiry_in_the_past_is_refused_at_the_form(self):
        from .forms import AttendanceCodeForm

        form = AttendanceCodeForm({
            "description": "d", "location_name": "l",
            "expires_at": (timezone.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertFalse(form.is_valid())
        self.assertIn("expires_at", form.errors)


class OnboardingTests(TestCase):
    """
    Taking somebody on is one act, and the thing that makes it safe is that
    it cannot be used to hand out power the person doing it does not have.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from accounts.models import Role, UserRole

        call_command("seed_permissions", verbosity=0)

        def make(email, role):
            person = User.objects.create_user(
                username=email, email=email, password="Testing!12345"
            )
            person.must_reset_password = False
            person.save(update_fields=["must_reset_password"])
            UserRole.objects.create(user=person, role=Role.objects.get(name=role))
            return person

        cls.hr = make("hr@test.local", "HR")
        cls.admin = make("admin@test.local", "Admin")
        cls.supervisor = make("sup@test.local", "Supervisor")

    def _payload(self, **overrides):
        from accounts.models import Role

        payload = {
            "first_name": "Moses",
            "last_name": "Toe",
            "email": "moses.toe@a1technical.test",
            "phone": "0770442118",
            "staff_id": "A1-0017",
            "job_title": "Technician",
            "department": "Field ops",
            "supervisor": "",
            "start_date": "",
            "roles": [Role.objects.get(name="Employee").pk],
        }
        payload.update(overrides)
        return payload

    def test_one_submission_creates_the_person_the_signin_and_the_access(self):
        from hr.models import Employee

        self.client.force_login(self.hr)
        response = self.client.post(reverse("hr-employee-create"), self._payload())
        self.assertEqual(response.status_code, 302)

        employee = Employee.objects.get(staff_id="A1-0017")
        account = employee.user

        # All three, from one form.
        self.assertEqual(account.email, "moses.toe@a1technical.test")
        self.assertEqual(account.username, "moses.toe@a1technical.test")
        self.assertEqual(employee.department, "Field ops")
        self.assertEqual(
            [r.role.name for r in account.user_roles.all()], ["Employee"]
        )

        # They can sign in, and are made to choose their own password first.
        self.assertTrue(account.must_reset_password)
        self.assertTrue(account.has_usable_password())

    def test_the_temporary_password_is_shown_once_and_is_not_guessable(self):
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("hr-employee-create"), self._payload(), follow=True
        )
        message = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("Temporary password:", message)

        secret = message.split("Temporary password:")[1].split()[0]
        self.assertGreaterEqual(len(secret), 10)
        # Not derived from anything on the form.
        for guessable in ("Moses", "Toe", "A1-0017", "moses.toe"):
            self.assertNotIn(guessable.lower(), secret.lower())

    def test_nothing_is_half_created_when_the_form_is_wrong(self):
        """
        An account with no employee record cannot clock in; an employee record
        with no account cannot sign in. Neither should ever exist.
        """
        from hr.models import Employee

        self.client.force_login(self.hr)
        self.client.post(
            reverse("hr-employee-create"), self._payload(staff_id="")
        )
        self.assertFalse(User.objects.filter(email="moses.toe@a1technical.test").exists())
        self.assertFalse(Employee.objects.filter(staff_id="A1-0017").exists())

    def test_a_duplicate_email_is_refused_with_an_actionable_message(self):
        self.client.force_login(self.hr)
        self.client.post(reverse("hr-employee-create"), self._payload())
        response = self.client.post(
            reverse("hr-employee-create"), self._payload(staff_id="A1-0018")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already signs in with that address")
        self.assertEqual(
            User.objects.filter(email="moses.toe@a1technical.test").count(), 1
        )

    # -- the guard --------------------------------------------------------

    @tag("acceptance")
    def test_onboarding_cannot_be_used_to_hand_out_power_you_lack(self):
        """
        The screen shows the temporary password, so creating an Admin would
        be creating one and knowing how to sign in as it. HR must not be able
        to, however the request is shaped.
        """
        from accounts.models import Role
        from hr.models import Employee

        admin_role = Role.objects.get(name="Admin")
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("hr-employee-create"), self._payload(roles=[admin_role.pk])
        )

        self.assertEqual(response.status_code, 200, "the post must not succeed")
        self.assertFalse(Employee.objects.filter(staff_id="A1-0017").exists())
        self.assertFalse(User.objects.filter(email="moses.toe@a1technical.test").exists())

    def test_the_form_only_offers_roles_the_creator_could_grant(self):
        from accounts.services import assignable_roles

        offered = {role.name for role in assignable_roles(self.hr)}
        self.assertIn("Employee", offered)
        self.assertNotIn("Admin", offered)
        self.assertNotIn("Finance", offered)

        # Somebody holding everything is narrowed by nothing.
        self.assertIn("Admin", {role.name for role in assignable_roles(self.admin)})

    @tag("acceptance")
    def test_assigning_a_role_anywhere_respects_the_same_rule(self):
        """
        Narrowing the form is a convenience. The rule lives in the service, so
        it holds for the Settings screen and any future caller too.
        """
        from django.core.exceptions import PermissionDenied

        from accounts import services
        from accounts.models import Role

        with self.assertRaises(PermissionDenied):
            services.assign_role(
                actor=self.hr,
                user=self.supervisor,
                role=Role.objects.get(name="Admin"),
            )

    def test_a_supervisor_cannot_take_anybody_on(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(
            self.client.get(reverse("hr-employee-create")).status_code, 403
        )
