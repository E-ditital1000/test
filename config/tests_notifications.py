"""
Telling somebody something is waiting on them.

The guarantees worth defending: a notification never breaks the action that
raised it, nobody is told about work they could not act on anyway, and no
message carries a password or a sign-in link — staff here sign in with work
addresses that are not all read, and that decision is not reopened by this.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Ticket

User = get_user_model()


def make_user(email, role_names=(), **extra):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345", **extra
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class TicketAssignmentNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")
        cls.supervisor = make_user("sup.mail@test.local", ["Supervisor"], first_name="Grace")
        cls.technician = make_user("tech.mail@test.local", ["Technician"], first_name="Moses")

    def setUp(self):
        mail.outbox = []
        self.ticket = Ticket.objects.create(
            reference="TKT-8001", customer=self.customer, service_type=self.service,
            status=self.status, description="Inverter fault light since Tuesday.",
        )
        self.client.force_login(self.supervisor)

    def _assign(self, **extra):
        payload = {"assigned_to": self.technician.pk}
        payload.update(extra)
        return self.client.post(
            reverse("crm-ticket-assign", args=[self.ticket.pk]), payload
        )

    def test_the_technician_is_told(self):
        self._assign()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["tech.mail@test.local"])
        self.assertIn("TKT-8001", message.subject)
        self.assertIn("Duport Road Clinic", message.body)
        self.assertIn("Inverter fault light", message.body)

    def test_it_says_when_the_work_is_expected(self):
        from datetime import date, timedelta

        start = date.today()
        self._assign(
            start_date=start.isoformat(),
            due_date=(start + timedelta(days=3)).isoformat(),
        )
        self.assertIn("Expected", mail.outbox[0].body)

    def test_no_message_carries_a_password_or_a_sign_in_link(self):
        """A decision the system made deliberately, pinned here."""
        self._assign()
        body = mail.outbox[0].body.lower()
        for word in ("password", "reset your", "sign-in link", "log in with"):
            self.assertNotIn(word, body)

    def test_a_mail_server_that_refuses_does_not_undo_the_assignment(self):
        """
        The guarantee that matters. Somebody has been given work; whether
        the message left the building is not their problem and must not
        show them an error about it.
        """
        with patch(
            "django.core.mail.EmailMessage.send", side_effect=OSError("connection refused")
        ):
            response = self._assign()

        self.assertEqual(response.status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, self.technician)

    def test_somebody_with_no_address_is_simply_not_written_to(self):
        nameless = make_user("noaddress@test.local", ["Technician"])
        nameless.email = ""
        nameless.save(update_fields=["email"])
        self._assign(assigned_to=nameless.pk)
        self.assertEqual(len(mail.outbox), 0)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, nameless)


class WhoHearsAboutItTests(TestCase):
    """
    Who is told is who may act on it — read from the permission, so a role
    edited in Settings changes the mailing list with nothing to keep in step
    by hand.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.finance = make_user("finance.mail@test.local", ["Finance"])
        cls.executive = make_user("exec.mail@test.local", ["Executive"])
        cls.technician = make_user("tech2.mail@test.local", ["Technician"])

    def test_only_people_who_could_act_are_written_to(self):
        from config.notifications import recipients_holding

        approvers = recipients_holding("approve_requisition")
        self.assertIn("finance.mail@test.local", approvers)
        self.assertIn("exec.mail@test.local", approvers)
        self.assertNotIn("tech2.mail@test.local", approvers)

    def test_whoever_raised_it_is_not_told_about_their_own(self):
        from config.notifications import recipients_holding

        self.assertNotIn(
            "finance.mail@test.local",
            recipients_holding("approve_requisition", exclude=self.finance),
        )

    def test_a_deactivated_account_is_not_written_to(self):
        from config.notifications import recipients_holding

        self.finance.is_active = False
        self.finance.save(update_fields=["is_active"])
        self.assertNotIn("finance.mail@test.local", recipients_holding("approve_requisition"))

    def test_the_test_suite_never_sends_anything_outward(self):
        from django.conf import settings

        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
            "a test run must not post to the real world",
        )
