"""
Tests for stripping sensitive keys from checkpoint_data on successful job completion.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem, IngestJob, ItemFile, JobType, JobStatus
from src.ingestion.tasks import strip_sensitive_checkpoint_data, process_audio_ingest


class StripSensitiveCheckpointDataTests(TestCase):
    """Unit tests for strip_sensitive_checkpoint_data helper."""

    def test_removes_only_sensitive_keys(self):
        data = {
            "transcription": "secret transcript",
            "final_text": "secret final",
            "chunk_paths": ["/tmp/audio.wav"],
            "detected_lang": "en",
        }
        result = strip_sensitive_checkpoint_data(data)
        self.assertNotIn("transcription", result)
        self.assertNotIn("final_text", result)
        self.assertEqual(result["chunk_paths"], ["/tmp/audio.wav"])
        self.assertEqual(result["detected_lang"], "en")

    def test_returns_copy_without_mutating_input(self):
        data = {"transcription": "x", "chunk_paths": ["/a"]}
        result = strip_sensitive_checkpoint_data(data)
        self.assertNotIn("transcription", result)
        self.assertIn("transcription", data)
        self.assertIsNot(result, data)

    def test_empty_or_none_unchanged(self):
        self.assertEqual(strip_sensitive_checkpoint_data({}), {})
        self.assertIsNone(strip_sensitive_checkpoint_data(None))


class ProcessAudioIngestCheckpointDataTests(TestCase):
    """Integration-style tests: DONE job has no sensitive keys; ERROR job keeps them."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="checkpointtest@example.com",
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
    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.log_api_usage")
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_content_ready")
    @patch("src.ingestion.tasks.broadcast_complete")
    @patch("src.lang_detect.services.is_same_language", return_value=True)
    @patch("src.lang_detect.services.detect_language_keywords", return_value="en")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_done_job_checkpoint_data_has_no_sensitive_keys(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_detect_lang,
        mock_same_lang,
        mock_broadcast_complete,
        mock_broadcast_content_ready,
        mock_broadcast_status,
        mock_log_api,
        mock_channel,
        mock_classify,
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

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            job = self._create_job_with_audio_file(path)
            process_audio_ingest.apply(args=[str(job.id)])
            job.refresh_from_db()
            self.assertEqual(job.status, JobStatus.DONE)
            self.assertNotIn("transcription", job.checkpoint_data)
            self.assertNotIn("final_text", job.checkpoint_data)
            self.assertIn("chunk_paths", job.checkpoint_data)
        finally:
            Path(path).unlink(missing_ok=True)

    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.log_api_usage")
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_error")
    @patch("src.lang_detect.services.is_same_language", return_value=True)
    @patch("src.lang_detect.services.detect_language_keywords", return_value="en")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_error_job_checkpoint_data_keeps_sensitive_keys(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_detect_lang,
        mock_same_lang,
        mock_broadcast_error,
        mock_broadcast_status,
        mock_log_api,
        mock_channel,
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
        transcribe_result = MagicMock(text="Secret transcript", duration=1.0, language="en")
        mock_transcribe.return_value = transcribe_result

        original_save = IngestItem.save

        def save_fail_on_finalize(self, *args, **kwargs):
            if not kwargs.get("update_fields") and self.content_text:
                raise RuntimeError("fail")
            return original_save(self, *args, **kwargs)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            job = self._create_job_with_audio_file(path)
            with patch.object(IngestItem, "save", save_fail_on_finalize):
                process_audio_ingest.apply(args=[str(job.id)])
            job.refresh_from_db()
            self.assertEqual(job.status, JobStatus.ERROR)
            self.assertIn("transcription", job.checkpoint_data)
            self.assertIn("final_text", job.checkpoint_data)
        finally:
            Path(path).unlink(missing_ok=True)
