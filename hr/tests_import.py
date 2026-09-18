"""
The staff import. It must refuse anything the Add employee screen would,
never grant a role the importer could not, and put the temporary passwords
somewhere they can be handed out -- and nowhere else.
"""
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import AuditEntry, Role, UserRole
from hr.importer import import_staff, passwords_csv, plan
from hr.models import Employee

User = get_user_model()
HEADER = "first_name,last_name,email,staff_id,phone,job_title,department,supervisor,start_date,roles\n"


def csv(*rows):
    return (HEADER + "".join(r + "\n" for r in rows)).encode("utf-8")


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class StaffImportRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.admin = make_user("admin@t.local", ["Admin"])
        cls.hr = make_user("hr@t.local", ["HR"])

    def test_everyone_gets_a_person_a_sign_in_and_roles(self):
        report, passwords = import_staff(csv(
            "Moses,Toe,moses@t.local,A1-101,+231 770 1,Electrician,Field,,2024-09-03,Technician|Employee",
            "Ruth,Doe,ruth@t.local,A1-102,,,,,,",
        ), actor=self.admin, commit=True)
        self.assertTrue(report.committed, report.errors)
        moses = Employee.objects.get(staff_id="A1-101")
        self.assertEqual(moses.user.email, "moses@t.local")
        self.assertTrue(moses.user.must_reset_password)
        self.assertEqual(str(moses.start_date), "2024-09-03")
        self.assertEqual(
            sorted(moses.user.user_roles.values_list("role__name", flat=True)),
            ["Employee", "Technician"],
        )
        # No roles given: the floor every account starts on.
        ruth = Employee.objects.get(staff_id="A1-102")
        self.assertEqual(list(ruth.user.user_roles.values_list("role__name", flat=True)), ["Employee"])

        self.assertEqual(len(passwords), 2)
        for employee, temporary in passwords:
            self.assertTrue(employee.user.check_password(temporary))
        self.assertEqual(AuditEntry.objects.filter(action="employee.onboarded").count(), 2)

    def test_nothing_is_written_without_commit(self):
        report, passwords = import_staff(csv("A,B,a@t.local,A1-1,,,,,,"), actor=self.admin)
        self.assertEqual(len(report.created), 1)
        self.assertEqual(passwords, [])
        self.assertFalse(Employee.objects.exists())

    def test_one_bad_row_means_nobody_is_taken_on(self):
        report, _ = import_staff(csv(
            "A,B,a@t.local,A1-1,,,,,,",
            "C,D,not-an-email,A1-2,,,,,,",
        ), actor=self.admin, commit=True)
        self.assertFalse(report.ok)
        self.assertFalse(Employee.objects.exists())
        self.assertFalse(User.objects.filter(email="a@t.local").exists())

    def test_you_cannot_grant_a_role_you_could_not_grant_on_screen(self):
        report, _ = import_staff(csv("A,B,a@t.local,A1-1,,,,,,Admin"), actor=self.hr, commit=True)
        self.assertIn("You cannot grant 'Admin'", report.errors[0][1])
        self.assertFalse(Employee.objects.exists())

    def test_an_unknown_role_is_named(self):
        report, _ = plan(csv("A,B,a@t.local,A1-1,,,,,,Technicain"), actor=self.admin)
        self.assertIn("no role called 'Technicain'", report.errors[0][1])

    def test_the_screens_own_rules_apply(self):
        """The same form as Add employee, so the same refusals."""
        existing = make_user("taken@t.local")
        Employee.objects.create(user=existing, staff_id="A1-TAKEN")
        report, _ = plan(csv(
            "A,B,taken@t.local,A1-1,,,,,,",
            "C,D,c@t.local,A1-TAKEN,,,,,,",
            ",D,d@t.local,A1-3,,,,,,",
        ), actor=self.admin)
        messages = " | ".join(m for _l, m in report.errors)
        self.assertIn("already signs in with that address", messages)
        self.assertIn("staff ID is already on the register", messages)
        self.assertIn("First name", messages)

    def test_duplicates_within_the_file_are_caught(self):
        report, _ = plan(csv(
            "A,B,same@t.local,A1-1,,,,,,",
            "C,D,SAME@t.local,a1-1,,,,,,",
        ), actor=self.admin)
        messages = " ".join(m for _l, m in report.errors)
        self.assertIn("also on line 2", messages)
        self.assertEqual(report.errors[0][0], 3)

    def test_dates_must_be_unambiguous(self):
        report, _ = plan(csv("A,B,a@t.local,A1-1,,,,,03/09/2024,"), actor=self.admin)
        self.assertIn("YYYY-MM-DD", report.errors[0][1])

    def test_a_supervisor_can_be_anywhere_in_the_file_or_already_on_the_register(self):
        boss = make_user("boss@t.local")
        Employee.objects.create(user=boss, staff_id="A1-BOSS")
        report, _ = import_staff(csv(
            "Tech,One,t1@t.local,A1-201,,,,lead@t.local,,",    # supervisor later in the file
            "Lead,Hand,lead@t.local,A1-200,,,,A1-BOSS,,",      # supervisor already here, by staff ID
        ), actor=self.admin, commit=True)
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(Employee.objects.get(staff_id="A1-201").supervisor.staff_id, "A1-200")
        self.assertEqual(Employee.objects.get(staff_id="A1-200").supervisor.staff_id, "A1-BOSS")

    def test_an_unknown_supervisor_or_oneself_is_refused(self):
        report, _ = plan(csv(
            "A,B,a@t.local,A1-1,,,,nobody@t.local,,",
            "C,D,c@t.local,A1-2,,,,A1-2,,",
        ), actor=self.admin)
        messages = " ".join(m for _l, m in report.errors)
        self.assertIn("'nobody@t.local' is not an email or staff ID", messages)
        self.assertIn("cannot be their own supervisor", messages)

    def test_the_password_sheet_opens_in_excel_and_holds_every_password(self):
        _report, passwords = import_staff(csv("José,Doe,jose@t.local,A1-1,,,,,,"), actor=self.admin, commit=True)
        sheet = passwords_csv(passwords)
        self.assertTrue(sheet.startswith("﻿"))
        self.assertIn("José Doe,jose@t.local,A1-1,{}".format(passwords[0][1]), sheet)


class StaffImportCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.hr = make_user("hr@t.local", ["HR"])
        cls.tech = make_user("tech@t.local", ["Technician"])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name)
        self.path = self.folder / "staff.csv"
        self.path.write_bytes(csv("Moses,Toe,moses@t.local,A1-101,,,,,,Employee"))

    def tearDown(self):
        self.dir.cleanup()

    def call(self, *args):
        out = StringIO()
        call_command("import_staff", str(self.path), *args, stdout=out)
        return out.getvalue()

    def test_the_default_checks_and_saves_nothing(self):
        output = self.call("--as", "hr@t.local")
        self.assertIn("Would create 1 person", output)
        self.assertFalse(Employee.objects.exists())

    def test_commit_writes_the_passwords_to_the_file_and_never_to_the_screen(self):
        sheet = self.folder / "passwords.csv"
        output = self.call("--as", "hr@t.local", "--commit", "--passwords-out", str(sheet))
        employee = Employee.objects.get()
        raw = sheet.read_bytes()
        self.assertNotIn(b"\r\r\n", raw, "every row would be followed by a blank one in Excel")
        temporary = raw.decode("utf-8").splitlines()[1].split(",")[-1]
        self.assertTrue(employee.user.check_password(temporary))
        self.assertNotIn(temporary, output)
        self.assertIn("then delete the file", output)

    def test_commit_needs_a_password_file_that_does_not_exist_yet(self):
        with self.assertRaisesMessage(CommandError, "needs --passwords-out"):
            self.call("--as", "hr@t.local", "--commit")
        sheet = self.folder / "passwords.csv"
        sheet.write_text("an earlier sheet nobody has handed out yet")
        with self.assertRaisesMessage(CommandError, "already exists"):
            self.call("--as", "hr@t.local", "--commit", "--passwords-out", str(sheet))
        self.assertEqual(sheet.read_text(), "an earlier sheet nobody has handed out yet")
        self.assertFalse(Employee.objects.exists())

    def test_only_somebody_who_can_take_staff_on_may_run_it(self):
        with self.assertRaisesMessage(CommandError, "does not hold manage_employees"):
            self.call("--as", "tech@t.local")
