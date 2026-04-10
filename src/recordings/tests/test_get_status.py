"""
Tests for get_status and get_pending_status views.
"""

import json
import uuid
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem, IngestJob, IngestStatus, JobType, JobStatus


class GetStatusPlaintextTests(TestCase):
    """Tests for get_status view returning plaintext IngestItem content."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="statustest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_get_pending_status_in_progress_when_no_redis_data(self):
        """get_pending_status returns in_progress when Redis has no data."""
        temp_id = uuid.uuid4()
        url = reverse('recordings:status_pending', args=[str(temp_id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'in_progress')

    @patch('src.recordings.views.get_pending_transcription')
    def test_get_pending_status_ready_when_transcription_stored(self, mock_get):
        """get_pending_status returns ready with transcribed_text when Redis has result."""
        mock_get.return_value = {
            'transcription': 'Hello world',
            'detected_language': 'en',
        }
        temp_id = uuid.uuid4()
        url = reverse('recordings:status_pending', args=[str(temp_id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['transcribed_text'], 'Hello world')
        self.assertEqual(data['detected_language'], 'en')

    @patch('src.recordings.views.get_pending_transcription')
    def test_get_pending_status_discarded(self, mock_get):
        """get_pending_status returns discarded when guard rejected."""
        mock_get.return_value = {'status': 'discarded', 'reason': 'No speech detected'}
        temp_id = uuid.uuid4()
        url = reverse('recordings:status_pending', args=[str(temp_id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'discarded')
        self.assertEqual(data['reason'], 'No speech detected')

    @patch('src.recordings.views.get_pending_transcription')
    def test_get_pending_status_error(self, mock_get):
        """get_pending_status returns error when task failed."""
        mock_get.return_value = {'status': 'error', 'error': 'Transcription failed'}
        temp_id = uuid.uuid4()
        url = reverse('recordings:status_pending', args=[str(temp_id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['error'], 'Transcription failed')

    def test_get_status_returns_content_text(self):
        """get_status should return content_text as stored."""
        plaintext_content = "This is diary content"
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            content_text=plaintext_content,
            summary_text="",
            title="test title",
            status=IngestStatus.PROCESSED,
        )

        url = reverse('recordings:status', args=[str(item.id)])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['content_text'], plaintext_content)

    def test_get_status_returns_none_for_non_processed_item(self):
        """get_status should return null content_text for non-processed items."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text="",
            summary_text="",
            title="Processing audio",
            status=IngestStatus.NEW,
        )

        url = reverse('recordings:status', args=[str(item.id)])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data['content_text'])

    def test_get_status_returns_calendar_conflict_when_parse_calendar_job_has_conflict(self):
        """get_status should return calendar_conflict and confirmation_url when PARSE_CALENDAR job has conflict in checkpoint_data."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text="Book physio Monday 3pm",
            summary_text="",
            title="test title",
            status=IngestStatus.TAGGED,
        )
        IngestJob.objects.create(
            user=self.user,
            item=item,
            job_type=JobType.PARSE_CALENDAR,
            status=JobStatus.DONE,
            checkpoint_data={
                "conflict": True,
                "confirmation_url": "/batch-calendar/confirm/abc-123/",
                "batch_id": "abc-123",
            },
        )

        url = reverse('recordings:status', args=[str(item.id)])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['calendar_conflict'])
        self.assertEqual(data['confirmation_url'], "/batch-calendar/confirm/abc-123/")


class UpdateEntryContentTests(TestCase):
    """Tests for update_entry_content view."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="updatetest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_update_entry_content_updates_plaintext(self):
        """update_entry_content should update content_text."""
        plaintext = "Original content"

        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text=plaintext,
            summary_text="",
            title="test title",
            status=IngestStatus.PROCESSED,
        )

        url = reverse('recordings:update_entry', args=[str(item.id)])
        new_content = "Updated content after edit"
        response = self.client.patch(
            url,
            data=json.dumps({'content_text': new_content}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))

        item.refresh_from_db()
        self.assertEqual(item.content_text, new_content)

    def test_update_entry_content_allows_tagged_status(self):
        """update_entry_content should accept items with TAGGED status."""
        plaintext = "Tagged content"

        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text=plaintext,
            summary_text="",
            title="test title",
            status=IngestStatus.TAGGED,
        )

        url = reverse('recordings:update_entry', args=[str(item.id)])
        new_content = "Updated tagged content"
        response = self.client.patch(
            url,
            data=json.dumps({'content_text': new_content}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))

        item.refresh_from_db()
        self.assertEqual(item.content_text, new_content)

    def test_update_entry_content_rejects_new_status(self):
        """update_entry_content should reject items with NEW or ERROR status."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text="",
            summary_text="",
            title="",
            status=IngestStatus.NEW,
        )

        url = reverse('recordings:update_entry', args=[str(item.id)])
        response = self.client.patch(
            url,
            data=json.dumps({'content_text': 'new'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_update_entry_content_rejects_empty_or_whitespace_only(self):
        """update_entry_content should reject empty or whitespace-only content to prevent data loss."""
        plaintext = "Original content"

        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            content_text=plaintext,
            summary_text="",
            title="test title",
            status=IngestStatus.PROCESSED,
        )

        url = reverse('recordings:update_entry', args=[str(item.id)])

        for invalid_content in ('', '   ', '\t\n', ' \n\t '):
            with self.subTest(invalid_content=repr(invalid_content)):
                response = self.client.patch(
                    url,
                    data=json.dumps({'content_text': invalid_content}),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 400, f"Expected 400 for {repr(invalid_content)}")
                item.refresh_from_db()
                self.assertEqual(item.content_text, plaintext, "Content should not be modified on validation failure")
