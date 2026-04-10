"""
Tests for recorder file attachments feature.

Tests cover:
- File attachment in auto-save mode (upload_audio view)
- File validation and Drive integration
"""

import json
import logging
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from src.accounts.models import UserPreferences
from src.ingestion.models import IngestItem, ItemFile, IngestJob, ItemType, Provider, JobType, JobStatus, TemplateType, IngestStatus, FileRole
from src.recordings.views import upload_audio, upload_file_to_drive

User = get_user_model()
logger = logging.getLogger(__name__)


class _CsrfBypassRequestFactory(RequestFactory):
    """RequestFactory that sets _dont_enforce_csrf_checks on every request."""

    def request(self, **kwargs):
        req = super().request(**kwargs)
        req._dont_enforce_csrf_checks = True
        return req


class RecorderFileAttachmentsTestCase(TestCase):
    """Test file attachments for voice recorder."""

    def setUp(self):
        """Set up test fixtures."""
        self.factory = _CsrfBypassRequestFactory()

        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.user.is_active = True
        self.user.save()

        UserPreferences.objects.get_or_create(user=self.user)

    def create_audio_file(self, filename="test_audio.webm", size=1000):
        """Create a fake audio file for testing."""
        audio_data = b"fake audio data" * (size // 15)
        return InMemoryUploadedFile(
            file=BytesIO(audio_data),
            field_name="audio",
            name=filename,
            content_type="audio/webm",
            size=len(audio_data),
            charset=None
        )

    def create_attachment_file(self, filename="test_file.pdf", size=5000):
        """Create a fake attachment file for testing."""
        file_data = b"fake file content" * (size // 17)
        return InMemoryUploadedFile(
            file=BytesIO(file_data),
            field_name="files",
            name=filename,
            content_type="application/pdf",
            size=len(file_data),
            charset=None
        )

    @patch('src.recordings.views.AudioChunker')
    @patch('src.recordings.views.process_audio_ingest')
    @patch('src.recordings.views.verify_drive_permissions')
    @patch('src.recordings.views.upload_local_file_to_user_drive_folder')
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_with_files_auto_save_mode(self, mock_upload_drive, mock_verify, mock_process_task, mock_chunker_cls):
        """Test uploading audio with file attachments in auto-save mode."""
        mock_chunker_cls.return_value.get_audio_duration.return_value = 1.0
        mock_verify.return_value = True
        mock_upload_drive.return_value = {
            'id': 'file-id-123',
            'name': 'test_file.pdf',
            'webViewLink': 'https://drive.google.com/file/d/file-id-123/view',
            'parent_folder_id': 'folder-id-456',
        }

        # Create request with audio and files
        request = self.factory.post('/voice/upload/')
        request.user = self.user

        # Add audio file
        audio_file = self.create_audio_file()
        request.FILES['audio'] = audio_file
        
        # Add attachment files
        attachment1 = self.create_attachment_file('doc1.pdf')
        attachment2 = self.create_attachment_file('doc2.pdf')
        request.FILES.setlist('files', [attachment1, attachment2])
        
        request.POST = {'template_type': 'plain'}

        # Call view
        response = upload_audio(request)
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        
        self.assertIn('item_id', data)
        self.assertIn('job_id', data)
        self.assertEqual(data['attachment_count'], 2)
        
        # Verify Drive upload was called twice (synchronous in view)
        self.assertEqual(mock_upload_drive.call_count, 2)
        
        # Verify ItemFile records were created
        item_id = uuid.UUID(data['item_id'])
        item = IngestItem.objects.get(id=item_id)
        attachments = ItemFile.objects.filter(item=item, role='attachment')
        self.assertEqual(attachments.count(), 2)
        
        # Verify attachment properties
        for attachment in attachments:
            self.assertEqual(attachment.role, 'attachment')
            self.assertIsNotNone(attachment.storage_url)
            self.assertIn('drive.google.com', attachment.storage_url)

    @patch('src.recordings.views.AudioChunker')
    @patch('src.recordings.views.process_audio_ingest')
    @patch('src.recordings.views.verify_drive_permissions')
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_without_files_auto_save_mode(self, mock_verify, mock_process_task, mock_chunker_cls):
        """Test uploading audio without files (backward compatibility)."""
        mock_chunker_cls.return_value.get_audio_duration.return_value = 1.0
        mock_verify.return_value = True

        request = self.factory.post('/voice/upload/')
        request.user = self.user
        request.FILES['audio'] = self.create_audio_file()
        request.POST = {'template_type': 'plain'}

        response = upload_audio(request)
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        
        self.assertEqual(data['attachment_count'], 0)

    @patch('src.recordings.views.AudioChunker')
    @patch('src.recordings.views.process_audio_ingest')
    @patch('src.recordings.views.verify_drive_permissions')
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_no_drive_access_skips_files(self, mock_verify, mock_process_task, mock_chunker_cls):
        """Test that files are skipped when user lacks Drive access."""
        mock_chunker_cls.return_value.get_audio_duration.return_value = 1.0
        mock_verify.return_value = False

        request = self.factory.post('/voice/upload/')
        request.user = self.user
        request.FILES['audio'] = self.create_audio_file()
        request.FILES.setlist('files', [self.create_attachment_file()])
        request.POST = {'template_type': 'plain'}

        response = upload_audio(request)
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        
        # Should still work, but attachment_count is 0
        self.assertEqual(data['attachment_count'], 0)

    @patch('src.recordings.views.AudioChunker')
    @patch('src.recordings.views.process_audio_ingest')
    @patch('src.recordings.views.verify_drive_permissions')
    @patch('src.recordings.views.upload_local_file_to_user_drive_folder')
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_drive_auth_error_skips_files(self, mock_upload_drive,
                                                       mock_verify, mock_process_task, mock_chunker_cls):
        """Test that Drive auth errors don't break audio upload."""
        from src.common.google_account.auth import GoogleAuthError

        mock_chunker_cls.return_value.get_audio_duration.return_value = 1.0
        mock_verify.return_value = True
        mock_upload_drive.side_effect = GoogleAuthError("Auth failed")

        request = self.factory.post('/voice/upload/')
        request.user = self.user
        request.FILES['audio'] = self.create_audio_file()
        request.FILES.setlist('files', [self.create_attachment_file()])
        request.POST = {'template_type': 'plain'}

        response = upload_audio(request)

        # View succeeds; returns count of locally saved files (1)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['attachment_count'], 1)
        # View creates ItemFile immediately; task runs sync but Drive fails, so storage_url stays empty
        item_id = uuid.UUID(data['item_id'])
        item = IngestItem.objects.get(id=item_id)
        attachments = ItemFile.objects.filter(item=item, role='attachment')
        self.assertEqual(attachments.count(), 1)
        self.assertEqual(attachments.first().storage_url, '')

    @patch('src.recordings.views.AudioChunker')
    @patch('src.recordings.views.process_audio_ingest')
    @patch('src.recordings.views.verify_drive_permissions')
    @patch('src.recordings.views.upload_local_file_to_user_drive_folder')
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_creates_itemfile_when_drive_fails(self, mock_upload_drive, mock_verify, mock_process_task, mock_chunker_cls):
        """ItemFile exists with empty storage_url when Drive upload fails."""
        from src.common.google_account.auth import GoogleAuthError

        mock_chunker_cls.return_value.get_audio_duration.return_value = 1.0
        mock_verify.return_value = True
        mock_upload_drive.side_effect = GoogleAuthError("Auth failed")

        request = self.factory.post('/voice/upload/')
        request.user = self.user
        request.FILES['audio'] = self.create_audio_file()
        request.FILES.setlist('files', [self.create_attachment_file('doc.pdf')])
        request.POST = {'template_type': 'plain'}

        response = upload_audio(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['attachment_count'], 1)

        item_id = uuid.UUID(data['item_id'])
        item = IngestItem.objects.get(id=item_id)
        attachments = ItemFile.objects.filter(item=item, role='attachment')
        self.assertEqual(attachments.count(), 1)
        att = attachments.first()
        self.assertEqual(att.filename, 'doc.pdf')
        self.assertEqual(att.storage_url, '')
        self.assertIsNotNone(att.bytes)
        self.assertGreater(att.bytes, 0)

    @patch('src.recordings.views.process_audio_ingest')
    @override_settings(
        AUDIO_TEMP_PATH=tempfile.gettempdir(),
        STORAGE_AUDIO_TEMP_PATH=tempfile.gettempdir(),
    )
    def test_upload_audio_missing_audio_file(self, mock_process_task):
        """Test that upload_audio fails gracefully without audio."""
        request = self.factory.post('/voice/upload/')
        request.user = self.user
        request.POST = {'template_type': 'plain'}

        response = upload_audio(request)
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('error', data)

    def test_item_file_attachment_role(self):
        """Test that ItemFile attachment role is correctly set."""
        item = IngestItem.objects.create(
            id=uuid.uuid4(),
            user=self.user,
            provider=Provider.MANUAL,
            item_type=ItemType.AUDIO,
            template_type=TemplateType.PLAIN,
            title="Test Recording"
        )

        attachment = ItemFile.objects.create(
            user=self.user,
            item=item,
            role='attachment',
            filename='test_file.pdf',
            mime_type='application/pdf',
            storage_url='https://drive.google.com/file/d/test-id/view',
            bytes=5000,
        )
        
        # Verify
        self.assertEqual(attachment.role, 'attachment')
        self.assertEqual(attachment.mime_type, 'application/pdf')
        self.assertIsNotNone(attachment.storage_url)


class BatchFileUploadToDriveTestCase(TestCase):
    """Tests for batch file upload to Google Drive with DB tracking."""

    def setUp(self):
        """Set up test fixtures."""
        self.factory = _CsrfBypassRequestFactory()

        self.user = User.objects.create_user(
            email="upload@example.com",
            password="testpass123",
        )
        self.user.is_active = True
        self.user.save()

        UserPreferences.objects.get_or_create(user=self.user)

    def _make_file(self, filename="doc.pdf", size=500):
        """Create a fake uploaded file."""
        data = b"x" * size
        return InMemoryUploadedFile(
            file=BytesIO(data),
            field_name="files",
            name=filename,
            content_type="application/pdf",
            size=len(data),
            charset=None,
        )

    @patch("src.recordings.views.verify_drive_permissions")
    @patch("src.recordings.views.upload_file_to_user_drive_folder")
    def test_upload_files_creates_ingest_item_and_item_files(
        self, mock_upload_drive, mock_verify
    ):
        """Batch upload creates one IngestItem and one ItemFile per file."""
        mock_verify.return_value = True
        mock_upload_drive.return_value = {
            "id": "drive-id-1",
            "name": "doc.pdf",
            "webViewLink": "https://drive.google.com/file/d/drive-id-1/view",
            "parent_folder_id": "folder-abc",
        }

        request = self.factory.post("/voice/upload-to-drive/")
        request.user = self.user
        request.FILES.setlist("files", [self._make_file("a.pdf"), self._make_file("b.txt")])

        response = upload_file_to_drive(request)

        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertIn("item_id", data)
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["files"]), 2)

        item = IngestItem.objects.get(id=data["item_id"])
        self.assertEqual(item.item_type, ItemType.FILE)
        self.assertEqual(item.status, IngestStatus.PROCESSED)
        self.assertEqual(item.provider, Provider.MANUAL)
        self.assertEqual(item.user, self.user)

        attachments = ItemFile.objects.filter(item=item)
        self.assertEqual(attachments.count(), 2)
        for att in attachments:
            self.assertEqual(att.role, FileRole.ATTACHMENT)
            self.assertIn("drive.google.com", att.storage_url)
            self.assertEqual(att.drive_folder_id, "folder-abc")

    def test_upload_files_no_files_returns_400(self):
        """POST with no files returns 400."""
        request = self.factory.post("/voice/upload-to-drive/")
        request.user = self.user

        response = upload_file_to_drive(request)

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data["error"], "no_file")

    @patch("src.recordings.views.verify_drive_permissions")
    def test_upload_files_no_drive_returns_403(self, mock_verify):
        """POST when user lacks Drive permissions returns 403."""
        mock_verify.return_value = False

        request = self.factory.post("/voice/upload-to-drive/")
        request.user = self.user
        request.FILES.setlist("files", [self._make_file()])

        response = upload_file_to_drive(request)

        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content)
        self.assertEqual(data["error"], "drive_not_connected")

    @patch("src.recordings.views.verify_drive_permissions")
    @patch("src.recordings.views.upload_file_to_user_drive_folder")
    def test_upload_files_drive_auth_error_returns_503(
        self, mock_upload_drive, mock_verify
    ):
        """GoogleAuthError during upload returns 503 and rolls back IngestItem."""
        from src.common.google_account.auth import GoogleAuthError

        mock_verify.return_value = True
        mock_upload_drive.side_effect = GoogleAuthError("Token expired")

        request = self.factory.post("/voice/upload-to-drive/")
        request.user = self.user
        request.FILES.setlist("files", [self._make_file()])

        response = upload_file_to_drive(request)

        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content)
        self.assertEqual(data["error"], "drive_auth_failed")

        # IngestItem should have been rolled back
        self.assertEqual(IngestItem.objects.filter(item_type=ItemType.FILE).count(), 0)

    @patch("src.recordings.views.verify_drive_permissions")
    @patch("src.recordings.views.upload_file_to_user_drive_folder")
    def test_upload_files_content_is_stored_plaintext(self, mock_upload_drive, mock_verify):
        """Title and content_text on IngestItem are stored as plaintext."""
        mock_verify.return_value = True
        mock_upload_drive.return_value = {
            "id": "drive-id-2",
            "name": "secret.pdf",
            "webViewLink": "https://drive.google.com/file/d/drive-id-2/view",
            "parent_folder_id": "folder-xyz",
        }

        request = self.factory.post("/voice/upload-to-drive/")
        request.user = self.user
        request.FILES.setlist("files", [self._make_file("secret.pdf")])

        response = upload_file_to_drive(request)

        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)

        item = IngestItem.objects.get(id=data["item_id"])

        self.assertNotEqual(item.title, "")
        self.assertIn("File Upload", item.title)
        self.assertNotEqual(item.content_text, "")

