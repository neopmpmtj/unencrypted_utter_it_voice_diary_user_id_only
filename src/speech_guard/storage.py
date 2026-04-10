"""
Calibration storage for the speech guard gate.

Stores per-user calibration data as JSON files on the filesystem.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_calibration_dir() -> Path:
    from src.common.config import get_config
    from src.common.utils import ensure_directory

    config = get_config()
    base = Path(config.storage.audio_temp_path)
    if not base.is_absolute():
        from django.conf import settings
        base = Path(settings.BASE_DIR) / base
    cal_dir = base / "guard_gate_calibration"
    return ensure_directory(cal_dir)


def get_calibration_path(user_id: int) -> Path:
    """Return the filesystem path for a user's calibration file."""
    return _get_calibration_dir() / f"{user_id}.json"


def load_calibration(user_id: int) -> dict[str, Any] | None:
    """
    Load calibration data for a user.

    Returns:
        Calibration dict with baseline_rms, baseline_energy_variance,
        baseline_zcr, calibrated_at; or None if not found/corrupt.
    """
    path = get_calibration_path(user_id)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        required = ("baseline_rms", "baseline_energy_variance", "baseline_zcr")
        if not all(k in data for k in required):
            logger.warning(f"Speech guard calibration incomplete for user {user_id}")
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Speech guard calibration load failed for user {user_id}: {e}")
        return None


def save_calibration(user_id: int, data: dict[str, Any]) -> bool:
    """
    Save calibration data for a user.

    Args:
        user_id: User ID
        data: Dict with baseline_rms, baseline_energy_variance, baseline_zcr,
              and optionally calibrated_at (ISO 8601)

    Returns:
        True if saved successfully.
    """
    path = get_calibration_path(user_id)
    if "calibrated_at" not in data:
        data = {**data, "calibrated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        _get_calibration_dir()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError as e:
        logger.warning(f"Speech guard calibration save failed for user {user_id}: {e}")
        return False
