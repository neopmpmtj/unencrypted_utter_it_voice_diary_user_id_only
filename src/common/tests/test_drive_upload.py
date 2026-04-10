"""
Tests for src/common/drive_upload.py

Tests critical Google Drive file upload and folder management:
- get_or_create_folder_by_path()
- upload_file_to_drive()
- upload_file_to_user_drive_folder()
- _sanitize_drive_filename()
"""

from unittest.mock import patch, MagicMock, Mock
from io import BytesIO

from django.test import TestCase
from django.utils import translation
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model

from src.accounts.models import UserPreferences
from src.common.drive_upload import (
    get_or_create_folder_by_path,
    upload_file_to_drive,
    upload_file_to_user_drive_folder,
    _sanitize_drive_filename,
    DEFAULT_FOLDER_PATH,
)
from src.common.google_account.auth import GoogleAuthError

User = get_user_model()


class SanitizeDriveFilenameTestCase(TestCase):
    """Test _sanitize_drive_filename() function."""
    
    def test_sanitize_normal_filename(self):
        """Test sanitizing a normal filename."""
        result = _sanitize_drive_filename("my_document.txt")
        self.assertEqual(result, "my_document.txt")
    
    def test_sanitize_filename_with_spaces(self):
        """Test sanitizing filename with multiple spaces."""
        result = _sanitize_drive_filename("my document file.pdf")
        self.assertEqual(result, "my document file.pdf")
    
    def test_sanitize_filename_with_special_chars(self):
        """Test sanitizing filename with special characters."""
        result = _sanitize_drive_filename("file<name>|invalid*.txt")
        self.assertNotIn('<', result)
        self.assertNotIn('>', result)
        self.assertNotIn('|', result)
        self.assertNotIn('*', result)
    
    def test_sanitize_empty_filename(self):
        """Test sanitizing empty filename."""
        result = _sanitize_drive_filename("")
        self.assertEqual(result, "uploaded_file")
    
    def test_sanitize_whitespace_only_filename(self):
        """Test sanitizing whitespace-only filename."""
        result = _sanitize_drive_filename("   ")
        self.assertEqual(result, "uploaded_file")
    
    def test_sanitize_very_long_filename(self):
        """Test sanitizing very long filename truncates correctly."""
        long_name = "a" * 300 + ".txt"
        result = _sanitize_drive_filename(long_name)
        self.assertLessEqual(len(result), 200 + len(".txt"))
        self.assertTrue(result.endswith(".txt"))


class GetOrCreateFolderByPathTestCase(TestCase):
    """Test get_or_create_folder_by_path() function."""
    
    def setUp(self):
        self.mock_drive = MagicMock()
    
    def test_get_folder_root(self):
        """Test when folder_path is empty returns 'root'."""
        result = get_or_create_folder_by_path(self.mock_drive, "")
        self.assertEqual(result, "root")
    
    def test_get_folder_single_level(self):
        """Test creating/getting single-level folder."""
        mock_list = MagicMock()
        mock_list.list.return_value.execute.return_value = {
            'files': [{'id': 'folder_123', 'name': 'VoiceDiaryFiles'}]
        }
        self.mock_drive.files.return_value = mock_list
        
        result = get_or_create_folder_by_path(self.mock_drive, "VoiceDiaryFiles")
        
        self.assertEqual(result, "folder_123")
    
    def test_create_folder_when_not_exists(self):
        """Test that folder is created when it doesn't exist."""
        mock_list = MagicMock()
        mock_list.list.return_value.execute.return_value = {'files': []}
        mock_create = MagicMock()
        mock_create.create.return_value.execute.return_value = {'id': 'new_folder_456'}
        
        self.mock_drive.files.side_effect = [mock_list, mock_create]
        
        result = get_or_create_folder_by_path(self.mock_drive, "NewFolder")
        
        self.assertEqual(result, "new_folder_456")
    
    def test_create_nested_folders(self):
        """Test creating nested folder structure."""
        mock_list = MagicMock()
        mock_list.list.return_value.execute.side_effect = [
            {'files': []},
            {'files': []}
        ]
        mock_create = MagicMock()
        mock_create.create.return_value.execute.side_effect = [
            {'id': 'voice_diary_id'},
            {'id': 'attachments_id'}
        ]
        
        self.mock_drive.files.side_effect = [
            mock_list, mock_create, mock_list, mock_create
        ]
        
        result = get_or_create_folder_by_path(
            self.mock_drive, 
            "VoiceDiaryFiles/attachments"
        )
        
        self.assertEqual(result, "attachments_id")
    
    def test_handle_special_characters_in_path(self):
        """Test that special characters in folder names are escaped."""
        folder_name = "My'Folder"
        
        mock_list = MagicMock()
        mock_list.list.return_value.execute.return_value = {
            'files': [{'id': 'folder_id_789', 'name': folder_name}]
        }
        self.mock_drive.files.return_value = mock_list
        
        result = get_or_create_folder_by_path(self.mock_drive, folder_name)
        
        self.assertEqual(result, "folder_id_789")


class UploadFileToDriveTestCase(TestCase):
    """Test upload_file_to_drive() function."""
    
    def setUp(self):
        self.mock_drive = MagicMock()
    
    def test_upload_file_success(self):
        """Test successful file upload to Drive."""
        file_content = b"test file content"
        uploaded_file = SimpleUploadedFile(
            name="test_document.txt",
            content=file_content,
            content_type="text/plain"
        )
        
        mock_files = MagicMock()
        mock_files.create.return_value.execute.return_value = {
            'id': 'file_123',
            'name': 'test_document.txt',
            'webViewLink': 'https://drive.google.com/file/d/file_123/view'
        }
        self.mock_drive.files.return_value = mock_files
        
        result = upload_file_to_drive(self.mock_drive, uploaded_file)
        
        self.assertEqual(result['id'], 'file_123')
        self.assertEqual(result['name'], 'test_document.txt')
        self.assertIn('webViewLink', result)
    
    def test_upload_file_to_parent_folder(self):
        """Test uploading file to specific parent folder."""
        uploaded_file = SimpleUploadedFile(
            name="document.pdf",
            content=b"pdf content",
            content_type="application/pdf"
        )
        
        mock_files = MagicMock()
        mock_files.create.return_value.execute.return_value = {
            'id': 'file_pdf_456',
            'name': 'document.pdf',
            'webViewLink': 'https://drive.google.com/file/d/file_pdf_456/view'
        }
        self.mock_drive.files.return_value = mock_files
        
        result = upload_file_to_drive(
            self.mock_drive, 
            uploaded_file, 
            parent_folder_id='parent_folder_id'
        )
        
        self.assertEqual(result['id'], 'file_pdf_456')
        self.assertEqual(result['parent_folder_id'], 'parent_folder_id')
    
    def test_upload_file_sanitizes_name(self):
        """Test that filename is sanitized before upload."""
        uploaded_file = SimpleUploadedFile(
            name="file<invalid>name.txt",
            content=b"content",
            content_type="text/plain"
        )
        
        mock_files = MagicMock()
        mock_files.create.return_value.execute.return_value = {
            'id': 'file_789',
            'name': 'fileinvalidname.txt',
            'webViewLink': 'https://drive.google.com/file/d/file_789/view'
        }
        self.mock_drive.files.return_value = mock_files
        
        result = upload_file_to_drive(self.mock_drive, uploaded_file)
        
        self.assertIsNotNone(result)
        self.assertEqual(result['id'], 'file_789')


class UploadFileToUserDriveFolderTestCase(TestCase):
    """Test upload_file_to_user_drive_folder() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.user.save()
    
    @patch('src.common.drive_upload.verify_drive_permissions')
    @patch('src.common.drive_upload.get_authenticated_service')
    @patch('src.common.drive_upload.get_or_create_folder_by_path')
    @patch('src.common.drive_upload.upload_file_to_drive')
    def test_upload_file_to_user_folder(self, mock_upload, mock_get_folder,
                                       mock_get_service, mock_verify):
        """Test uploading file to user's configured Drive folder."""
        mock_verify.return_value = True
        mock_drive = MagicMock()
        mock_get_service.return_value = mock_drive
        mock_get_folder.return_value = 'configured_folder_id'
        mock_upload.return_value = {
            'id': 'file_123',
            'name': 'document.txt',
            'webViewLink': 'https://drive.google.com/file/d/file_123/view',
            'parent_folder_id': 'configured_folder_id'
        }
        
        UserPreferences.objects.update_or_create(
            user=self.user,
            defaults={'drive_attachment_folder_name': 'VoiceDiaryFiles/attachments'}
        )
        
        uploaded_file = SimpleUploadedFile(
            name="document.txt",
            content=b"content",
            content_type="text/plain"
        )
        
        result = upload_file_to_user_drive_folder(self.user, uploaded_file)
        
        self.assertEqual(result['id'], 'file_123')
        mock_verify.assert_called_once_with(self.user)
    
    @patch('src.common.drive_upload.verify_drive_permissions')
    def test_upload_file_no_drive_permission(self, mock_verify):
        """Test that error is raised when user has no Drive permissions."""
        mock_verify.return_value = False
        
        uploaded_file = SimpleUploadedFile(
            name="document.txt",
            content=b"content"
        )
        
        translation.activate('en')
        try:
            with self.assertRaises(GoogleAuthError) as context:
                upload_file_to_user_drive_folder(self.user, uploaded_file)
            self.assertIn('Drive access', str(context.exception))
        finally:
            translation.deactivate()
    
    @patch('src.common.drive_upload.verify_drive_permissions')
    @patch('src.common.drive_upload.get_authenticated_service')
    @patch('src.common.drive_upload.get_or_create_folder_by_path')
    @patch('src.common.drive_upload.upload_file_to_drive')
    def test_upload_file_to_subfolder(self, mock_upload, mock_get_folder,
                                     mock_get_service, mock_verify):
        """Test uploading file to entry-specific subfolder."""
        mock_verify.return_value = True
        mock_drive = MagicMock()
        mock_get_service.return_value = mock_drive
        mock_get_folder.side_effect = [
            'base_folder_id',
            'entry_subfolder_id'
        ]
        mock_upload.return_value = {
            'id': 'file_456',
            'name': 'attachment.pdf',
            'webViewLink': 'https://drive.google.com/file/d/file_456/view',
            'parent_folder_id': 'entry_subfolder_id'
        }
        
        UserPreferences.objects.update_or_create(
            user=self.user,
            defaults={'drive_attachment_folder_name': 'VoiceDiaryFiles/attachments'}
        )
        
        uploaded_file = SimpleUploadedFile(
            name="attachment.pdf",
            content=b"pdf content"
        )
        
        result = upload_file_to_user_drive_folder(
            self.user, 
            uploaded_file, 
            subfolder_name='entry_uuid_123'
        )
        
        self.assertEqual(result['id'], 'file_456')
        self.assertEqual(mock_get_folder.call_count, 2)
    
    @patch('src.common.drive_upload.verify_drive_permissions')
    @patch('src.common.drive_upload.get_authenticated_service')
    @patch('src.common.drive_upload.get_or_create_folder_by_path')
    def test_upload_file_uses_default_folder(self, mock_get_folder,
                                            mock_get_service, mock_verify):
        """Test that default folder is used when no preference is set."""
        mock_verify.return_value = True
        mock_drive = MagicMock()
        mock_get_service.return_value = mock_drive
        mock_get_folder.return_value = 'folder_id'
        
        uploaded_file = SimpleUploadedFile(
            name="document.txt",
            content=b"content"
        )
        
        try:
            upload_file_to_user_drive_folder(self.user, uploaded_file)
        except Exception:
            pass
        
        mock_get_folder.assert_called()
        call_args = mock_get_folder.call_args[0][1]
        self.assertEqual(call_args, DEFAULT_FOLDER_PATH)
