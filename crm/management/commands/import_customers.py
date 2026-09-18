"""
    python manage.py import_customers customers.csv                 # check only
    python manage.py import_customers customers.csv --commit --as ops@a1.lr

Checks by default and writes nothing. Run it, read the report, fix the file,
run it again, and add --commit only when the report is what you expect.
Columns and examples: docs/importing-customers.md.
"""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.decorators import user_has_permission
from config.csv_import import ImportFileError, print_report
from crm.importer import import_customers


class Command(BaseCommand):
    help = "Load customers, their sites and contacts from a CSV. Checks only unless --commit."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The CSV file.")
        parser.add_argument("--commit", action="store_true", help="Actually save. Without it, nothing is written.")
        parser.add_argument(
            "--as", dest="actor", metavar="EMAIL",
            help="The person the import is recorded against. Needed with --commit; must hold create_customer.",
        )

    def handle(self, *args, path, commit, actor, **options):
        file = Path(path)
        if not file.is_file():
            raise CommandError(f"No file at {file}.")

        user = None
        if commit:
            if not actor:
                raise CommandError("--commit needs --as EMAIL: every customer records who created it.")
            user = get_user_model().objects.filter(email__iexact=actor, is_active=True).first()
            if user is None:
                raise CommandError(f"No active account with the email {actor}.")
            if not user_has_permission(user, "create_customer"):
                raise CommandError(f"{actor} does not hold create_customer, so cannot create customers.")

        try:
            report = import_customers(file.read_bytes(), actor=user, commit=commit, source=file.name)
        except ImportFileError as exc:
            raise CommandError(str(exc))

        print_report(report, self.stdout.write, noun="customer")

        if not report.ok:
            raise CommandError(
                f"{len(report.errors)} row{'' if len(report.errors) == 1 else 's'} must be fixed. "
                "Nothing was saved."
            )
        if report.committed:
            self.stdout.write(self.style.SUCCESS("\nSaved."))
        elif commit:
            self.stdout.write("\nNothing to save.")
        else:
            self.stdout.write(self.style.WARNING("\nChecked only. Nothing was saved. Add --commit --as EMAIL to save."))
