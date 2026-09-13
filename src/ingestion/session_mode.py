"""
Session modes — JOURNAL vs INSTRUCTION.  Single source of truth for the cap rule.

WHY TWO MODES
-------------
The diary holds two very different kinds of recordings:

* **Instruction** — a short utterance ("add milk to my list", "move the meeting to
  3pm"). Meant to be classified and *acted upon*.
* **Journal** — a long talk (thinking out loud, brainstorming, "mental diarrhea").
  NOT an instruction. It must never be classified or acted upon; it is summarized
  (``conversation_summarizer``) and kept for later recall.

THE CUE
-------
The recorder caps one recording at 240s and automatically starts a new segment
while keeping the same session id (``recording_group_id``). A long talk is
therefore always stored as ``240s, 240s, …, remainder`` under one session id, so:
**a session that contains a cap-length tranche is a long conversation.**

THE RULE (Pedro, 2026-09-13)
----------------------------
* session contains a cap tranche ⇒ **JOURNAL** — never classify any clip of it,
  including its sub-cap tail
* never reaches the cap ⇒ **INSTRUCTION** — classify → act, as before

Never test for exact equality: a real clip in live data is 335s (the client may
overrun the cap), so anything ≥ ``cap − tolerance`` counts.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from django.db.models import Q

logger = logging.getLogger(__name__)

CAP_SECONDS_DEFAULT = 240
CAP_TOLERANCE_DEFAULT = 0.5
GAP_SECONDS_DEFAULT = 600  # max gap between clips of one conversation (legacy fallback)

JOURNAL = "journal"
INSTRUCTION = "instruction"


def clip_duration_seconds(item) -> Optional[int]:
    """
    Raw recording length in whole seconds.

    Prefer the client-reported ``recording_duration_seconds``: after silence
    removal the processed audio can be far shorter (a real clip is 335s raw /
    59s processed), and the cap test must use the raw value.
    """
    if item.recording_duration_seconds is not None:
        return item.recording_duration_seconds
    if item.audio_duration_seconds is not None:
        return int(round(item.audio_duration_seconds))
    return None


def clip_is_cap(item, cap_seconds: int = CAP_SECONDS_DEFAULT,
                tolerance: float = CAP_TOLERANCE_DEFAULT) -> bool:
    """True when this clip hit the recording cap — the tell of a long conversation."""
    duration = clip_duration_seconds(item)
    if duration is None:
        return False
    return duration >= (cap_seconds - tolerance)


def _cap_tranche_filter(cap_seconds: int, tolerance: float) -> Q:
    """SQL equivalent of :func:`clip_is_cap` (raw duration, else processed duration)."""
    threshold = cap_seconds - tolerance
    return Q(recording_duration_seconds__gte=threshold) | Q(
        recording_duration_seconds__isnull=True, audio_duration_seconds__gte=threshold
    )


def session_contains_cap_tranche(user_id: int, group_id, cap_seconds: int = CAP_SECONDS_DEFAULT,
                                 tolerance: float = CAP_TOLERANCE_DEFAULT) -> bool:
    """Does this recording session (by ``recording_group_id``) hold a cap-length clip?"""
    from src.ingestion.models import IngestItem

    return (
        IngestItem.objects.filter(
            user_id=user_id, recording_group_id=group_id, is_deleted=False
        )
        .filter(_cap_tranche_filter(cap_seconds, tolerance))
        .exists()
    )


def cap_groups_for(user_id: int, group_ids, cap_seconds: int = CAP_SECONDS_DEFAULT,
                   tolerance: float = CAP_TOLERANCE_DEFAULT) -> set:
    """
    Which of these recording sessions contain a cap-length tranche (one query).

    Used by the entries API to mark journal conversations for the UI badge.
    """
    from src.ingestion.models import IngestItem

    ids = [g for g in group_ids if g]
    if not ids:
        return set()
    rows = (
        IngestItem.objects.filter(
            user_id=user_id, is_deleted=False, recording_group_id__in=ids
        )
        .filter(_cap_tranche_filter(cap_seconds, tolerance))
        .values_list("recording_group_id", flat=True)
        .distinct()
    )
    return {str(g) for g in rows}


def _legacy_neighbour_is_cap(user_id: int, item, cap_seconds: int, tolerance: float,
                             gap_seconds: int) -> bool:
    """
    Fallback for clips without a client session id (older clients).

    A sub-cap clip next to a cap-length clip (within the conversation gap window)
    belongs to the same talk — treat it as journal rather than guessing it is an
    instruction.
    """
    from src.ingestion.models import IngestItem

    if item.occurred_at is None:
        return False
    window_start = item.occurred_at - timedelta(seconds=gap_seconds)
    window_end = item.occurred_at + timedelta(seconds=gap_seconds)
    return (
        IngestItem.objects.filter(
            user_id=user_id,
            recording_group_id__isnull=True,
            is_deleted=False,
            item_type="audio",
            occurred_at__gte=window_start,
            occurred_at__lte=window_end,
        )
        .exclude(pk=item.pk)
        .filter(_cap_tranche_filter(cap_seconds, tolerance))
        .exists()
    )


def resolve_session_mode(user_id: int, item, *, cap_seconds: int = CAP_SECONDS_DEFAULT,
                         tolerance: float = CAP_TOLERANCE_DEFAULT,
                         gap_seconds: int = GAP_SECONDS_DEFAULT) -> str:
    """
    ``"journal"`` or ``"instruction"`` for the session this item belongs to.

    Callers pass the item being processed; the session is resolved via its
    ``recording_group_id`` (all tranches, whatever their processing status), with a
    cap-chain fallback for legacy clips that have none.
    """
    if item is None:
        return INSTRUCTION

    # The clip itself hit the cap → long conversation by definition.
    if clip_is_cap(item, cap_seconds, tolerance):
        return JOURNAL

    group_id = getattr(item, "recording_group_id", None)
    if group_id:
        if session_contains_cap_tranche(user_id, group_id, cap_seconds, tolerance):
            return JOURNAL
        return INSTRUCTION

    if _legacy_neighbour_is_cap(user_id, item, cap_seconds, tolerance, gap_seconds):
        return JOURNAL
    return INSTRUCTION


def complete_journal_session(item, *, completion_content: str = "", completion_language: str = "") -> None:
    """
    Post-processing for a journal item.

    Everything here normally happens *inside* the classification task; journal
    items skip classification, so we must do these three things ourselves or the
    entry would hang in the UI and never become searchable:

    1. mark the item final (``tagged``, the same terminal status classified items get)
    2. broadcast completion (the UI waits for it)
    3. enqueue retrieval indexing (search / chat recall)

    Never raises: a journal entry must survive a broadcast or indexing hiccup.
    """
    from src.ingestion.models import IngestStatus

    try:
        if item.status != IngestStatus.TAGGED:
            item.status = IngestStatus.TAGGED
            item.save(update_fields=["status"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Journal post-processing: could not set status for item %s: %s", item.id, exc)

    try:
        from channels.layers import get_channel_layer

        from src.ingestion.tasks import broadcast_complete

        broadcast_complete(
            get_channel_layer(),
            str(item.id),
            completion_content or (item.content_text or ""),
            completion_language or (item.detected_language or ""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Journal post-processing: completion broadcast failed for item %s: %s", item.id, exc)

    try:
        from src.retrieval.tasks import index_entry_prep_task

        index_entry_prep_task.delay(str(item.id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Journal post-processing: indexing failed for item %s: %s", item.id, exc)
