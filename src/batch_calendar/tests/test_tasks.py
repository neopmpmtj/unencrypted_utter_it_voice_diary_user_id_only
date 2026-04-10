"""Tests for batch calendar tasks: parsed_events_json count matches DB rows and FK chain."""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser, UserFeatureConfig, UserPreferences
from src.batch_calendar.models import BatchCalendarEvent, BatchCalendarRequest
from src.ingestion.models import IngestItem, IngestJob, IngestStatus, JobType, JobStatus

from src.batch_calendar.tasks import parse_batch_calendar_task

TZ_STR = "Europe/Lisbon"


def _make_event_dict(i: int) -> dict:
    base = datetime(2026, 3, 1, 10, 0, 0) + timedelta(hours=i)
    start_str = base.strftime("%Y-%m-%dT%H:%M:%S")
    end_dt = base + timedelta(minutes=30)
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "summary": f"Event {i + 1}",
        "start": {"dateTime": start_str, "timeZone": TZ_STR},
        "end": {"dateTime": end_str, "timeZone": TZ_STR},
    }


def _make_conflict_result(event_index: int, event_data: dict, is_available: bool = True) -> dict:
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(
        datetime(2026, 3, 1, 10, 0, 0) + timedelta(hours=event_index),
        tz,
    )
    end_dt = start_dt + timedelta(minutes=30)
    conflicting = [{"start": "2026-03-01T10:00:00", "end": "2026-03-01T10:30:00"}] if not is_available else []
    alt_slot = {"start": "2026-03-01T14:00:00", "end": "2026-03-01T14:30:00"}
    alt_slots = [alt_slot] if not is_available else []
    alt_by_day = (
        [
            {
                "date": "2026-03-01",
                "date_formatted": "Sun 01 Mar 2026",
                "slots": [{**alt_slot, "start_formatted": "Sun 01 Mar 2026 at 14:00", "end_formatted": "14:30", "flat_index": 0}],
            }
        ]
        if not is_available
        else []
    )
    return {
        "event_index": event_index,
        "event_data": event_data,
        "is_available": is_available,
        "conflicting_events": conflicting,
        "alternative_slots": alt_slots,
        "alternative_slots_by_day": alt_by_day,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }


class ParsedEventsCountMatchesDbRowsTests(TestCase):
    """Assert parsed_events_json length equals BatchCalendarEvent count and FK chain is correct."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="batch@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
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
        self.user_config.enable_calendar_integration = True
        self.user_config.save()

    def _run_task_with_n_events(self, n: int):
        events = [_make_event_dict(i) for i in range(n)]
        conflict_results = [_make_conflict_result(i, ev) for i, ev in enumerate(events)]
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
        )
        with patch("src.common.utils.content.get_item_title_and_content") as mock_decrypt:
            mock_decrypt.return_value = ("", "Book meeting Monday 10am")
            with patch("src.batch_calendar.tasks.extract_batch_events") as mock_extract:
                mock_extract.return_value = (events, None, {})
                with patch("src.batch_calendar.tasks.check_batch_availability") as mock_check:
                    mock_check.return_value = conflict_results
                    with patch("src.batch_calendar.tasks.insert_event") as mock_insert:
                        mock_insert.return_value = {
                            "id": "fake-id",
                            "htmlLink": "https://example.com",
                        }
                        parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])
                        return item, mock_insert

    def _assert_count_and_fk_chain(self, item, expected_count: int, mock_insert=None):
        batch = BatchCalendarRequest.objects.get(ingest_item=item)
        self.assertEqual(len(batch.parsed_events_json), expected_count)
        self.assertEqual(batch.events.count(), expected_count)
        self.assertEqual(batch.ingest_item, item)
        for ev in batch.events.all():
            self.assertEqual(ev.batch_request.ingest_item, item)
        if mock_insert is not None:
            self.assertEqual(
                mock_insert.call_count,
                expected_count,
                "insert_event must be called once per event (all records inserted into Google Calendar)",
            )

    def test_parsed_events_count_matches_db_rows_one_event(self):
        item, mock_insert = self._run_task_with_n_events(1)
        self._assert_count_and_fk_chain(item, 1, mock_insert)

    def test_parsed_events_count_matches_db_rows_multiple_events(self):
        for n in (2, 3, 4):
            item, mock_insert = self._run_task_with_n_events(n)
            self._assert_count_and_fk_chain(item, n, mock_insert)


class ParseBatchCalendarConflictPathTests(TestCase):
    """Assert conflict path sets job checkpoint and returns conflict/batch_id for frontend notification."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="batch_conflict@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
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
        self.user_config.enable_calendar_integration = True
        self.user_config.save()

    def test_conflict_path_sets_job_checkpoint_and_returns_conflict_batch_id(self):
        """When events have conflicts, task sets PARSE_CALENDAR job checkpoint with conflict/confirmation_url and returns conflict=True."""
        events = [_make_event_dict(i) for i in range(2)]
        conflict_results = [
            _make_conflict_result(0, events[0], is_available=False),
            _make_conflict_result(1, events[1], is_available=True),
        ]
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
        )
        with patch("src.common.utils.content.get_item_title_and_content") as mock_decrypt:
            mock_decrypt.return_value = ("", "Book physio Monday 10am and Tuesday 2pm")
            with patch("src.batch_calendar.tasks.extract_batch_events") as mock_extract:
                mock_extract.return_value = (events, None, {})
                with patch("src.batch_calendar.tasks.check_batch_availability") as mock_check:
                    mock_check.return_value = conflict_results
                    with patch("src.batch_calendar.tasks.broadcast_batch_calendar_status") as mock_broadcast:
                        with patch("src.batch_calendar.tasks.broadcast_complete"):
                            result = parse_batch_calendar_task.apply(args=[str(item.id), "", "en"]).result

        self.assertFalse(result.get("success"))
        self.assertTrue(result.get("conflict"))
        self.assertIn("batch_id", result)

        batch = BatchCalendarRequest.objects.get(ingest_item=item)
        self.assertEqual(str(batch.id), result["batch_id"])

        job = IngestJob.objects.filter(item=item, job_type=JobType.PARSE_CALENDAR).first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertTrue(job.checkpoint_data.get("conflict"))
        self.assertIn("/batch-calendar/confirm/", job.checkpoint_data.get("confirmation_url", ""))
        self.assertEqual(job.checkpoint_data.get("batch_id"), str(batch.id))

        mock_broadcast.assert_called()
        call_kwargs = mock_broadcast.call_args
        self.assertEqual(call_kwargs[0][2], "calendar_conflict")
        self.assertTrue(call_kwargs[1].get("extra_data", {}).get("conflict"))
        self.assertIn("confirmation_url", call_kwargs[1].get("extra_data", {}))


class UserTimezoneThreadingTests(TestCase):
    """Verify that parse_batch_calendar_task threads user timezone through the pipeline."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="tz@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.timezone = "Europe/Paris"
        prefs.onboarding_completed = True
        prefs.save()
        try:
            user_config = UserFeatureConfig.get_for_user(self.user)
        except Exception:
            user_config = UserFeatureConfig.objects.create(
                user=self.user,
                enable_calendar_integration=True,
            )
        user_config.enable_calendar_integration = True
        user_config.save()

    def _make_item(self):
        return IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
        )

    @patch("src.batch_calendar.tasks.insert_event")
    @patch("src.batch_calendar.tasks.check_batch_availability")
    @patch("src.batch_calendar.tasks.extract_batch_events")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_extract_called_with_user_timezone(
        self, mock_decrypt, mock_extract, mock_check, mock_insert
    ):
        """When user.preferences.timezone = Europe/Paris, extract_batch_events receives user_timezone='Europe/Paris'."""
        item = self._make_item()
        event = _make_event_dict(0)
        mock_decrypt.return_value = ("", "meeting Monday 3pm")
        mock_extract.return_value = ([event], None, {})
        mock_check.return_value = [_make_conflict_result(0, event, is_available=True)]
        mock_insert.return_value = {"id": "g1", "htmlLink": "https://cal.example.com/1"}

        parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])

        mock_extract.assert_called_once()
        _, kwargs = mock_extract.call_args
        self.assertEqual(kwargs.get("user_timezone"), "Europe/Paris")

    @patch("src.batch_calendar.tasks.insert_event")
    @patch("src.batch_calendar.tasks.check_batch_availability")
    @patch("src.batch_calendar.tasks.extract_batch_events")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_conflict_check_called_with_user_timezone(
        self, mock_decrypt, mock_extract, mock_check, mock_insert
    ):
        """check_batch_availability receives the user's timezone, not the global default."""
        item = self._make_item()
        event = _make_event_dict(0)
        mock_decrypt.return_value = ("", "meeting Monday 3pm")
        mock_extract.return_value = ([event], None, {})
        mock_check.return_value = [_make_conflict_result(0, event, is_available=True)]
        mock_insert.return_value = {"id": "g1", "htmlLink": "https://cal.example.com/1"}

        parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])

        mock_check.assert_called_once()
        _, kwargs = mock_check.call_args
        self.assertEqual(kwargs.get("default_timezone"), "Europe/Paris")

    @patch("src.batch_calendar.tasks.insert_event")
    @patch("src.batch_calendar.tasks.check_batch_availability")
    @patch("src.batch_calendar.tasks.extract_batch_events")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_fallback_to_config_default_when_no_prefs(
        self, mock_decrypt, mock_extract, mock_check, mock_insert
    ):
        """If user has no preferences object, falls back to config.default_timezone (Europe/Lisbon)."""
        item = self._make_item()
        UserPreferences.objects.filter(user=self.user).delete()
        event = _make_event_dict(0)
        mock_decrypt.return_value = ("", "meeting Monday 3pm")
        mock_extract.return_value = ([event], None, {})
        mock_check.return_value = [_make_conflict_result(0, event, is_available=True)]
        mock_insert.return_value = {"id": "g1", "htmlLink": "https://cal.example.com/1"}

        parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])

        _, kwargs = mock_extract.call_args
        self.assertEqual(kwargs.get("user_timezone"), "Europe/Lisbon")  # config default
