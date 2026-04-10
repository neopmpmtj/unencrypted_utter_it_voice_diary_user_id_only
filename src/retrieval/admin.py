from django.contrib import admin

from .models import AssistantChatMessage, ChatSession, ItemRetrievalProjection, UserChatMessage


@admin.register(ItemRetrievalProjection)
class ItemRetrievalProjectionAdmin(admin.ModelAdmin):
    list_display = (
        "ingest_item", "user", "primary_subject_key", "primary_intent_key",
        "governance_key", "overall_confidence", "is_sensitive", "updated_at",
    )
    list_filter = ("is_sensitive", "is_actionable", "has_attachment")
    search_fields = ("summary", "primary_subject_key", "primary_intent_key")
    raw_id_fields = ("ingest_item", "user", "latest_classification_run")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "created_at", "updated_at")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserChatMessage)
class UserChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "sequence_index", "created_at")
    raw_id_fields = ("session",)
    readonly_fields = ("created_at",)


@admin.register(AssistantChatMessage)
class AssistantChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "sequence_index", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("session",)
    readonly_fields = ("created_at", "metadata")
