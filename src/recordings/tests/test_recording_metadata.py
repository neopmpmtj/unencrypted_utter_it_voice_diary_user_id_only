"""Tests that voice uploads log original duration and a session group id."""

import json
import tempfile
import uuid
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase, override_settings

from src.accounts.models import UserPreferences
from src.ingestion.models import IngestItem
from src.recordings.views import upload_audio

User = get_user_model()


class _CsrfBypassRequestFactory:
    def post(self, *args, **kwargs):
        from django.test import RequestFactory
        req = RequestFactory().post(*args, **kwargs)
        req._dont_enforce_csrf_checks = True
        return req


class UploadRecordingMetadataTests(TestCase):
    def setUp(self):
        self.factory = _CsrfBypassRequestFactory()
        self.user = User.objects.create_user(
            email="recmeta@example.com",
            password="testpass123",
        )
        self.user.is_active = True
        self.user.save()
        UserPreferences.objects.get_or_create(user=self.user)

    def _audio_request(self, extra_post=None, file_duration=180.4):
        audio_file = InMemoryUploadedFile(
            file=BytesIO(b"x" * 1024),
            field_name="audio",
            name="test.webm",
            content_type="audio/webm",
            size=1024,
            charset=None,
        )
        request = self.factory.post("/voice/upload/")
        request.user = self.user
        request.FILES["audio"] = audio_file
        post = {"template_type": "plain"}
        if extra_post:
            post.update(extra_post)
        request.POST = post
        return request, file_duration

    @patch("src.recordings.views.process_audio_ingest")
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_stores_client_duration_and_group_id(self, mock_process_task):
        group_id = uuid.uuid4()
        request, file_duration = self._audio_request({
            "recording_duration_seconds": "240",
            "recording_group_id": str(group_id),
        })

        with patch("src.recordings.views.AudioChunker") as mock_chunker_cls:
            mock_chunker_cls.return_value.get_audio_duration.return_value = file_duration
            response = upload_audio(request)

        self.assertEqual(response.status_code, 200)
        item_id = json.loads(response.content)["item_id"]
        item = IngestItem.objects.get(id=item_id)
        self.assertEqual(item.recording_duration_seconds, 240)
        self.assertEqual(item.recording_group_id, group_id)
        self.assertEqual(item.audio_duration_seconds, file_duration)

    @patch("src.recordings.views.process_audio_ingest")
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_falls_back_to_original_file_duration(self, mock_process_task):
        request, file_duration = self._audio_request()

        with patch("src.recordings.views.AudioChunker") as mock_chunker_cls:
            mock_chunker_cls.return_value.get_audio_duration.return_value = file_duration
            response = upload_audio(request)

        self.assertEqual(response.status_code, 200)
        item_id = json.loads(response.content)["item_id"]
        item = IngestItem.objects.get(id=item_id)
        self.assertEqual(item.recording_duration_seconds, 180)
        self.assertIsNone(item.recording_group_id)

    @patch("src.recordings.views.process_audio_ingest")
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_ignores_invalid_group_id(self, mock_process_task):
        request, file_duration = self._audio_request({
            "recording_group_id": "not-a-uuid",
            "recording_duration_seconds": "not-a-number",
        })

        with patch("src.recordings.views.AudioChunker") as mock_chunker_cls:
            mock_chunker_cls.return_value.get_audio_duration.return_value = file_duration
            response = upload_audio(request)

        self.assertEqual(response.status_code, 200)
        item_id = json.loads(response.content)["item_id"]
        item = IngestItem.objects.get(id=item_id)
        self.assertIsNone(item.recording_group_id)
        self.assertEqual(item.recording_duration_seconds, 180)
