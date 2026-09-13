"""
Conversation Summarizer — service layer.

This module turns "a long recording, stored as several clips" into "one summary".
It is the public surface of the app: the pipeline hook, the management command
and the admin all call into here.

PIPELINE (top to bottom)
------------------------
    IngestItem clips (shared recording_group_id)
        │
        ▼
    find_sessions()            🧩 group clips into conversations (NO AI)
        │
        ▼
    session_is_long()          ✅ ≥1 clip at the 240s cap ⇒ a long talk
    session_is_complete()      ⏳ tail arrived? or quiet long enough?
        │
        ▼
    build_transcript()         📄 one chronological transcript
        │
        ▼
    build_messages()           📝 editable template + few-shot examples
        │
        ▼
    call_llm()                 🤖 THE ONLY AI CALL (one per session)
        │
        ▼
    parse_summary()            🧾 strict JSON, with a plain-text fallback
        │
        ▼
    ConversationSummary        💾 canonical row + per-clip display copy

DESIGN PRINCIPLES
-----------------
1. **Grouping is deterministic code; the LLM only compresses text.** Reproducible,
   testable, cheap (one call per conversation).
2. **Nothing is ever dropped.** The summary is an *addition*; clips are never
   deleted or modified beyond the display copy.
3. **Idempotent.** A session is "done" when a ConversationSummary row is READY and
   covers the current clip count. A late clip bumps ``revision`` and rewrites the
   same row — never a second one.
4. **Failures never break the pipeline.** LLM errors are recorded (status FAILED +
   SummaryRun) and retried on the next entry, not raised.
5. **Injection-friendly for tests.** ``call_llm`` is module-level and injectable, so
   the whole suite runs with a fake model and zero API cost.

NOT A TIMER
-----------
There is deliberately no scheduler here. The hook runs on every finalized entry
and also sweeps the user's other closed-but-unsummarized sessions (see
``summarize_due_sessions``), which covers sessions that ended exactly on the cap
— the only case where no further entry would ever arrive.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from django.db import transaction
from django.utils import timezone

from .models import (
    ConversationSummary,
    ConversationType,
    SummaryAgentConfig,
    SummaryPromptTemplate,
    SummaryRun,
    SummaryStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (used when no SummaryAgentConfig row exists)
# ---------------------------------------------------------------------------
CAP_SECONDS_DEFAULT = 240
CAP_TOLERANCE_DEFAULT = 0.5
QUIET_SECONDS_DEFAULT = 600
MIN_CHARS_DEFAULT = 200
CHUNK_CHARS_DEFAULT = 60_000
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_OUTPUT_TOKENS = 700

# Statuses that mean "this session still wants a summary".
UNFINISHED_STATUSES = (SummaryStatus.PENDING, SummaryStatus.FAILED, SummaryStatus.PARTIAL)


@dataclass
class Session:
    """One conversation: the ordered clips that share a recording session."""

    user_id: int
    clips: list
    recording_group_id: Optional[uuid.UUID] = None

    @property
    def clip_count(self) -> int:
        return len(self.clips)

    @property
    def first(self):
        return self.clips[0]

    @property
    def last(self):
        return self.clips[-1]

    @property
    def started_at(self):
        return self.first.occurred_at

    @property
    def ended_at(self):
        return self.last.occurred_at

    @property
    def total_duration_seconds(self) -> int:
        return int(sum(clip_duration_seconds(c) or 0 for c in self.clips))

    def key(self) -> tuple:
        return (self.user_id, str(self.recording_group_id) if self.recording_group_id else None)


@dataclass
class SummarizeResult:
    """Outcome of one summarize attempt (used by the command / hook reporting)."""

    user_id: int
    recording_group_id: Optional[str]
    action: str  # created | updated | skipped | failed | dry_run
    reason: str = ""
    summary_id: Optional[str] = None
    clip_count: int = 0
    summary_text: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "recording_group_id": self.recording_group_id,
            "action": self.action,
            "reason": self.reason,
            "summary_id": self.summary_id,
            "clips": self.clip_count,
            "summary": self.summary_text,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Pure helpers — no DB, no network (the testable "brain")
# ---------------------------------------------------------------------------

def clip_duration_seconds(item) -> Optional[int]:
    """
    Raw recording length in whole seconds.

    Prefer the client-reported ``recording_duration_seconds``: after silence
    removal the processed audio can be far shorter (a real clip in this diary is
    335s raw / 59s processed), and the cap test must use the raw value.
    """
    if item.recording_duration_seconds is not None:
        return item.recording_duration_seconds
    if item.audio_duration_seconds is not None:
        return int(round(item.audio_duration_seconds))
    return None


def is_cap_clip(item, cap_seconds: int = CAP_SECONDS_DEFAULT,
                tolerance: float = CAP_TOLERANCE_DEFAULT) -> bool:
    """
    True when this clip hit the recording cap — the tell of a long conversation.

    Never test for exact equality: real data contains a clip at 335s (the client
    is allowed to overrun), so anything at/over ``cap - tolerance`` counts.
    """
    duration = clip_duration_seconds(item)
    if duration is None:
        return False
    return duration >= (cap_seconds - tolerance)


def session_is_long(clips, cap_seconds: int = CAP_SECONDS_DEFAULT,
                    tolerance: float = CAP_TOLERANCE_DEFAULT) -> bool:
    """A session is a 'long conversation' when at least one clip hit the cap."""
    return any(is_cap_clip(c, cap_seconds, tolerance) for c in clips)


def session_is_complete(clips, now=None, cap_seconds: int = CAP_SECONDS_DEFAULT,
                        tolerance: float = CAP_TOLERANCE_DEFAULT,
                        quiet_seconds: int = QUIET_SECONDS_DEFAULT) -> tuple[bool, str]:
    """
    Is the conversation over? Returns ``(complete, reason)``.

    Two ways to be finished:
      * ``tail``  — the last clip is *shorter* than the cap: the talk ended on its
        own and the recorder stopped without splitting.
      * ``quiet`` — the last clip hit the cap but is old enough that no follow-up
        clip ever arrived (ended exactly on the cap, crash, lost upload). Measured
        from the clip's **end**, never its start.
    """
    now = now or timezone.now()
    last = clips[-1]

    if not is_cap_clip(last, cap_seconds, tolerance):
        return True, "tail"

    ended = last.occurred_at
    if ended is not None:
        ended = ended + timezone.timedelta(seconds=clip_duration_seconds(last) or 0)
        if (now - ended).total_seconds() >= quiet_seconds:
            return True, "quiet"

    return False, "open"


def build_transcript(clips) -> str:
    """
    One chronological transcript, with a header per clip so the model can see the
    boundaries even though it is a single continuous conversation.
    """
    total = len(clips)
    parts = []
    for index, item in enumerate(clips, start=1):
        stamp = item.occurred_at.strftime("%Y-%m-%d %H:%M UTC") if item.occurred_at else "?"
        duration = clip_duration_seconds(item) or 0
        text = (item.content_text or "").strip() or "[no transcript]"
        parts.append(f"--- Clip {index}/{total} ({duration}s, {stamp}) ---\n{text}")
    return "\n\n".join(parts)


def render_template(text: str, context: dict) -> str:
    """Substitute ``{{placeholders}}`` in prompt text; unknown ones are left as-is."""
    if not text:
        return ""
    rendered = text
    for key, value in context.items():
        rendered = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", str(value), rendered)
    return rendered


def build_messages(template, context: dict, examples: Iterable = ()) -> list[dict]:
    """
    Build the chat messages: rendered system prompt, then each few-shot example as
    a user/assistant pair, then the real transcript.

    Examples come from the database (``SummaryExample``) so the agent's behaviour
    can be tuned without a deploy.
    """
    messages = [{"role": "system", "content": render_template(template.system_prompt, context)}]

    for example in examples:
        messages.append({"role": "user", "content": example.input_excerpt})
        messages.append({"role": "assistant", "content": example.expected_output})

    messages.append({"role": "user", "content": render_template(template.user_prompt, context)})
    return messages


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_summary(raw: str) -> dict:
    """
    Parse the model's reply into a dict.

    Accepts bare JSON or JSON inside a markdown fence (models sometimes add one
    despite instructions). If nothing parseable is found we degrade to a
    plain-text summary rather than losing the content — the caller marks the row
    ``partial``.
    """
    text = (raw or "").strip()
    if not text:
        return {"ok": False, "summary": "", "structured": None, "partial": True}

    candidates = [text]
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    first_brace, last_brace = text.find("{"), text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace:last_brace + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        summary = data.get("summary") or ""
        if isinstance(summary, list):  # tolerate a list of sentences
            summary = " ".join(str(s) for s in summary)
        return {
            "ok": True,
            "partial": False,
            "structured": data,
            "summary": str(summary).strip(),
            "type": str(data.get("type") or "").strip().lower(),
            "title": str(data.get("title") or "").strip()[:120],
        }

    return {"ok": True, "partial": True, "structured": None, "summary": text}


# ---------------------------------------------------------------------------
# Configuration + gating
# ---------------------------------------------------------------------------

def summarization_enabled(user, cfg=None) -> bool:
    """
    Is summarization allowed for this user?

    BOTH gates must be open:
      * the user's own preference ``UserPreferences.enable_conversation_summary``
        (off ⇒ keep the raw input, pipeline skips entirely), and
      * the agent config's ``enabled`` flag (global kill switch).
    """
    from src.accounts.models import UserPreferences

    prefs = UserPreferences.objects.filter(user=user).first()
    user_wants_it = getattr(prefs, "enable_conversation_summary", True) if prefs else True
    agent_allows_it = cfg.enabled if cfg is not None else True
    return bool(user_wants_it and agent_allows_it)


def _cfg_value(cfg, name, default):
    return getattr(cfg, name, default) if cfg is not None else default


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def _session_clips_for_group(user_id: int, group_id):
    from src.ingestion.models import IngestItem

    return list(
        IngestItem.objects.filter(
            user_id=user_id,
            recording_group_id=group_id,
            is_deleted=False,
            item_type="audio",
        ).order_by("occurred_at", "ingested_at")
    )


def _recent_group_ids(user_id: int, since) -> list:
    """
    Group ids whose NEWEST clip is inside the window.

    Deliberately computed per group (not per clip): filtering clips by date would
    slice a long conversation in half and summarize a partial talk.
    """
    from django.db.models import Max

    from src.ingestion.models import IngestItem

    rows = (
        IngestItem.objects.filter(
            user_id=user_id,
            is_deleted=False,
            item_type="audio",
            recording_group_id__isnull=False,
        )
        .values("recording_group_id")
        .annotate(last_clip_at=Max("occurred_at"))
    )
    return [
        row["recording_group_id"]
        for row in rows
        if row["last_clip_at"] and row["last_clip_at"] >= since
    ]


def find_sessions(user=None, cfg=None, since=None) -> list[Session]:
    """
    All candidate conversations for a user (or every user).

    Primary path: the client's own ``recording_group_id`` — present on every clip
    in live data, so no guessing is needed. Fallback (null group id): time-based
    cap-chain detection, for older clients.

    ``since`` bounds the work to recently-active conversations (the pipeline hook
    passes a window so a per-entry sweep stays cheap). It is only honoured when a
    specific ``user`` is given; the CLI sweep examines everything.
    """
    from src.ingestion.models import IngestItem

    cap_seconds = _cfg_value(cfg, "cap_seconds", CAP_SECONDS_DEFAULT)
    tolerance = _cfg_value(cfg, "cap_tolerance", CAP_TOLERANCE_DEFAULT)

    qs = IngestItem.objects.filter(
        is_deleted=False,
        item_type="audio",
        recording_duration_seconds__isnull=False,
    )
    user_id = None
    if user is not None:
        user_id = getattr(user, "pk", user)
        qs = qs.filter(user_id=user_id)

    clips = list(qs.order_by("user_id", "occurred_at", "ingested_at"))

    recent_group_ids = None
    if since is not None and user_id is not None:
        recent_group_ids = set(_recent_group_ids(user_id, since))

    sessions: list[Session] = []
    grouped: dict = {}
    orphans: list = []
    for clip in clips:
        if clip.recording_group_id:
            if recent_group_ids is not None and clip.recording_group_id not in recent_group_ids:
                continue
            grouped.setdefault((clip.user_id, clip.recording_group_id), []).append(clip)
        else:
            orphans.append(clip)

    for (user_id, group_id), members in grouped.items():
        if session_is_long(members, cap_seconds, tolerance):
            sessions.append(Session(user_id=user_id, clips=members, recording_group_id=group_id))

    sessions.extend(_fallback_sessions(orphans, cap_seconds, tolerance))

    sessions.sort(key=lambda s: s.ended_at or timezone.now(), reverse=True)
    return sessions


def _fallback_sessions(clips, cap_seconds, tolerance, gap_seconds: int = 600) -> list[Session]:
    """
    Legacy path: clips with no client group id.

    Walk chronologically; a conversation starts at a cap clip, continues while
    clips keep hitting the cap, and is closed by the first short (non-cap) clip or
    by a time gap larger than ``gap_seconds``.
    """
    per_user: dict = {}
    for clip in clips:
        per_user.setdefault(clip.user_id, []).append(clip)

    sessions: list[Session] = []
    for user_id, items in per_user.items():
        items.sort(key=lambda i: (i.occurred_at or timezone.now()))
        current: list = []
        previous = None
        for item in items:
            gap = 0.0
            if previous is not None and item.occurred_at and previous.occurred_at:
                gap = (item.occurred_at - previous.occurred_at).total_seconds()
            previous = item

            if not current:
                if is_cap_clip(item, cap_seconds, tolerance):
                    current = [item]
                continue

            if gap > gap_seconds:
                if session_is_long(current, cap_seconds, tolerance):
                    sessions.append(Session(user_id=user_id, clips=current))
                current = [item] if is_cap_clip(item, cap_seconds, tolerance) else []
            elif is_cap_clip(item, cap_seconds, tolerance):
                current.append(item)
            else:
                current.append(item)
                sessions.append(Session(user_id=user_id, clips=current))
                current = []

        if current and session_is_long(current, cap_seconds, tolerance):
            sessions.append(Session(user_id=user_id, clips=current))

    return sessions


def session_needs_summary(session: Session, force: bool = False) -> tuple[bool, str]:
    """
    Should this session be (re)summarized? Returns ``(needs, reason)``.

    Done = a READY row covering the current clip count. A late-arriving clip
    changes the count and makes the session "dirty" again.
    """
    if session.recording_group_id is None:
        return True, "no-group-id"

    existing = ConversationSummary.objects.filter(
        user_id=session.user_id, recording_group_id=session.recording_group_id
    ).first()

    if existing is None:
        return True, "new"
    if force:
        return True, "forced"
    if existing.status in UNFINISHED_STATUSES:
        return True, f"status-{existing.status}"
    if existing.clip_count != session.clip_count:
        return True, "clip-count-changed"
    return False, "up-to-date"


# ---------------------------------------------------------------------------
# The LLM call (module-level ⇒ injectable in tests)
# ---------------------------------------------------------------------------

def call_llm(messages, *, model: str, temperature: float, max_output_tokens: int,
             api_key: Optional[str] = None) -> tuple[str, dict]:
    """
    THE ONLY AI CALL. Returns ``(text, usage)`` where usage carries token counts.

    Kept deliberately small and injectable: tests pass a fake via ``llm=``.
    """
    if not api_key:
        from src.common.config import get_config

        api_key = get_config().ai.openai_api_key
    if not api_key:
        raise RuntimeError("No OpenAI API key configured (AI_OPENAI_API_KEY)")

    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=120.0)
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_output_tokens,
        messages=messages,
    )
    text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    return text, {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def _log_usage(user, model: str, usage: dict, ingest_item=None) -> None:
    """Feed the existing token/cost dashboard; never let logging break a summary."""
    try:
        from src.ingestion.tasks import log_api_usage

        log_api_usage(user, model, "input_tokens", usage.get("input_tokens", 0), ingest_item,
                      origin="conversation_summarizer")
        log_api_usage(user, model, "output_tokens", usage.get("output_tokens", 0), ingest_item,
                      origin="conversation_summarizer")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not log summarizer API usage: %s", exc)


# ---------------------------------------------------------------------------
# Summarizing
# ---------------------------------------------------------------------------

def summarize_session(session: Session, *, force: bool = False, dry_run: bool = False,
                      llm: Optional[Callable] = None, cfg=None):
    """
    Summarize ONE conversation. Returns a ``SummarizeResult``.

    Never raises for LLM/parse problems — it records them and lets the caller
    carry on (the pipeline must not break because an API hiccuped).
    """
    from src.accounts.models import CustomUser

    user = CustomUser.objects.filter(pk=session.user_id).first()
    if user is None:
        return SummarizeResult(session.user_id, None, "skipped", reason="no-user")

    group_id = str(session.recording_group_id) if session.recording_group_id else None
    needs, reason = session_needs_summary(session, force=force)
    if not needs:
        return SummarizeResult(session.user_id, group_id, "skipped", reason=reason,
                               clip_count=session.clip_count)

    complete, complete_reason = session_is_complete(
        session.clips,
        cap_seconds=_cfg_value(cfg, "cap_seconds", CAP_SECONDS_DEFAULT),
        tolerance=_cfg_value(cfg, "cap_tolerance", CAP_TOLERANCE_DEFAULT),
        quiet_seconds=_cfg_value(cfg, "quiet_seconds", QUIET_SECONDS_DEFAULT),
    )
    if not complete and not force:
        return SummarizeResult(session.user_id, group_id, "skipped", reason=complete_reason,
                               clip_count=session.clip_count)

    transcript = build_transcript(session.clips)
    min_chars = _cfg_value(cfg, "min_chars", MIN_CHARS_DEFAULT)
    if len(transcript) < min_chars and not force:
        return SummarizeResult(session.user_id, group_id, "skipped", reason="too-short",
                               clip_count=session.clip_count)

    template = None
    if cfg is not None and cfg.default_template_id:
        template = cfg.default_template
    if template is None:
        template = SummaryPromptTemplate.get_default()
    if template is None:
        return SummarizeResult(session.user_id, group_id, "skipped", reason="no-template")

    if dry_run:
        return SummarizeResult(session.user_id, group_id, "dry_run", reason=complete_reason,
                               clip_count=session.clip_count)

    model = _cfg_value(cfg, "model", DEFAULT_MODEL)
    temperature = _cfg_value(cfg, "temperature", DEFAULT_TEMPERATURE)
    max_output_tokens = _cfg_value(cfg, "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

    context = {
        "transcript": transcript,
        "clip_count": session.clip_count,
        "duration_minutes": round(session.total_duration_seconds / 60),
        "started_at": session.started_at.strftime("%Y-%m-%d %H:%M UTC") if session.started_at else "?",
        "ended_at": session.ended_at.strftime("%Y-%m-%d %H:%M UTC") if session.ended_at else "?",
        "language": session.first.detected_language or "",
        "user_name": user.get_full_name() or user.email.split("@")[0],
    }

    messages = build_messages(template, context, template.examples.filter(is_active=True))
    input_chars = sum(len(m.get("content") or "") for m in messages)

    call = llm or call_llm
    started = time.monotonic()
    try:
        raw, usage = call(messages, model=model, temperature=temperature,
                          max_output_tokens=max_output_tokens)
    except Exception as exc:  # noqa: BLE001 - record and continue
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.error("Summarizer LLM call failed for group %s: %s", group_id, exc)
        SummaryRun.objects.create(
            user=user, recording_group_id=session.recording_group_id, template=template,
            template_version=template.version, model=model, input_chars=input_chars,
            latency_ms=latency_ms, ok=False, error=str(exc)[:2000],
        )
        _mark_failed(session, user, template, model, str(exc))
        return SummarizeResult(session.user_id, group_id, "failed", error=str(exc),
                               clip_count=session.clip_count)

    latency_ms = int((time.monotonic() - started) * 1000)
    usage = usage or {}
    parsed = parse_summary(raw)

    summary_row = _persist(session, user, template, model, usage, parsed)
    SummaryRun.objects.create(
        summary=summary_row, user=user, recording_group_id=session.recording_group_id,
        template=template, template_version=template.version, model=model,
        input_chars=input_chars, tokens_in=usage.get("input_tokens", 0) or 0,
        tokens_out=usage.get("output_tokens", 0) or 0, latency_ms=latency_ms, ok=True,
    )
    _log_usage(user, model, usage, ingest_item=session.first)

    return SummarizeResult(
        session.user_id, group_id, "created" if reason == "new" else "updated",
        reason=reason, summary_id=str(summary_row.id), clip_count=session.clip_count,
        summary_text=summary_row.summary_text,
    )


def _mark_failed(session: Session, user, template, model: str, error: str) -> None:
    if session.recording_group_id is None:
        return
    with transaction.atomic():
        row, _ = ConversationSummary.objects.select_for_update().get_or_create(
            user=user,
            recording_group_id=session.recording_group_id,
            defaults={
                "clip_count": session.clip_count,
                "total_duration_seconds": session.total_duration_seconds,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "template": template,
                "template_version": template.version if template else None,
                "model_used": model,
            },
        )
        row.status = SummaryStatus.FAILED
        row.attempts = (row.attempts or 0) + 1
        row.last_error = (error or "")[:2000]
        row.save(update_fields=["status", "attempts", "last_error", "updated_at"])


def _persist(session: Session, user, template, model: str, usage: dict, parsed: dict):
    """
    Write the canonical row (idempotent) and stamp the display copy on each clip.

    ``revision`` counts how many times this session has been summarized, so a late
    clip visibly produces a *new revision* of the same conversation summary.
    """
    conversation_type = parsed.get("type") or ""
    if conversation_type not in ConversationType.values:
        conversation_type = ConversationType.OTHER

    with transaction.atomic():
        row = None
        if session.recording_group_id is not None:
            row = (
                ConversationSummary.objects.select_for_update()
                .filter(user=user, recording_group_id=session.recording_group_id)
                .first()
            )

        if row is None:
            row = ConversationSummary(
                user=user,
                recording_group_id=session.recording_group_id or uuid.uuid4(),
                revision=1,
            )
            created = True
        else:
            created = False
            row.revision = (row.revision or 1) + 1

        row.title = parsed.get("title") or row.title or ""
        row.conversation_type = conversation_type
        row.summary_text = parsed.get("summary") or ""
        row.structured_data = parsed.get("structured")
        row.template = template
        row.template_version = template.version if template else None
        row.model_used = model
        row.clip_count = session.clip_count
        row.total_duration_seconds = session.total_duration_seconds
        row.started_at = session.started_at
        row.ended_at = session.ended_at
        row.status = SummaryStatus.PARTIAL if parsed.get("partial") else SummaryStatus.READY
        row.attempts = (row.attempts or 0) + 1
        row.last_error = ""
        row.tokens_in = (row.tokens_in or 0) + (usage.get("input_tokens", 0) or 0)
        row.tokens_out = (row.tokens_out or 0) + (usage.get("output_tokens", 0) or 0)
        row.save()

        # Display copy on every clip (the UI also shows it once per session).
        group_uuid = row.recording_group_id
        for clip in session.clips:
            updates = {}
            if clip.summary_text != row.summary_text:
                updates["summary_text"] = row.summary_text
            if group_uuid and clip.recording_group_id != group_uuid and created:
                updates["recording_group_id"] = group_uuid
            if updates:
                for key, value in updates.items():
                    setattr(clip, key, value)
                clip.save(update_fields=list(updates))

    return row


def summarize_due_sessions(user=None, *, force: bool = False, dry_run: bool = False,
                           llm: Optional[Callable] = None, limit: int = 50,
                           cfg=None, since=None) -> list[SummarizeResult]:
    """
    The sweep: summarize every conversation that is closed and not yet summarized.

    Called on every finalized entry, so a session that ended exactly on the cap is
    picked up as soon as *any* later entry arrives — no scheduler required.
    """
    from src.accounts.models import CustomUser

    results: list[SummarizeResult] = []
    processed = 0
    for session in find_sessions(user=user, cfg=cfg, since=since):
        if processed >= limit:
            break

        session_cfg = cfg
        if session_cfg is None:
            session_user = CustomUser.objects.filter(pk=session.user_id).first()
            session_cfg = SummaryAgentConfig.get_for_user(session_user)

        # Respect the user's preference before doing any work at all.
        session_user = CustomUser.objects.filter(pk=session.user_id).first()
        if not summarization_enabled(session_user, session_cfg):
            continue

        needs, reason = session_needs_summary(session, force=force)
        if not needs:
            continue

        result = summarize_session(session, force=force, dry_run=dry_run,
                                   llm=llm, cfg=session_cfg)
        if result.action != "skipped":
            processed += 1
        if result.action != "skipped" or dry_run:
            results.append(result)

    return results


def summarize_for_user_and_group(user, group_id, *, force: bool = False, llm=None, cfg=None):
    """Convenience wrapper: summarize one known session by its group id."""
    from src.ingestion.models import IngestItem

    clips = list(
        IngestItem.objects.filter(
            user=user, recording_group_id=group_id, is_deleted=False, item_type="audio"
        ).order_by("occurred_at", "ingested_at")
    )
    if not clips:
        return SummarizeResult(getattr(user, "pk", 0), str(group_id), "skipped", reason="no-clips")

    cfg = cfg or SummaryAgentConfig.get_for_user(user)
    if not summarization_enabled(user, cfg):
        return SummarizeResult(user.pk, str(group_id), "skipped", reason="disabled")
    session = Session(user_id=user.pk, clips=clips, recording_group_id=group_id)
    return summarize_session(session, force=force, llm=llm, cfg=cfg)
