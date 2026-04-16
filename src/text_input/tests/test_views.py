"""Tests for text_input HTTP views (ingest with attachments)."""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import FileRole, IngestItem, ItemFile


class IngestTextLocalFilesystemTests(TestCase):
    """Multipart ingest with files when STORAGE local mode is enabled."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="textlocal@example.com",
            password="Pass12345",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    @patch("src.text_input.views.check_token_quota")
    @patch("src.text_input.views.get_config")
    def test_ingest_multipart_saves_attachments_locally(self, mock_get_config, mock_quota):
        mock_quota.return_value = (
            True,
            10_000,
            {"used_tokens": 0, "limit_tokens": 50_000, "remaining_tokens": 10_000},
        )
        root = Path(tempfile.mkdtemp())
        try:
            mock_storage = MagicMock()
            mock_storage.save_attachments_to_local_filesystem = True
            mock_storage.local_storage_root = str(root)
            mock_storage.local_attachments_subdir = "attachments"
            mock_storage.local_recordings_subdir = "recordings"
            mock_get_config.return_value.storage = mock_storage

            pdf = SimpleUploadedFile(
                "from_text.pdf", b"attachment-bytes", content_type="application/pdf"
            )
            response = self.client.post(
                reverse("text_input:ingest"),
                data={
                    "text": "Note body with one file",
                    "template_type": "plain",
                    "files": pdf,
                },
            )

            self.assertEqual(response.status_code, 201)
            data = json.loads(response.content)
            self.assertEqual(data["attachment_count"], 1)

            item = IngestItem.objects.get(id=data["id"])
            att = ItemFile.objects.get(item=item, role=FileRole.ATTACHMENT)
            self.assertEqual(att.drive_folder_id, "")
            self.assertIn(str(root), att.storage_url)
            self.assertIn("attachments", att.storage_url)
            self.assertTrue(Path(att.storage_url).exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)
