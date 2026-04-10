"""
GIGO Monitor Models

Tracks input quality metrics (word count, rank) and nudge adherence.
"""

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class GigoRank(models.TextChoices):
    LOW = "low", _("Low")
    MEDIUM = "medium", _("Medium")
    HIGH = "high", _("High")


class GigoItemType(models.TextChoices):
    AUDIO = "audio", _("Audio")
    TEXT = "text", _("Text")


class GigoEntry(models.Model):
    """One row per input (audio or text) with quality metrics."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gigo_entries",
    )
    ingest_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gigo_entries",
    )
    item_type = models.CharField(
        max_length=20,
        choices=GigoItemType.choices,
    )
    word_count = models.PositiveIntegerField(default=0)
    rank = models.CharField(
        max_length=20,
        choices=GigoRank.choices,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "gigo_gigoentry"
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} {self.item_type} {self.rank} @ {self.created_at}"


class GigoUserState(models.Model):
    """Per-user counter and alert flag for consecutive low ranks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gigo_state",
    )
    consecutive_low_count = models.PositiveIntegerField(default=0)
    alert_pending = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "gigo_gigouserstate"

    def __str__(self):
        return f"{self.user_id} low_count={self.consecutive_low_count} alert={self.alert_pending}"


class GigoNudgeLog(models.Model):
    """One row per nudge shown to a user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gigo_nudge_logs",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "gigo_gigonudgelog"
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} @ {self.created_at}"
