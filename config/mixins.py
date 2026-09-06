"""
Cross-cutting abstract bases. These encode the architectural rules from the
Phase One brief section 4 so that individual modules cannot quietly opt out
of them.
"""
import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """
    Nothing is ever hard-deleted. Records are retired with an actor and a
    timestamp so history and existing links survive.
    """

    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True

    def deactivate(self, actor=None):
        from django.utils import timezone

        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivated_by = actor
        self.save(update_fields=["is_active", "deactivated_at", "deactivated_by"])


class JobLineageModel(models.Model):
    """
    One record per job, carried through. A ticket mints `job_ref`; the
    project it converts to, the field jobs worked under it and the invoice
    raised from it all carry the same value, so a job is traceable end to
    end without joining four systems by name.
    """

    job_ref = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    class Meta:
        abstract = True


class MobileOriginatedModel(models.Model):
    """
    Offline-first contract for every record a phone can create. The client
    generates `client_uuid` before the record leaves the device, so
    resubmission of the same payload — from the same device or a second one
    — is idempotent. GPS is captured at the event only, never continuously,
    and a missing fix never blocks the write.
    """

    client_uuid = models.UUIDField(unique=True)
    device_timestamp = models.DateTimeField()
    server_received_at = models.DateTimeField(auto_now_add=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_m = models.FloatField(null=True, blank=True)
    location_unavailable = models.BooleanField(default=False)

    class Meta:
        abstract = True

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None
