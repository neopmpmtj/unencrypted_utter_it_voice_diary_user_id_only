"""
Tests for recent recordings tool (Listen to Recordings).
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem, ItemFile, FileRole, IngestStatus, ItemType


class ListRecordingsViewTests(TestCase):
    """Tests for list_recordings view."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email='recent@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.interface_language = 'en'
        prefs.save()

    def test_list_requires_login(self):
        """list_recordings should redirect to login when not authenticated."""
        url = reverse('recent_recordings:list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_list_returns_200_when_authenticated(self):
        """list_recordings should return 200 when user is logged in."""
        self.client.force_login(self.user)
        url = reverse('recent_recordings:list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_list_shows_empty_state_when_no_recordings(self):
        """Page should show empty state when no recordings in retention window."""
        self.client.force_login(self.user)
        url = reverse('recent_recordings:list')
        response = self.client.get(url)
        self.assertContains(response, 'No recording available')
        self.assertContains(response, 'Listen to Last Recording')

    def test_list_shows_recordings_with_audio_file(self):
        """Page should list recordings that have existing audio files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            user_dir = base / str(self.user.id)
            user_dir.mkdir(parents=True)
            audio_path = user_dir / 'test-audio.webm'
            audio_path.write_bytes(b'fake webm audio')

            item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                status=IngestStatus.PROCESSED,
                is_deleted=False,
                occurred_at=timezone.now(),
                title='',
            )
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ORIGINAL,
                storage_url=str(audio_path),
            )

            with patch('src.vd_tools.recent_recordings.views.get_config') as mock_config:
                mock_storage = type('Storage', (), {'audio_temp_path': str(base)})()
                mock_config.return_value = type('Config', (), {'storage': mock_storage})()

                self.client.force_login(self.user)
                url = reverse('recent_recordings:list')
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Voice Recording')
                self.assertContains(response, 'preload="none"')

    def test_list_shows_only_last_recording(self):
        """Page should show only the single most recent recording, not older ones."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            user_dir = base / str(self.user.id)
            user_dir.mkdir(parents=True)

            older_path = user_dir / 'older.webm'
            older_path.write_bytes(b'older audio')
            newer_path = user_dir / 'newer.webm'
            newer_path.write_bytes(b'newer audio')

            now = timezone.now()
            older_item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                status=IngestStatus.PROCESSED,
                is_deleted=False,
                occurred_at=now - timezone.timedelta(minutes=30),
                title='',
            )
            ItemFile.objects.create(
                user=self.user,
                item=older_item,
                role=FileRole.ORIGINAL,
                storage_url=str(older_path),
            )
            newer_item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                status=IngestStatus.PROCESSED,
                is_deleted=False,
                occurred_at=now - timezone.timedelta(minutes=5),
                title='',
            )
            ItemFile.objects.create(
                user=self.user,
                item=newer_item,
                role=FileRole.ORIGINAL,
                storage_url=str(newer_path),
            )

            with patch('src.vd_tools.recent_recordings.views.get_config') as mock_config:
                mock_storage = type('Storage', (), {'audio_temp_path': str(base)})()
                mock_config.return_value = type('Config', (), {'storage': mock_storage})()

                self.client.force_login(self.user)
                url = reverse('recent_recordings:list')
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, str(newer_item.id))
                self.assertNotContains(response, str(older_item.id))

    def test_list_excludes_recordings_outside_retention_window(self):
        """Recordings older than retention_hours are not shown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            user_dir = base / str(self.user.id)
            user_dir.mkdir(parents=True)
            audio_path = user_dir / 'old-audio.webm'
            audio_path.write_bytes(b'old audio')

            with patch('src.vd_tools.recent_recordings.views.get_audio_retention_hours', return_value=1):
                item = IngestItem.objects.create(
                    user=self.user,
                    item_type=ItemType.AUDIO,
                    status=IngestStatus.PROCESSED,
                    is_deleted=False,
                    occurred_at=timezone.now() - timezone.timedelta(hours=2),
                    title='',
                )
                ItemFile.objects.create(
                    user=self.user,
                    item=item,
                    role=FileRole.ORIGINAL,
                    storage_url=str(audio_path),
                )

                with patch('src.vd_tools.recent_recordings.views.get_config') as mock_config:
                    mock_storage = type('Storage', (), {'audio_temp_path': str(base)})()
                    mock_config.return_value = type('Config', (), {'storage': mock_storage})()

                    self.client.force_login(self.user)
                    url = reverse('recent_recordings:list')
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'No recording available')
                    self.assertNotContains(response, str(item.id))


class ServeAudioViewTests(TestCase):
    """Tests for serve_audio view."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email='serve@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.interface_language = 'en'
        prefs.save()

    def test_serve_requires_login(self):
        """serve_audio should return 404 when not authenticated (login redirect)."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type=ItemType.AUDIO,
            is_deleted=False,
        )
        ItemFile.objects.create(
            user=self.user,
            item=item,
            role=FileRole.ORIGINAL,
            storage_url='/nonexistent/path.webm',
        )
        url = reverse('recent_recordings:serve_audio', args=[item.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_serve_404_for_other_users_item(self):
        """serve_audio should return 404 when item belongs to another user."""
        other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='Pass123',
        )
        other_user.is_email_verified = True
        other_user.save()

        item = IngestItem.objects.create(
            user=other_user,
            item_type=ItemType.AUDIO,
            is_deleted=False,
        )
        ItemFile.objects.create(
            user=other_user,
            item=item,
            role=FileRole.ORIGINAL,
            storage_url='/nonexistent/path.webm',
        )

        self.client.force_login(self.user)
        url = reverse('recent_recordings:serve_audio', args=[item.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_serve_404_when_file_missing(self):
        """serve_audio should return 404 when audio file does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            fake_path = base / str(self.user.id) / 'nonexistent.webm'

            item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                is_deleted=False,
            )
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ORIGINAL,
                storage_url=str(fake_path),
            )

            with patch('src.vd_tools.recent_recordings.views.get_config') as mock_config:
                mock_storage = type('Storage', (), {'audio_temp_path': str(base)})()
                mock_config.return_value = type('Config', (), {'storage': mock_storage})()

                self.client.force_login(self.user)
                url = reverse('recent_recordings:serve_audio', args=[item.id])
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)

    def test_serve_404_when_item_outside_retention_window(self):
        """serve_audio returns 404 when recording is older than retention_hours."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            user_dir = base / str(self.user.id)
            user_dir.mkdir(parents=True)
            audio_path = user_dir / 'old-audio.webm'
            audio_path.write_bytes(b'old audio')

            with patch('src.vd_tools.recent_recordings.views.get_audio_retention_hours', return_value=1):
                item = IngestItem.objects.create(
                    user=self.user,
                    item_type=ItemType.AUDIO,
                    is_deleted=False,
                    occurred_at=timezone.now() - timezone.timedelta(hours=2),
                )
                ItemFile.objects.create(
                    user=self.user,
                    item=item,
                    role=FileRole.ORIGINAL,
                    storage_url=str(audio_path),
                )

                with patch('src.vd_tools.recent_recordings.views.get_config') as mock_config:
                    mock_storage = type('Storage', (), {'audio_temp_path': str(base)})()
                    mock_config.return_value = type('Config', (), {'storage': mock_storage})()

                    self.client.force_login(self.user)
                    url = reverse('recent_recordings:serve_audio', args=[item.id])
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 404)

    def test_serve_returns_audio_when_file_exists(self):
        """serve_audio should stream file when it exists and user owns item."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            user_dir = base / str(self.user.id)
            user_dir.mkdir(parents=True)
            audio_path = user_dir / 'test-audio.webm'
            audio_path.write_bytes(b'fake webm audio')

            item = IngestItem.objects.create(
                user=self.user,
                item_type=ItemType.AUDIO,
                is_deleted=False,
                occurred_at=timezone.now(),
            )
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ORIGINAL,
                storage_url=str(audio_path),
            )

            with patch('src.vd_tools.recent_recordings.views.get_config') as mock_config:
                mock_storage = type('Storage', (), {'audio_temp_path': str(base)})()
                mock_config.return_value = type('Config', (), {'storage': mock_storage})()

                self.client.force_login(self.user)
                url = reverse('recent_recordings:serve_audio', args=[item.id])
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'audio/webm')
                self.assertEqual(b''.join(response.streaming_content), b'fake webm audio')
