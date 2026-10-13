"""
Taking a record out, and saying why it will not go.

A-1 asked that an Admin or HR be able to remove anything. The database
already draws the line in the right place and nobody had noticed: every
relationship that represents *evidence* is `PROTECT`, and everything that is
merely part of the thing being removed is `CASCADE`.

So a field job with no check-in deletes cleanly, taking its crew and its
checklist with it, and a field job somebody has already checked in to and
assessed cannot be deleted at all. A project deletes until it has a
requisition, a visit, an expense, an invoice or a quotation hanging off it.
That is exactly the rule A-1 chose — erase the plan, never the history — and
it is enforced by the schema rather than by a view remembering to check.

What was missing is the explanation. A refusal arrived as ProtectedError, a
500, and a page that said nothing. This turns it into a sentence naming what
is in the way, so the person removing knows whether to go and remove that
first or to leave the record alone.
"""
from django.db.models import ProtectedError


class CannotRemove(Exception):
    """Refused, with something the person can act on."""


def _describe(blockers):
    """
    "2 assessments and 1 check-in" — counted by kind, because a list of
    forty object repr strings tells nobody anything.
    """
    counted = {}
    for obj in blockers:
        name = obj._meta.verbose_name
        counted[name] = counted.get(name, 0) + 1

    parts = [
        f"{count} {name}{'' if count == 1 else 's'}"
        for name, count in sorted(counted.items())
    ]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def remove(instance, *, actor, reason="", audit_action=None):
    """
    Delete a record, or raise CannotRemove saying what is holding it.

    Audited before the delete rather than after, because afterwards there is
    no object left to describe. The row that survives in the audit log is the
    only trace that the record ever existed, so it carries who removed it,
    what it was, and the reason they gave.
    """
    from accounts import audit

    label = str(instance)
    model = instance._meta.verbose_name
    snapshot = audit.snapshot(
        instance,
        fields=[
            f.name for f in instance._meta.fields
            if f.name not in ("id", "created_at", "updated_at")
        ],
    )

    entry = audit.record_change(
        actor=actor,
        action=audit_action or f"{instance._meta.model_name}.removed",
        target=instance,
        before=snapshot,
        after=None,
        reason=reason,
    )

    try:
        instance.delete()
    except ProtectedError as error:
        # Nothing was removed, so the audit row would be a lie.
        entry.delete()
        raise CannotRemove(
            f"This {model} cannot be removed: {_describe(error.protected_objects)} "
            f"still depend{'s' if len(error.protected_objects) == 1 else ''} on it. "
            "Those are the record of what happened and are kept on purpose. "
            "Remove them first if they were a mistake too, or leave this where "
            "it is."
        ) from error

    return label
