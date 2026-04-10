"""
Unit tests for speech guard services.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.speech_guard.services import should_proceed_to_transcription, run_calibration


class ShouldProceedToTranscriptionTests(TestCase):
    """Tests for should_proceed_to_transcription."""

    @patch("src.common.config.get_config")
    def test_returns_true_when_guard_disabled(self, mock_get_config):
        mock_config = MagicMock()
        mock_config.speech_guard.enabled = False
        mock_get_config.return_value = mock_config

        proceed, reason = should_proceed_to_transcription(Path("/nonexistent"), 1)
        self.assertTrue(proceed)
        self.assertIsNone(reason)

    @patch("src.common.config.get_config")
    def test_returns_true_when_user_id_none(self, mock_get_config):
        mock_config = MagicMock()
        mock_config.speech_guard.enabled = True
        mock_get_config.return_value = mock_config

        proceed, reason = should_proceed_to_transcription(Path("/nonexistent"), None)
        self.assertTrue(proceed)
        self.assertIsNone(reason)

    @patch("src.common.config.get_config")
    def test_returns_true_when_file_not_exists(self, mock_get_config):
        mock_config = MagicMock()
        mock_config.speech_guard.enabled = True
        mock_get_config.return_value = mock_config

        proceed, reason = should_proceed_to_transcription(Path("/nonexistent/path.wav"), 1)
        self.assertTrue(proceed)
        self.assertIsNone(reason)


class RunCalibrationTests(TestCase):
    """Tests for run_calibration."""

    @patch("src.common.config.get_config")
    def test_returns_false_when_guard_disabled(self, mock_get_config):
        mock_config = MagicMock()
        mock_config.speech_guard.enabled = False
        mock_get_config.return_value = mock_config

        result = run_calibration(Path("/any"), 1)
        self.assertFalse(result)

    @patch("src.common.config.get_config")
    def test_returns_false_when_user_id_none(self, mock_get_config):
        mock_config = MagicMock()
        mock_config.speech_guard.enabled = True
        mock_get_config.return_value = mock_config

        result = run_calibration(Path("/any"), None)
        self.assertFalse(result)
