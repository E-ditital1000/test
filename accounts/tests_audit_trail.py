"""
What the audit log actually recorded.

Found while giving an Admin the right to correct a ticket, which is what
A-1 asked for in "Super admin can't delete or edit ticket, field job". The
edit worked; the row it wrote said nothing had changed.

The cause is one line of Django that every edit screen here was built
around. `ModelForm.is_valid()` writes the cleaned data onto `form.instance`
during `_post_clean` — long before anybody calls `save()`. So the obvious
shape:

    if request.method == "POST" and form.is_valid():
        before = snapshot(instance)
        after = snapshot(form.save())

takes both halves of the change *after* the change. They are the same
object, so `before` and `after` were always identical, on every correction
in the system since the log was built.

Nothing failed and nothing was lost. The audit log simply recorded that
somebody edited something and declined to say what — which is exactly the
question it exists to answer, and the reason A-1 can show a corrected
attendance record to anybody and have it stand up.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse

from accounts import audit
from accounts.models import AuditEntry, Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Ticket
from hr.models import Department

User = get_user_model()


class SnapshotStoredTests(TestCase):
    """The helper, on its own."""

    def setUp(self):
        self.department = Department.objects.create(name="Field Operations")

    @tag("acceptance")
    def test_it_reads_the_database_and_not_the_instance_in_hand(self):
        from hr.forms import DepartmentForm

        form = DepartmentForm(
            {"name": "Field Ops", "order": 0, "is_active": "on"},
            instance=self.department,
        )
        self.assertTrue(form.is_valid(), form.errors)
        # Validating has already overwritten the object in memory. This is
        # the whole bug, in two lines.
        self.assertEqual(self.department.name, "Field Ops")
        self.assertEqual(
            audit.snapshot(self.department, fields=["name"])["name"], "Field Ops"
        )
        self.assertEqual(
            audit.snapshot_stored(self.department, fields=["name"])["name"],
            "Field Operations",
        )

    def test_an_unsaved_record_has_no_stored_version(self):
        self.assertIsNone(audit.snapshot_stored(Department(name="New"), fields=["name"]))
        self.assertIsNone(audit.snapshot_stored(None, fields=["name"]))


class EveryEditSaysWhatChanged(TestCase):
    """
    One screen per module, because the fault was in the shape they all
    share rather than in any one of them.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.admin = User.objects.create_user(
            username="audit.admin@test.local", email="audit.admin@test.local",
            password="Testing!12345", first_name="Daniel",
        )
        cls.admin.must_reset_password = False
        cls.admin.save(update_fields=["must_reset_password"])
        UserRole.objects.create(user=cls.admin, role=Role.objects.get(name="Admin"))

    def setUp(self):
        self.client.force_login(self.admin)

    def assert_change_recorded(self, action, field, was, now):
        entry = AuditEntry.objects.filter(action=action).order_by("-id").first()
        self.assertIsNotNone(entry, f"no {action} row was written")
        self.assertEqual(entry.before[field], was)
        self.assertEqual(entry.after[field], now)

    @tag("acceptance")
    def test_a_corrected_department_records_its_old_name(self):
        department = Department.objects.create(name="Field Operations")
        self.client.post(
            reverse("hr-department-edit", args=[department.pk]),
            {"name": "Field Ops", "order": 0, "is_active": "on"},
        )
        self.assert_change_recorded(
            "department.saved", "name", "Field Operations", "Field Ops"
        )

    def test_a_corrected_service_type_records_its_old_name(self):
        service = ServiceType.objects.create(code="solar", name="Solar")
        self.client.post(
            reverse("settings-service-type-edit", args=[service.pk]),
            {"code": "solar", "name": "Solar PV", "description": "",
             "order": 0, "is_active": "on"},
        )
        self.assert_change_recorded("service_type.saved", "name", "Solar", "Solar PV")

    def test_a_corrected_status_records_its_old_label(self):
        status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New"
        )
        self.client.post(
            reverse("settings-status-edit", args=[status.pk]),
            {"kind": StatusOption.TICKET, "code": "new", "label": "Just in",
             "order": 0, "is_active": "on"},
        )
        self.assert_change_recorded("status_option.saved", "label", "New", "Just in")

    @tag("acceptance")
    def test_a_corrected_ticket_records_its_old_description(self):
        service = ServiceType.objects.create(code="gen", name="Generators")
        status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="open", label="Open", is_default=True
        )
        customer = Customer.objects.create(name="Duport Road Clinic")
        ticket = Ticket.objects.create(
            reference="TKT-8001", customer=customer, service_type=service,
            status=status, description="Genset will not start.",
        )
        self.client.post(
            reverse("crm-ticket-edit", args=[ticket.pk]),
            {
                "customer": customer.pk, "site": "", "service_type": service.pk,
                "priority": Ticket.NORMAL,
                "description": "Genset starts and cuts out after a minute.",
            },
        )
        self.assert_change_recorded(
            "ticket.corrected", "description",
            "Genset will not start.",
            "Genset starts and cuts out after a minute.",
        )
