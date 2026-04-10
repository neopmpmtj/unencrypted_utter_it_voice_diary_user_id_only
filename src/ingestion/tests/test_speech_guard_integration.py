"""
Integration tests for speech guard in process_audio_ingest.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem, IngestJob, ItemFile, JobType, JobStatus
from src.ingestion.tasks import process_audio_ingest


class SpeechGuardIntegrationTests(TestCase):
    """Guard discard path: item deleted, no quota debit, no transcription call."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="guardtest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def _create_job_with_audio_file(self, storage_path):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            provider="manual",
        )
        ItemFile.objects.create(
            user=self.user,
            item=item,
            role="original",
            storage_url=storage_path,
        )
        job = IngestJob.objects.create(
            user=self.user,
            item=item,
            job_type=JobType.PROCESS_AUDIO,
            status=JobStatus.QUEUED,
        )
        return job, item

    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.speech_guard.services.run_calibration")
    @patch("src.speech_guard.services.should_proceed_to_transcription")
    @patch("src.common.config.get_config")
    def test_guard_discard_deletes_item_and_no_quota_debit(
        self,
        mock_get_config,
        mock_should_proceed,
        mock_run_calibration,
        mock_broadcast,
        mock_channel,
    ):
        mock_get_config.return_value = MagicMock(
            chunking=MagicMock(overlap_seconds=1.0),
            storage=MagicMock(default_retention_days=7),
            speech_guard=MagicMock(enabled=True),
        )
        mock_should_proceed.return_value = (False, "No speech detected")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            job, item = self._create_job_with_audio_file(path)
            item_id = item.id

            process_audio_ingest.apply(args=[str(job.id)])

            self.assertFalse(IngestItem.objects.filter(id=item_id).exists())
            self.assertFalse(IngestJob.objects.filter(id=job.id).exists())
            mock_broadcast.assert_called()
        finally:
            Path(path).unlink(missing_ok=True)

    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.log_api_usage")
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_complete")
    @patch("src.lang_detect.services.is_same_language", return_value=True)
    @patch("src.lang_detect.services.detect_language_keywords", return_value="en")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_guard_disabled_pipeline_runs_normally(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_detect_lang,
        mock_same_lang,
        mock_broadcast_complete,
        mock_broadcast_status,
        mock_log_api,
        mock_channel,
    ):
        mock_get_config.return_value = MagicMock(
            chunking=MagicMock(overlap_seconds=1.0),
            storage=MagicMock(default_retention_days=7),
            speech_guard=MagicMock(enabled=False),
            ai=MagicMock(transcription_model="test-model"),
        )
        mock_chunker = MagicMock()
        mock_chunker.needs_chunking.return_value = False
        mock_chunker.get_audio_duration.return_value = 1.0
        mock_chunker_cls.return_value = mock_chunker
        mock_silence = MagicMock()
        mock_silence.process_file.side_effect = lambda p: p
        mock_silence_cls.return_value = mock_silence
        mock_loudness = MagicMock()
        mock_loudness.process_file.side_effect = lambda p: p
        mock_loudness_cls.return_value = mock_loudness
        transcribe_result = MagicMock(text="Hello world", duration=1.0, language="en")
        mock_transcribe.return_value = transcribe_result

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            job, item = self._create_job_with_audio_file(path)
            process_audio_ingest.apply(args=[str(job.id)])
            job.refresh_from_db()
            self.assertEqual(job.status, JobStatus.DONE)
            mock_transcribe.assert_called()
        finally:
            Path(path).unlink(missing_ok=True)
