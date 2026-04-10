"""
Retrieval Models — v14

Unified ItemRetrievalProjection (replaces EntryIndex: embeddings + taxonomy +
entities in one table), plus ChatSession and message models for the query chatbot.
"""

import uuid

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


# ---------------------------------------------------------------------------
# Unified retrieval projection
# ---------------------------------------------------------------------------

class ItemRetrievalProjection(models.Model):
    """
    Single retrieval table: one row per IngestItem.

    Combines the old EntryIndex (embeddings, keywords, summary) with the new
    taxonomy classification data (primary/secondary keys, entities, governance).
    Refreshed after every successful classification run.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    ingest_item = models.OneToOneField(
        "ingestion.IngestItem",
        on_delete=models.CASCADE,
        related_name="retrieval_projection",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="retrieval_projections",
    )
    latest_classification_run = models.ForeignKey(
        "classification.ItemClassificationRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # --- Taxonomy keys (from classification) ---
    primary_subject_key = models.TextField(blank=True, default="")
    secondary_subject_keys = models.JSONField(default=list, blank=True)

    primary_intent_key = models.TextField(blank=True, default="")
    secondary_intent_keys = models.JSONField(default=list, blank=True)

    primary_context_key = models.TextField(blank=True, default="")
    secondary_context_keys = models.JSONField(default=list, blank=True)

    time_keys = models.JSONField(default=list, blank=True)
    governance_key = models.TextField(blank=True, default="")

    # --- Entity data ---
    entity_ids = models.JSONField(default=list, blank=True)
    # JSON strings (plaintext: list[str])
    entity_names_normalized = models.TextField(blank=True, default="")
    entity_roles = models.TextField(blank=True, default="")

    # --- Temporal / source metadata ---
    occurred_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(null=True, blank=True)
    detected_language = models.CharField(max_length=10, blank=True, default="")

    # --- Searchable text ---
    content_text_searchable = models.TextField(blank=True, default="")
    summary_text_searchable = models.TextField(blank=True, default="")
    embedding_ready_text = models.TextField(blank=True, default="")

    # --- From old EntryIndex: vector search / keyword fields (plaintext) ---
    summary = models.TextField(blank=True, default="")
    # JSON string (plaintext: list[str])
    keywords = models.TextField(blank=True, default="")
    list_items_flat = models.TextField(blank=True, default="")
    financial_items_flat = models.TextField(blank=True, default="")
    todo_items_flat = models.TextField(blank=True, default="")
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    token_index = models.JSONField(default=list, blank=True)
    has_attachment = models.BooleanField(default=False)
    attachment_types = models.JSONField(default=list, blank=True)

    # --- Classification metadata ---
    overall_confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    is_actionable = models.BooleanField(default=False)
    is_sensitive = models.BooleanField(default=False)
    last_classified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "item retrieval projections"
        indexes = [
            models.Index(fields=["user", "primary_subject_key"], name="idx_proj_subject"),
            models.Index(fields=["user", "primary_intent_key"], name="idx_proj_intent"),
            models.Index(fields=["user", "primary_context_key"], name="idx_proj_context"),
            models.Index(fields=["user", "governance_key"], name="idx_proj_governance"),
            models.Index(fields=["user", "occurred_at"], name="idx_proj_user_date"),
        ]

    def __str__(self) -> str:
        return f"Projection({self.ingest_item_id})"


# ---------------------------------------------------------------------------
# Chat models
# ---------------------------------------------------------------------------

class ChatSession(models.Model):
    """A multi-turn conversation session for the diary chatbot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    title = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"Session {self.pk}"


class UserChatMessage(models.Model):
    """A user message in a chat session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="user_messages",
    )
    content = models.TextField()
    sequence_index = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence_index"]

    def __str__(self) -> str:
        return f"user: {self.content[:50]}"


class AssistantChatMessage(models.Model):
    """An assistant message in a chat session."""

    class Status(models.TextChoices):
        READ = "read", "Read"
        UNREAD = "unread", "Unread"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="assistant_messages",
    )
    content = models.TextField()
    # JSON string (plaintext: list[dict])
    source_entries = models.TextField(blank=True, default="")
    sequence_index = models.IntegerField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.READ,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence_index"]

    def __str__(self) -> str:
        return f"assistant: {self.content[:50]}"
