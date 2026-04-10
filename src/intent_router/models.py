import uuid
from django.db import models


class SoftDeleteManager(models.Manager):
    """Manager that excludes soft-deleted records by default."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class ItemTriageResult(models.Model):
    """Stores the triage routing decision for an IngestItem."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.OneToOneField(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="triage_result",
    )
    primary_route = models.CharField(max_length=20)
    confidence = models.FloatField()
    contains_time_reference = models.BooleanField(default=False)
    contains_multiple_items = models.BooleanField(default=False)
    raw_output = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["primary_route"]),
            models.Index(fields=["is_deleted"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} → {self.primary_route} ({self.confidence:.2f})"
