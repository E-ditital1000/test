"""
The customer import. The guarantees that matter: nothing is written unless
asked and nothing is wrong, it never touches a customer already on the
register, and it reads the file Operations will actually export.
"""
from io import StringIO
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import AuditEntry, Role, UserRole
from config.csv_import import ImportFileError
from crm.importer import import_customers, plan
from crm.models import Contact, Customer, Site

User = get_user_model()

HEADER = "name,phone,email,address,contact_name,contact_job_title,contact_phone,contact_email,site_name,site_address,site_latitude,site_longitude\n"


def csv(*rows):
    return (HEADER + "".join(r + "\n" for r in rows)).encode("utf-8")


class ImportRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.ops = User.objects.create_user(username="ops@t.local", email="ops@t.local", password="x")
        UserRole.objects.create(user=cls.ops, role=Role.objects.get(name="Admin"))

    def run_import(self, raw, commit=True):
        return import_customers(raw, actor=self.ops, commit=commit, source="test.csv")

    def test_a_clean_file_creates_customers_sites_and_contacts(self):
        report = self.run_import(csv(
            "Liberty Gold,+231 770 111 222,ops@lg.lr,Broad St,Amos Kollie,Manager,+231 880 1,amos@lg.lr,Main yard,Broad St,6.30,-10.80",
            "Liberty Gold,,,,Mary Doe,,,,Ganta depot,,,",
        ))
        self.assertTrue(report.committed)
        customer = Customer.objects.get()
        self.assertEqual(customer.created_by, self.ops)
        self.assertEqual(sorted(s.name for s in customer.sites.all()), ["Ganta depot", "Main yard"])
        contacts = list(customer.contacts.all())
        self.assertEqual([c.name for c in contacts], ["Amos Kollie", "Mary Doe"])
        self.assertTrue(contacts[0].is_primary)
        self.assertFalse(contacts[1].is_primary)
        self.assertEqual(AuditEntry.objects.filter(action="customer.imported").count(), 1)

    def test_a_row_can_say_whether_it_is_a_person_or_an_organisation(self):
        raw = (
            "name,kind,phone\n"
            "Liberty Gold,organisation,+231 770 111 222\n"
            "Faith Jallah,individual,+231 886 400 100\n"
            "New Hope Clinic,,+231 777 555 010\n"
        ).encode("utf-8")
        report = self.run_import(raw)

        self.assertTrue(report.committed, report.errors)
        kinds = dict(Customer.objects.values_list("name", "kind"))
        self.assertEqual(kinds["Faith Jallah"], Customer.INDIVIDUAL)
        self.assertEqual(kinds["Liberty Gold"], Customer.ORGANISATION)
        # Said nothing: most of the register is organisations.
        self.assertEqual(kinds["New Hope Clinic"], Customer.ORGANISATION)

    def test_either_spelling_of_organisation_is_read(self):
        """Operations will export whichever their spellchecker prefers."""
        raw = b"name,kind\nLiberty Gold,Organization\nNew Hope Clinic,Company\n"
        self.assertTrue(self.run_import(raw).committed)
        self.assertEqual(
            set(Customer.objects.values_list("kind", flat=True)), {Customer.ORGANISATION}
        )

    def test_a_kind_nobody_recognises_is_an_error_rather_than_a_guess(self):
        report = self.run_import(b"name,kind\nLiberty Gold,charity\n")
        self.assertFalse(report.committed)
        self.assertTrue(any("charity" in message for _, message in report.errors))
        self.assertFalse(Customer.objects.exists())

    def test_nothing_is_written_without_commit(self):
        report = self.run_import(csv("Liberty Gold,,,,,,,,,,,"), commit=False)
        self.assertFalse(report.committed)
        self.assertEqual(len(report.created), 1)
        self.assertFalse(Customer.objects.exists())

    def test_one_bad_row_means_nothing_is_written(self):
        """A half-loaded list is worse than none: nobody can tell which half is missing."""
        report = self.run_import(csv(
            "Good One,,,,,,,,,,,",
            "Bad One,,not-an-email,,,,,,,,,",
        ))
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0][0], 3)
        self.assertFalse(Customer.objects.exists())

    def test_customers_already_on_the_register_are_skipped_not_changed(self):
        Customer.objects.create(name="Liberty Gold Ltd", phone="0770 111 222", notes="edited by hand")
        report = self.run_import(csv(
            "liberty gold ltd.,,,,,,,,,,,",          # same name, different spelling
            "Other Name,+231 770 111 222,,,,,,,,,,",  # same phone, different format
        ))
        self.assertEqual(len(report.skipped), 2)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Customer.objects.get().notes, "edited by hand")

    def test_running_the_same_file_twice_creates_nothing_the_second_time(self):
        raw = csv("Liberty Gold,,,,,,,,Main yard,,,")
        self.run_import(raw)
        report = self.run_import(raw)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Site.objects.count(), 1)
        self.assertEqual(len(report.skipped), 1)

    def test_each_row_reports_every_problem_it_has(self):
        report = self.run_import(csv(",0770 1,,,,,,,,,,", "X,,bad,,,,,,,,7,"), commit=False)
        lines = [line for line, _message in report.errors]
        self.assertEqual(lines.count(2), 1)   # no name
        self.assertEqual(lines.count(3), 2)   # bad email and half a coordinate

    def test_coordinates_are_checked(self):
        report = self.run_import(csv("X,,,,,,,,Yard,,north,-10", "Y,,,,,,,,Yard,,95,-10"), commit=False)
        messages = " ".join(m for _l, m in report.errors)
        self.assertIn("'north' is not a number", messages)
        self.assertIn("outside -90 to 90", messages)

    def test_values_too_long_for_the_database_are_refused(self):
        report = self.run_import(csv("X" * 151 + ",,,,,,,,,,,"), commit=False)
        self.assertIn("the most it can hold is 150", report.errors[0][1])

    def test_a_contact_detail_without_a_contact_name_is_refused(self):
        report = self.run_import(csv("X,,,,,,+231 880 1,,,,,"), commit=False)
        self.assertIn("no contact name", report.errors[0][1])

    def test_conflicting_details_for_one_customer_keep_the_first_and_say_so(self):
        report = self.run_import(csv("X,0770 1,,,,,,,,,,", "X,0880 2,,,,,,,,,,"))
        self.assertEqual(Customer.objects.get().phone, "0770 1")
        self.assertIn("The first is kept", report.warnings[0][1])

    def test_two_names_sharing_a_phone_are_flagged(self):
        report = self.run_import(csv("A Co,+231 770 111 222,,,,,,,,,,", "B Co,0770 111 222,,,,,,,,,,"), commit=False)
        self.assertIn("Check they are not the same customer", report.warnings[0][1])


class ImportFileFormatTests(TestCase):
    def test_what_excel_saves_is_read(self):
        """A byte-order mark, Windows-1252 accents, semicolons and headers with spaces."""
        raw = "﻿Name;Contact Name;Site Name\nCafé Côte;José;Dépôt\n".encode("utf-8")
        report, customers = plan(raw)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(customers[0]["fields"]["name"], "Café Côte")

        raw = "Name;Contact Name\nCafé Côte;José\n".encode("cp1252")
        report, customers = plan(raw)
        self.assertEqual(customers[0]["contacts"][0]["name"], "José")

    def test_blank_lines_are_ignored_and_unknown_columns_warned(self):
        report, customers = plan(b"name,Region\nA,West\n,\n\nB,East\n")
        self.assertEqual(len(customers), 2)
        self.assertIn("'Region'", report.warnings[0][1])

    def test_line_numbers_match_the_spreadsheet_after_a_multi_line_cell(self):
        raw = b'name,address,email\nA,"12 Broad St\nMonrovia",\nB,,bad\n'
        report, customers = plan(raw)
        self.assertEqual(customers[0]["fields"]["address"], "12 Broad St Monrovia")
        self.assertEqual(report.errors[0][0], 4)

    def test_a_file_without_a_name_column_is_refused_whole(self):
        with self.assertRaisesMessage(ImportFileError, "Missing column: name"):
            plan(b"customer,phone\nA,1\n")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(ImportFileError):
            plan(b"")


class ImportCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.ops = User.objects.create_user(username="ops@t.local", email="ops@t.local", password="x")
        UserRole.objects.create(user=cls.ops, role=Role.objects.get(name="Admin"))
        cls.hr = User.objects.create_user(username="hr@t.local", email="hr@t.local", password="x")
        UserRole.objects.create(user=cls.hr, role=Role.objects.get(name="HR"))

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "customers.csv"
        self.path.write_bytes(csv("Liberty Gold,,,,,,,,,,,"))

    def tearDown(self):
        self.dir.cleanup()

    def call(self, *args):
        out = StringIO()
        call_command("import_customers", str(self.path), *args, stdout=out)
        return out.getvalue()

    def test_the_default_is_a_check_that_saves_nothing(self):
        output = self.call()
        self.assertIn("Would create 1 customer", output)
        self.assertIn("Nothing was saved", output)
        self.assertFalse(Customer.objects.exists())

    def test_commit_saves_as_the_named_person(self):
        output = self.call("--commit", "--as", "ops@t.local")
        self.assertIn("Created 1 customer", output)
        self.assertEqual(Customer.objects.get().created_by, self.ops)

    def test_commit_needs_somebody_allowed_to_create_customers(self):
        with self.assertRaisesMessage(CommandError, "needs --as"):
            self.call("--commit")
        with self.assertRaisesMessage(CommandError, "does not hold create_customer"):
            self.call("--commit", "--as", "hr@t.local")
        self.assertFalse(Customer.objects.exists())

    def test_errors_make_the_command_fail(self):
        """So a script or CI running it stops, rather than carrying on."""
        self.path.write_bytes(csv("X,,bad,,,,,,,,,"))
        with self.assertRaisesMessage(CommandError, "Nothing was saved"):
            self.call("--commit", "--as", "ops@t.local")
