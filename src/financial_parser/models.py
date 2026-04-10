"""
Financial Parser Models

Models for storing extracted financial entries (expenses and income) linked to diary entries.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _


class FinancialRecordStatus(models.TextChoices):
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    PENDING = "pending", _("Pending")


class FinancialRecordManager(models.Manager):
    """Manager that excludes soft-deleted financial records by default."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class FinancialRecord(models.Model):
    """
    Stores one extracted financial record per source diary entry.

    Each FinancialRecord represents parsed expenses/income from an IngestItem
    and contains zero or more FinancialItem children.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="financial_records",
    )

    source_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="financial_records",
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="manual_financial_records",
    )

    record_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Inferred record name (e.g. 'Despesas de hoje')",
    )
    record_context = models.TextField(
        blank=True,
        default="",
        help_text="Optional context (e.g. 'viagem a Paris')",
    )

    llm_response = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw LLM extraction response",
    )

    status = models.CharField(
        max_length=20,
        choices=FinancialRecordStatus.choices,
        default=FinancialRecordStatus.PENDING,
    )
    error_message = models.TextField(blank=True, default="")

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FinancialRecordManager()
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
                name="uniq_user_source_item_financial_record_notnull",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.record_name or 'Unnamed'} ({self.status})"

    def mark_success(self):
        self.status = FinancialRecordStatus.SUCCESS
        self.error_message = ""
        self.save(update_fields=["status", "error_message", "updated_at"])

    def mark_failed(self, error_message: str):
        self.status = FinancialRecordStatus.FAILED
        self.error_message = error_message
        self.save(update_fields=["status", "error_message", "updated_at"])


class FinancialItemManager(models.Manager):
    """Manager that excludes soft-deleted financial items by default."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class FinancialItem(models.Model):
    """
    A single expense or income entry within a FinancialRecord.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    financial_record = models.ForeignKey(
        FinancialRecord,
        on_delete=models.CASCADE,
        related_name="items",
    )

    item_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Order within the record (0-based)",
    )

    type = models.CharField(
        max_length=20,
        choices=[("expense", "Expense"), ("income", "Income")],
        default="expense",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Amount (positive)",
    )
    currency = models.CharField(max_length=10, default="EUR")
    category = models.CharField(max_length=100, blank=True, default="")
    merchant = models.CharField(max_length=255, blank=True, default="")
    transaction_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    payment_method = models.CharField(max_length=50, blank=True, default="")

    item_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full item dict from LLM (for extensibility)",
    )

    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now, editable=False)

    objects = FinancialItemManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["financial_record", "item_index"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["financial_record", "item_index"],
                name="uniq_financial_record_item_index",
            ),
        ]
        ordering = ["financial_record", "item_index"]

    def __str__(self) -> str:
        return f"#{self.item_index}: {self.type} {self.amount} {self.currency}"


class HypermarketLineItem(models.Model):
    """
    One row per line item from a hypermarket/grocery invoice.
    Linked to FinancialRecord; stores product-level detail for retrieval indexing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    financial_record = models.ForeignKey(
        FinancialRecord,
        on_delete=models.CASCADE,
        related_name="hypermarket_line_items",
    )

    line_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Order within the invoice (0-based)",
    )

    description = models.TextField(blank=True, default="", help_text="Product name/description")
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    gmail_message_id = models.CharField(max_length=255, null=True, blank=True)
    gmail_filename = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=["financial_record", "line_index"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["financial_record", "line_index"],
                name="uniq_hypermarket_financial_record_line_index",
            ),
        ]
        ordering = ["financial_record", "line_index"]

    def __str__(self) -> str:
        return f"#{self.line_index}: {self.description[:50]} {self.total}"
