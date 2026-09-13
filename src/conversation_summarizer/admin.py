"""
Django admin for the conversation summarizer.

Everything the agent does is editable here — prompts, examples and the runtime
knobs — so its behaviour can be tuned without a code change or a deploy.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    ConversationSummary,
    SummaryAgentConfig,
    SummaryExample,
    SummaryPromptTemplate,
    SummaryRun,
)


class SummaryExampleInline(admin.StackedInline):
    model = SummaryExample
    extra = 1
    fields = ("sort_order", "label", "is_active", "input_excerpt", "expected_output")


@admin.register(SummaryAgentConfig)
class SummaryAgentConfigAdmin(admin.ModelAdmin):
    list_display = (
        "scope", "enabled", "model", "temperature",
        "quiet_seconds", "cap_seconds", "updated_at",
    )
    list_filter = ("enabled", "is_global", "model", "language_policy")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("is_global", "user", "enabled", "updated_by")}),
        (_("LLM"), {"fields": ("model", "temperature", "max_output_tokens", "language_policy")}),
        (_("Session detection"), {"fields": ("cap_seconds", "cap_tolerance", "quiet_seconds")}),
        (_("Input guards"), {"fields": ("min_chars", "chunk_chars", "map_reduce_enabled")}),
        (_("Output"), {"fields": ("default_template",)}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description=_("Scope"))
    def scope(self, obj):
        return _("global") if obj.is_global else f"user: {obj.user}"


@admin.register(SummaryPromptTemplate)
class SummaryPromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "conversation_type", "version", "is_active", "is_default", "updated_at")
    list_filter = ("conversation_type", "is_active", "is_default")
    search_fields = ("name", "system_prompt", "user_prompt")
    readonly_fields = ("version", "created_at", "updated_at")
    inlines = [SummaryExampleInline]
    fieldsets = (
        (None, {"fields": ("name", "conversation_type", "is_active", "is_default", "version")}),
        (_("Prompt"), {"fields": ("system_prompt", "user_prompt")}),
        (_("Audit"), {"fields": ("updated_by", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(SummaryExample)
class SummaryExampleAdmin(admin.ModelAdmin):
    list_display = ("template", "sort_order", "label", "is_active", "updated_at")
    list_filter = ("is_active", "template")
    search_fields = ("label", "input_excerpt", "expected_output")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ConversationSummary)
class ConversationSummaryAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "user", "title", "conversation_type",
        "clip_count", "revision", "status", "model_used",
    )
    list_filter = ("status", "conversation_type", "model_used")
    search_fields = ("title", "summary_text", "recording_group_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id", "user", "recording_group_id", "title", "conversation_type",
        "summary_text", "structured_data", "template", "template_version",
        "model_used", "clip_count", "total_duration_seconds", "started_at",
        "ended_at", "revision", "status", "attempts", "last_error",
        "tokens_in", "tokens_out", "created_at", "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("id", "user", "recording_group_id", "status", "revision")}),
        (_("Result"), {"fields": ("title", "conversation_type", "summary_text", "structured_data")}),
        (_("Session"), {"fields": ("clip_count", "total_duration_seconds", "started_at", "ended_at")}),
        (_("Provenance"), {"fields": ("template", "template_version", "model_used")}),
        (_("Diagnostics"), {"fields": ("attempts", "last_error", "tokens_in", "tokens_out")}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        # Summaries are produced by the pipeline, never typed by hand.
        return False


@admin.register(SummaryRun)
class SummaryRunAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "user", "kind", "model",
        "input_chars", "tokens_in", "tokens_out", "latency_ms", "ok",
    )
    list_filter = ("ok", "kind", "model")
    search_fields = ("recording_group_id", "error")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id", "summary", "user", "recording_group_id", "template",
        "template_version", "model", "kind", "input_chars", "tokens_in",
        "tokens_out", "latency_ms", "ok", "error", "created_at",
    )

    def has_add_permission(self, request):
        return False
