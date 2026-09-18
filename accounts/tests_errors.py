"""
The server-error page and the log line behind it. A crash during the pilot
has to leave a trace somebody can find, and the person who hit it has to be
able to point at it.
"""
import re

from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import path


def boom(request):
    raise RuntimeError("the pilot found a bug")


def fine(request):
    return HttpResponse("ok")


# This module doubles as the urlconf for these tests: the real site has no
# view that fails on purpose, and should not grow one.
urlpatterns = [path("boom/", boom), path("fine/", fine)]
handler500 = "accounts.views.server_error"


@override_settings(ROOT_URLCONF="accounts.tests_errors", DEBUG=False)
class ServerErrorTests(TestCase):
    def setUp(self):
        # By default the test client re-raises; a real browser gets the page.
        self.client = Client(raise_request_exception=False)

    def test_a_crash_shows_the_designed_page_with_a_reference(self):
        with (
            self.assertLogs("django.request", level="ERROR"),
            self.assertLogs("a1360.errors", level="ERROR"),
        ):
            response = self.client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("Something went wrong", body)
        self.assertRegex(body, r'<b class="mono">[A-Z0-9]{6}</b>')

    def test_the_reference_on_screen_is_the_one_in_the_log(self):
        with (
            self.assertLogs("django.request", level="ERROR"),
            self.assertLogs("a1360.errors", level="ERROR") as logs,
        ):
            response = self.client.get("/boom/?page=2")
        shown = re.search(r'<b class="mono">([A-Z0-9]{6})</b>', response.content.decode()).group(1)
        line = logs.output[0]
        self.assertIn(f"ref={shown}", line)
        self.assertIn("GET /boom/?page=2", line)

    def test_the_traceback_itself_is_logged(self):
        """Django's own request logger carries it; our config must not silence it."""
        with self.assertLogs("django.request", level="ERROR") as logs:
            with self.assertLogs("a1360.errors", level="ERROR"):
                self.client.get("/boom/")
        self.assertTrue(any("RuntimeError" in "\n".join(r.exc_text or "" for r in logs.records) or r.exc_info for r in logs.records))

    def test_the_page_needs_no_database_and_no_session(self):
        """Whatever failed may be either, so the page must render with neither."""
        from django.template.loader import render_to_string

        with self.assertNumQueries(0):
            html = render_to_string("500.html", {"error_ref": "ABC123"})
        self.assertIn("ABC123", html)
        self.assertNotIn("serviceWorker", html)


class LoggingConfigTests(TestCase):
    def test_errors_reach_stderr_and_warnings_do_not_drown_them(self):
        import logging

        from django.conf import settings

        django_logger = settings.LOGGING["loggers"]["django"]
        self.assertEqual(django_logger["level"], "ERROR")
        self.assertIn("stderr", django_logger["handlers"])
        self.assertEqual(settings.LOGGING["handlers"]["stderr"]["class"], "logging.StreamHandler")
        self.assertTrue(logging.getLogger("a1360.errors").isEnabledFor(logging.ERROR))
