"""
Somewhere to send people.

A site is where work happens, and the picker on a field job reads from it.
Nothing turned a customer's own address into one, so for most customers that
picker was empty and a technician could be sent to a job with no location on
it at all.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import ServiceType, StatusOption
from crm.models import Customer, Site
from crm.services import ensure_main_site
from projects.models import Project

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(username=email, email=email, password="Testing!12345")
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class MainSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.admin = make_user("admin@t.local", ["Admin"])

    def test_a_customers_address_becomes_somewhere_work_can_be_booked(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("crm-customer-create"),
            {
                "kind": Customer.ORGANISATION,
                "name": "Liberty Gold",
                "phone": "+231 770 111 222",
                "email": "",
                "address": "12 Broad Street, Monrovia",
                "notes": "",
            },
        )
        site = Customer.objects.get(name="Liberty Gold").sites.get()
        self.assertEqual(site.name, "Main address")
        self.assertEqual(site.address, "12 Broad Street, Monrovia")

    def test_a_customer_with_no_address_gets_no_site(self):
        customer = Customer.objects.create(name="No Address Ltd")
        self.assertIsNone(ensure_main_site(customer))
        self.assertEqual(customer.sites.count(), 0)

    def test_it_never_adds_a_second_one(self):
        """Once somebody has named their depots, the register's address is not one."""
        customer = Customer.objects.create(name="Liberty Gold", address="12 Broad Street")
        Site.objects.create(customer=customer, name="Ganta depot", address="Ganta")
        self.assertIsNone(ensure_main_site(customer))
        self.assertEqual([s.name for s in customer.sites.all()], ["Ganta depot"])

    def test_an_imported_customer_gets_one_too(self):
        from crm.importer import import_customers

        raw = b"name,address\nLiberty Gold,12 Broad Street Monrovia\n"
        import_customers(raw, actor=self.admin, commit=True, source="test.csv")
        self.assertEqual(Customer.objects.get().sites.get().name, "Main address")

    def test_an_imported_customer_with_its_own_site_keeps_only_that(self):
        from crm.importer import import_customers

        raw = b"name,address,site_name,site_address\nLiberty Gold,12 Broad St,Ganta depot,Ganta\n"
        import_customers(raw, actor=self.admin, commit=True, source="test.csv")
        self.assertEqual([s.name for s in Customer.objects.get().sites.all()], ["Ganta depot"])


class FieldJobSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.supervisor = make_user("super@t.local", ["Supervisor"])
        cls.service = ServiceType.objects.create(code="solar", name="Solar install")
        cls.status = StatusOption.objects.create(
            kind="project", code="open", label="Open", is_default=True
        )
        cls.customer = Customer.objects.create(name="Liberty Gold", address="12 Broad Street")
        ensure_main_site(cls.customer)
        cls.other = Customer.objects.create(name="New Hope Clinic", address="Sinkor")
        ensure_main_site(cls.other)
        cls.project = Project.objects.create(
            reference="PRJ-0001", name="Yard solar", customer=cls.customer,
            service_type=cls.service, status=cls.status,
        )

    def test_the_picker_offers_the_customers_address_with_the_address_on_it(self):
        self.client.force_login(self.supervisor)
        body = self.client.get(
            reverse("fieldjobs-schedule-for-project", args=[self.project.pk])
        ).content.decode()
        self.assertIn("Main address — 12 Broad Street", body)

    def test_it_never_offers_another_customers_site(self):
        self.client.force_login(self.supervisor)
        body = self.client.get(
            reverse("fieldjobs-schedule-for-project", args=[self.project.pk])
        ).content.decode()
        self.assertNotIn("Sinkor", body)

    def test_a_customer_with_no_site_says_so_instead_of_showing_a_blank(self):
        bare = Customer.objects.create(name="Nothing Recorded")
        project = Project.objects.create(
            reference="PRJ-0002", name="Survey", customer=bare,
            service_type=self.service, status=self.status,
        )
        self.client.force_login(self.supervisor)
        body = self.client.get(
            reverse("fieldjobs-schedule-for-project", args=[project.pk])
        ).content.decode()
        self.assertIn("No site on this customer yet", body)

    def test_a_job_can_still_be_scheduled_against_the_site(self):
        self.client.force_login(self.supervisor)
        technician = make_user("tech@t.local", ["Technician"])
        site = self.customer.sites.get()
        response = self.client.post(
            reverse("fieldjobs-schedule-for-project", args=[self.project.pk]),
            {
                "customer": self.customer.pk,
                "site": site.pk,
                "service_type": self.service.pk,
                "assigned_to": technician.pk,
                "scheduled_for": (timezone.now() + timezone.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
                "instructions": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        from fieldjobs.models import FieldJob

        self.assertEqual(FieldJob.objects.get().site, site)
