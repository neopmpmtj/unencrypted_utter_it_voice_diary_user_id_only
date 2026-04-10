"""Tests for batch_calendar API views."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.batch_calendar.models import BatchCalendarEvent, BatchCalendarRequest, BatchRequestStatus
from src.ingestion.models import IngestItem


class BatchCalendarParseApiTests(TestCase):
    """Tests for POST /batch-calendar/api/parse/."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.free_user = CustomUser.objects.create_user(
            email="batchfree@example.com",
            password="Pass123",
        )
        self.free_user.is_email_verified = True
        self.free_user.tier = "free"
        self.free_user.save()

        self.pro_user = CustomUser.objects.create_user(
            email="batchpro@example.com",
            password="Pass123",
        )
        self.pro_user.is_email_verified = True
        self.pro_user.tier = "pro"
        self.pro_user.save()

        for u in (self.free_user, self.pro_user):
            prefs = UserPreferences.objects.get(user=u)
            prefs.onboarding_completed = True
            prefs.save()

        self.url = reverse("batch_calendar:api_parse")

    def test_parse_api_403_when_not_logged_in(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "book meeting"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (302, 401))

    def test_parse_api_400_for_invalid_json(self):
        self.client.force_login(self.pro_user)
        response = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_parse_api_400_for_empty_text(self):
        self.client.force_login(self.pro_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("src.batch_calendar.views.check_batch_availability")
    @patch("src.batch_calendar.views.extract_batch_events")
    def test_parse_api_200_with_events_for_pro_user(self, mock_extract, mock_conflict):
        ev_data = {
            "summary": "Fisioterapia",
            "start": {"dateTime": "2026-03-01T17:00:00", "timeZone": "Europe/Lisbon"},
            "end": {"dateTime": "2026-03-01T18:00:00", "timeZone": "Europe/Lisbon"},
        }
        mock_extract.return_value = ([ev_data], None, {})
        start_dt = timezone.make_aware(datetime(2026, 3, 1, 17, 0, 0))
        end_dt = timezone.make_aware(datetime(2026, 3, 1, 18, 0, 0))
        mock_conflict.return_value = [
            {
                "event_index": 0,
                "event_data": ev_data,
                "is_available": True,
                "conflicting_events": [],
                "alternative_slots": [],
                "start_datetime": start_dt,
                "end_datetime": end_dt,
            },
        ]

        self.client.force_login(self.pro_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "book physio Mon-Fri at 5pm"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("batch_id", data)
        self.assertIn("events", data)
        self.assertEqual(len(data["events"]), 1)


class BatchCalendarConfirmApiTests(TestCase):
    """Tests for POST /batch-calendar/api/confirm/<batch_id>/."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = CustomUser.objects.create_user(
            email="batchconfirm@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    @patch("src.batch_calendar.views.insert_event")
    def test_confirm_api_inserts_events(self, mock_insert):
        mock_insert.return_value = {"id": "gid123", "htmlLink": "https://calendar.google.com/event/123"}

        start_dt = timezone.make_aware(datetime(2026, 3, 1, 10, 0, 0))
        end_dt = timezone.make_aware(datetime(2026, 3, 1, 11, 0, 0))

        batch = BatchCalendarRequest.objects.create(
            user=self.user,
            input_text="book meeting",
            parsed_events_json=[
                {
                    "summary": "Meeting",
                    "start": {"dateTime": "2026-03-01T10:00:00", "timeZone": "Europe/Lisbon"},
                    "end": {"dateTime": "2026-03-01T11:00:00", "timeZone": "Europe/Lisbon"},
                },
            ],
            status=BatchRequestStatus.PENDING,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=0,
            event_data=batch.parsed_events_json[0],
            summary="Meeting",
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone="Europe/Lisbon",
            conflicting_events=[],
            alternative_slots=[],
        )

        url = reverse("batch_calendar:api_confirm", args=[batch.id])
        self.client.force_login(self.user)
        response = self.client.post(url, data=json.dumps({}), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("inserted_count"), 1)
        self.assertEqual(
            mock_insert.call_count,
            1,
            "insert_event must be called once per event (all records inserted into Google Calendar)",
        )

    @patch("src.batch_calendar.views.insert_event")
    def test_confirm_api_returns_redirect_to_pending_list_when_more_pending(self, mock_insert):
        mock_insert.return_value = {"id": "gid123", "htmlLink": "https://calendar.google.com/event/123"}

        start_dt = timezone.make_aware(datetime(2026, 3, 1, 10, 0, 0))
        end_dt = timezone.make_aware(datetime(2026, 3, 1, 11, 0, 0))

        batch1 = BatchCalendarRequest.objects.create(
            user=self.user,
            input_text="book meeting 1",
            parsed_events_json=[
                {
                    "summary": "Meeting 1",
                    "start": {"dateTime": "2026-03-01T10:00:00", "timeZone": "Europe/Lisbon"},
                    "end": {"dateTime": "2026-03-01T11:00:00", "timeZone": "Europe/Lisbon"},
                },
            ],
            status=BatchRequestStatus.PENDING,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch1,
            event_index=0,
            event_data=batch1.parsed_events_json[0],
            summary="Meeting 1",
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone="Europe/Lisbon",
            conflicting_events=[],
            alternative_slots=[],
        )

        batch2 = BatchCalendarRequest.objects.create(
            user=self.user,
            input_text="book meeting 2",
            parsed_events_json=[
                {
                    "summary": "Meeting 2",
                    "start": {"dateTime": "2026-03-02T10:00:00", "timeZone": "Europe/Lisbon"},
                    "end": {"dateTime": "2026-03-02T11:00:00", "timeZone": "Europe/Lisbon"},
                },
            ],
            status=BatchRequestStatus.PENDING,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch2,
            event_index=0,
            event_data=batch2.parsed_events_json[0],
            summary="Meeting 2",
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone="Europe/Lisbon",
            conflicting_events=[],
            alternative_slots=[],
        )

        url = reverse("batch_calendar:api_confirm", args=[batch1.id])
        self.client.force_login(self.user)
        response = self.client.post(url, data=json.dumps({}), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("redirect_url", data)
        self.assertIn("/batch-calendar/pending/", data["redirect_url"])

    @patch("src.batch_calendar.views.insert_event")
    def test_confirm_api_returns_redirect_to_entries_when_no_more_pending(self, mock_insert):
        mock_insert.return_value = {"id": "gid123", "htmlLink": "https://calendar.google.com/event/123"}

        start_dt = timezone.make_aware(datetime(2026, 3, 1, 10, 0, 0))
        end_dt = timezone.make_aware(datetime(2026, 3, 1, 11, 0, 0))

        batch = BatchCalendarRequest.objects.create(
            user=self.user,
            input_text="book meeting",
            parsed_events_json=[
                {
                    "summary": "Meeting",
                    "start": {"dateTime": "2026-03-01T10:00:00", "timeZone": "Europe/Lisbon"},
                    "end": {"dateTime": "2026-03-01T11:00:00", "timeZone": "Europe/Lisbon"},
                },
            ],
            status=BatchRequestStatus.PENDING,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=0,
            event_data=batch.parsed_events_json[0],
            summary="Meeting",
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone="Europe/Lisbon",
            conflicting_events=[],
            alternative_slots=[],
        )

        url = reverse("batch_calendar:api_confirm", args=[batch.id])
        self.client.force_login(self.user)
        response = self.client.post(url, data=json.dumps({}), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("redirect_url", data)
        self.assertIn("/entries/", data["redirect_url"])

    @patch("src.batch_calendar.views.insert_event")
    def test_confirm_api_inserts_all_events(self, mock_insert):
        mock_insert.return_value = {"id": "gid123", "htmlLink": "https://calendar.google.com/event/123"}

        start_dt = timezone.make_aware(datetime(2026, 3, 1, 10, 0, 0))
        end_dt = timezone.make_aware(datetime(2026, 3, 1, 11, 0, 0))
        start_dt2 = timezone.make_aware(datetime(2026, 3, 1, 14, 0, 0))
        end_dt2 = timezone.make_aware(datetime(2026, 3, 1, 15, 0, 0))

        batch = BatchCalendarRequest.objects.create(
            user=self.user,
            input_text="book two meetings",
            parsed_events_json=[
                {
                    "summary": "Meeting 1",
                    "start": {"dateTime": "2026-03-01T10:00:00", "timeZone": "Europe/Lisbon"},
                    "end": {"dateTime": "2026-03-01T11:00:00", "timeZone": "Europe/Lisbon"},
                },
                {
                    "summary": "Meeting 2",
                    "start": {"dateTime": "2026-03-01T14:00:00", "timeZone": "Europe/Lisbon"},
                    "end": {"dateTime": "2026-03-01T15:00:00", "timeZone": "Europe/Lisbon"},
                },
            ],
            status=BatchRequestStatus.PENDING,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=0,
            event_data=batch.parsed_events_json[0],
            summary="Meeting 1",
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone="Europe/Lisbon",
            conflicting_events=[],
            alternative_slots=[],
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=1,
            event_data=batch.parsed_events_json[1],
            summary="Meeting 2",
            start_datetime=start_dt2,
            end_datetime=end_dt2,
            timezone="Europe/Lisbon",
            conflicting_events=[],
            alternative_slots=[],
        )

        url = reverse("batch_calendar:api_confirm", args=[batch.id])
        self.client.force_login(self.user)
        response = self.client.post(url, data=json.dumps({}), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("inserted_count"), 2)
        self.assertEqual(
            mock_insert.call_count,
            2,
            "insert_event must be called once per event (all records inserted into Google Calendar)",
        )


class BatchCalendarCancelApiTests(TestCase):
    """Tests for POST /batch-calendar/api/cancel/<batch_id>/."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = CustomUser.objects.create_user(
            email="batchcancel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_cancel_api_sets_cancelled_and_soft_deletes_batch_and_entry(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            is_deleted=False,
        )
        batch = BatchCalendarRequest.objects.create(
            user=self.user,
            ingest_item=item,
            input_text="book meeting",
            parsed_events_json=[],
            status=BatchRequestStatus.PENDING,
        )

        url = reverse("batch_calendar:api_cancel", args=[batch.id])
        self.client.force_login(self.user)
        response = self.client.post(url, data=json.dumps({}), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["redirect_url"], reverse("recordings:record"))

        batch_refreshed = BatchCalendarRequest.all_objects.get(id=batch.id)
        self.assertEqual(batch_refreshed.status, BatchRequestStatus.CANCELLED)
        self.assertTrue(batch_refreshed.is_deleted)
        self.assertIsNotNone(batch_refreshed.deleted_at)

        self.assertFalse(BatchCalendarRequest.objects.filter(id=batch.id).exists())

        item.refresh_from_db()
        self.assertTrue(item.is_deleted)
        self.assertIsNotNone(item.deleted_at)
        self.assertFalse(IngestItem.objects.filter(id=item.id, is_deleted=False).exists())


class BatchCalendarConfirmationPageTests(TestCase):
    """Tests for GET /batch-calendar/confirm/<batch_id>/ conflict resolution UI."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = CustomUser.objects.create_user(
            email="batchconfirmpage@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_confirmation_page_renders_slot_card_and_override_button_with_conflicts(self):
        """Page with conflicts must include day-nav-slot-container, btn-override, confirmation.js, and slot data for JS to render."""
        alternative_slots = [
            {"start": "2026-03-03T14:00:00", "end": "2026-03-03T15:00:00", "start_formatted": "Tue 03 Mar 2026 at 14:00", "end_formatted": "15:00"},
            {"start": "2026-03-03T15:00:00", "end": "2026-03-03T16:00:00", "start_formatted": "Tue 03 Mar 2026 at 15:00", "end_formatted": "16:00"},
        ]
        alternative_slots_by_day = [
            {
                "date": "2026-03-03",
                "date_formatted": "Tue 03 Mar 2026",
                "slots": [
                    {**alternative_slots[0], "flat_index": 0},
                    {**alternative_slots[1], "flat_index": 1},
                ],
            },
        ]
        batch = BatchCalendarRequest.objects.create(
            user=self.user,
            input_text="book lunch at A Tasca",
            parsed_events_json=[{"summary": "Lunch", "start": {}, "end": {}}],
            status=BatchRequestStatus.PENDING,
        )
        BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=0,
            event_data={"summary": "Lunch"},
            summary="Lunch",
            start_datetime=None,
            end_datetime=None,
            timezone="Europe/Lisbon",
            conflicting_events=[{"start": "2026-03-03T13:00:00", "end": "2026-03-03T14:00:00"}],
            alternative_slots=alternative_slots,
            alternative_slots_by_day=alternative_slots_by_day,
        )

        url = reverse("batch_calendar:confirm", args=[batch.id])
        self.client.force_login(self.user)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("day-nav-slot-container", html)
        self.assertIn("btn-override", html)
        self.assertIn("batch_calendar/js/confirmation.js", html)
        self.assertIn("eventsWithConflicts", html)
        self.assertIn("alternative_slots", html)


class CalendarConflictSelectionHighlightTests(TestCase):
    """Tests that CSS provides persistent selection highlighting for conflict resolution UI."""

    def test_slot_card_selected_css_exists(self):
        """CSS must define .slot-card.selected so selection persists after click."""
        src_dir = Path(__file__).resolve().parents[2]
        css_path = src_dir / "theme" / "static" / "src" / "css" / "input.css"
        self.assertTrue(css_path.exists(), f"Expected {css_path} to exist")
        css = css_path.read_text()
        self.assertIn(".slot-card.selected", css, "input.css must define .slot-card.selected for selection highlight")

    def test_btn_override_selected_css_exists(self):
        """CSS must define .btn-override.selected so override selection persists after click."""
        src_dir = Path(__file__).resolve().parents[2]
        css_path = src_dir / "theme" / "static" / "src" / "css" / "input.css"
        self.assertTrue(css_path.exists(), f"Expected {css_path} to exist")
        css = css_path.read_text()
        self.assertIn(".btn-override.selected", css, "input.css must define .btn-override.selected for selection highlight")
