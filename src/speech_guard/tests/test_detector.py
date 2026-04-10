"""
Unit tests for SpeechGuardDetector.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.speech_guard.detector import SpeechGuardDetector
from src.speech_guard.storage import save_calibration, load_calibration


class SpeechGuardDetectorTests(TestCase):
    """Unit tests for frame segmentation, RMS/ZCR, and decision logic."""

    def setUp(self):
        self.config = MagicMock()
        self.config.analysis_window_seconds = 5.0
        self.config.frame_duration_ms = 30
        self.config.frame_hop_ms = 15
        self.config.sample_rate = 16000
        self.config.energy_multiplier = 2.5
        self.config.min_active_ratio = 0.15
        self.config.variance_multiplier = 4.0
        self.config.zcr_multiplier = 1.5
        self.config.ambiguous_ratio_low = 0.10
        self.config.calibration_percentile = 0.20

    def test_detect_returns_true_when_no_calibration(self):
        detector = SpeechGuardDetector(self.config)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake")
            path = Path(f.name)
        try:
            with patch.object(detector, "_load_audio_segment") as mock_load:
                mock_load.return_value = None
                result = detector.detect(path, 99999)
                self.assertTrue(result)

            with patch.object(detector, "_load_audio_segment") as mock_load:
                import numpy as np
                mock_load.return_value = (np.zeros(16000), 16000)
                with patch("src.speech_guard.detector.load_calibration", return_value=None):
                    result = detector.detect(path, 99999)
                    self.assertTrue(result)
        finally:
            path.unlink(missing_ok=True)

    def test_detect_returns_true_when_user_id_none(self):
        detector = SpeechGuardDetector(self.config)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake")
            path = Path(f.name)
        try:
            result = detector.detect(path, None)
            self.assertTrue(result)
        finally:
            path.unlink(missing_ok=True)

    def test_calibrate_returns_false_when_user_id_none(self):
        detector = SpeechGuardDetector(self.config)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake")
            path = Path(f.name)
        try:
            result = detector.calibrate(path, None)
            self.assertFalse(result)
        finally:
            path.unlink(missing_ok=True)


class CalibrationStorageTests(TestCase):
    """Tests for calibration load/save."""

    def test_save_and_load_calibration(self):
        with patch("src.speech_guard.storage._get_calibration_dir") as mock_dir:
            tmp = tempfile.mkdtemp()
            mock_dir.return_value = Path(tmp)
            try:
                data = {
                    "baseline_rms": 0.01,
                    "baseline_energy_variance": 0.0001,
                    "baseline_zcr": 0.05,
                    "baseline_zcr_variance": 0.001,
                }
                self.assertTrue(save_calibration(42, data))
                loaded = load_calibration(42)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["baseline_rms"], 0.01)
                self.assertEqual(loaded["baseline_zcr"], 0.05)
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
