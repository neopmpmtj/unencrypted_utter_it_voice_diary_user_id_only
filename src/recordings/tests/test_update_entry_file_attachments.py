"""
Tests for update_entry_content view - multipart/form-data with file attachments.

Covers the session-based file attachment flow: when the user edits an entry
and attaches files in edit mode, the POST request sends multipart/form-data
with content_text and files, uploads files to Drive, and creates ItemFile records.

IMPORTANT: Django 5.0 only parses multipart/form-data for POST requests
(request.POST and request.FILES are empty for PATCH/PUT). The frontend must
use POST for multipart edit saves; PATCH is reserved for JSON-only updates.
"""

import json
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem, ItemFile


def _create_attachment_file(filename="edit_attach.pdf", size=2000):
    """Create a fake attachment file for testing."""
    data = b"fake attachment content" * (size // 22)
    return SimpleUploadedFile(filename, data, content_type="application/pdf")


class UpdateEntryFileAttachmentsTests(TestCase):
    """Tests for update_entry_content multipart/form-data with file attachments."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)

        self.user = CustomUser.objects.create_user(
            email="updatefiles@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.is_test_user = True
        self.user.save()

        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def _create_item(self, plaintext="Original content"):
        return IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text=plaintext,
            summary_text="",
            title="test title",
            status="processed",
        )

    @patch("src.recordings.views.classify_item_task")
    @patch("src.recordings.views.verify_drive_permissions")
    @patch("src.recordings.views.upload_local_file_to_user_drive_folder")
    @patch("src.recordings.views.get_config")
    def test_update_entry_multipart_with_files_creates_item_files(
        self, mock_get_config, mock_upload_drive, mock_verify, mock_classify
    ):
        """update_entry_content accepts multipart with files and creates ItemFile records."""
        mock_get_config.return_value.storage.audio_temp_path = tempfile.gettempdir()
        mock_verify.return_value = True
        mock_upload_drive.return_value = {
            "id": "drive-file-1",
            "name": "edit_attach.pdf",
            "webViewLink": "https://drive.google.com/file/d/drive-file-1/view",
            "parent_folder_id": "folder-123",
        }

        item = self._create_item()
        url = reverse("recordings:update_entry", args=[str(item.id)])
        f1 = _create_attachment_file("doc1.pdf")
        f2 = _create_attachment_file("doc2.pdf")

        multipart_data = {
            "content_text": "Updated content with attachments",
            "files": [f1, f2],
        }
        response = self.client.post(url, data=multipart_data)

        if response.status_code != 200:
            self.fail(f"Expected 200, got {response.status_code}: {response.content.decode()}")
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("attachment_count"), 2)

        item.refresh_from_db()
        self.assertEqual(item.content_text, "Updated content with attachments")

        attachments = ItemFile.objects.filter(item=item, role="attachment")
        self.assertEqual(attachments.count(), 2)
        for att in attachments:
            self.assertIn("drive.google.com", att.storage_url)
        mock_classify.delay.assert_called_once()

    @patch("src.recordings.views.classify_item_task")
    @patch("src.recordings.views.get_config")
    def test_update_entry_multipart_text_only_backward_compat(
        self, mock_get_config, mock_classify
    ):
        """update_entry_content accepts multipart with content_text only (no files)."""
        mock_get_config.return_value.storage.audio_temp_path = tempfile.gettempdir()

        item = self._create_item()
        url = reverse("recordings:update_entry", args=[str(item.id)])

        multipart_data = {"content_text": "Updated via multipart without files"}
        response = self.client.post(url, data=multipart_data)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("attachment_count"), 0)

        item.refresh_from_db()
        self.assertEqual(item.content_text, "Updated via multipart without files")

    @patch("src.recordings.views.classify_item_task")
    @patch("src.recordings.views.verify_drive_permissions")
    @patch("src.recordings.views.get_config")
    def test_update_entry_multipart_no_drive_skips_files(
        self, mock_get_config, mock_verify, mock_classify
    ):
        """When user has no Drive access, files are skipped but text is still updated."""
        mock_get_config.return_value.storage.audio_temp_path = tempfile.gettempdir()
        mock_verify.return_value = False

        item = self._create_item()
        url = reverse("recordings:update_entry", args=[str(item.id)])
        f1 = _create_attachment_file()

        multipart_data = {
            "content_text": "Updated but no Drive",
            "files": [f1],
        }
        response = self.client.post(url, data=multipart_data)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("attachment_count"), 0)

        item.refresh_from_db()
        self.assertEqual(item.content_text, "Updated but no Drive")

        attachments = ItemFile.objects.filter(item=item, role="attachment")
        self.assertEqual(attachments.count(), 0)

    @patch("src.recordings.views.classify_item_task")
    def test_update_entry_json_still_works(self, mock_classify):
        """application/json continues to work (backward compatibility)."""
        item = self._create_item()
        url = reverse("recordings:update_entry", args=[str(item.id)])

        response = self.client.patch(
            url,
            data=json.dumps({"content_text": "Updated via JSON"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))

        item.refresh_from_db()
        self.assertEqual(item.content_text, "Updated via JSON")

    def test_patch_multipart_rejected_due_to_django_limitation(self):
        """PATCH with multipart/form-data fails because Django 5.0 only parses POST bodies.

        This is a regression guard: if Django changes this behaviour in a future
        version, this test will start failing and we can revisit the allowed methods.
        """
        item = self._create_item()
        url = reverse("recordings:update_entry", args=[str(item.id)])
        f = _create_attachment_file()

        response = self.client.patch(
            url,
            data=b"--boundary\r\nContent-Disposition: form-data; name=\"content_text\"\r\n\r\nHello\r\n--boundary--\r\n",
            content_type="multipart/form-data; boundary=boundary",
        )

        self.assertIn(response.status_code, (400, 415),
                       "PATCH+multipart should fail; Django does not parse FILES for non-POST")
