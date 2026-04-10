"""
Tests that API calls are logged to accounts_apiusagelog table.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.accounts.models import APIUsageLog, CustomUser, UserPreferences
from src.ingestion.models import IngestItem, IngestJob, ItemFile, JobType, JobStatus
from src.ingestion.tasks import process_audio_ingest, transcribe_only_task


class ProcessAudioIngestAPIUsageLoggingTests(TestCase):
    """Tests that process_audio_ingest logs API usage to accounts_apiusagelog."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="apiusage@example.com",
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

    @patch("src.classification.tasks.classify_item_task")
    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_complete")
    @patch("src.lang_detect.services.is_same_language", return_value=True)
    @patch("src.lang_detect.services.detect_language_keywords", return_value="en")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_process_audio_ingest_logs_whisper_when_no_translation(
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
        mock_channel,
        mock_classify,
    ):
        mock_config = MagicMock()
        mock_config.chunking = MagicMock(overlap_seconds=1.0)
        mock_config.storage = MagicMock(default_retention_days=7)
        mock_config.ai = MagicMock(transcription_model="test-transcription-model")
        mock_config.speech_guard = MagicMock(enabled=False)
        mock_get_config.return_value = mock_config
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
        transcribe_result = MagicMock(text="Hello world", duration=60.0, language="en")
        mock_transcribe.return_value = transcribe_result

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            job, item = self._create_job_with_audio_file(path)
            initial_count = APIUsageLog.objects.filter(user=self.user).count()
            process_audio_ingest.apply(args=[str(job.id)])
            job.refresh_from_db()
            self.assertEqual(job.status, JobStatus.DONE, f"Job failed: {job.last_error}")
            logs = list(APIUsageLog.objects.filter(user=self.user).order_by("id"))
            self.assertEqual(len(logs), initial_count + 1)
            whisper_log = logs[-1]
            self.assertEqual(whisper_log.service, "test-transcription-model")
            self.assertEqual(whisper_log.usage_type, "audio_minutes")
            self.assertGreater(whisper_log.amount, 0)
            self.assertEqual(whisper_log.ingest_item, item)
            self.assertEqual(whisper_log.origin, "process_audio_ingest")
        finally:
            Path(path).unlink(missing_ok=True)

    @patch("src.classification.tasks.classify_item_task")
    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_complete")
    @patch("src.translation.services.translate_text")
    @patch("src.lang_detect.services.is_same_language", return_value=False)
    @patch("src.lang_detect.services.detect_language_keywords", return_value="en")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_process_audio_ingest_logs_whisper_and_gpt_when_translation_runs(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_detect_lang,
        mock_same_lang,
        mock_translate,
        mock_broadcast_complete,
        mock_broadcast_status,
        mock_channel,
        mock_classify,
    ):
        UserPreferences.objects.filter(user=self.user).update(
            preferred_language="pt-pt",
            enable_translation=True,
        )
        mock_config = MagicMock()
        mock_config.chunking = MagicMock(overlap_seconds=1.0)
        mock_config.storage = MagicMock(default_retention_days=7)
        mock_config.speech_guard = MagicMock(enabled=False)
        mock_config.ai = MagicMock(
            transcription_model="test-transcription-model",
            translation_model="test-translation-model",
        )
        mock_get_config.return_value = mock_config
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
        transcribe_result = MagicMock(text="Hello world", duration=60.0, language="en")
        mock_transcribe.return_value = transcribe_result
        mock_translate.return_value = ("translated text", {"input": 10, "output": 5, "total": 15})

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            job, item = self._create_job_with_audio_file(path)
            initial_count = APIUsageLog.objects.filter(user=self.user).count()
            process_audio_ingest.apply(args=[str(job.id)])
            job.refresh_from_db()
            self.assertEqual(job.status, JobStatus.DONE, f"Job failed: {job.last_error}")
            logs = list(APIUsageLog.objects.filter(user=self.user).order_by("id"))
            self.assertEqual(len(logs), initial_count + 3)
            new_logs = logs[initial_count:]
            whisper_log = next(l for l in new_logs if l.usage_type == "audio_minutes")
            self.assertEqual(whisper_log.usage_type, "audio_minutes")
            self.assertGreater(whisper_log.amount, 0)
            self.assertEqual(whisper_log.ingest_item, item)
            self.assertEqual(whisper_log.origin, "process_audio_ingest")
            self.assertEqual(whisper_log.service, "test-transcription-model")
            input_log = next(l for l in new_logs if l.usage_type == "input_tokens")
            self.assertEqual(input_log.service, "test-translation-model")
            self.assertEqual(input_log.amount, 10)
            self.assertEqual(input_log.ingest_item, item)
            self.assertEqual(input_log.origin, "process_audio_ingest")
            output_log = next(l for l in new_logs if l.usage_type == "output_tokens")
            self.assertEqual(output_log.service, "test-translation-model")
            self.assertEqual(output_log.amount, 5)
            self.assertEqual(output_log.ingest_item, item)
            self.assertEqual(output_log.origin, "process_audio_ingest")
        finally:
            Path(path).unlink(missing_ok=True)


class TranscribeOnlyTaskAPIUsageLoggingTests(TestCase):
    """Tests that transcribe_only_task logs API usage to accounts_apiusagelog."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="transcribeonly@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    @patch("src.ingestion.tasks.store_pending_transcription", return_value=True)
    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_transcription_ready")
    @patch("src.ingestion.tasks.check_transcription_rate_limit", return_value=(True, {}))
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_transcribe_only_task_logs_whisper(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_rate_limit,
        mock_broadcast_ready,
        mock_broadcast_status,
        mock_channel,
        mock_store,
    ):
        mock_config = MagicMock()
        mock_config.chunking = MagicMock(overlap_seconds=1.0)
        mock_config.ai = MagicMock(transcription_model="test-transcription-model")
        mock_config.speech_guard = MagicMock(enabled=False)
        mock_get_config.return_value = mock_config
        mock_chunker = MagicMock()
        mock_chunker.needs_chunking.return_value = False
        mock_chunker.get_audio_duration.return_value = 60.0
        mock_chunker_cls.return_value = mock_chunker
        mock_silence = MagicMock()
        mock_silence.process_file.side_effect = lambda p: p
        mock_silence_cls.return_value = mock_silence
        mock_loudness = MagicMock()
        mock_loudness.process_file.side_effect = lambda p: p
        mock_loudness_cls.return_value = mock_loudness
        transcribe_result = MagicMock(text="Hello world", duration=60.0, language="en")
        mock_transcribe.return_value = transcribe_result

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            temp_id = str(uuid.uuid4())
            initial_count = APIUsageLog.objects.filter(user=self.user).count()
            transcribe_only_task.apply(
                args=[temp_id, path, self.user.id, "plain"]
            )
            logs = list(APIUsageLog.objects.filter(user=self.user).order_by("id"))
            self.assertEqual(len(logs), initial_count + 1)
            whisper_log = logs[-1]
            self.assertEqual(whisper_log.service, "test-transcription-model")
            self.assertEqual(whisper_log.usage_type, "audio_minutes")
            self.assertGreater(whisper_log.amount, 0)
            self.assertIsNone(whisper_log.ingest_item)
            self.assertEqual(whisper_log.origin, "transcribe_only_task")
        finally:
            Path(path).unlink(missing_ok=True)

    @patch("src.ingestion.tasks.store_pending_transcription", return_value=True)
    @patch("src.ingestion.tasks.get_channel_layer", return_value=None)
    @patch("src.ingestion.tasks.broadcast_status")
    @patch("src.ingestion.tasks.broadcast_transcription_ready")
    @patch("src.transcription.services.transcribe_audio")
    @patch("src.ingestion.audio_services.LoudnessNormalizer")
    @patch("src.ingestion.audio_services.SilenceRemover")
    @patch("src.ingestion.audio_services.AudioChunker")
    @patch("src.common.config.get_config")
    def test_transcribe_only_task_with_no_user_does_not_log(
        self,
        mock_get_config,
        mock_chunker_cls,
        mock_silence_cls,
        mock_loudness_cls,
        mock_transcribe,
        mock_broadcast_ready,
        mock_broadcast_status,
        mock_channel,
        mock_store,
    ):
        mock_config = MagicMock()
        mock_config.chunking = MagicMock(overlap_seconds=1.0)
        mock_config.ai = MagicMock(transcription_model="test-transcription-model")
        mock_config.speech_guard = MagicMock(enabled=False)
        mock_get_config.return_value = mock_config
        mock_chunker = MagicMock()
        mock_chunker.needs_chunking.return_value = False
        mock_chunker.get_audio_duration.return_value = 60.0
        mock_chunker_cls.return_value = mock_chunker
        mock_silence = MagicMock()
        mock_silence.process_file.side_effect = lambda p: p
        mock_silence_cls.return_value = mock_silence
        mock_loudness = MagicMock()
        mock_loudness.process_file.side_effect = lambda p: p
        mock_loudness_cls.return_value = mock_loudness
        transcribe_result = MagicMock(text="Hello world", duration=60.0, language="en")
        mock_transcribe.return_value = transcribe_result

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav")
            path = f.name
        try:
            temp_id = str(uuid.uuid4())
            nonexistent_user_id = 999999
            initial_count = APIUsageLog.objects.count()
            transcribe_only_task.apply(
                args=[temp_id, path, nonexistent_user_id, "plain"]
            )
            self.assertEqual(APIUsageLog.objects.count(), initial_count)
        finally:
            Path(path).unlink(missing_ok=True)
