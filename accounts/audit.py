"""
The single way anything is written to the audit log. Every approval and
every amendment across all modules goes through here, so "actor, timestamp,
before, after, reason" is uniform and cannot be half-implemented per module.
"""
from django.forms.models import model_to_dict

from .models import AuditEntry


def snapshot(instance, fields=None):
    """A JSON-safe before/after picture of a model instance."""
    if instance is None:
        return None
    data = model_to_dict(instance, fields=fields)
    return {key: _jsonable(value) for key, value in data.items()}


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def record_change(*, actor, action, target, before=None, after=None, reason=""):
    """
    Append one audit row. `target` is any model instance; `before`/`after`
    are snapshots taken either side of the change.
    """
    return AuditEntry.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        action=action,
        target_type=f"{target._meta.app_label}.{target._meta.object_name}",
        target_id=str(target.pk),
        before=before,
        after=after,
        reason=reason,
    )
