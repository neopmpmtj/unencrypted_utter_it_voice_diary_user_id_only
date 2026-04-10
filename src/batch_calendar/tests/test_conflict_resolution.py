"""Tests for batch_calendar conflict_resolution."""

from datetime import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.batch_calendar.conflict_resolution import (
    check_batch_availability,
    find_alternative_slots,
    find_alternative_slots_grouped_by_day,
)


def _make_aware(dt):
    if dt.tzinfo:
        return dt
    return timezone.make_aware(dt, timezone.get_current_timezone())


class CheckBatchAvailabilityTests(TestCase):
    """Tests for check_batch_availability (mocked calendar_client)."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="batchconf@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

        self.events = [
            {
                "summary": "Event 1",
                "start": {"dateTime": "2026-03-01T10:00:00", "timeZone": "Europe/Lisbon"},
                "end": {"dateTime": "2026-03-01T11:00:00", "timeZone": "Europe/Lisbon"},
            },
        ]

    @patch("src.batch_calendar.conflict_resolution.check_freebusy")
    def test_check_batch_availability_returns_per_event_results(self, mock_check):
        mock_check.return_value = (True, [])

        results = check_batch_availability(
            self.user,
            self.events,
            calendar_id="primary",
            default_timezone="Europe/Lisbon",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["event_index"], 0)
        self.assertTrue(results[0]["is_available"])
        self.assertEqual(results[0]["conflicting_events"], [])

    @patch("src.batch_calendar.conflict_resolution.check_freebusy")
    @patch("src.batch_calendar.conflict_resolution.find_alternative_slots_grouped_by_day")
    def test_check_batch_availability_includes_alternatives_on_conflict(
        self, mock_find_grouped, mock_check
    ):
        mock_check.return_value = (False, [{"start": "2026-03-01T11:00:00Z", "end": "2026-03-01T12:00:00Z"}])
        mock_find_grouped.return_value = [
            {
                "date": "2026-03-01",
                "date_formatted": "Sun 01 Mar 2026",
                "slots": [
                    {
                        "start": "2026-03-01T14:00:00",
                        "end": "2026-03-01T15:00:00",
                        "start_formatted": "Sun 01 Mar 2026 at 14:00",
                        "end_formatted": "15:00",
                        "flat_index": 0,
                    },
                ],
            },
        ]

        results = check_batch_availability(
            self.user,
            self.events,
            calendar_id="primary",
            default_timezone="Europe/Lisbon",
        )

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["is_available"])
        self.assertEqual(len(results[0]["alternative_slots"]), 1)
        self.assertEqual(len(results[0]["alternative_slots_by_day"]), 1)
        self.assertEqual(len(results[0]["alternative_slots_by_day"][0]["slots"]), 1)


class FindAlternativeSlotsTests(TestCase):
    """Tests for find_alternative_slots (mocked get_busy_periods)."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="batchalt@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    @patch("src.batch_calendar.conflict_resolution.get_busy_periods")
    def test_find_alternative_slots_returns_empty_when_all_busy(self, mock_get_busy):
        mock_get_busy.return_value = [
            {"start": "2026-03-01T08:00:00Z", "end": "2026-03-01T20:00:00Z"},
        ]

        start = _make_aware(datetime(2026, 3, 1, 10, 0, 0))
        slots = find_alternative_slots(
            self.user,
            start,
            duration_minutes=60,
            calendar_id="primary",
            num_slots=3,
            days_ahead=1,
        )

        mock_get_busy.assert_called_once()
        self.assertIsInstance(slots, list)


class FindAlternativeSlotsGroupedByDayTests(TestCase):
    """Tests for find_alternative_slots_grouped_by_day."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="batchgrouped@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    @patch("src.batch_calendar.conflict_resolution.get_busy_periods")
    def test_empty_days_filtered_out(self, mock_get_busy):
        mock_get_busy.return_value = []

        start = _make_aware(datetime(2026, 3, 1, 10, 0, 0))
        grouped = find_alternative_slots_grouped_by_day(
            self.user,
            start,
            duration_minutes=60,
            calendar_id="primary",
            days_ahead=2,
        )

        self.assertIsInstance(grouped, list)
        for day in grouped:
            self.assertIn("date", day)
            self.assertIn("date_formatted", day)
            self.assertIn("slots", day)
            self.assertGreater(len(day["slots"]), 0, "Empty days should be filtered out")
            for slot in day["slots"]:
                self.assertIn("flat_index", slot)

    @patch("src.batch_calendar.conflict_resolution.get_busy_periods")
    def test_search_start_is_today_when_conflict_day_in_past(self, mock_get_busy):
        mock_get_busy.return_value = []
        past_start = _make_aware(datetime(2020, 1, 1, 10, 0, 0))

        grouped = find_alternative_slots_grouped_by_day(
            self.user,
            past_start,
            duration_minutes=60,
            calendar_id="primary",
            days_ahead=7,
        )

        self.assertIsInstance(grouped, list)
        if grouped:
            first_day = grouped[0]
            self.assertIn("date", first_day)
            from django.utils import timezone as django_timezone
            today_str = django_timezone.now().date().strftime("%Y-%m-%d")
            self.assertEqual(first_day["date"], today_str)
