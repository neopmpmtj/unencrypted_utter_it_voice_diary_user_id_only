"""
Tests for recording upload (no quota pre-check; token-based quotas apply to text/rewrite/chat).
"""

import json
import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase, override_settings

from src.accounts.models import UserPreferences
from src.recordings.views import upload_audio

User = get_user_model()


class _CsrfBypassRequestFactory:
    def post(self, *args, **kwargs):
        from django.test import RequestFactory
        req = RequestFactory().post(*args, **kwargs)
        req._dont_enforce_csrf_checks = True
        return req


class UploadAudioNoQuotaPrecheckTests(TestCase):
    """Recording upload has no quota pre-check; upload is always allowed."""

    def setUp(self):
        self.factory = _CsrfBypassRequestFactory()
        self.user = User.objects.create_user(
            email="quota@example.com",
            password="testpass123",
        )
        self.user.is_active = True
        self.user.tier = "free"
        self.user.save()
        UserPreferences.objects.get_or_create(user=self.user)

    def _create_audio_request(self, duration_sec=60):
        audio_data = b"x" * 1024
        audio_file = InMemoryUploadedFile(
            file=BytesIO(audio_data),
            field_name="audio",
            name="test.webm",
            content_type="audio/webm",
            size=len(audio_data),
            charset=None,
        )
        request = self.factory.post("/voice/upload/")
        request.user = self.user
        request.FILES["audio"] = audio_file
        request.POST = {"template_type": "plain"}
        return request, audio_file

    @patch("src.recordings.views.process_audio_ingest")
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_allowed_without_quota_check(self, mock_process_task):
        """Recording upload is allowed regardless of token usage (no pre-check)."""
        request, _ = self._create_audio_request()

        with patch("src.recordings.views.AudioChunker") as mock_chunker_cls:
            mock_chunker = mock_chunker_cls.return_value
            mock_chunker.get_audio_duration.return_value = 10.0

            response = upload_audio(request)

        self.assertEqual(response.status_code, 200)
        mock_process_task.delay.assert_called_once()
