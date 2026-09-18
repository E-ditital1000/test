"""
    python manage.py import_staff staff.csv --as hr@a1.lr                        # check only
    python manage.py import_staff staff.csv --as hr@a1.lr --commit --passwords-out passwords.csv

Checks by default and writes nothing. --as is needed even to check, because
which roles a row may carry depends on who is granting them. Columns and
examples: docs/importing-staff.md.
"""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.decorators import user_has_permission
from config.csv_import import ImportFileError, print_report
from hr.importer import import_staff, passwords_csv


class Command(BaseCommand):
    help = "Take on staff from a CSV: person, sign-in and roles. Checks only unless --commit."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The CSV file.")
        parser.add_argument(
            "--as", dest="actor", metavar="EMAIL", required=True,
            help="Who is taking them on. Must hold manage_employees; roles are limited to theirs.",
        )
        parser.add_argument("--commit", action="store_true", help="Actually save. Without it, nothing is written.")
        parser.add_argument(
            "--passwords-out", metavar="FILE",
            help="Where to write the temporary passwords. Needed with --commit; must not exist yet.",
        )

    def handle(self, *args, path, actor, commit, passwords_out, **options):
        file = Path(path)
        if not file.is_file():
            raise CommandError(f"No file at {file}.")

        user = get_user_model().objects.filter(email__iexact=actor, is_active=True).first()
        if user is None:
            raise CommandError(f"No active account with the email {actor}.")
        if not user_has_permission(user, "manage_employees"):
            raise CommandError(f"{actor} does not hold manage_employees, so cannot take staff on.")

        out = None
        if commit:
            if not passwords_out:
                raise CommandError(
                    "--commit needs --passwords-out FILE: every new person gets a temporary "
                    "password, and this is the only place it will ever be written."
                )
            out = Path(passwords_out)
            # Never overwrite: an earlier sheet may be the only copy of
            # passwords nobody has handed out yet.
            if out.exists():
                raise CommandError(f"{out} already exists. Choose a new file name.")
            if not out.parent.is_dir():
                raise CommandError(f"No folder at {out.parent}.")

        try:
            report, passwords = import_staff(file.read_bytes(), actor=user, commit=commit)
        except ImportFileError as exc:
            raise CommandError(str(exc))

        print_report(report, self.stdout.write, noun="person", plural="people")

        if not report.ok:
            raise CommandError(
                f"{len(report.errors)} problem{'' if len(report.errors) == 1 else 's'} to fix. "
                "Nothing was saved."
            )
        if report.committed:
            # newline="": the csv module already ends rows with \r\n, and
            # letting Windows translate them again gives \r\r\n -- a blank
            # row between every person when the sheet is opened in Excel.
            out.write_text(passwords_csv(passwords), encoding="utf-8", newline="")
            self.stdout.write(self.style.SUCCESS(f"\nSaved. Temporary passwords are in {out}."))
            self.stdout.write(
                "Hand each one over in person, then delete the file. Each person must "
                "choose their own password at first sign-in."
            )
        elif commit:
            self.stdout.write("\nNothing to save.")
        else:
            self.stdout.write(self.style.WARNING(
                "\nChecked only. Nothing was saved. Add --commit --passwords-out FILE to save."
            ))
