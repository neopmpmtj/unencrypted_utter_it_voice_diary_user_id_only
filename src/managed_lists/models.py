"""
Managed Lists Models

Abstract base models for the Record/Item pattern shared across list types,
plus concrete TodoRecord, TodoItem, and ManagedListProjection models.
"""

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# Shared soft-delete manager
# ---------------------------------------------------------------------------

class SoftDeleteManager(models.Manager):
    """Manager that excludes soft-deleted records by default."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


# ---------------------------------------------------------------------------
# Abstract bases
# ---------------------------------------------------------------------------

class ManagedRecordStatus(models.TextChoices):
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    PENDING = "pending", _("Pending")


class AbstractManagedRecord(models.Model):
    """
    Shared base for ListRecord, FinancialRecord, TodoRecord, etc.

    Provides: UUID PK, user, source_item link, LLM response storage,
    status lifecycle, soft-delete, and timestamps.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.PROTECT,
        related_name="%(class)s_records",
    )
    source_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="%(class)s_records",
    )

    record_name = models.CharField(max_length=255, blank=True, default="")
    record_context = models.TextField(blank=True, default="")

    llm_response = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw LLM extraction response",
    )

    status = models.CharField(
        max_length=20,
        choices=ManagedRecordStatus.choices,
        default=ManagedRecordStatus.PENDING,
    )
    error_message = models.TextField(blank=True, default="")

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def mark_success(self):
        self.status = ManagedRecordStatus.SUCCESS
        self.error_message = ""
        self.save(update_fields=["status", "error_message", "updated_at"])

    def mark_failed(self, error_message: str):
        self.status = ManagedRecordStatus.FAILED
        self.error_message = error_message
        self.save(update_fields=["status", "error_message", "updated_at"])


class AbstractManagedItem(models.Model):
    """
    Shared base for ListItem, FinancialItem, TodoItem, etc.

    Provides: UUID PK, ordering index, text/description, item_data JSON,
    entity association fields, and timestamp.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    item_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Order of this item within the record (0-based)",
    )

    text = models.TextField(blank=True, default="", help_text="Primary item content")
    description = models.TextField(blank=True, default="", help_text="Optional extra detail")

    item_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full item dict from LLM (for extensibility)",
    )

    entity = models.ForeignKey(
        "classification.EntityCatalog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_items",
        help_text="Primary associated entity (person, vendor, org, etc.)",
    )
    entity_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Denormalized entity display name for search/display",
    )
    entity_type = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="Denormalized entity type (PERSON, VENDOR, ORGANIZATION, etc.)",
    )

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Todo-specific choices
# ---------------------------------------------------------------------------

class TodoPriority(models.IntegerChoices):
    LOWEST = 1, _("Lowest")
    LOW = 2, _("Low")
    MEDIUM = 3, _("Medium")
    HIGH = 4, _("High")
    URGENT = 5, _("Urgent")


class TodoCompletionStatus(models.TextChoices):
    OPEN = "open", _("Open")
    IN_PROGRESS = "in_progress", _("In Progress")
    ON_HOLD = "on_hold", _("On Hold")
    DONE = "done", _("Done")
    CANCELLED = "cancelled", _("Cancelled")


# ---------------------------------------------------------------------------
# Concrete Todo models
# ---------------------------------------------------------------------------

class TodoRecord(AbstractManagedRecord):
    """
    Stores one extracted to-do set per source diary entry.

    Each TodoRecord is parsed from an IngestItem and contains
    zero or more TodoItem children. Manually-created records have
    source_item=None and created_by set to the creating user.
    """

    # Override to allow null — manually-created records have no IngestItem source
    source_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="todorecord_records",
    )
    created_by = models.ForeignKey(
        "accounts.CustomUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="todo_records",
    )

    objects = SoftDeleteManager()
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
                condition=models.Q(status="success"),
                name="uniq_user_source_item_todo_record_success",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.record_name or 'Unnamed todo'} ({self.status})"


class TodoItem(AbstractManagedItem):
    """
    A single to-do item within a TodoRecord.

    Supports subtasks via parent FK, priority levels, completion tracking,
    due dates, topic/subtopic categorization, and recurrence.
    """

    todo_record = models.ForeignKey(
        TodoRecord,
        on_delete=models.CASCADE,
        related_name="items",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        help_text="Parent item for subtask entries (null for top-level)",
    )

    priority = models.IntegerField(
        choices=TodoPriority.choices,
        default=TodoPriority.MEDIUM,
    )
    completion_status = models.CharField(
        max_length=20,
        choices=TodoCompletionStatus.choices,
        default=TodoCompletionStatus.OPEN,
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    due_date = models.DateField(null=True, blank=True, help_text="Optional due date")
    due_time = models.TimeField(null=True, blank=True, help_text="Optional due time")

    topic = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Topic category (mapped from taxonomy subject key)",
    )
    subtopic = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Finer granularity within topic",
    )

    recurrence_rule = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Recurrence pattern: daily, weekly, monthly, or empty",
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["todo_record", "parent", "item_index"]),
            models.Index(fields=["completion_status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["topic", "subtopic"]),
            models.Index(fields=["entity_type"]),
            models.Index(fields=["is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["todo_record", "parent", "item_index"],
                condition=Q(is_deleted=False),
                name="uniq_todo_record_parent_item_index",
            ),
        ]
        ordering = ["todo_record", "item_index"]

    def __str__(self) -> str:
        status_icon = {"open": "☐", "in_progress": "◐", "on_hold": "⏸", "done": "☑", "cancelled": "☒"}.get(
            self.completion_status, "☐"
        )
        return f"{status_icon} #{self.item_index}: {self.text[:50]}"


# ---------------------------------------------------------------------------
# ManagedListProjection — cross-list denormalized index
# ---------------------------------------------------------------------------

class ManagedListType(models.TextChoices):
    SHOPPING = "shopping", _("Shopping")
    TODO = "todo", _("To-Do")
    FINANCIAL = "financial", _("Financial")
    CONTACT = "contact", _("Contact")
    GENERAL = "general", _("General")


class ManagedListProjection(models.Model):
    """
    Denormalized table — one row per item across ALL managed list types.

    Key for unified search and future Phase 2 LLM SQL interface.
    Populated explicitly in each parser's mark_success() flow.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="managed_list_projections",
    )
    source_ingest_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="managed_list_projections",
    )

    list_type = models.CharField(max_length=20, choices=ManagedListType.choices)
    record_id = models.UUIDField(help_text="PK of the concrete record (TodoRecord, ListRecord, etc.)")
    item_id = models.UUIDField(help_text="PK of the concrete item (TodoItem, ListItem, etc.)")

    # Standardized fields
    title = models.TextField(blank=True, default="", help_text="Primary text/name")
    description = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Maps to: list_name, financial category, topic",
    )
    topic = models.CharField(max_length=255, blank=True, default="")
    subtopic = models.CharField(max_length=255, blank=True, default="")
    item_status = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="open, done, expense, income, etc.",
    )
    priority = models.IntegerField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default="")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=30, blank=True, default="")

    # Entity fields — denormalized from the concrete item's entity association
    entity_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="e.g. Zara, Dr. Silva, João",
    )
    entity_type = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="PERSON, VENDOR, ORGANIZATION, etc.",
    )
    entity_catalog_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="FK value for drill-down if needed",
    )

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "list_type"]),
            models.Index(fields=["user", "topic", "subtopic"]),
            models.Index(fields=["user", "item_status"]),
            models.Index(fields=["user", "due_date"]),
            models.Index(fields=["user", "entity_type"]),
            models.Index(fields=["user", "entity_name"]),
            models.Index(fields=["record_id"]),
            models.Index(fields=["item_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.list_type}:{self.title[:50]}"
