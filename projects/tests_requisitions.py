"""
Requisitions: what a job needs, listed by whoever can see the job.

A requisition used to be a sentence and one typed figure. Nobody could buy
against it, check it, or price a quotation from it, and the person who knew
the items — the technician on the site — had no screen to write them on.

What is pinned down here: the items are what the amount is made of, a
technician reaches the screen only for their own jobs, and approval is still
somebody else's.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from approvals.models import Approval
from config.models import PolicySetting, ServiceType, StatusOption
from crm.models import Customer
from fieldjobs.models import FieldJob
from hr.models import Employee
from projects.models import Project, Requisition

User = get_user_model()


def make_user(email, role_names=(), staff_id=None):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345",
        first_name=email.split("@")[0].title(), last_name="Toe",
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    if staff_id:
        Employee.objects.create(user=user, staff_id=staff_id)
    return user


def rows(*items, total=None):
    """Formset POST data for the item rows."""
    data = {
        "form-TOTAL_FORMS": str(total or max(len(items), 4)),
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "40",
    }
    for index in range(int(data["form-TOTAL_FORMS"])):
        item = items[index] if index < len(items) else {}
        data[f"form-{index}-description"] = item.get("description", "")
        data[f"form-{index}-quantity"] = item.get("quantity", "")
        data[f"form-{index}-unit"] = item.get("unit", "")
        data[f"form-{index}-estimated_unit_cost"] = item.get("cost", "")
    return data


class RequisitionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.REQUISITION_THRESHOLD, value="5000")
        cls.customer = Customer.objects.create(name="Liberty Gold")
        cls.service = ServiceType.objects.create(code="solar", name="Solar install")
        cls.status = StatusOption.objects.create(
            kind="project", code="open", label="Open", is_default=True
        )
        cls.manager = make_user("manager@t.local", ["Project Manager"])
        cls.technician = make_user("tech@t.local", ["Technician"], staff_id="A1-100")
        cls.stranger = make_user("other@t.local", ["Technician"], staff_id="A1-200")
        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Yard solar", customer=cls.customer,
            service_type=cls.service, status=cls.status, manager=cls.manager,
        )
        cls.job = FieldJob.objects.create(
            reference="FJ-0001", project=cls.project, customer=cls.customer,
            service_type=cls.service, assigned_to=cls.technician,
            scheduled_for=timezone.now() + timedelta(hours=2),
        )

    def post_items(self, url, *items, description="Second fix on the roof", needed_by=""):
        data = {"description": description, "needed_by": needed_by}
        data.update(rows(*items))
        return self.client.post(url, data)


class TechnicianRequisitionTests(RequisitionTestCase):
    def url(self, job=None):
        return reverse("fieldjobs-job-requisition", args=[(job or self.job).pk])

    @tag("acceptance")
    def test_a_technician_lists_what_the_job_needs_from_the_site(self):
        """
        The whole point: the person who knows the items writes them down,
        instead of a sentence reaching the office three days later.
        """
        self.client.force_login(self.technician)
        response = self.post_items(
            self.url(),
            {"description": "2.5mm twin cable", "quantity": "3", "unit": "rolls", "cost": "85"},
            {"description": "32A breaker", "quantity": "2", "cost": "40"},
        )
        self.assertRedirects(response, reverse("fieldjobs-job-detail", args=[self.job.pk]))

        requisition = Requisition.objects.get()
        self.assertEqual(requisition.project, self.project)
        self.assertEqual(requisition.raised_by, self.technician)
        self.assertEqual(
            [(i.description, i.quantity, i.unit) for i in requisition.items.all()],
            [
                ("2.5mm twin cable", Decimal("3.00"), "rolls"),
                ("32A breaker", Decimal("2.00"), ""),
            ],
        )
        # 3 x 85 + 2 x 40
        self.assertEqual(requisition.amount, Decimal("335.00"))
        self.assertEqual(requisition.approval_state, "submitted")

    def test_the_amount_is_the_items_and_cannot_be_typed_over_them(self):
        self.client.force_login(self.technician)
        data = {"description": "Roof work", "needed_by": "", "amount": "999999"}
        data.update(rows({"description": "Rail", "quantity": "10", "cost": "12.50"}))
        self.client.post(self.url(), data)
        requisition = Requisition.objects.get()
        self.assertEqual(requisition.amount, Decimal("125.00"))
        self.assertEqual(requisition.items_total, requisition.amount)

    def test_items_without_a_cost_are_still_listed(self):
        """A technician is asked what is needed, not made to invent a price."""
        self.client.force_login(self.technician)
        self.post_items(self.url(), {"description": "Scaffold tower", "quantity": "1"})
        requisition = Requisition.objects.get()
        self.assertEqual(requisition.items.count(), 1)
        self.assertIsNone(requisition.items.first().estimated_unit_cost)
        self.assertEqual(requisition.amount, Decimal("0"))
        self.assertFalse(requisition.has_estimates)

    def test_blank_rows_are_dropped_and_an_empty_list_is_refused(self):
        self.client.force_login(self.technician)
        response = self.post_items(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "List at least one thing the job needs")
        self.assertFalse(Requisition.objects.exists())

        self.post_items(self.url(), {"description": "Cable", "quantity": "1"})
        self.assertEqual(Requisition.objects.get().items.count(), 1)

    def test_a_quantity_with_no_item_is_refused(self):
        self.client.force_login(self.technician)
        response = self.post_items(self.url(), {"quantity": "4", "cost": "10"})
        self.assertContains(response, "Say what this is")
        self.assertFalse(Requisition.objects.exists())

    def test_a_technician_cannot_reach_another_crews_job(self):
        """A 404, not a refusal: another crew's job is not theirs to learn about."""
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.post_items(self.url(), {"description": "Cable", "quantity": "1"})
        self.assertFalse(Requisition.objects.exists())

    def test_a_job_with_no_project_says_so_instead_of_failing(self):
        loose = FieldJob.objects.create(
            reference="FJ-0002", customer=self.customer, service_type=self.service,
            assigned_to=self.technician, scheduled_for=timezone.now() + timedelta(days=1),
        )
        self.client.force_login(self.technician)
        response = self.client.get(self.url(loose), follow=True)
        self.assertContains(response, "not under a project yet")
        self.assertFalse(Requisition.objects.exists())

    def test_raising_one_is_not_approving_it(self):
        self.client.force_login(self.technician)
        self.post_items(self.url(), {"description": "Cable", "quantity": "1", "cost": "100"})
        requisition = Requisition.objects.get()
        self.assertFalse(requisition.is_approved)
        # And the technician cannot decide it either.
        self.assertEqual(
            self.client.post(
                reverse("finance-requisition-decide", args=[requisition.pk]),
                {"decision": Approval.APPROVED},
            ).status_code,
            403,
        )

    def test_the_job_screen_offers_it(self):
        self.client.force_login(self.technician)
        body = self.client.get(reverse("fieldjobs-job-detail", args=[self.job.pk])).content.decode()
        self.assertIn(self.url(), body)


class OfficeRequisitionTests(RequisitionTestCase):
    def url(self):
        return reverse("projects-requisition-create", args=[self.project.pk])

    def test_the_office_form_makes_the_same_kind_of_record(self):
        self.client.force_login(self.manager)
        self.post_items(
            self.url(),
            {"description": "Inverter", "quantity": "1", "cost": "1850"},
            {"description": "Mounting rail", "quantity": "6", "unit": "lengths", "cost": "95"},
        )
        requisition = Requisition.objects.get()
        self.assertEqual(requisition.items.count(), 2)
        self.assertEqual(requisition.amount, Decimal("2420.00"))
        self.assertEqual(requisition.raised_by, self.manager)

    def test_a_large_one_still_routes_to_an_executive(self):
        """The threshold is applied to the total of the items."""
        self.client.force_login(self.manager)
        self.post_items(self.url(), {"description": "Generator", "quantity": "1", "cost": "7500"})
        requisition = Requisition.objects.get()
        self.assertEqual(requisition.amount, Decimal("7500.00"))
        self.assertTrue(requisition.requires_executive_approval())

    def test_a_date_that_has_passed_is_refused(self):
        self.client.force_login(self.manager)
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.post_items(
            self.url(), {"description": "Cable", "quantity": "1"}, needed_by=yesterday
        )
        self.assertContains(response, "That date has passed")
        self.assertFalse(Requisition.objects.exists())

    def test_somebody_without_the_permission_is_refused(self):
        nobody = make_user("clerk@t.local", ["Employee"])
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_the_project_lists_what_was_asked_for(self):
        self.client.force_login(self.manager)
        self.post_items(self.url(), {"description": "Mounting rail", "quantity": "6", "unit": "lengths"})
        body = self.client.get(reverse("projects-detail", args=[self.project.pk])).content.decode()
        self.assertIn("6 lengths Mounting rail", body)

    def test_finance_sees_the_items_it_is_approving(self):
        self.client.force_login(self.manager)
        self.post_items(self.url(), {"description": "Inverter", "quantity": "1", "cost": "1850"})
        finance = make_user("finance@t.local", ["Finance"])
        self.client.force_login(finance)
        body = self.client.get(reverse("finance-requisitions")).content.decode()
        self.assertIn("Inverter", body)
