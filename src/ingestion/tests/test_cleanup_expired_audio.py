"""
Tests for cleanup_expired_audio_files task (garbage collection after retention period).
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem, ItemFile, FileRole, IngestStatus, ItemType
from src.ingestion.tasks import cleanup_expired_audio_files


class CleanupExpiredAudioFilesTests(TestCase):
    """Tests for cleanup_expired_audio_files Celery task."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='cleanup@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_deletes_audio_files_when_scheduled_at_in_past(self):
        """Items with audio_deletion_scheduled_at <= now have their audio files deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            audio_path = base / 'expired-audio.webm'
            audio_path.write_bytes(b'fake webm audio')

            item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                status=IngestStatus.PROCESSED,
                is_deleted=False,
                audio_deletion_scheduled_at=timezone.now() - timezone.timedelta(hours=1),
            )
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ORIGINAL,
                storage_url=str(audio_path),
            )

            self.assertTrue(audio_path.exists())

            deleted_count = cleanup_expired_audio_files()

            self.assertEqual(deleted_count, 1)
            self.assertFalse(audio_path.exists())
            item.refresh_from_db()
            self.assertIsNone(item.audio_deletion_scheduled_at)

    def test_does_not_delete_when_scheduled_at_in_future(self):
        """Items with audio_deletion_scheduled_at in the future are not deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            audio_path = base / 'future-audio.webm'
            audio_path.write_bytes(b'fake webm audio')

            item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                status=IngestStatus.PROCESSED,
                is_deleted=False,
                audio_deletion_scheduled_at=timezone.now() + timezone.timedelta(hours=1),
            )
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ORIGINAL,
                storage_url=str(audio_path),
            )

            deleted_count = cleanup_expired_audio_files()

            self.assertEqual(deleted_count, 0)
            self.assertTrue(audio_path.exists())
            item.refresh_from_db()
            self.assertIsNotNone(item.audio_deletion_scheduled_at)

    def test_ignores_items_without_deletion_scheduled(self):
        """Items with audio_deletion_scheduled_at=None are not touched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            audio_path = base / 'no-schedule.webm'
            audio_path.write_bytes(b'fake webm audio')

            item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                status=IngestStatus.PROCESSED,
                is_deleted=False,
                audio_deletion_scheduled_at=None,
            )
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ORIGINAL,
                storage_url=str(audio_path),
            )

            deleted_count = cleanup_expired_audio_files()

            self.assertEqual(deleted_count, 0)
            self.assertTrue(audio_path.exists())
