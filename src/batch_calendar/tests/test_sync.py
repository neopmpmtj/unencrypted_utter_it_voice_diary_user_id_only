"""
Tests for process_calendar_sync: CalendarEvent and BatchCalendarEvent handling.

For CalendarEvent: soft-deletes IngestItem and CalendarEvent when event deleted on Google.
For BatchCalendarEvent: only cancels the event, does NOT soft-delete IngestItem.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.batch_calendar.models import (
    BatchCalendarEvent,
    BatchCalendarRequest,
    BatchEventStatus,
    BatchRequestStatus,
    CalendarEvent,
    CalendarEventStatus,
    CalendarWatchChannel,
)
from src.batch_calendar.services import process_calendar_sync
from src.ingestion.models import IngestItem


class ProcessCalendarSyncBatchEventTests(TestCase):
    """Test that BatchCalendarEvent sync cancels event only, does NOT soft-delete IngestItem."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="syncbatch@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )
        self.batch = BatchCalendarRequest.objects.create(
            user=self.user,
            ingest_item=self.item,
            input_text="book meeting",
            parsed_events_json=[],
            status=BatchRequestStatus.CONFIRMED,
        )
        self.batch_ev = BatchCalendarEvent.objects.create(
            batch_request=self.batch,
            event_index=0,
            event_data={},
            summary="Meeting",
            google_event_id="evt_batch_gone",
            status=BatchEventStatus.SUCCESS,
        )
        self.channel = CalendarWatchChannel.objects.create(
            user=self.user,
            channel_id="ch-batch-test",
            resource_id="",
            calendar_id="primary",
            sync_token="tok_abc",
            expiration=None,
            is_active=True,
        )

    @patch("src.common.google_account.auth.get_authenticated_service")
    def test_batch_event_cancelled_on_google_deletion_ingest_item_unchanged(self, mock_get_service):
        """When BatchCalendarEvent's google_event_id is cancelled on Google, only event is cancelled."""
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "items": [{"id": "evt_batch_gone", "status": "cancelled"}],
            "nextSyncToken": "tok_new",
        }
        mock_events = MagicMock()
        mock_events.list.return_value = mock_list
        mock_get_service.return_value.events.return_value = mock_events

        result = process_calendar_sync("ch-batch-test")

        self.assertTrue(result["success"])
        self.assertEqual(result["deleted_count"], 1)

        self.batch_ev.refresh_from_db()
        self.assertEqual(self.batch_ev.status, BatchEventStatus.CANCELLED)

        self.item.refresh_from_db()
        self.assertFalse(self.item.is_deleted)
        self.assertIsNone(self.item.deleted_at)


class ProcessCalendarSyncCalendarEventTests(TestCase):
    """Test that CalendarEvent sync soft-deletes IngestItem and CalendarEvent."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="synccal@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )
        self.cal_event = CalendarEvent.objects.create(
            user=self.user,
            source_item=self.item,
            summary="Meeting",
            google_event_id="evt_cal_gone",
            status=CalendarEventStatus.SUCCESS,
        )
        self.channel = CalendarWatchChannel.objects.create(
            user=self.user,
            channel_id="ch-cal-test",
            resource_id="",
            calendar_id="primary",
            sync_token="tok_abc",
            expiration=None,
            is_active=True,
        )

    @patch("src.common.google_account.auth.get_authenticated_service")
    def test_calendar_event_soft_deletes_item_and_event_on_google_deletion(self, mock_get_service):
        """When CalendarEvent's google_event_id is cancelled on Google, both are soft-deleted."""
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "items": [{"id": "evt_cal_gone", "status": "cancelled"}],
            "nextSyncToken": "tok_new",
        }
        mock_events = MagicMock()
        mock_events.list.return_value = mock_list
        mock_get_service.return_value.events.return_value = mock_events

        result = process_calendar_sync("ch-cal-test")

        self.assertTrue(result["success"])
        self.assertEqual(result["deleted_count"], 1)

        self.item.refresh_from_db()
        self.assertTrue(self.item.is_deleted)
        self.assertIsNotNone(self.item.deleted_at)

        self.cal_event.refresh_from_db()
        self.assertTrue(self.cal_event.is_deleted)
        self.assertEqual(self.cal_event.status, CalendarEventStatus.CANCELLED)
