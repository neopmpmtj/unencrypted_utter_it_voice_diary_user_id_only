"""
Admin-configurable parameters for audio retention.

Uses GlobalSettings for runtime configuration. Keys:
- storage.audio_retention_days: Days to keep original audio files (default 3)
- storage.audio_retention_hours: Hours to keep original audio for playback (default 1)
"""

from src.accounts.models import GlobalSettings


def get_audio_retention_hours() -> int:
    """Hours to keep original audio files for playback. Admin-configurable via GlobalSettings."""
    value = GlobalSettings.get_value('storage.audio_retention_hours', 1)
    return int(value) if value is not None else 1


def get_audio_retention_days() -> int:
    """Days to keep original audio files. Admin-configurable via GlobalSettings."""
    value = GlobalSettings.get_value('storage.audio_retention_days', 3)
    return int(value) if value is not None else 3
