"""
Browser tests for the behaviour no server-rendered assertion can reach.

Everything else in this suite checks what Django produced. These four checks
exist because the bugs that actually shipped were in what the *browser* did
with it:

  * The profile menu rendered open on every page load, because a component
    that sets `display` beats the browser's handling of the `hidden`
    attribute. 113 tests were green throughout.
  * The clock and assessment screens told a technician "No signal - saved on
    this phone" while they were online, for the same reason.

The offline layer is the least forgiving code in the build and the hardest
to reason about from the outside, so it is the thing worth driving a real
browser at.

Skipped automatically when Playwright or its browser is not installed, so a
box without them still runs the rest of the suite:

    pip install playwright && python -m playwright install chromium
"""
import os
import time
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import tag

from accounts.models import Role, UserRole
from config.models import CorrectionReason, PolicySetting
from hr.models import AttendanceEvent, Employee

User = get_user_model()

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT = True
except ImportError:  # pragma: no cover - depends on the box
    PLAYWRIGHT = False


def browser_available():
    if not PLAYWRIGHT:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(PLAYWRIGHT and browser_available(), "playwright/chromium not installed")
@tag("browser")
class ShellBehaviourTests(StaticLiveServerTestCase):
    """The interactions the shell promises."""

    @classmethod
    def setUpClass(cls):
        # Playwright's sync API refuses to run inside an asyncio loop, which
        # is what Django's async-unsafe guard would otherwise complain about.
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()
        super().tearDownClass()

    def setUp(self):
        # A live-server case runs on TransactionTestCase, which truncates
        # tables between tests and never calls setUpTestData — so the
        # fixtures are built per test rather than once.
        super().setUp()
        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        PolicySetting.objects.create(key=PolicySetting.LATE_AFTER_MINUTES, value="15")
        CorrectionReason.objects.create(code="forgot", label="Forgot to clock in")

        self.password = "Testing!12345"
        self.worker = User.objects.create_user(
            username="tech@test.local", email="tech@test.local",
            password=self.password, first_name="Moses", last_name="Toe",
        )
        self.worker.must_reset_password = False
        self.worker.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=self.worker, role=Role.objects.get(name="Technician"))
        Employee.objects.create(user=self.worker, staff_id="A1-002")

    def _page(self):
        context = self.browser.new_context()
        page = context.new_page()
        page.goto(f"{self.live_server_url}/login/")
        page.fill("#id_email", self.worker.email)
        page.fill("#id_password", self.password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        return context, page

    # -- the bug that shipped ---------------------------------------------

    def test_the_profile_menu_is_closed_until_it_is_clicked(self):
        context, page = self._page()
        try:
            menu = page.locator("#user-sheet")
            self.assertFalse(menu.is_visible(), "the menu must not render open")

            page.click("#user-toggle")
            self.assertTrue(menu.is_visible(), "clicking the avatar must open it")

            # Clicking away closes it again.
            page.click("body", position={"x": 5, "y": 400})
            self.assertFalse(menu.is_visible(), "clicking away must close it")
        finally:
            context.close()

    def test_the_offline_banner_is_hidden_while_online(self):
        """
        This is the one that told a technician their work was stuck on the
        phone while they had perfectly good signal.
        """
        context, page = self._page()
        try:
            page.goto(f"{self.live_server_url}/hr/clock/")
            page.wait_for_load_state("networkidle")
            self.assertFalse(
                page.locator("#queue-banner").is_visible(),
                "the 'no signal' banner must not show while online",
            )
        finally:
            context.close()

    def test_the_sidebar_collapses_and_is_remembered(self):
        context, page = self._page()
        try:
            page.click("#nav-toggle")
            self.assertTrue(
                page.locator("body.nav-collapsed").count(), "the sidebar must collapse"
            )
            # The choice survives a reload, per device.
            page.reload()
            page.wait_for_load_state("networkidle")
            self.assertTrue(
                page.locator("body.nav-collapsed").count(),
                "the collapsed state must be remembered",
            )
        finally:
            context.close()


@unittest.skipUnless(PLAYWRIGHT and browser_available(), "playwright/chromium not installed")
@tag("browser")
class OfflineClockTests(StaticLiveServerTestCase):
    """
    The offline contract, driven through a real browser: a clock event
    written on the device with no network must survive, and must reach the
    server exactly once when the network returns.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        call_command("seed_permissions", verbosity=0)
        PolicySetting.objects.create(key=PolicySetting.WORKDAY_START, value="08:00")
        self.password = "Testing!12345"
        self.worker = User.objects.create_user(
            username="tech2@test.local", email="tech2@test.local",
            password=self.password, first_name="Sarah", last_name="Gbah",
        )
        self.worker.must_reset_password = False
        self.worker.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=self.worker, role=Role.objects.get(name="Technician"))
        self.employee = Employee.objects.create(user=self.worker, staff_id="A1-003")

    def _events(self):
        """
        Count this employee's clock events, tolerating SQLite's table lock.

        The live-server thread writes while this thread reads, and SQLite
        serialises that by refusing the reader. It is an artefact of running
        the suite on SQLite — production is PostgreSQL — so it is retried
        rather than allowed to fail a test about offline sync.
        """
        from django.db import OperationalError

        try:
            return AttendanceEvent.objects.filter(employee=self.employee).count()
        except OperationalError:
            return None

    def _wait_for_events(self, expected, timeout=30):
        deadline = time.monotonic() + timeout
        seen = None
        while time.monotonic() < deadline:
            seen = self._events()
            if seen == expected:
                return seen
            time.sleep(0.25)
        return seen

    def _signed_in(self):
        context = self.browser.new_context()
        # No GPS permission granted: the clock must record anyway.
        page = context.new_page()
        page.goto(f"{self.live_server_url}/login/")
        page.fill("#id_email", self.worker.email)
        page.fill("#id_password", self.password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        return context, page

    def test_a_clock_event_taken_offline_syncs_once_when_the_network_returns(self):
        context, page = self._signed_in()
        try:
            page.goto(f"{self.live_server_url}/hr/clock/")
            page.wait_for_load_state("networkidle")

            # Go offline, then clock in. The event must be kept on the device.
            context.set_offline(True)
            page.click("button.act")

            # Wait for the queue to appear rather than for a fixed number of
            # seconds. The GPS fix gives up after five, but a loaded machine
            # takes longer, and a sleep long enough to be safe everywhere is
            # a sleep that makes the suite slow everywhere.
            page.wait_for_function(
                "() => window.A1.store.get('a1.clock.queue')"
                ".then(function (q) { return !!(q && q.length); })",
                timeout=30000,
            )

            self.assertEqual(
                self._wait_for_events(0, timeout=2), 0,
                "nothing should have reached the server while offline",
            )
            queued = page.evaluate("() => window.A1.store.get('a1.clock.queue')")
            self.assertTrue(queued, "the event must be queued on the device")
            self.assertIn("client_uuid", queued[0],
                          "the queued event must carry the id it was created with")
            self.assertTrue(page.locator("#queue-banner").is_visible(),
                            "the technician must be told it is held on the phone")

            # Signal returns. The queued event uploads by itself.
            context.set_offline(False)
            page.evaluate("window.dispatchEvent(new Event('online'))")

            self.assertEqual(
                self._wait_for_events(1), 1,
                "the queued event must reach the server exactly once",
            )
        finally:
            context.close()

    def test_gps_refusal_never_blocks_the_clock(self):
        """A denied or absent fix records the event marked location unavailable."""
        context, page = self._signed_in()
        try:
            page.goto(f"{self.live_server_url}/hr/clock/")
            page.wait_for_load_state("networkidle")
            page.click("button.act")

            self._wait_for_events(1)
            event = AttendanceEvent.objects.filter(employee=self.employee).first()
            self.assertIsNotNone(event, "the clock must record without a GPS fix")
            self.assertTrue(event.location_unavailable)
        finally:
            context.close()
