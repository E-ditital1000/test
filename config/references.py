"""
Human-readable record references — TKT-0001, PRJ-0001, FJ-0001, INV-0001.

These are what people say to each other on the phone, so they are short,
sequential and stable. They are deliberately NOT the job lineage: `job_ref`
is the UUID that ties a ticket to its project, field jobs and invoice, and it
survives even if a reference is ever reissued.

Allocation takes a row lock so two receptionists creating a ticket in the
same second cannot be handed the same number.
"""
import re

from django.db import transaction


def next_reference(model, prefix, *, field="reference", width=4):
    """
    The next free reference for `model`, as `PREFIX-0001`.

    Call inside a transaction — on PostgreSQL the lock is held until commit,
    which is what makes concurrent allocation safe.
    """
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    highest = 0

    rows = model.objects.filter(**{f"{field}__startswith": f"{prefix}-"})
    connection = transaction.get_connection()
    # SQLite has no row locking, so asking for one raises rather than being
    # ignored. Development runs on SQLite; the lock matters on Postgres.
    if connection.in_atomic_block and connection.features.has_select_for_update:
        rows = rows.select_for_update()

    for value in rows.values_list(field, flat=True):
        match = pattern.match(value or "")
        if match:
            highest = max(highest, int(match.group(1)))

    return f"{prefix}-{highest + 1:0{width}d}"
