"""
Django admin for the conversation summarizer.

Everything the agent does is editable here — prompts, examples and the runtime
knobs — so its behaviour can be tuned without a code change or a deploy.
"""

from django.contrib import admin, messages
from django.shortcuts import render
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
    actions = ["preview_on_last_session"]
    fieldsets = (
        (None, {"fields": ("name", "conversation_type", "is_active", "is_default", "version")}),
        (_("Prompt"), {"fields": ("system_prompt", "user_prompt")}),
        (_("Audit"), {"fields": ("updated_by", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    # ------------------------------------------------------------------ preview

    @admin.action(description=_("Preview on my last session (costs one LLM call, saves nothing)"))
    def preview_on_last_session(self, request, queryset):
        """
        Run the selected prompt against the admin's most recent conversation and
        show exactly what the model would receive and reply.

        Deliberately writes NO ConversationSummary — it exists so prompts and
        few-shot examples can be iterated on safely. The call is recorded in
        SummaryRun (kind='preview') so its cost is still visible.
        """
        import time

        from .models import SummaryAgentConfig, SummaryRun
        from .services import (
            DEFAULT_MAX_OUTPUT_TOKENS,
            DEFAULT_MODEL,
            DEFAULT_TEMPERATURE,
            build_messages,
            build_session_context,
            build_transcript,
            call_llm,
            find_sessions,
            parse_summary,
        )

        if queryset.count() != 1:
            self.message_user(
                request, _("Select exactly one template to preview."), level=messages.WARNING
            )
            return None

        template = queryset.first()
        sessions = find_sessions(user=request.user)
        session = sessions[0] if sessions else None

        if session is None:
            self.message_user(
                request,
                _("You have no recorded conversations to preview against yet."),
                level=messages.WARNING,
            )
            return None

        cfg = SummaryAgentConfig.get_for_user(request.user)
        model = getattr(cfg, "model", None) or DEFAULT_MODEL
        temperature = getattr(cfg, "temperature", None)
        temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
        max_output_tokens = getattr(cfg, "max_output_tokens", None) or DEFAULT_MAX_OUTPUT_TOKENS

        transcript = build_transcript(session.clips)
        context = build_session_context(session, request.user, transcript)
        messages_list = build_messages(
            template, context, template.examples.filter(is_active=True)
        )
        input_chars = sum(len(m.get("content") or "") for m in messages_list)

        raw, usage, parsed, error = "", {}, None, ""
        started = time.monotonic()
        try:
            raw, usage = call_llm(
                messages_list,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            parsed = parse_summary(raw)
        except Exception as exc:  # noqa: BLE001 - show the error in the page
            error = str(exc)
        latency_ms = int((time.monotonic() - started) * 1000)

        SummaryRun.objects.create(
            user=request.user,
            recording_group_id=session.recording_group_id,
            template=template,
            template_version=template.version,
            model=model,
            kind="preview",
            input_chars=input_chars,
            tokens_in=(usage or {}).get("input_tokens", 0) or 0,
            tokens_out=(usage or {}).get("output_tokens", 0) or 0,
            latency_ms=latency_ms,
            ok=not error,
            error=error[:2000],
        )

        import json as _json

        return render(
            request,
            "admin/conversation_summarizer/preview.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Prompt preview on your last session"),
                "template": template,
                "session": session,
                "model": model,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "input_chars": input_chars,
                "messages": messages_list,
                "raw": raw,
                "parsed": parsed,
                "parsed_pretty": _json.dumps(parsed.get("structured"), indent=2, ensure_ascii=False)
                if parsed and parsed.get("structured")
                else "",
                "usage": usage or {},
                "latency_ms": latency_ms,
                "error": error,
                "opts": self.model._meta,
            },
        )


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
