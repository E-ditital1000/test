"""
A customer is a person or an organisation.

The register asked both the same five questions, so an organisation went in
as a name and a switchboard number, and the human being anyone would
actually ring was a second errand nobody ran. The form now asks which kind
first, and asks an organisation who to ask for while somebody is still
looking at it.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, UserRole

from .forms import CustomerForm
from .models import Contact, Customer

User = get_user_model()


class CustomerKindTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.receptionist = User.objects.create_user(
            username="recep@t.local", email="recep@t.local", password="Testing!12345"
        )
        cls.receptionist.must_reset_password = False
        cls.receptionist.save(update_fields=["must_reset_password"])
        UserRole.objects.create(
            user=cls.receptionist, role=Role.objects.get(name="Receptionist")
        )

    def setUp(self):
        self.client.force_login(self.receptionist)

    def _post(self, **fields):
        payload = {
            "kind": Customer.ORGANISATION, "name": "Harmony Foods Ltd",
            "phone": "", "email": "", "address": "", "notes": "",
            "contact_name": "", "contact_job_title": "",
            "contact_phone": "", "contact_email": "",
        }
        payload.update(fields)
        return self.client.post(reverse("crm-customer-create"), payload)

    # -- what gets saved --------------------------------------------------

    def test_an_organisation_keeps_the_person_who_answers_its_phone(self):
        response = self._post(
            contact_name="Amos Kollie", contact_job_title="Operations Manager",
            contact_phone="+231 880 123 456", contact_email="amos@harmony.example",
        )
        self.assertEqual(response.status_code, 302)

        customer = Customer.objects.get()
        self.assertEqual(customer.kind, Customer.ORGANISATION)
        contact = customer.contacts.get()
        self.assertEqual(contact.name, "Amos Kollie")
        self.assertEqual(contact.job_title, "Operations Manager")
        self.assertTrue(contact.is_primary, "the first contact is the main one")

    def test_an_organisation_can_be_saved_before_anyone_knows_who_to_ask_for(self):
        """Asked for, not insisted on: a name nobody has yet is not a reason
        to refuse the customer."""
        self.assertEqual(self._post().status_code, 302)
        self.assertEqual(Customer.objects.get().kind, Customer.ORGANISATION)
        self.assertFalse(Contact.objects.exists())

    def test_an_individual_is_their_own_contact(self):
        """The contact half belongs to organisations. Posted for a person —
        by a browser running no JavaScript, which never hid it — it is
        dropped rather than saved somewhere nobody looks."""
        response = self._post(
            kind=Customer.INDIVIDUAL, name="Faith Jallah", contact_name="Faith Jallah",
        )
        self.assertEqual(response.status_code, 302)

        customer = Customer.objects.get()
        self.assertEqual(customer.kind, Customer.INDIVIDUAL)
        self.assertFalse(customer.contacts.exists())

    def test_a_job_title_with_nobody_attached_is_refused(self):
        response = self._post(contact_job_title="Operations Manager")
        self.assertEqual(response.status_code, 200)
        self.assertIn("contact_name", response.context["form"].errors)
        self.assertFalse(Customer.objects.exists())

    # -- what the form asks -----------------------------------------------

    def test_the_form_asks_what_kind_before_anything_else(self):
        response = self.client.get(reverse("crm-customer-create"))
        form = response.context["form"]
        self.assertEqual(list(form.fields)[0], "kind")
        self.assertEqual(
            [value for value, _ in form.fields["kind"].choices],
            [Customer.INDIVIDUAL, Customer.ORGANISATION],
        )

    def test_the_name_field_asks_for_what_that_kind_is_called(self):
        self.assertEqual(CustomerForm().fields["name"].label, "Organisation name")
        self.assertEqual(
            CustomerForm({"kind": Customer.INDIVIDUAL}).fields["name"].label, "Full name"
        )

    def test_an_individuals_form_keeps_its_wording_when_it_comes_back_with_errors(self):
        """A failed submit must not hand back a form relabelled as something
        the person was not filling in."""
        response = self._post(kind=Customer.INDIVIDUAL, name="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].fields["name"].label, "Full name")

    def test_a_customer_who_already_has_contacts_is_not_asked_again(self):
        customer = Customer.objects.create(name="Harmony Foods Ltd")
        Contact.objects.create(customer=customer, name="Amos Kollie", is_primary=True)

        form = CustomerForm(instance=customer)
        self.assertEqual(form.contact_fields(), [])
        self.assertNotIn("contact_name", form.fields)

    def test_editing_that_customer_still_saves(self):
        """The contact half is absent from this form, and its validation must
        not go looking for it."""
        customer = Customer.objects.create(name="Harmony Foods Ltd")
        Contact.objects.create(customer=customer, name="Amos Kollie", is_primary=True)

        response = self.client.post(
            reverse("crm-customer-edit", args=[customer.pk]),
            {"kind": Customer.ORGANISATION, "name": "Harmony Foods Limited",
             "phone": "", "email": "", "address": "", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Harmony Foods Limited")
        self.assertEqual(customer.contacts.count(), 1)

    # -- what the register shows ------------------------------------------

    def test_the_register_and_the_record_say_which_kind_each_customer_is(self):
        Customer.objects.create(name="Faith Jallah", kind=Customer.INDIVIDUAL)
        organisation = Customer.objects.create(name="Harmony Foods Ltd")

        self.assertContains(self.client.get(reverse("crm-customers")), "Individual")
        self.assertContains(
            self.client.get(reverse("crm-customer-detail", args=[organisation.pk])),
            "Organisation",
        )

    def test_a_customer_recorded_with_nothing_said_is_an_organisation(self):
        """Which is what most of A-1's work is, and what the import assumes."""
        self.assertEqual(Customer.objects.create(name="Novatech").kind, Customer.ORGANISATION)
