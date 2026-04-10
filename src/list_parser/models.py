"""
List Parser Models

Models for storing extracted item lists linked to diary entries.
"""

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _


class Unit(models.Model):
    """
    Global reference units for list item quantities.

    Each row defines a canonical unit name (e.g. 'kg') and a JSON list of
    aliases the LLM should recognise (e.g. ['quilo', 'quilos', 'kilogram']).
    The prompt is built dynamically from active rows.
    """

    name = models.CharField(
        max_length=30,
        unique=True,
        help_text="Canonical name used in LLM output and stored in ListItem.unit",
    )
    display_name = models.CharField(
        max_length=60,
        blank=True,
        default="",
        help_text="Human-friendly label (optional)",
    )
    aliases = models.JSONField(
        default=list,
        blank=True,
        help_text='List of alternative spellings, e.g. ["quilo","quilos","kilogram"]',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.display_name or self.name


class ListRecordStatus(models.TextChoices):
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    PENDING = "pending", _("Pending")


class ListRecordManager(models.Manager):
    """Manager that excludes soft-deleted list records by default."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class ListRecord(models.Model):
    """
    Stores one extracted list per source diary entry.

    Each ListRecord represents a named list parsed from an IngestItem
    and contains zero or more ListItem children.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="list_records",
    )

    source_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="list_records",
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="manual_list_records",
    )

    list_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Inferred list name (e.g. 'compras' from 'lista de compras')",
    )
    list_context = models.TextField(
        blank=True,
        default="",
        help_text="Optional context/occasion for the list (e.g. John's birthday party)",
    )

    llm_response = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw LLM extraction response",
    )

    status = models.CharField(
        max_length=20,
        choices=ListRecordStatus.choices,
        default=ListRecordStatus.PENDING,
    )
    error_message = models.TextField(blank=True, default="")

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ListRecordManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["source_item"]),
            models.Index(fields=["user", "is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source_item"],
                condition=models.Q(status="success") & models.Q(source_item__isnull=False),
                name="uniq_user_source_item_list_record_notnull",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.list_name or 'Unnamed list'} ({self.status})"

    def mark_success(self):
        self.status = ListRecordStatus.SUCCESS
        self.error_message = ""
        self.save(update_fields=["status", "error_message", "updated_at"])

    def mark_failed(self, error_message: str):
        self.status = ListRecordStatus.FAILED
        self.error_message = error_message
        self.save(update_fields=["status", "error_message", "updated_at"])


class ListItemManager(models.Manager):
    """Manager that excludes soft-deleted list items by default."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class ListItem(models.Model):
    """
    A single item within a ListRecord.

    Each item stores its text, optional description, optional due date,
    and the full LLM dict for extensibility.
    Top-level items have parent=None; sublist items have parent pointing to
    the ListItem representing the sublist subject (e.g. Paul, John).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    list_record = models.ForeignKey(
        ListRecord,
        on_delete=models.CASCADE,
        related_name="items",
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        help_text="Parent item for sublist entries (null for top-level)",
    )

    item_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Order of this item within the list or within parent (0-based)",
    )

    text = models.TextField(help_text="Primary item content")
    description = models.TextField(blank=True, default="", help_text="Optional extra detail")
    due_date = models.DateField(null=True, blank=True, help_text="Optional due date")
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional quantity (e.g. 2, 1.5) when mentioned in the source text",
    )
    unit = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="Optional unit (e.g. kg, litre, unit) when mentioned in the source text",
    )

    item_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full item dict from LLM (for extensibility)",
    )

    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now, editable=False)

    objects = ListItemManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["list_record", "parent", "item_index"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["list_record", "parent", "item_index"],
                name="uniq_list_record_parent_item_index",
            ),
        ]
        ordering = ["list_record", "item_index"]

    def __str__(self) -> str:
        return f"#{self.item_index}: {self.text[:50]}"
