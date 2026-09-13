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
