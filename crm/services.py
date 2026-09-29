"""
Customer records, and the one thing the register quietly owed the field.

A site is where work happens. Most customers have exactly one — the address
somebody typed when the customer was created — but nothing turned that
address into a site, so the picker on a field job was empty and a technician
was sent to a job with no location on it at all.
"""
from .models import Site

MAIN_SITE_NAME = "Main address"


def ensure_main_site(customer):
    """
    Give a customer with an address a site to be visited at.

    Only ever adds the first one: once somebody has named their depots and
    branches, the address on the customer record is not a place of work and
    inventing a site from it would put a wrong option in the picker.
    """
    address = (customer.address or "").strip()
    if not address or customer.sites.exists():
        return None
    return Site.objects.create(customer=customer, name=MAIN_SITE_NAME, address=address)
