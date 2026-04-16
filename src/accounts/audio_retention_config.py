"""
Admin-configurable parameters for audio retention.

Uses GlobalSettings for runtime configuration. Keys:
- storage.audio_retention_days: Days to keep original audio files and playback window (default 3)
"""

from datetime import timedelta

from src.accounts.models import GlobalSettings


def get_audio_retention_days() -> int:
    """Days to keep original audio files. Admin-configurable via GlobalSettings."""
    value = GlobalSettings.get_value('storage.audio_retention_days', 3)
    return int(value) if value is not None else 3


def get_audio_original_retention_timedelta() -> timedelta:
    """How long original audio files remain after processing (matches Listen to Last Recording)."""
    days = get_audio_retention_days()
    return timedelta(0) if days == 0 else timedelta(days=days)
