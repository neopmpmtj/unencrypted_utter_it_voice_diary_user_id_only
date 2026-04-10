"""
Public API for the speech guard gate.
"""

import logging
from pathlib import Path

from .detector import SpeechGuardDetector

logger = logging.getLogger(__name__)


def should_proceed_to_transcription(
    audio_path: Path | str, user_id: int | None
) -> tuple[bool, str | None]:
    """
    Determine whether to send audio to transcription or discard.

    Returns:
        (proceed, reason):
        - (True, None): speech detected or guard disabled -> proceed
        - (False, "No speech detected"): discard
    """
    from src.common.config import get_config

    config = get_config().speech_guard
    if not config.enabled:
        return True, None

    if user_id is None:
        return True, None

    path = Path(audio_path)
    if not path.exists():
        return True, None

    detector = SpeechGuardDetector(config)
    if detector.detect(path, user_id):
        return True, None
    return False, "No speech detected"


def run_calibration(audio_path: Path | str, user_id: int | None) -> bool:
    """
    Run calibration on audio and save baseline for user.

    Returns True if calibration was saved successfully.
    """
    from src.common.config import get_config

    config = get_config().speech_guard
    if not config.enabled or not user_id:
        return False

    path = Path(audio_path)
    if not path.exists():
        return False

    detector = SpeechGuardDetector(config)
    return detector.calibrate(path, user_id)
