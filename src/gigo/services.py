"""
GIGO Monitor Services

Records input quality metrics, updates consecutive low counter,
and manages nudge state.
"""

from django.utils import timezone

from .models import GigoEntry, GigoNudgeLog, GigoRank, GigoUserState


def _word_count(text: str) -> int:
    """Count words in text (split on whitespace)."""
    if not text or not isinstance(text, str):
        return 0
    return len(text.split())


def compute_rank(word_count: int) -> str:
    """
    Compute rank from word count.
    <=7: low, 8-15: medium, >15: high
    """
    if word_count <= 7:
        return GigoRank.LOW
    if word_count <= 15:
        return GigoRank.MEDIUM
    return GigoRank.HIGH


def record_entry(
    user,
    item,
    content_text: str,
    item_type: str,
):
    """
    Record a GIGO entry and update user state.

    Args:
        user: CustomUser (can be None for system entries; will skip recording)
        item: IngestItem instance (can be None)
        content_text: Plain text content for word count
        item_type: "audio" or "text"
    """
    if not user:
        return

    word_count = _word_count(content_text)
    rank = compute_rank(word_count)

    GigoEntry.objects.create(
        user=user,
        ingest_item=item,
        item_type=item_type,
        word_count=word_count,
        rank=rank,
    )

    state, _ = GigoUserState.objects.get_or_create(
        user=user,
        defaults={"consecutive_low_count": 0, "alert_pending": False},
    )

    if rank == GigoRank.LOW:
        state.consecutive_low_count += 1
        if state.consecutive_low_count >= 3:
            state.alert_pending = True
            GigoNudgeLog.objects.create(user=user)
    else:
        state.consecutive_low_count = 0

    state.save(update_fields=["consecutive_low_count", "alert_pending", "last_updated"])


def get_alert_pending(user) -> bool:
    """Return True if user has a pending GIGO alert."""
    if not user or not user.is_authenticated:
        return False
    try:
        state = GigoUserState.objects.get(user=user)
        return state.alert_pending
    except GigoUserState.DoesNotExist:
        return False


def dismiss_alert(user):
    """
    Clear alert_pending for user. Does not reset consecutive_low_count.
    """
    if not user or not user.is_authenticated:
        return
    GigoUserState.objects.filter(user=user, alert_pending=True).update(
        alert_pending=False
    )
