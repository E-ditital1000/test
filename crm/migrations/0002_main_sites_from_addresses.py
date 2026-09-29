"""
Customers recorded before a site was made from their address have none, so
the picker on a field job is empty for them and a technician can be sent out
with no location on the job. This gives each of them the site the register
already implied.

Only customers with an address and no sites at all are touched: where
somebody has named their depots, the address on the record is not a place of
work and adding one would put a wrong option in the picker.
"""
from django.db import migrations

MAIN_SITE_NAME = "Main address"


def create_main_sites(apps, schema_editor):
    Customer = apps.get_model("crm", "Customer")
    Site = apps.get_model("crm", "Site")

    missing = (
        Customer.objects.filter(is_active=True)
        .exclude(address="")
        .filter(sites__isnull=True)
        .distinct()
    )
    Site.objects.bulk_create([
        Site(customer=customer, name=MAIN_SITE_NAME, address=customer.address.strip())
        for customer in missing
        if customer.address.strip()
    ])


def remove_main_sites(apps, schema_editor):
    """
    Only the ones this migration could have made: a site with the name it
    used, no coordinates, and nothing booked against it.
    """
    Site = apps.get_model("crm", "Site")
    Site.objects.filter(
        name=MAIN_SITE_NAME,
        latitude__isnull=True,
        longitude__isnull=True,
        tickets__isnull=True,
        projects__isnull=True,
        field_jobs__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("crm", "0001_initial")]

    operations = [migrations.RunPython(create_main_sites, remove_main_sites)]
