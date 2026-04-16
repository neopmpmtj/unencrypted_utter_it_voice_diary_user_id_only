"""
Tests for src/common/tasks.py

Tests upload_attachments_to_drive_task: updates existing ItemFile records
when Drive upload succeeds (does not create duplicate ItemFiles).
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from src.accounts.models import UserPreferences
from src.common.tasks import upload_attachments_to_drive_task
from src.ingestion.models import IngestItem, ItemFile, ItemType, Provider, FileRole

User = get_user_model()


class UploadAttachmentsToDriveTaskTestCase(TestCase):
    """Test upload_attachments_to_drive_task updates ItemFile, does not create duplicate."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="task@example.com",
            password="testpass123",
        )
        self.user.is_active = True
        self.user.save()
        UserPreferences.objects.get_or_create(user=self.user)

        self.item = IngestItem.objects.create(
            id=uuid.uuid4(),
            user=self.user,
            provider=Provider.MANUAL,
            item_type=ItemType.AUDIO,
            title="Test Recording",
        )

    def _create_itemfile(self, filename="doc.pdf", storage_url=""):
        return ItemFile.objects.create(
            user=self.user,
            item=self.item,
            role=FileRole.ATTACHMENT,
            filename=filename,
            mime_type="application/pdf",
            storage_url=storage_url,
            bytes=5000,
        )

    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    @patch("src.common.drive_upload.upload_local_file_to_user_drive_folder")
    def test_task_updates_existing_itemfile(self, mock_upload):
        """Task updates existing ItemFile with Drive URL; does not create duplicate."""
        itemfile = self._create_itemfile("doc.pdf", storage_url="")
        local_path = Path(tempfile.gettempdir()) / "doc.pdf"
        local_path.write_bytes(b"fake pdf content")

        mock_upload.return_value = {
            "id": "drive-123",
            "name": "doc.pdf",
            "webViewLink": "https://drive.google.com/file/d/drive-123/view",
            "parent_folder_id": "folder-456",
        }

        attachment_infos = [
            {
                "itemfile_id": str(itemfile.id),
                "local_path": str(local_path),
                "filename": "doc.pdf",
                "mime_type": "application/pdf",
                "size": 5000,
            }
        ]

        upload_attachments_to_drive_task(
            str(self.item.id),
            self.user.id,
            attachment_infos,
        )

        itemfile.refresh_from_db()
        self.assertEqual(itemfile.storage_url, "https://drive.google.com/file/d/drive-123/view")
        self.assertEqual(itemfile.drive_folder_id, "folder-456")
        self.assertEqual(itemfile.filename, "doc.pdf")

        count = ItemFile.objects.filter(item=self.item, role=FileRole.ATTACHMENT).count()
        self.assertEqual(count, 1)

        if local_path.exists():
            local_path.unlink(missing_ok=True)

    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    @patch("src.common.config.get_config")
    @patch("src.common.drive_upload.upload_local_file_to_user_drive_folder")
    def test_task_skips_when_local_filesystem_enabled(self, mock_upload, mock_get_config):
        """Local storage mode must not call Drive upload."""
        mock_cfg = MagicMock()
        mock_cfg.storage.save_attachments_to_local_filesystem = True
        mock_get_config.return_value = mock_cfg

        itemfile = self._create_itemfile("doc.pdf", storage_url="")
        local_path = Path(tempfile.gettempdir()) / "skip_drive.pdf"
        local_path.write_bytes(b"x")

        upload_attachments_to_drive_task(
            str(self.item.id),
            self.user.id,
            [
                {
                    "itemfile_id": str(itemfile.id),
                    "local_path": str(local_path),
                    "filename": "doc.pdf",
                    "mime_type": "application/pdf",
                    "size": 1,
                }
            ],
        )

        mock_upload.assert_not_called()
        itemfile.refresh_from_db()
        self.assertEqual(itemfile.storage_url, "")
        local_path.unlink(missing_ok=True)
