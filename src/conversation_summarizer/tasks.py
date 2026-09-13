"""
Pipeline hooks for the conversation summarizer.

THE TRIGGER (there is deliberately no timer)
--------------------------------------------
Pedro's requirement: it must live **in the pipeline** and run **every time an entry
is made** — not on a schedule. So ``process_audio_ingest`` (and the text ingest
path) enqueue :func:`summarizer_on_entry_task` right after an entry is finalized.

Each run does two things:

1. **Evaluates its own session** — is the conversation this clip belongs to now
   closed? (tail arrived, or quiet long enough)
2. **Sweeps the user's other sessions** — any closed-but-unsummarized conversation
   is summarized now.

The sweep is what makes the design timer-free: a session that ended *exactly* on
the 240s cap has no further clip coming, so nothing would ever fire for it — the
next entry the user makes (minutes later, per Pedro's usage) picks it up.

WHY NO CELERY RETRY LOOP
------------------------
A failed summary is recorded (status FAILED + SummaryRun) and retried **the next
time an entry is made** — which is the natural cadence here. Retrying inside the
worker would only add retry storms on top of a mechanism that already recovers.

COST
----
Cheap by construction: the sweep is bounded to conversations active in the last
``SWEEP_MAX_AGE_DAYS``, and the only LLM call happens for a conversation that is
closed AND not yet summarized. A mid-conversation clip costs a couple of queries
and exits.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import SummaryAgentConfig
from .services import summarization_enabled, summarize_due_sessions

logger = logging.getLogger(__name__)

#: Only sweep conversations active within this window, so a per-entry hook stays
#: bounded even for a user with a long history. Older sessions are handled by the
#: manual command (and by design are never auto-backfilled).
SWEEP_MAX_AGE_DAYS = 30

#: Safety valve: never summarize more than this many conversations in one run.
SWEEP_LIMIT = 25

#: C2 safety net: delay before re-checking a conversation a sweep missed.
RECHECK_DELAY_SECONDS = 60


@shared_task(name="src.conversation_summarizer.tasks.summarizer_on_entry_task")
def summarizer_on_entry_task(item_id: str) -> dict:
    """
    Entry-finalized hook. Never raises: the ingestion pipeline must not break
    because summarization had a bad day.
    """
    from src.ingestion.models import IngestItem

    try:
        item = (
            IngestItem.objects.select_related("user")
            .filter(pk=item_id)
            .first()
        )
        if item is None:
            return {"action": "skipped", "reason": "no-item", "item_id": str(item_id)}
        if item.is_deleted or item.user is None:
            return {"action": "skipped", "reason": "deleted-or-orphan", "item_id": str(item_id)}

        user = item.user
        cfg = SummaryAgentConfig.get_for_user(user)

        # The user's own switch comes FIRST: when off, keep the raw input and
        # spend nothing — not even a query beyond this check.
        if not summarization_enabled(user, cfg):
            return {"action": "skipped", "reason": "disabled-for-user", "item_id": str(item_id)}

        since = timezone.now() - timedelta(days=SWEEP_MAX_AGE_DAYS)
        results = summarize_due_sessions(
            user=user, cfg=cfg, since=since, limit=SWEEP_LIMIT
        )

        summarized = [r.as_dict() for r in results if r.action in ("created", "updated")]
        failed = [r.as_dict() for r in results if r.action == "failed"]

        if summarized:
            logger.info(
                "Conversation summarizer: %d conversation(s) summarized for user %s (trigger: item %s)",
                len(summarized), user.pk, item_id,
            )
        if failed:
            logger.warning(
                "Conversation summarizer: %d conversation(s) failed for user %s",
                len(failed), user.pk,
            )

        # C2 safety net: if our own conversation still looks closed-and-unsaved —
        # the sweep may have run before this clip's row was visible to it — schedule
        # ONE deferred re-check. Event-driven continuation, not a poller.
        try:
            _schedule_recheck_if_still_pending(user, item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not schedule a summarizer re-check: %s", exc)

        return {
            "action": "summarized" if summarized else ("failed" if failed else "nothing-to-do"),
            "item_id": str(item_id),
            "user_id": user.pk,
            "summarized": summarized,
            "failed": failed,
        }

    except Exception as exc:  # noqa: BLE001 - the pipeline must survive anything here
        logger.exception("Conversation summarizer hook failed for item %s: %s", item_id, exc)
        return {"action": "error", "item_id": str(item_id), "error": str(exc)}


def _schedule_recheck_if_still_pending(user, item) -> None:
    """
    C2 safety net. If the conversation that triggered the hook still looks closed
    but has no ready summary, schedule ONE deferred re-check of just that
    conversation (~60s later).

    Why: the sweep is a query, and a conversation can be closed-yet-invisible to
    it for a moment (the 2026-09-13 10:45 incident). A single re-check 60 seconds
    later turns a silently-missed trigger into a short delay instead of a two-hour
    wait for the next entry.

    Deduped with an atomic cache key so repeated hooks cannot stack re-checks;
    the re-check itself is idempotent, so a stray duplicate is harmless.
    """
    group_id = getattr(item, "recording_group_id", None)
    if not group_id:
        return

    from .models import ConversationSummary, SummaryStatus
    from .services import find_sessions, session_is_complete, session_needs_summary

    if ConversationSummary.objects.filter(
        user=user, recording_group_id=group_id, status=SummaryStatus.READY
    ).exists():
        return  # already done

    session = next(
        (s for s in find_sessions(user=user) if str(s.recording_group_id) == str(group_id)),
        None,
    )
    if session is None:
        return  # not a long conversation (or not visible at all)

    complete, _reason = session_is_complete(
        session.clips,
        cap_seconds=240,  # module defaults; per-user overrides do not change the cue
    )
    if not complete:
        return  # the talk may still be going — normal, no safety net needed

    needs, _needs_reason = session_needs_summary(session)
    if not needs:
        return

    try:
        from django.core.cache import cache

        if not cache.add(f"summarizer:recheck:{user.pk}:{group_id}", 1, timeout=120):
            return  # a re-check for this conversation was already scheduled
    except Exception as exc:  # noqa: BLE001 - a cache hiccup must not block the net
        logger.warning("Summarizer re-check dedupe unavailable: %s", exc)

    summarizer_recheck_task.apply_async(
        args=[user.pk, str(group_id)], countdown=RECHECK_DELAY_SECONDS
    )
    logger.info(
        "Conversation summarizer: scheduled a re-check for group %s in %ss",
        group_id, RECHECK_DELAY_SECONDS,
    )


@shared_task(name="src.conversation_summarizer.tasks.summarizer_recheck_task")
def summarizer_recheck_task(user_id: int, group_id: str) -> dict:
    """
    One-shot re-check of a single conversation (C2).

    Runs the normal summarize path for just this user+group. Idempotent: anything
    already up to date is skipped by the usual gates. Never raises.
    """
    from src.accounts.models import CustomUser

    from .models import SummaryAgentConfig
    from .services import summarization_enabled, summarize_for_user_and_group

    try:
        user = CustomUser.objects.filter(pk=user_id).first()
        if user is None:
            return {"action": "skipped", "reason": "no-user"}
        cfg = SummaryAgentConfig.get_for_user(user)
        if not summarization_enabled(user, cfg):
            return {"action": "skipped", "reason": "disabled-for-user"}
        result = summarize_for_user_and_group(user, group_id, cfg=cfg)
        if result.action in ("created", "updated"):
            logger.info(
                "Conversation summarizer: re-check produced a summary for group %s (%s clips)",
                group_id, result.clip_count,
            )
        return result.as_dict()
    except Exception as exc:  # noqa: BLE001 - the safety net must never raise
        logger.exception("Summarizer re-check failed for group %s: %s", group_id, exc)
        return {"action": "error", "error": str(exc)}
