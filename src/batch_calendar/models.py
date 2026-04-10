"""
Batch Calendar Models

Models for tracking batch calendar requests and their individual events.
Also hosts legacy CalendarEvent and CalendarWatchChannel (from deprecated calendar_parser).
"""

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _


class CalendarEventStatus(models.TextChoices):
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    PENDING = "pending", _("Pending")
    PENDING_CONFIRMATION = "pending_confirmation", _("Pending Confirmation")
    CANCELLED = "cancelled", _("Cancelled")
    CONFLICTED = "conflicted", _("Conflicted")


class CalendarEventManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class CalendarEvent(models.Model):
    """Legacy single-event calendar model. Used for sync and historical data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="calendar_events")
    source_item = models.ForeignKey("ingestion.IngestItem", on_delete=models.CASCADE, related_name="calendar_events")
    replaces_event = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replaced_by")
    google_event_id = models.CharField(max_length=255, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=500, blank=True, default="")
    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=50, default="UTC")
    html_link = models.URLField(blank=True, default="")
    status = models.CharField(max_length=20, choices=CalendarEventStatus.choices, default=CalendarEventStatus.PENDING)
    error_message = models.TextField(blank=True, default="")
    llm_response = models.JSONField(default=dict, blank=True)
    api_response = models.JSONField(default=dict, blank=True)
    conflicting_events = models.JSONField(default=list, blank=True)
    alternative_slots = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = CalendarEventManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "calendar_parser_calendarevent"
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
                name="uniq_user_source_item_calendar_event_success",
            )
        ]

    def __str__(self):
        return f"{self.summary} ({self.status})"

    def mark_success(self, google_event_id: str, api_response: dict):
        self.status = CalendarEventStatus.SUCCESS
        self.google_event_id = google_event_id
        self.api_response = api_response
        self.html_link = api_response.get("htmlLink", "")
        self.error_message = ""
        self.save()

    def mark_failed(self, error_message: str):
        self.status = CalendarEventStatus.FAILED
        self.error_message = error_message
        self.save()

    def mark_pending_confirmation(self, conflicting_events: list, alternative_slots: list):
        self.status = CalendarEventStatus.PENDING_CONFIRMATION
        self.conflicting_events = conflicting_events
        self.alternative_slots = alternative_slots
        self.save()

    def mark_cancelled(self):
        self.status = CalendarEventStatus.CANCELLED
        self.is_deleted = True
        self.deleted_at = django_timezone.now()
        self.save()

    def mark_conflicted(self):
        self.status = CalendarEventStatus.CONFLICTED
        self.save()

    def get_audit_chain(self):
        chain = [self]
        current = self
        while current.replaces_event:
            current = current.replaces_event
            chain.append(current)
        return chain


class CalendarWatchChannel(models.Model):
    """Google Calendar push notification channel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_watch_channels")
    channel_id = models.CharField(max_length=64, unique=True)
    resource_id = models.CharField(max_length=255, blank=True, default="")
    calendar_id = models.CharField(max_length=255, default="primary")
    sync_token = models.CharField(max_length=512, blank=True, default="")
    expiration = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "calendar_parser_calendarwatchchannel"
        indexes = [
            models.Index(fields=["channel_id"]),
            models.Index(fields=["user", "is_active"]),
        ]


class BatchCalendarRequestManager(models.Manager):
    """Manager that excludes soft-deleted batch requests by default."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class BatchRequestStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CONFIRMED = "confirmed", _("Confirmed")
    CANCELLED = "cancelled", _("Cancelled")
    FAILED = "failed", _("Failed")
    PARTIAL = "partial", _("Partial")


class BatchEventStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PENDING_CONFIRMATION = "pending_confirmation", _("Pending Confirmation")
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")
    SKIPPED = "skipped", _("Skipped")


class BatchCalendarRequest(models.Model):
    """
    Tracks a batch calendar request (parse + confirm flow).

    Stores the user's input text, parsed events from the LLM, and overall status.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="batch_calendar_requests",
    )
    ingest_item = models.ForeignKey(
        "ingestion.IngestItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batch_calendar_requests",
        help_text="IngestItem that triggered this batch (when from pipeline)",
    )

    input_text = models.TextField(help_text="Original user input text")
    parsed_events_json = models.JSONField(
        default=list,
        blank=True,
        help_text="Raw LLM response: list of event dicts in Google Calendar format",
    )
    error_message = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=BatchRequestStatus.choices,
        default=BatchRequestStatus.PENDING,
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BatchCalendarRequestManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "is_deleted"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["ingest_item"]),
        ]

    def __str__(self) -> str:
        return f"BatchCalendarRequest {self.id} ({self.status})"


class BatchCalendarEvent(models.Model):
    """
    Tracks a single event within a batch request.

    Each event has its own status (success, failed, pending_confirmation, etc.)
    and stores conflict/alternative slot data when applicable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    batch_request = models.ForeignKey(
        BatchCalendarRequest,
        on_delete=models.CASCADE,
        related_name="events",
    )

    event_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Order of this event within the batch (0-based)",
    )
    event_data = models.JSONField(
        default=dict,
        help_text="Full event in Google Calendar API format",
    )

    summary = models.CharField(max_length=255, blank=True, default="")
    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=50, default="UTC")

    google_event_id = models.CharField(max_length=255, blank=True, default="")
    html_link = models.URLField(blank=True, default="")
    api_response = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=24,
        choices=BatchEventStatus.choices,
        default=BatchEventStatus.PENDING,
    )
    error_message = models.TextField(blank=True, default="")

    conflicting_events = models.JSONField(default=list, blank=True)
    alternative_slots = models.JSONField(default=list, blank=True)
    alternative_slots_by_day = models.JSONField(
        default=list,
        blank=True,
        help_text="Slots grouped by day for day-navigation UI",
    )

    created_at = models.DateTimeField(default=django_timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["batch_request", "event_index"]),
        ]
        ordering = ["batch_request", "event_index"]

    def __str__(self) -> str:
        return f"{self.summary} ({self.status})"

    def mark_success(self, google_event_id: str, api_response: dict) -> None:
        self.status = BatchEventStatus.SUCCESS
        self.google_event_id = google_event_id
        self.api_response = api_response
        self.html_link = api_response.get("htmlLink", "")
        self.error_message = ""
        self.save()

    def mark_failed(self, error_message: str) -> None:
        self.status = BatchEventStatus.FAILED
        self.error_message = error_message
        self.save()

    def mark_pending_confirmation(
        self,
        conflicting_events: list,
        alternative_slots: list,
        alternative_slots_by_day=None,
    ) -> None:
        self.status = BatchEventStatus.PENDING_CONFIRMATION
        self.conflicting_events = conflicting_events
        self.alternative_slots = alternative_slots
        self.alternative_slots_by_day = alternative_slots_by_day or []
        self.save()
