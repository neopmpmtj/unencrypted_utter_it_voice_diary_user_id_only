"""
Batch Calendar Conflict Resolution

Isolated module for FreeBusy checking and alternative slot finding.
Duplicated from calendar_parser for maintainability and separate enhancement.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.utils.translation import gettext as _
from zoneinfo import ZoneInfo

from .calendar_client import check_freebusy, get_busy_periods

logger = logging.getLogger(__name__)


def _parse_datetime_from_event(event_data: Dict[str, Any], field: str) -> Optional[datetime]:
    """Parse datetime from event data (start or end)."""
    try:
        field_data = event_data.get(field, {})
        datetime_str = field_data.get("dateTime") or field_data.get("date")
        if datetime_str:
            if "T" in datetime_str:
                if "+" in datetime_str or datetime_str.endswith("Z"):
                    return datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
                return datetime.fromisoformat(datetime_str)
            return datetime.strptime(datetime_str, "%Y-%m-%d")
    except Exception as e:
        logger.warning("Failed to parse %s datetime: %s", field, e)
    return None


def _ensure_aware(dt: Optional[datetime], tz_str: str) -> Optional[datetime]:
    """Return timezone-aware datetime. If naive, interpret in given timezone."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt
    try:
        from django.utils import timezone as django_timezone

        tz = ZoneInfo(tz_str)
        return django_timezone.make_aware(dt, tz)
    except Exception as e:
        logger.warning("Could not make datetime aware with tz %s: %s", tz_str, e)
        return dt


def check_batch_availability(
    user,
    events_list: List[Dict[str, Any]],
    calendar_id: str = "primary",
    default_timezone: str = "Europe/Lisbon",
) -> List[Dict[str, Any]]:
    """
    Check availability for each event in a batch.

    Args:
        user: Django User instance
        events_list: List of event dicts in Google Calendar API format
        calendar_id: Google Calendar ID
        default_timezone: Timezone for naive datetimes

    Returns:
        List of per-event dicts:
        {
            "event_index": int,
            "event_data": dict,
            "is_available": bool,
            "conflicting_events": list,
            "alternative_slots": list (if conflict),
            "start_datetime": datetime,
            "end_datetime": datetime,
        }
    """
    results = []
    for i, event_data in enumerate(events_list):
        tz_str = (
            event_data.get("start", {}).get("timeZone")
            or event_data.get("end", {}).get("timeZone")
            or default_timezone
        )
        start_dt = _parse_datetime_from_event(event_data, "start")
        end_dt = _parse_datetime_from_event(event_data, "end")
        start_dt = _ensure_aware(start_dt, tz_str)
        end_dt = _ensure_aware(end_dt, tz_str)

        if not start_dt or not end_dt:
            results.append({
                "event_index": i,
                "event_data": event_data,
                "is_available": False,
                "conflicting_events": [],
                "alternative_slots": [],
                "alternative_slots_by_day": [],
                "start_datetime": start_dt,
                "end_datetime": end_dt,
                "error": _("Could not parse datetime"),
            })
            continue

        is_available, conflicting = check_freebusy(user, start_dt, end_dt, calendar_id)

        alternative_slots = []
        alternative_slots_by_day = []
        if not is_available:
            duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
            alternative_slots_by_day = find_alternative_slots_grouped_by_day(
                user,
                start_dt,
                duration_minutes,
                calendar_id,
                days_ahead=7,
            )
            for day in alternative_slots_by_day:
                for slot in day["slots"]:
                    alternative_slots.append({
                        "start": slot["start"],
                        "end": slot["end"],
                        "start_formatted": slot["start_formatted"],
                        "end_formatted": slot["end_formatted"],
                    })

        results.append({
            "event_index": i,
            "event_data": event_data,
            "is_available": is_available,
            "conflicting_events": conflicting,
            "alternative_slots": alternative_slots,
            "alternative_slots_by_day": alternative_slots_by_day,
            "start_datetime": start_dt,
            "end_datetime": end_dt,
        })

    return results


def find_alternative_slots_grouped_by_day(
    user,
    original_start: datetime,
    duration_minutes: int,
    calendar_id: str = "primary",
    days_ahead: int = 7,
    working_hours: Tuple[int, int] = (8, 20),
) -> List[Dict[str, Any]]:
    """
    Find alternative available time slots grouped by day.

    Never returns slots before today. Empty days are filtered out.
    Each slot includes flat_index for API compatibility.

    Returns:
        List of dicts: [{"date": "YYYY-MM-DD", "date_formatted": "...", "slots": [...]}]
    """
    from django.utils import timezone as django_timezone

    now = django_timezone.now()
    if original_start.tzinfo:
        now = now.astimezone(original_start.tzinfo) if now.tzinfo else now
    else:
        now = now.replace(tzinfo=None) if now.tzinfo else now

    if original_start.date() < now.date():
        search_start = datetime.combine(now.date(), datetime.min.time())
        if original_start.tzinfo:
            search_start = search_start.replace(tzinfo=original_start.tzinfo)
    else:
        search_start = original_start
    search_end = search_start + timedelta(days=days_ahead)

    busy_periods = get_busy_periods(user, search_start, search_end, calendar_id)

    busy_slots = []
    for period in busy_periods:
        try:
            start_str = period.get("start", "")
            end_str = period.get("end", "")
            busy_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            busy_end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if original_start.tzinfo is None:
                busy_start = busy_start.replace(tzinfo=None)
                busy_end = busy_end.replace(tzinfo=None)
            busy_slots.append((busy_start, busy_end))
        except Exception as e:
            logger.warning("Failed to parse busy period: %s", e)
            continue

    busy_slots.sort(key=lambda x: x[0])

    def is_slot_available(slot_start: datetime, slot_end: datetime) -> bool:
        for b_start, b_end in busy_slots:
            if slot_start < b_end and slot_end > b_start:
                return False
        return True

    def is_within_working_hours(dt: datetime) -> bool:
        return working_hours[0] <= dt.hour < working_hours[1]

    duration = timedelta(minutes=duration_minutes)
    slot_interval = timedelta(minutes=30)
    days_with_slots = []
    flat_index = 0

    current_date = search_start.date()
    end_date = search_end.date()

    while current_date <= end_date:
        day_start = datetime.combine(
            current_date, datetime.min.time().replace(hour=working_hours[0])
        )
        if original_start.tzinfo:
            day_start = day_start.replace(tzinfo=original_start.tzinfo)
        day_end = datetime.combine(
            current_date, datetime.min.time().replace(hour=working_hours[1])
        )
        if original_start.tzinfo:
            day_end = day_end.replace(tzinfo=original_start.tzinfo)

        if day_start < search_start:
            day_start = search_start
        if day_end > search_end:
            day_end = search_end

        day_slots = []
        current_time = day_start

        while current_time + duration <= day_end:
            slot_start = current_time
            slot_end = slot_start + duration

            if is_within_working_hours(slot_start) and is_within_working_hours(
                slot_end - timedelta(minutes=1)
            ):
                if is_slot_available(slot_start, slot_end):
                    day_slots.append({
                        "start": slot_start.isoformat(),
                        "end": slot_end.isoformat(),
                        "start_formatted": slot_start.strftime("%a %d %b %Y at %H:%M"),
                        "end_formatted": slot_end.strftime("%H:%M"),
                        "flat_index": flat_index,
                    })
                    flat_index += 1

            current_time += slot_interval

        if day_slots:
            days_with_slots.append({
                "date": current_date.strftime("%Y-%m-%d"),
                "date_formatted": day_start.strftime("%a %d %b %Y"),
                "slots": day_slots,
            })

        current_date += timedelta(days=1)

    return days_with_slots


def find_alternative_slots(
    user,
    original_start: datetime,
    duration_minutes: int,
    calendar_id: str = "primary",
    num_slots: int = 5,
    days_ahead: int = 7,
    working_hours: Tuple[int, int] = (8, 20),
) -> List[Dict[str, Any]]:
    """
    Find alternative available time slots (flat list, legacy).

    Returns:
        List of dicts with keys: start, end, start_formatted, end_formatted
    """
    grouped = find_alternative_slots_grouped_by_day(
        user, original_start, duration_minutes, calendar_id, days_ahead, working_hours
    )
    flat = []
    for day in grouped:
        for slot in day["slots"]:
            flat.append({
                "start": slot["start"],
                "end": slot["end"],
                "start_formatted": slot["start_formatted"],
                "end_formatted": slot["end_formatted"],
            })
    return flat[:num_slots] if num_slots else flat
