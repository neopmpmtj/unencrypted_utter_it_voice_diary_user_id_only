"""
Speech guard detector: frame-level RMS/ZCR analysis and decision logic.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
from pydub import AudioSegment

from .storage import load_calibration, save_calibration

logger = logging.getLogger(__name__)


class SpeechGuardDetector:
    """
    Detects whether audio contains speech using adaptive calibration.
    """

    def __init__(self, config=None):
        if config is None:
            from src.common.config import get_config
            config = get_config().speech_guard
        self.config = config

    def _load_audio_segment(self, audio_path: Path) -> tuple[np.ndarray, int] | None:
        """
        Load first N seconds of audio, resample to 16kHz mono, return as float array.

        Returns:
            (samples, sample_rate) or None on failure.
        """
        try:
            seg = AudioSegment.from_file(str(audio_path))
        except Exception as e:
            logger.warning(f"Speech guard: failed to load audio {audio_path}: {e}")
            return None

        duration_ms = len(seg)
        max_ms = int(self.config.analysis_window_seconds * 1000)
        if duration_ms > max_ms:
            seg = seg[:max_ms]

        seg = seg.set_frame_rate(self.config.sample_rate)
        seg = seg.set_channels(1)

        samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
        return samples, self.config.sample_rate

    def _frame_segment(
        self, samples: np.ndarray, sample_rate: int
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Split samples into overlapping frames. Returns list of (rms, zcr) per frame."""
        frame_len = int(self.config.frame_duration_ms * sample_rate / 1000)
        hop_len = int(self.config.frame_hop_ms * sample_rate / 1000)
        frames: list[tuple[float, float]] = []

        i = 0
        while i + frame_len <= len(samples):
            frame = samples[i : i + frame_len]
            rms = float(np.sqrt(np.mean(frame ** 2)))
            zcr = float(np.sum(np.abs(np.diff(np.sign(frame)))) / 2) / len(frame)
            frames.append((rms, zcr))
            i += hop_len

        return frames

    def _compute_baseline_from_quiet_frames(
        self, frame_data: list[tuple[float, float]]
    ) -> dict[str, float]:
        """Compute baseline from quietest percentile of frames."""
        if not frame_data:
            return {}
        rms_vals = [f[0] for f in frame_data]
        zcr_vals = [f[1] for f in frame_data]
        n_quiet = max(1, int(len(frame_data) * self.config.calibration_percentile))
        sorted_idx = np.argsort(rms_vals)[:n_quiet]
        quiet_rms = [rms_vals[i] for i in sorted_idx]
        quiet_zcr = [zcr_vals[i] for i in sorted_idx]
        baseline_rms = float(np.mean(quiet_rms))
        baseline_energy_variance = float(np.var(quiet_rms)) if len(quiet_rms) > 1 else 0.0
        baseline_zcr = float(np.mean(quiet_zcr))
        baseline_zcr_variance = float(np.var(quiet_zcr)) if len(quiet_zcr) > 1 else 0.0
        return {
            "baseline_rms": baseline_rms,
            "baseline_energy_variance": baseline_energy_variance,
            "baseline_zcr": baseline_zcr,
            "baseline_zcr_variance": baseline_zcr_variance,
        }

    def detect(self, audio_path: Path, user_id: int | None) -> bool:
        """
        Run detection on the first N seconds of audio.

        Returns:
            True if speech detected (proceed to transcription), False to discard.
        """
        result = self._load_audio_segment(audio_path)
        if result is None:
            return True
        samples, sample_rate = result

        frame_data = self._frame_segment(samples, sample_rate)
        if not frame_data:
            return True

        calibration = load_calibration(user_id) if user_id else None
        if calibration is None:
            return True

        baseline_rms = calibration["baseline_rms"]
        baseline_energy_variance = calibration["baseline_energy_variance"]
        baseline_zcr_variance = calibration.get("baseline_zcr_variance", 0.0)

        threshold = baseline_rms * self.config.energy_multiplier
        active_count = sum(1 for rms, _ in frame_data if rms > threshold)
        active_ratio = active_count / len(frame_data)

        rms_vals = [f[0] for f in frame_data]
        energy_variance = float(np.var(rms_vals))
        variance_threshold = baseline_energy_variance * self.config.variance_multiplier

        if active_ratio < self.config.min_active_ratio:
            return False
        if energy_variance < variance_threshold:
            return False

        if self.config.ambiguous_ratio_low <= active_ratio < self.config.min_active_ratio + 0.05:
            zcr_vals = [f[1] for f in frame_data]
            zcr_variance = float(np.var(zcr_vals))
            zcr_threshold = baseline_zcr_variance * self.config.zcr_multiplier
            if zcr_variance < zcr_threshold:
                return False

        return True

    def calibrate(self, audio_path: Path, user_id: int | None) -> bool:
        """
        Run calibration on full audio and save baseline for user.

        Returns True if calibration was saved successfully.
        """
        if not user_id:
            return False
        try:
            seg = AudioSegment.from_file(str(audio_path))
        except Exception as e:
            logger.warning(f"Speech guard: calibration load failed {audio_path}: {e}")
            return False

        seg = seg.set_frame_rate(self.config.sample_rate)
        seg = seg.set_channels(1)
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0

        frame_data = self._frame_segment(samples, self.config.sample_rate)
        if not frame_data:
            return False

        baseline = self._compute_baseline_from_quiet_frames(frame_data)
        return save_calibration(user_id, baseline)
