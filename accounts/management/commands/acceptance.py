"""
The Phase One release gate.

Runs the acceptance scenarios the delivery plan names and reports pass or
fail. Phase One does not ship on a partial pass, so this exits non-zero if
anything fails — it is meant to be the last thing run before a go/no-go, and
to be usable from CI without anybody reading scrollback.

    python manage.py acceptance

The scenarios themselves live beside the code they test, tagged
`acceptance`. Keeping them there rather than in a separate suite means they
are maintained by whoever changes the behaviour, not by whoever remembers.
"""
import contextlib
import io

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test.utils import get_runner


# What the plan asks the gate to prove, and where each is asserted. Printed
# so a reader can see the scenario list without opening the test files.
SCENARIOS = [
    ("Lineage end to end",
     "A call becomes a ticket, converts to a project, is assessed, approved, "
     "costed and invoiced - one job reference the whole way"),
    ("Offline assessment",
     "Completed with no signal, held for days, then synced exactly once"),
    ("Idempotent sync",
     "The same client UUID from two devices creates one record"),
    ("GPS never blocks",
     "A clock event and a check-in record with no fix, marked location unavailable"),
    ("Actionable refusal",
     "A clock-out with no open clock-in is refused with a message that says what to do"),
    ("Corrections supersede",
     "The original event survives and a blank reason cannot submit"),
    ("Assessment questions are configuration",
     "The form is generated from the service type, and retiring a question "
     "never alters a recorded answer"),
    ("Role matrix",
     "Every pre-built role attempts every gated endpoint and is correctly "
     "allowed or refused, and no gated screen escapes the matrix"),
    ("Scope qualifies permission",
     "A supervisor is refused another team's assessments and attendance"),
    ("Approval threshold",
     "Finance is refused above the Settings-owned threshold; an Executive is not"),
    ("Derived figures",
     "Invoice paid-state and every report tally against their own rows"),
    ("Attention queue",
     "Ranked by how long each item waited, and never shows what the viewer "
     "cannot clear"),
    ("Attendance codes",
     "A clock event needs the code posted at the location; the expiry is "
     "judged at the scan, so an event held offline still counts, and "
     "withdrawal takes effect at once"),
    ("JavaScript parses",
     "Every script this system ships, including the offline layer and the "
     "service worker, is parsed rather than trusted"),
]

# Proven on a device, not in a test runner. Named here so a partial pass is
# never mistaken for a full one.
MANUAL = [
    ("Two-tap clock-in under ten seconds", "on a low-end Android, timed"),
    ("A full assessment on a low-end Android", "in airplane mode, then synced"),
    ("Legible in sunlight, usable one-handed", "on the device the crews carry"),
]

# Browser behaviour runs under its own tag because it needs Playwright and a
# chromium download. Reported separately so a box without them cannot mistake
# a skip for a pass.
BROWSER_HINT = [
    "Browser behaviour runs under its own tag and needs a browser:",
    "    pip install playwright && python -m playwright install chromium",
    "    python manage.py test --tag browser",
]


class Command(BaseCommand):
    help = "Run the Phase One acceptance scenarios and report a pass/fail gate."

    # This is read in a terminal, and Windows consoles default to cp1252.
    # Keeping the output ASCII means the gate never fails on its own printing.

    captured = ""

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose", action="store_true", help="Show the individual test output."
        )

    def handle(self, *args, **options):
        rule = "=" * 72
        self.stdout.write(rule)
        self.stdout.write("A1 360 - PHASE ONE ACCEPTANCE GATE")
        self.stdout.write(rule)
        self.stdout.write("")
        self.stdout.write("Scenarios covered automatically:")
        for name, detail in SCENARIOS:
            self.stdout.write(f"  * {name}")
            self.stdout.write(f"      {detail}")
        self.stdout.write("")

        runner_class = get_runner(settings)
        runner = runner_class(
            verbosity=2 if options["verbose"] else 0,
            interactive=False,
            tags=["acceptance"],
        )

        # The runner reports to stderr. Quiet unless asked, because the gate's
        # output is the verdict — a reader should not have to find it in a log.
        if options["verbose"]:
            failures = runner.run_tests([])
        else:
            sink = io.StringIO()
            with contextlib.redirect_stderr(sink):
                failures = runner.run_tests([])
            self.captured = sink.getvalue()

        self.stdout.write(rule)
        if failures:
            self.stdout.write(
                self.style.ERROR(f"GATE: FAIL - {failures} scenario(s) did not pass.")
            )
            self.stdout.write("")
            self.stdout.write(
                "Phase One does not ship on a partial pass. Re-run with --verbose "
                "to see which scenario failed."
            )
            self.stdout.write("")
            # Show what broke rather than making anyone re-run to find out.
            for line in (self.captured or "").splitlines():
                if line.startswith(("FAIL:", "ERROR:")):
                    self.stdout.write("  " + line)
            self.stdout.write(rule)
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("GATE: PASS - every automated scenario holds."))
        self.stdout.write("")
        self.stdout.write("Still to be proven on a real device before go/no-go:")
        for name, detail in MANUAL:
            self.stdout.write(f"  [ ] {name} - {detail}")
        self.stdout.write("")
        self.stdout.write(
            "A pass here is necessary, not sufficient. The manual checks above "
            "are part of the gate."
        )
        self.stdout.write("")
        for line in BROWSER_HINT:
            self.stdout.write(line)
        self.stdout.write(rule)
