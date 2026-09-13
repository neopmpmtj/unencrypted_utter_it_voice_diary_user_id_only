"""
Conversation Summarizer — data model.

This app owns everything about summarizing a *long* voice-diary conversation:
the prompt the LLM is given (editable, versioned), the few-shot examples, the
runtime knobs, the resulting summary and an audit trail of every LLM call.

WHY THIS APP EXISTS
-------------------
The recorder caps a single recording at 240s. A longer talk is therefore stored
as several consecutive clips that share one ``recording_group_id``. This app
groups those clips back into ONE conversation, summarizes it once, and stores
the result — automatically, from the ingestion pipeline (see
``tasks.summarizer_on_entry_task`` in a later phase).

Design notes
------------
* Deterministic code decides *grouping*; the LLM only compresses text.
* All prompts/examples are **data**, editable in Django admin — no deploy needed.
* Every summary records which template *version* produced it (traceability).
* Per-user on/off lives in ``accounts.UserPreferences.enable_conversation_summary``;
  this app holds the per-user *tuning* override plus the global default.
"""

import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ConversationType(models.TextChoices):
    """What kind of talk this was. Detected by the LLM, stored as data."""

    MEETING = "meeting", _("Meeting")
    BRAINSTORM = "brainstorm", _("Brainstorm")
    DECISION = "decision", _("Decision")
    JOURNAL = "journal", _("Journal")
    OTHER = "other", _("Other")


class SummaryStatus(models.TextChoices):
    """Lifecycle of one conversation summary."""

    PENDING = "pending", _("Pending")
    READY = "ready", _("Ready")
    PARTIAL = "partial", _("Partial")  # produced, but some input was unusable
    FAILED = "failed", _("Failed")


class LanguagePolicy(models.TextChoices):
    """Which language the summary must be written in."""

    SOURCE = "source", _("Same as the transcript")
    STORED = "stored", _("User's stored language")


class SummaryAgentConfig(models.Model):
    """
    Runtime configuration for the summarizer agent.

    Two levels, same model:

    * the **global default** row (``is_global=True``, ``user`` NULL) — exactly one;
    * optional **per-user overrides** (``user`` set).

    ``get_for_user()`` resolves override → global → hard-coded defaults, so a
    new user needs no configuration row at all.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    is_global = models.BooleanField(
        default=False,
        help_text=_("True for the single cluster-wide default row (user must be empty)."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="summary_agent_configs",
        help_text=_("Leave empty for the global default."),
    )

    enabled = models.BooleanField(
        default=True,
        help_text=_(
            "Master switch for this scope. A user is only summarized when their "
            "own preference (UserPreferences.enable_conversation_summary) AND this "
            "flag are both true."
        ),
    )

    # --- LLM knobs ---------------------------------------------------------
    model = models.CharField(max_length=100, default="gpt-4.1-mini")
    temperature = models.FloatField(default=0.2)
    max_output_tokens = models.PositiveIntegerField(default=700)

    # --- Session detection -------------------------------------------------
    cap_seconds = models.PositiveIntegerField(
        default=240,
        help_text=_("Recording cap. A clip at/over this length means 'long conversation'."),
    )
    cap_tolerance = models.FloatField(
        default=0.5,
        help_text=_(
            "Seconds of slack when testing the cap. Real data contains clips slightly "
            "over 240s — never test for exact equality."
        ),
    )
    quiet_seconds = models.PositiveIntegerField(
        default=600,
        help_text=_(
            "A session whose last clip ended longer ago than this is considered "
            "finished even if no short tail clip ever arrived."
        ),
    )

    # --- Input guards ------------------------------------------------------
    min_chars = models.PositiveIntegerField(
        default=200,
        help_text=_("Skip sessions whose combined transcript is shorter than this."),
    )
    chunk_chars = models.PositiveIntegerField(
        default=60000,
        help_text=_("Transcripts above this size are summarized chunk-by-chunk (map-reduce)."),
    )
    map_reduce_enabled = models.BooleanField(default=True)

    # --- Output ------------------------------------------------------------
    language_policy = models.CharField(
        max_length=20, choices=LanguagePolicy.choices, default=LanguagePolicy.SOURCE
    )
    default_template = models.ForeignKey(
        "SummaryPromptTemplate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="configs_as_default",
        help_text=_("Template used when the conversation type has no specific template."),
    )

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = _("Summary agent configuration")
        verbose_name_plural = _("Summary agent configurations")
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                name="uniq_summary_agent_config_per_user",
                condition=models.Q(user__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["is_global"],
                name="uniq_global_summary_agent_config",
                condition=models.Q(is_global=True),
            ),
            models.CheckConstraint(
                check=(
                    models.Q(is_global=True, user__isnull=True)
                    | models.Q(is_global=False, user__isnull=False)
                ),
                name="summary_agent_config_scope_exclusive",
            ),
        ]

    def __str__(self):
        scope = "global" if self.is_global else f"user={self.user_id}"
        return f"SummaryAgentConfig({scope}, model={self.model}, enabled={self.enabled})"

    @classmethod
    def get_for_user(cls, user):
        """
        Resolve the config that applies to ``user``: personal override, else the
        global row, else ``None`` (callers fall back to field defaults).
        """
        override = cls.objects.filter(user=user).first() if user and user.pk else None
        if override:
            return override
        return cls.objects.filter(is_global=True).first()

    @classmethod
    def get_global(cls):
        return cls.objects.filter(is_global=True).first()


class SummaryPromptTemplate(models.Model):
    """
    The editable prompt — Pedro's "well defined agent", stored as data.

    ``system_prompt`` and ``user_prompt`` are free text (admin-editable). The
    ``user_prompt`` supports placeholders, validated on save:

        {{transcript}} {{clip_count}} {{duration_minutes}} {{started_at}}
        {{language}} {{user_name}}

    ``version`` increments on save so every produced summary can record exactly
    which prompt text generated it.
    """

    ALLOWED_PLACEHOLDERS = (
        "transcript",
        "clip_count",
        "duration_minutes",
        "started_at",
        "ended_at",
        "language",
        "user_name",
    )

    PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    conversation_type = models.CharField(
        max_length=20,
        choices=ConversationType.choices,
        default=ConversationType.OTHER,
        help_text=_(
            "Which kind of conversation this template targets. 'meeting' is the "
            "most common case in practice."
        ),
    )

    system_prompt = models.TextField(
        help_text=_("Framing + rules given to the model, including the response schema.")
    )
    user_prompt = models.TextField(
        help_text=_(
            "The message carrying the transcript. Placeholders: "
            "{{transcript}}, {{clip_count}}, {{duration_minutes}}, {{started_at}}, "
            "{{ended_at}}, {{language}}, {{user_name}}"
        ),
    )

    version = models.PositiveIntegerField(default=1, editable=False)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text=_("Used when no template matches the detected conversation type."),
    )

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = _("Summary prompt template")
        verbose_name_plural = _("Summary prompt templates")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (v{self.version}, {self.conversation_type})"

    def clean(self):
        """
        Refuse unknown ``{{placeholders}}`` so a typo in admin can't silently
        feed the model an unsubstituted template.
        """
        super().clean()
        text = f"{self.system_prompt or ''}\n{self.user_prompt or ''}"
        unknown = sorted(set(self.PLACEHOLDER_RE.findall(text)) - set(self.ALLOWED_PLACEHOLDERS))
        if unknown:
            raise ValidationError(
                {
                    "user_prompt": _("Unknown placeholder(s): %(names)s. Allowed: %(allowed)s")
                    % {
                        "names": ", ".join(unknown),
                        "allowed": ", ".join(self.ALLOWED_PLACEHOLDERS),
                    }
                }
            )

    def save(self, *args, **kwargs):
        """
        Bump ``version`` whenever the prompt text changes, so every produced
        summary can be traced to the exact wording that generated it. Edits to
        other fields (name, is_active, …) do not bump it.
        """
        if not self._state.adding:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("system_prompt", "user_prompt")
                .first()
            )
            if previous and (
                previous["system_prompt"] != self.system_prompt
                or previous["user_prompt"] != self.user_prompt
            ):
                self.version = (self.version or 1) + 1
        super().save(*args, **kwargs)

    @classmethod
    def get_default(cls):
        """Active default template, falling back to any active one."""
        return (
            cls.objects.filter(is_active=True, is_default=True).first()
            or cls.objects.filter(is_active=True).order_by("name").first()
        )

    @classmethod
    def get_for_type(cls, conversation_type):
        """Template for a specific conversation type, else the default."""
        return (
            cls.objects.filter(is_active=True, conversation_type=conversation_type).first()
            or cls.get_default()
        )


class SummaryExample(models.Model):
    """
    A few-shot example: "for this kind of input, the output should look like this".

    Examples belong to a template and are prepended to the LLM conversation in
    ``sort_order``. Editable in admin — change the agent's behaviour with no code
    change and no deploy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        SummaryPromptTemplate, on_delete=models.CASCADE, related_name="examples"
    )

    label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=_("Short human label, e.g. 'meeting with action items'."),
    )
    input_excerpt = models.TextField(
        help_text=_("Transcript excerpt shown to the model as the example input.")
    )
    expected_output = models.TextField(
        help_text=_("The exact JSON the model should produce for that input.")
    )

    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Summary example")
        verbose_name_plural = _("Summary examples")
        ordering = ["sort_order", "created_at"]

    def __str__(self):
        return self.label or f"Example #{self.sort_order} for {self.template.name}"


class ConversationSummary(models.Model):
    """
    The canonical summary of ONE conversation (one ``recording_group_id``).

    Source of truth for "this session is summarized". Idempotent by
    ``(user, recording_group_id)``: a late-arriving clip bumps ``revision`` and
    rewrites this same row rather than creating a second one.

    The individual clips are **never** removed or modified by the summarizer —
    this row is an addition, not a replacement.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="conversation_summaries",
    )

    recording_group_id = models.UUIDField(
        help_text=_("Shared UUID of the clips this summary covers"),
    )

    title = models.CharField(max_length=120, blank=True, default="")
    conversation_type = models.CharField(
        max_length=20, choices=ConversationType.choices, blank=True, default=""
    )

    summary_text = models.TextField(blank=True, default="")
    structured_data = models.JSONField(
        null=True,
        blank=True,
        help_text=_(
            "Full parsed JSON response: type, title, summary, key_points, "
            "decisions, action_items, people, language."
        ),
    )

    # Provenance
    template = models.ForeignKey(
        SummaryPromptTemplate, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="summaries",
    )
    template_version = models.PositiveIntegerField(null=True, blank=True)
    model_used = models.CharField(max_length=100, blank=True, default="")

    # Session shape
    clip_count = models.PositiveIntegerField(default=0)
    total_duration_seconds = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Lifecycle
    revision = models.PositiveIntegerField(
        default=1,
        help_text=_("Incremented each time the session is (re)summarized."),
    )
    status = models.CharField(
        max_length=20, choices=SummaryStatus.choices, default=SummaryStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Conversation summary")
        verbose_name_plural = _("Conversation summaries")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "recording_group_id"],
                name="uniq_user_conversation_summary_group",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.title or 'Untitled'} ({self.clip_count} clips, rev {self.revision})"


class SummaryRun(models.Model):
    """
    Audit of a single LLM call. One row per attempt, success or failure.

    Answers "why does this summary look like this?" — which prompt version, which
    model, how many tokens, how long, and what went wrong if it did. Also feeds
    the token/cost dashboard.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    summary = models.ForeignKey(
        ConversationSummary, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="runs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="summary_runs",
    )
    recording_group_id = models.UUIDField(null=True, blank=True)

    template = models.ForeignKey(
        SummaryPromptTemplate, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="runs",
    )
    template_version = models.PositiveIntegerField(null=True, blank=True)
    model = models.CharField(max_length=100, blank=True, default="")

    kind = models.CharField(
        max_length=20,
        default="summarize",
        help_text=_("'summarize' or 'map_reduce_chunk'."),
    )
    input_chars = models.PositiveIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)

    ok = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = _("Summary run")
        verbose_name_plural = _("Summary runs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        state = "ok" if self.ok else "failed"
        return f"SummaryRun({self.kind}, {self.model}, {state}, {self.input_chars} chars)"
