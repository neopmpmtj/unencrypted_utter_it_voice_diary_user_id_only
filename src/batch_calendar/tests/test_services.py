"""Tests for batch calendar services: delete_batch_calendar_for_item and timezone threading."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser, UserFeatureConfig, UserPreferences
from src.batch_calendar.config_batch_calendar.batch_calendar_config import BatchCalendarConfig
from src.batch_calendar.models import BatchCalendarEvent, BatchCalendarRequest, BatchEventStatus
from src.ingestion.models import IngestItem, IngestStatus

from src.batch_calendar.services import delete_batch_calendar_for_item


class DeleteBatchCalendarForItemTests(TestCase):
    """Tests for delete_batch_calendar_for_item when IngestItem is deleted."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="batchdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        try:
            self.user_config = UserFeatureConfig.get_for_user(self.user)
        except Exception:
            self.user_config = UserFeatureConfig.objects.create(
                user=self.user,
                enable_calendar_integration=True,
            )
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
        )

    @patch("src.batch_calendar.calendar_client.delete_event")
    def test_deletes_from_google_and_removes_db_records(self, mock_delete_google):
        mock_delete_google.return_value = True
        batch = BatchCalendarRequest.objects.create(
            user=self.user,
            ingest_item=self.item,
            input_text="book meeting",
            parsed_events_json=[{"summary": "Meeting"}],
            status="confirmed",
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=0,
            event_data={},
            summary="Meeting",
            google_event_id="gid123",
            status=BatchEventStatus.SUCCESS,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=1,
            event_data={},
            summary="Meeting 2",
            google_event_id="gid456",
            status=BatchEventStatus.SUCCESS,
        )

        delete_batch_calendar_for_item(self.item)

        self.assertEqual(mock_delete_google.call_count, 2)
        self.assertFalse(BatchCalendarRequest.objects.filter(ingest_item=self.item).exists())
        self.assertFalse(BatchCalendarEvent.objects.filter(batch_request=batch).exists())

    @patch("src.batch_calendar.calendar_client.delete_event")
    def test_no_batch_skips_google_delete(self, mock_delete_google):
        delete_batch_calendar_for_item(self.item)
        mock_delete_google.assert_not_called()



class ExtractBatchEventsTimezoneTests(TestCase):
    """Tests for timezone threading in extract_batch_events."""

    def _make_mock_response(self, events):
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({"events": events})
        mock_resp.usage_metadata = None
        return mock_resp

    @patch("src.batch_calendar.services.OpenAI")
    def test_user_timezone_passed_to_prompt(self, mock_openai_cls):
        """The prompt sent to LLM contains the user's timezone, not the config default."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"events": []}'))]
        mock_response.usage = MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(
            BatchCalendarConfig,
            "get_prompt",
            wraps=lambda text, date, time, timezone=None: "",
        ) as mock_prompt:
            from src.batch_calendar.services import extract_batch_events
            extract_batch_events("meeting at 3pm", user_timezone="Europe/Paris")
            _, kwargs = mock_prompt.call_args
            self.assertEqual(kwargs.get("timezone"), "Europe/Paris")

    @patch("src.batch_calendar.services.OpenAI")
    def test_invalid_timezone_falls_back_to_config_default(self, mock_openai_cls):
        """An unrecognised timezone string falls back to config.default_timezone."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"events": []}'))]
        mock_response.usage = MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(
            BatchCalendarConfig,
            "get_prompt",
            wraps=lambda text, date, time, timezone=None: "",
        ) as mock_prompt:
            from src.batch_calendar.services import extract_batch_events
            extract_batch_events("meeting at 3pm", user_timezone="Fake/Zone")
            _, kwargs = mock_prompt.call_args
            # Falls back to config default (Europe/Lisbon)
            self.assertEqual(kwargs.get("timezone"), "Europe/Lisbon")
