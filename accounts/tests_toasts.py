"""
The toast contract.

Most of a toast is feel, which a test runner cannot judge. What it can pin
down is the part that would do real harm if it broke: which messages are
allowed to disappear on their own, and what a reader sees when the script
never runs.
"""
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase, tag
from django.template.loader import render_to_string
from django.urls import reverse

from accounts.models import Role, UserRole

User = get_user_model()

TOAST = re.compile(r'<div class="toast toast-(\w+)"[^>]*>')


def toasts_in(body):
    """(level, sticky) for every toast rendered in `body`."""
    return [(m.group(1), "data-sticky" in m.group(0)) for m in TOAST.finditer(body)]


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class ToastRenderingTests(TestCase):
    """The partial on its own, fed each kind of message."""

    def render(self, *entries):
        request = RequestFactory().get("/")
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        for level, text, extra in entries:
            messages.add_message(request, level, text, extra_tags=extra)
        return render_to_string("shell/_toasts.html", {"messages": messages.get_messages(request)})

    def test_an_ordinary_success_is_allowed_to_leave_on_its_own(self):
        self.assertEqual(toasts_in(self.render((messages.SUCCESS, "Saved.", ""))),
                         [("success", False)])

    def test_errors_and_warnings_stay_until_closed(self):
        """Something went wrong; the reader has to actually see it."""
        rendered = self.render(
            (messages.ERROR, "That could not be saved.", ""),
            (messages.WARNING, "Expires soon.", ""),
        )
        self.assertEqual(toasts_in(rendered), [("error", True), ("warning", True)])

    def test_a_sticky_success_stays_and_says_why(self):
        rendered = self.render((messages.SUCCESS, "Temporary password: abc", "sticky"))
        self.assertEqual(toasts_in(rendered), [("success", True)])
        self.assertIn("Stays until you close it", rendered)

    def test_errors_carry_the_alert_role(self):
        """So a screen reader interrupts for a failure but not for a save."""
        rendered = self.render(
            (messages.ERROR, "Failed.", ""),
            (messages.SUCCESS, "Saved.", ""),
        )
        self.assertIn('role="alert"', rendered)
        self.assertIn('role="status"', rendered)

    def test_nothing_is_hidden_until_the_script_runs(self):
        """
        The stack is only floated and hidden once the inline script sets
        [data-armed]. The server never sets it, so if the script does not run
        -- no JavaScript, or it failed on a weak connection -- every message
        is still plainly visible in the page.
        """
        rendered = self.render((messages.SUCCESS, "Temporary password: abc", "sticky"))
        self.assertNotIn("data-armed=", rendered)
        self.assertNotIn('data-state="', rendered)

    def test_the_script_is_inline_beside_the_stack(self):
        """
        A separate file could fail to load on a weak connection and leave
        armed toasts hidden forever. Inline, it arrives with the page or not
        at all.
        """
        rendered = self.render((messages.SUCCESS, "Saved.", ""))
        self.assertRegex(rendered, r"</section>\s*<script>")
        self.assertNotIn("<script src=", rendered)

    def test_message_text_is_escaped(self):
        """An employee's name is user input, and it lands in these toasts."""
        rendered = self.render((messages.SUCCESS, "<img src=x onerror=alert(1)> onboarded", ""))
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;img", rendered)

    def test_a_page_with_no_messages_emits_nothing(self):
        self.assertEqual(self.render().strip(), "")


class ToastFlowTests(TestCase):
    """The two places a one-time password reaches a person."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.admin = make_user("admin@test.local", ["Admin"])
        cls.hr = make_user("hr@test.local", ["HR"])

    @tag("acceptance")
    def test_the_onboarding_password_never_auto_dismisses(self):
        """
        The temporary password is shown exactly once. An auto-dismissing
        toast would take the only copy away while HR glanced at the new
        starter, and they could not sign in.
        """
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("hr-employee-create"),
            {
                "first_name": "New", "last_name": "Starter",
                "email": "new.starter@test.local", "phone": "",
                "staff_id": "A1-9001", "job_title": "", "department": "",
                "supervisor": "", "start_date": "",
                "roles": [Role.objects.get(name="Employee").pk],
            },
            follow=True,
        )
        body = response.content.decode()
        self.assertIn("Temporary password", body)
        self.assertEqual(toasts_in(body), [("success", True)])

    @tag("acceptance")
    def test_the_settings_password_never_auto_dismisses(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("settings-user-create"),
            {
                "first_name": "Office", "last_name": "User",
                "email": "office.user@test.local", "is_active": "on",
                "roles": [Role.objects.get(name="Employee").pk],
            },
            follow=True,
        )
        body = response.content.decode()
        self.assertIn("Temporary password", body)
        self.assertEqual(toasts_in(body), [("success", True)])

    def test_sign_in_feedback_stays_beside_the_form(self):
        """
        "Email or password is incorrect" is about the fields in front of the
        reader, so it stays next to them rather than floating off to a corner.
        """
        response = self.client.post(
            reverse("login"), {"email": "nobody@test.local", "password": "wrong"}
        )
        body = response.content.decode()
        self.assertIn('class="msg msg-', body)
        self.assertNotIn("data-toasts", body)
