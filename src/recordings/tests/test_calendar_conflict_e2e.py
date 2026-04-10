"""
End-to-end tests for calendar conflict flow: audio and text input types.

Asserts that when parse_batch_calendar_task detects a conflict,
get_status returns calendar_conflict and confirmation_url for both audio and text items.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserFeatureConfig, UserPreferences
from src.batch_calendar.tasks import parse_batch_calendar_task
from src.ingestion.models import IngestItem, IngestJob, IngestStatus, JobType, JobStatus
from src.retrieval.models import ItemRetrievalProjection


def _make_event_dict(i: int) -> dict:
    base = datetime(2026, 3, 1, 10, 0, 0) + timedelta(hours=i)
    start_str = base.strftime("%Y-%m-%dT%H:%M:%S")
    end_dt = base + timedelta(minutes=30)
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "summary": f"Event {i + 1}",
        "start": {"dateTime": start_str, "timeZone": "Europe/Lisbon"},
        "end": {"dateTime": end_str, "timeZone": "Europe/Lisbon"},
    }


def _make_conflict_result(event_index: int, event_data: dict, is_available: bool = False) -> dict:
    from django.utils import timezone
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(
        datetime(2026, 3, 1, 10, 0, 0) + timedelta(hours=event_index),
        tz,
    )
    end_dt = start_dt + timedelta(minutes=30)
    conflicting = [{"start": "2026-03-01T10:00:00", "end": "2026-03-01T10:30:00"}] if not is_available else []
    return {
        "event_index": event_index,
        "event_data": event_data,
        "is_available": is_available,
        "conflicting_events": conflicting,
        "alternative_slots": [{"start": "2026-03-01T14:00:00", "end": "2026-03-01T14:30:00"}] if not is_available else [],
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }


class CalendarConflictE2EAudioTests(TestCase):
    """E2E: audio item -> parse_batch_calendar (conflict) -> get_status returns calendar_conflict."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="e2e_audio@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        self.client.force_login(self.user)
        self.user_config = UserFeatureConfig.get_for_user(self.user)
        self.user_config.enable_calendar_integration = True
        self.user_config.save()

    def test_audio_item_conflict_flow_get_status_returns_calendar_conflict(self):
        """Audio item: parse_batch_calendar with conflict -> get_status returns calendar_conflict."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status=IngestStatus.TAGGED,
            content_text="Book physio Monday 3pm",
            summary_text="",
            title="",
        )
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.task.create.todo",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )

        events = [_make_event_dict(0)]
        conflict_results = [_make_conflict_result(0, events[0], is_available=False)]

        with patch("src.batch_calendar.tasks.extract_batch_events") as mock_extract:
            mock_extract.return_value = (events, None, {})
            with patch("src.batch_calendar.tasks.check_batch_availability") as mock_check:
                mock_check.return_value = conflict_results
                with patch("src.batch_calendar.tasks.broadcast_batch_calendar_status"):
                    with patch("src.batch_calendar.tasks.broadcast_complete"):
                        with patch("src.retrieval.tasks.index_entry_prep_task"):
                            parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])

        job = IngestJob.objects.filter(item=item, job_type=JobType.PARSE_CALENDAR).first()
        self.assertIsNotNone(job)
        self.assertTrue(job.checkpoint_data.get("conflict"))

        url = reverse("recordings:status", args=[str(item.id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["calendar_conflict"])
        self.assertIn("/batch-calendar/confirm/", data["confirmation_url"])


class CalendarConflictE2ETextTests(TestCase):
    """E2E: text item -> parse_batch_calendar (conflict) -> get_status returns calendar_conflict."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="e2e_text@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        self.client.force_login(self.user)
        self.user_config = UserFeatureConfig.get_for_user(self.user)
        self.user_config.enable_calendar_integration = True
        self.user_config.save()

    def test_text_item_conflict_flow_get_status_returns_calendar_conflict(self):
        """Text item: parse_batch_calendar with conflict -> get_status returns calendar_conflict."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
            content_text="Book physio Monday 3pm",
            summary_text="",
            title="",
        )
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.task.create.todo",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )

        events = [_make_event_dict(0)]
        conflict_results = [_make_conflict_result(0, events[0], is_available=False)]

        with patch("src.batch_calendar.tasks.extract_batch_events") as mock_extract:
            mock_extract.return_value = (events, None, {})
            with patch("src.batch_calendar.tasks.check_batch_availability") as mock_check:
                mock_check.return_value = conflict_results
                with patch("src.batch_calendar.tasks.broadcast_batch_calendar_status"):
                    with patch("src.batch_calendar.tasks.broadcast_complete"):
                        with patch("src.retrieval.tasks.index_entry_prep_task"):
                            parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])

        job = IngestJob.objects.filter(item=item, job_type=JobType.PARSE_CALENDAR).first()
        self.assertIsNotNone(job)
        self.assertTrue(job.checkpoint_data.get("conflict"))

        url = reverse("recordings:status", args=[str(item.id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["calendar_conflict"])
        self.assertIn("/batch-calendar/confirm/", data["confirmation_url"])


class CalendarConflictE2EFreeUserTests(TestCase):
    """E2E: free user -> parse_batch_calendar_task (conflict) -> get_status returns calendar_conflict."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="e2e_single@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "free"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        self.client.force_login(self.user)
        self.user_config = UserFeatureConfig.get_for_user(self.user)
        self.user_config.enable_calendar_integration = True
        self.user_config.save()

    def test_free_user_conflict_flow_get_status_returns_calendar_conflict(self):
        """Free user: parse_batch_calendar_task with conflict -> get_status returns calendar_conflict."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
            content_text="Book physio Monday 3pm",
            summary_text="",
            title="",
        )
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.task.create.todo",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )

        events = [_make_event_dict(0)]
        conflict_results = [_make_conflict_result(0, events[0], is_available=False)]

        with patch("src.batch_calendar.tasks.extract_batch_events") as mock_extract:
            mock_extract.return_value = (events, None, {})
            with patch("src.batch_calendar.tasks.check_batch_availability") as mock_check:
                mock_check.return_value = conflict_results
                with patch("src.batch_calendar.tasks.broadcast_batch_calendar_status"):
                    with patch("src.batch_calendar.tasks.broadcast_complete"):
                        with patch("src.retrieval.tasks.index_entry_prep_task"):
                            parse_batch_calendar_task.apply(args=[str(item.id), "", "en"])

        job = IngestJob.objects.filter(item=item, job_type=JobType.PARSE_CALENDAR).first()
        self.assertIsNotNone(job)
        self.assertTrue(job.checkpoint_data.get("conflict"))

        url = reverse("recordings:status", args=[str(item.id)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["calendar_conflict"])
        self.assertIn("/batch-calendar/confirm/", data["confirmation_url"])
