"""
Calendar Client Adapter

Single coupling point to the main app for Google Calendar operations.
When splitting to a separate VPS, only this file changes to HTTP calls.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.common.google_account.auth import (
    GoogleAuthError,
    get_authenticated_service,
)

logger = logging.getLogger(__name__)


def insert_event(
    user,
    event_data: Dict[str, Any],
    calendar_id: str = "primary",
) -> Optional[Dict[str, Any]]:
    """
    Create an event in Google Calendar.

    Args:
        user: Django User instance for authentication
        event_data: Event dict in Google Calendar API format
        calendar_id: Google Calendar ID

    Returns:
        API response dict or None on failure
    """
    try:
        service = get_authenticated_service(user, "calendar")
        response = service.events().insert(
            calendarId=calendar_id,
            body=event_data,
        ).execute()
        logger.info("Created calendar event: %s", response.get("id"))
        return response
    except GoogleAuthError as e:
        logger.error("Google auth error creating calendar event: %s", e)
        raise
    except Exception as e:
        logger.error("Failed to create calendar event: %s", e)
        return None


def delete_event(
    user,
    calendar_id: str,
    event_id: str,
) -> bool:
    """
    Delete an event from Google Calendar.
    Returns True on success, False on failure. Logs exceptions; does not raise.
    """
    if not event_id or not event_id.strip():
        return False
    try:
        service = get_authenticated_service(user, "calendar")
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id.strip(),
        ).execute()
        logger.info("Deleted Google Calendar event: %s", event_id)
        return True
    except GoogleAuthError as e:
        logger.error("Google auth error deleting calendar event: %s", e)
        return False
    except Exception as e:
        logger.error("Failed to delete Google Calendar event %s: %s", event_id, e)
        return False


def check_freebusy(
    user,
    start_datetime: datetime,
    end_datetime: datetime,
    calendar_id: str = "primary",
) -> tuple[bool, List[Dict[str, Any]]]:
    """
    Check if a time slot is available using Google Calendar FreeBusy API.

    Returns:
        Tuple of (is_available, conflicting_busy_periods)
    """
    try:
        service = get_authenticated_service(user, "calendar")
        start_iso = start_datetime.isoformat()
        end_iso = end_datetime.isoformat()
        if start_datetime.tzinfo is None:
            start_iso += "Z"
        if end_datetime.tzinfo is None:
            end_iso += "Z"

        body = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "items": [{"id": calendar_id}],
        }
        result = service.freebusy().query(body=body).execute()
        busy_periods = (
            result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        )
        is_available = len(busy_periods) == 0
        return is_available, busy_periods
    except GoogleAuthError as e:
        logger.error("Google auth error checking availability: %s", e)
        raise
    except Exception as e:
        logger.error("Failed to check availability: %s", e)
        return True, []


def get_busy_periods(
    user,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str = "primary",
) -> List[Dict[str, Any]]:
    """
    Get all busy periods in a time range.

    Returns:
        List of busy periods with 'start' and 'end' keys
    """
    try:
        service = get_authenticated_service(user, "calendar")
        time_min_iso = time_min.isoformat()
        time_max_iso = time_max.isoformat()
        if time_min.tzinfo is None:
            time_min_iso += "Z"
        if time_max.tzinfo is None:
            time_max_iso += "Z"

        body = {
            "timeMin": time_min_iso,
            "timeMax": time_max_iso,
            "items": [{"id": calendar_id}],
        }
        result = service.freebusy().query(body=body).execute()
        return result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    except Exception as e:
        logger.error("Failed to get busy periods: %s", e)
        return []
