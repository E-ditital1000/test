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


def snapshot_stored(instance, fields=None):
    """
    The record as the database still has it, rather than as a form has
    already left it.

    `ModelForm.is_valid()` writes cleaned data onto `form.instance` during
    `_post_clean`, long before anybody calls `save()`. So the natural
    reading —

        if request.method == "POST" and form.is_valid():
            before = snapshot(instance)          # the NEW values
            after = snapshot(form.save())        # the same new values

    — takes both halves of the change after the change, and every edit in
    the system was audited as "nothing changed". It is the same object.

    Re-reading costs one SELECT on a screen somebody is already waiting on,
    and cannot be got wrong by moving a line. Use this for `before` on any
    edit that goes through a form; `snapshot` is still right for `after`,
    and for a `before` taken before anything has touched the instance.
    """
    if instance is None or instance.pk is None:
        return None
    stored = instance.__class__._default_manager.filter(pk=instance.pk).first()
    return snapshot(stored, fields=fields)


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
