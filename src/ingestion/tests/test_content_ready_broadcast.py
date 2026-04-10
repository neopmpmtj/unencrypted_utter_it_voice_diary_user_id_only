"""
Tests for broadcast_content_ready in process_audio_ingest.

Verifies that transcription text is broadcast as soon as it is saved,
so the user can view/edit while classification runs in the background.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem, IngestJob, ItemFile, JobType, JobStatus
from src.ingestion.tasks import process_audio_ingest


class ContentReadyBroadcastTests(TestCase):
    """Tests that broadcast_content_ready is called with correct content after ingest saves."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="contentready@example.com",
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
        return job

    @patch("src.classification.tasks.classify_item_task")
    @patch("src.ingestion.tasks.broadcast_content_ready")
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_complete")
    @patch("src.ingestion.tasks.log_api_usage")
    @patch("src.lang_detect.services.is_same_language", return_value=True)
    @patch("src.lang_detect.services.detect_language_keywords", return_value="en")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_broadcast_content_ready_called_with_transcription(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_detect_lang,
        mock_same_lang,
        mock_log_api,
        mock_broadcast_complete,
        mock_broadcast_status,
        mock_broadcast_content_ready,
        mock_classify_task,
    ):
        mock_get_config.return_value = MagicMock(
            chunking=MagicMock(overlap_seconds=1.0),
            storage=MagicMock(default_retention_days=7),
            speech_guard=MagicMock(enabled=False),
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

        channel_layer = MagicMock()
        with patch("src.ingestion.tasks.get_channel_layer", return_value=channel_layer):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(b"fake wav")
                path = f.name
            try:
                job = self._create_job_with_audio_file(path)
                process_audio_ingest.apply(args=[str(job.id)])
                mock_broadcast_content_ready.assert_called_once()
                call_args = mock_broadcast_content_ready.call_args
                self.assertEqual(call_args[0][0], channel_layer)
                self.assertEqual(str(call_args[0][1]), str(job.item.id))
                self.assertEqual(call_args[0][2], "Hello world")
                self.assertEqual(call_args[0][3], "en")
            finally:
                Path(path).unlink(missing_ok=True)
