"""
The forms in this module that change as they are filled in, driven in a real
browser — the only place that behaviour is visible at all.

  * The Site list following the Customer list, on both screens that choose
    the two at once: a new ticket, and a scheduled field job. It used to
    fill only on submit, and submitting created the record, so no site could
    ever be chosen for a new one. Every server-side assertion was green
    throughout. They share one script, and a shared script is exactly the
    thing that gets fixed for one caller and broken for the other.

  * The customer form following the kind of customer chosen.

Skipped when Playwright or its browser is not installed; see
accounts/tests_browser.py.
"""
import os
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import tag

# The module, not its classes: names imported here would be collected and
# run a second time as crm tests.
from accounts import tests_browser as browser
from accounts.models import Role, UserRole

from .models import Customer, Site

User = get_user_model()


@unittest.skipUnless(
    browser.PLAYWRIGHT and browser.browser_available(), "playwright/chromium not installed"
)
@tag("browser")
class FormsThatRespondTests(StaticLiveServerTestCase):
    server_thread_class = browser._SerialLiveServerThread

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls._playwright = browser.sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch(args=browser.LOCAL_ONLY)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        call_command("seed_permissions", verbosity=0)
        self.password = "Testing!12345"
        self.receptionist = User.objects.create_user(
            username="recep@test.local", email="recep@test.local", password=self.password,
        )
        self.receptionist.must_reset_password = False
        self.receptionist.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=self.receptionist, role=Role.objects.get(name="Receptionist"))
        # Scheduling a field job is a different permission and so a different
        # person, but the same picker.
        self.supervisor = User.objects.create_user(
            username="sup@test.local", email="sup@test.local", password=self.password,
        )
        self.supervisor.must_reset_password = False
        self.supervisor.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=self.supervisor, role=Role.objects.get(name="Supervisor"))

        self.one_site = Customer.objects.create(name="EL Organization", address="CHHC+66C")
        self.main = Site.objects.create(
            customer=self.one_site, name="Main address", address="CHHC+66C"
        )
        self.two_sites = Customer.objects.create(name="Harmony Foods Ltd")
        Site.objects.create(customer=self.two_sites, name="Ikeja site")
        Site.objects.create(customer=self.two_sites, name="Apapa depot")
        self.no_sites = Customer.objects.create(name="Walk-in")

    def _open(self, user, path):
        context = self.browser.new_context()
        page = context.new_page()
        page.goto(f"{self.live_server_url}/login/")
        page.fill("#id_email", user.email)
        page.fill("#id_password", self.password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        page.goto(f"{self.live_server_url}{path}")
        page.wait_for_load_state("networkidle")
        return context, page

    def _new_ticket(self):
        return self._open(self.receptionist, "/tickets/new/")

    def _options(self, page):
        return page.eval_on_selector_all("#id_site option", "os => os.map(o => o.textContent)")

    def test_picking_a_customer_offers_their_sites_at_once(self):
        context, page = self._new_ticket()
        try:
            self.assertEqual(self._options(page), ["Choose a customer first"])

            # One site: it is the answer, so it is chosen.
            page.select_option("#id_customer", str(self.one_site.pk))
            self.assertEqual(self._options(page), ["Which site?", "Main address — CHHC+66C"])
            self.assertEqual(page.input_value("#id_site"), str(self.main.pk))

            # More than one: offered, not guessed.
            page.select_option("#id_customer", str(self.two_sites.pk))
            self.assertEqual(self._options(page), ["Which site?", "Apapa depot", "Ikeja site"])
            self.assertEqual(page.input_value("#id_site"), "")

            page.select_option("#id_customer", str(self.no_sites.pk))
            self.assertEqual(self._options(page), ["No site on this customer yet"])
        finally:
            context.close()

    def test_scheduling_a_field_job_offers_only_that_customers_sites(self):
        """
        The picker here offered every site in the company, so a crew could be
        sent to another customer's address.
        """
        context, page = self._open(self.supervisor, "/field-jobs/schedule/")
        try:
            self.assertEqual(self._options(page), ["Choose a customer first"])

            page.select_option("#id_customer", str(self.two_sites.pk))
            self.assertEqual(
                self._options(page), ["Where is this visit?", "Apapa depot", "Ikeja site"]
            )

            # Never another customer's, whichever is chosen.
            page.select_option("#id_customer", str(self.one_site.pk))
            self.assertEqual(
                self._options(page), ["Where is this visit?", "Main address — CHHC+66C"]
            )
            self.assertEqual(page.input_value("#id_site"), str(self.main.pk))
        finally:
            context.close()

    def test_the_customer_form_follows_the_kind_that_is_chosen(self):
        """
        An organisation is asked who to ask for; a person is not, because a
        person is their own contact. Whether the block is on screen changes
        nothing about what saves — the server drops it for an individual
        either way — so this is the only place the behaviour is visible.
        """
        context, page = self._open(self.receptionist, "/customers/new/")
        try:
            block = page.locator("[data-organisation-only]")
            label = page.locator("label[for='id_name']")

            # Organisation is where a blank form starts: most of the work is.
            self.assertTrue(block.is_visible())
            self.assertEqual(label.text_content().strip(), "Organisation name")

            page.check("input[name='kind'][value='individual']")
            self.assertFalse(block.is_visible(), "a person is their own contact")
            self.assertEqual(label.text_content().strip(), "Full name")

            page.check("input[name='kind'][value='organisation']")
            self.assertTrue(block.is_visible())
            self.assertEqual(label.text_content().strip(), "Organisation name")
        finally:
            context.close()
