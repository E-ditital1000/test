"""
Loading the existing customer list into A1 360.

One CSV, one row per customer-site-contact. A customer with several sites
or contacts simply appears on several rows under the same name; the first
row carries the customer's own details and later rows add to it.

It never changes a customer that is already in the system. A name, phone
or email already on the register makes the row "already there": skipped
and reported, so the file can be corrected and run again safely, and an
import can never overwrite something the office has since edited by hand.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from config.csv_import import Report, read_rows

from .models import Contact, Customer, Site
from .services import ensure_main_site

REQUIRED = ["name"]
CUSTOMER_COLUMNS = ["name", "phone", "email", "address", "notes"]
CONTACT_COLUMNS = ["contact_name", "contact_job_title", "contact_phone", "contact_email"]
SITE_COLUMNS = ["site_name", "site_address", "site_latitude", "site_longitude"]
KNOWN = CUSTOMER_COLUMNS + ["kind"] + CONTACT_COLUMNS + SITE_COLUMNS

# What a cleaned list is likely to say, in either spelling. A list that says
# nothing gets organisations, which is what most of the register is — and a
# word nobody here recognises is an error rather than a quiet guess.
KINDS = {
    "individual": Customer.INDIVIDUAL,
    "person": Customer.INDIVIDUAL,
    "private": Customer.INDIVIDUAL,
    "organisation": Customer.ORGANISATION,
    "organization": Customer.ORGANISATION,
    "company": Customer.ORGANISATION,
    "business": Customer.ORGANISATION,
    "institution": Customer.ORGANISATION,
}


def _limits():
    """Each column's maximum length, read from the models so they cannot drift."""
    def length(model, field):
        return model._meta.get_field(field).max_length

    limits = {c: length(Customer, c) for c in ("name", "phone", "email")}
    limits.update({
        "contact_name": length(Contact, "name"),
        "contact_job_title": length(Contact, "job_title"),
        "contact_phone": length(Contact, "phone"),
        "contact_email": length(Contact, "email"),
        "site_name": length(Site, "name"),
    })
    return limits


def name_key(name):
    """'  ACME  Ltd. ' and 'Acme Ltd' are the same customer."""
    return " ".join(name.lower().replace(".", " ").replace(",", " ").split())


def phone_key(phone):
    digits = "".join(ch for ch in phone if ch.isdigit())
    # The last nine digits: +231 77 012 3456 and 077 012 3456 are one number.
    return digits[-9:] if len(digits) >= 7 else ""


def _email_ok(report, line, value, label):
    if not value:
        return True
    try:
        validate_email(value)
        return True
    except ValidationError:
        report.error(line, f"{label} '{value}' is not an email address.")
        return False


def _kind(report, line, value):
    """Which kind of customer this row is. Blank is an organisation."""
    if not value.strip():
        return Customer.ORGANISATION
    kind = KINDS.get(value.strip().lower())
    if kind is None:
        report.error(
            line,
            f"Kind '{value}' is not one this import reads. Write 'individual' "
            f"for a person or 'organisation' for a company or institution, "
            f"or leave it empty for an organisation.",
        )
    return kind


def _coordinate(report, line, value, label, limit):
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        report.error(line, f"{label} '{value}' is not a number.")
        return None
    if abs(number) > limit:
        report.error(line, f"{label} {value} is outside -{limit} to {limit}.")
        return None
    return number.quantize(Decimal("0.000001"))


def _existing_index():
    """Every active customer, findable by name, phone and email."""
    by_name, by_phone, by_email = {}, {}, {}
    for customer in Customer.objects.filter(is_active=True).only("id", "name", "phone", "email"):
        by_name.setdefault(name_key(customer.name), customer)
        if phone_key(customer.phone):
            by_phone.setdefault(phone_key(customer.phone), customer)
        if customer.email:
            by_email.setdefault(customer.email.lower(), customer)
    return by_name, by_phone, by_email


def plan(raw):
    """
    Read and check the file without writing anything. Returns (report,
    customers) where customers is the list of what would be created, each
    {"line", "fields", "sites", "contacts"}.
    """
    report = Report()
    rows, unknown = read_rows(raw, required=REQUIRED, known=KNOWN)
    for header in unknown:
        report.warn(None, f"Column '{header}' is not one this import reads, so it is ignored.")
    if not rows:
        report.warn(None, "The file has a header but no rows.")

    by_name, by_phone, by_email = _existing_index()
    limits = _limits()
    planned = {}           # name_key -> customer plan
    phone_owner = {}       # phone_key -> name_key, within this file
    email_owner = {}

    for row in rows:
        v, line = row.values, row.line
        name = v.get("name", "")
        if not name:
            report.error(line, "No customer name. Every row needs one.")
            continue
        key = name_key(name)

        # Already on the register: leave it alone.
        existing = (
            by_name.get(key)
            or by_phone.get(phone_key(v.get("phone", "")))
            or (by_email.get(v.get("email", "").lower()) if v.get("email") else None)
        )
        if existing and key not in planned:
            how = "name" if by_name.get(key) == existing else (
                "phone number" if by_phone.get(phone_key(v.get("phone", ""))) == existing else "email"
            )
            report.skip(line, f"'{name}' is already a customer (matches '{existing.name}' by {how}).")
            continue

        errors_before = len(report.errors)
        for column, limit in limits.items():
            if limit and len(v.get(column, "")) > limit:
                report.error(
                    line, f"{column.replace('_', ' ').capitalize()} is {len(v[column])} "
                    f"characters; the most it can hold is {limit}."
                )
        _email_ok(report, line, v.get("email", ""), "Email")
        _email_ok(report, line, v.get("contact_email", ""), "Contact email")
        # Checked on every row so a typo is reported wherever it is written,
        # though only the customer's first row decides what they are.
        kind = _kind(report, line, v.get("kind", ""))
        latitude = _coordinate(report, line, v.get("site_latitude", ""), "Latitude", 90)
        longitude = _coordinate(report, line, v.get("site_longitude", ""), "Longitude", 180)
        # On what was typed, not what parsed: a bad number is its own error.
        if bool(v.get("site_latitude")) != bool(v.get("site_longitude")):
            report.error(line, "A site needs both latitude and longitude, or neither.")
        if len(report.errors) > errors_before:
            continue

        customer = planned.get(key)
        if customer is None:
            # Two different names sharing a phone or email in the same file
            # is usually one customer spelt two ways.
            other = phone_owner.get(phone_key(v.get("phone", ""))) or (
                email_owner.get(v.get("email", "").lower()) if v.get("email") else None
            )
            if other:
                report.warn(
                    line,
                    f"'{name}' shares a phone or email with '{planned[other]['fields']['name']}' "
                    f"(line {planned[other]['line']}). Check they are not the same customer.",
                )
            customer = {
                "line": line,
                "fields": {c: v.get(c, "") for c in CUSTOMER_COLUMNS} | {"kind": kind},
                "sites": [],
                "contacts": [],
            }
            planned[key] = customer
            if phone_key(v.get("phone", "")):
                phone_owner.setdefault(phone_key(v["phone"]), key)
            if v.get("email"):
                email_owner.setdefault(v["email"].lower(), key)
        else:
            for column in ("phone", "email", "address"):
                given, kept = v.get(column, ""), customer["fields"][column]
                if given and kept and given != kept:
                    report.warn(
                        line,
                        f"'{name}' has {column} '{given}' here but '{kept}' on line "
                        f"{customer['line']}. The first is kept.",
                    )
                elif given and not kept:
                    customer["fields"][column] = given

        if v.get("site_name") or v.get("site_address"):
            site_name = v.get("site_name") or v.get("site_address")
            if any(name_key(s["name"]) == name_key(site_name) for s in customer["sites"]):
                report.warn(line, f"Site '{site_name}' is listed twice for '{name}'; once is kept.")
            else:
                customer["sites"].append({
                    "name": site_name,
                    "address": v.get("site_address", ""),
                    "latitude": latitude,
                    "longitude": longitude,
                })

        if v.get("contact_name"):
            if any(name_key(c["name"]) == name_key(v["contact_name"]) for c in customer["contacts"]):
                report.warn(line, f"Contact '{v['contact_name']}' is listed twice for '{name}'; once is kept.")
            else:
                customer["contacts"].append({
                    "name": v["contact_name"],
                    "job_title": v.get("contact_job_title", ""),
                    "phone": v.get("contact_phone", ""),
                    "email": v.get("contact_email", ""),
                })
        elif v.get("contact_phone") or v.get("contact_email"):
            report.error(line, "A contact phone or email with no contact name.")

    customers = list(planned.values())
    for customer in customers:
        report.created.append(
            "{} (line {}) with {} site{} and {} contact{}".format(
                customer["fields"]["name"], customer["line"],
                len(customer["sites"]), "" if len(customer["sites"]) == 1 else "s",
                len(customer["contacts"]), "" if len(customer["contacts"]) == 1 else "s",
            )
        )
    return report, customers


def import_customers(raw, *, actor, commit=False, source=""):
    """
    Plan the import and, only if `commit` and nothing is wrong, write it in
    one transaction: all of it or none of it. A half-loaded list is worse
    than an unloaded one, because nobody can tell which half is missing.
    """
    from accounts import audit

    report, customers = plan(raw)
    if not commit or not report.ok or not customers:
        return report

    with transaction.atomic():
        for planned in customers:
            customer = Customer.objects.create(created_by=actor, **planned["fields"])
            for site in planned["sites"]:
                Site.objects.create(customer=customer, **site)
            # A row with an address but no site column still leaves somewhere
            # a field job can be booked at.
            ensure_main_site(customer)
            for index, contact in enumerate(planned["contacts"]):
                # The first contact given is the main one.
                Contact.objects.create(customer=customer, is_primary=index == 0, **contact)
            audit.record_change(
                actor=actor,
                action="customer.imported",
                target=customer,
                after={"source": source, "line": planned["line"]},
            )
    report.committed = True
    return report
