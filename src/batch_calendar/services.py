"""
Batch Calendar Services

OpenAI LLM-based batch event extraction from natural language.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# [Google Gemini API — google-genai library imports]
# from google import genai
# from google.genai import types
from openai import OpenAI

from .config_batch_calendar.batch_calendar_config import get_batch_calendar_config

logger = logging.getLogger(__name__)


def _strip_markdown_json_fences(text: str) -> str:
    """Remove markdown code fences so json.loads can parse the content."""
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


# [Google Gemini API — Gemini usage metadata extractor]
# def _gemini_usage_dict(response) -> dict:
#     """Extract usage dict from Gemini response. Keys: input, output, total."""
#     um = getattr(response, "usage_metadata", None)
#     if not um:
#         return {"input": 0, "output": 0, "total": 0}
#     inp = getattr(um, "prompt_token_count", 0) or getattr(um, "input_token_count", 0)
#     out = getattr(um, "candidates_token_count", 0) or getattr(um, "output_token_count", 0)
#     total = getattr(um, "total_token_count", 0) or (inp + out)
#     return {"input": inp or 0, "output": out or 0, "total": total or 0}


def extract_batch_events(
    content_text: str,
    config: Optional[Any] = None,
    user_timezone: str = "Europe/Lisbon",
) -> tuple[Optional[List[Dict[str, Any]]], Optional[str], dict]:
    """
    Extract multiple calendar events from natural language using Gemini.

    Args:
        content_text: User input text (e.g. "book physiotherapy Mon-Fri at 5pm")
        config: Optional BatchCalendarConfig instance

    Returns:
        Tuple of (events_list, error_message, usage_dict).
        - events_list: List of event dicts in Google Calendar API format, or None on failure
        - error_message: Error string if extraction failed, else None
        - usage_dict: Token usage from API (input, output, total)
    """
    if config is None:
        config = get_batch_calendar_config()

    if not config.openai_api_key:
        raise ValueError(
            "OpenAI API key not configured. Set OPENAI_API_KEY."
        )

    logger.debug("extract_batch_events input: content_text=%r", content_text[:200])

    # Validate the requested timezone; fall back to config default if unknown.
    try:
        tz = ZoneInfo(user_timezone)
        effective_timezone = user_timezone
    except (ZoneInfoNotFoundError, Exception):
        logger.warning("Unknown timezone %r, falling back to %s", user_timezone, config.default_timezone)
        tz = ZoneInfo(config.default_timezone)
        effective_timezone = config.default_timezone

    now = datetime.now(tz=tz)
    system_date = now.strftime("%Y-%m-%d")
    system_time = now.strftime("%H:%M:%S")
    prompt = config.get_prompt(content_text, system_date, system_time, timezone=effective_timezone)

    # [Google Gemini API — Gemini client and generate_content call]
    # client = genai.Client(api_key=config.gemini_api_key)
    client = OpenAI(api_key=config.openai_api_key, timeout=60.0)
    delay = config.retry_delay
    last_exception = None

    for attempt in range(config.max_retries + 1):
        try:
            if attempt > 0:
                logger.debug(
                    "Retry attempt %d/%d after %.2fs delay",
                    attempt,
                    config.max_retries,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * config.retry_backoff_factor, 60.0)

            start_time = time.time()
            response = client.chat.completions.create(
                model=config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
            api_duration = time.time() - start_time
            logger.debug("Batch extraction API call completed in %.3fs", api_duration)
            usage = response.usage
            usage_dict = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total": getattr(usage, "total_tokens", 0) if usage else 0,
            }

            response_text = (response.choices[0].message.content or "").strip()
            logger.debug("extract_batch_events raw LLM response:\n%s", response_text[:500])
            response_text = _strip_markdown_json_fences(response_text)

            try:
                result = json.loads(response_text)

                if "error" in result:
                    err_msg = result["error"]
                    logger.warning("LLM returned error: %s", err_msg)
                    return None, err_msg, usage_dict

                events = result.get("events")
                if not events or not isinstance(events, list):
                    logger.warning("LLM response missing or invalid 'events' array")
                    return None, "No events extracted from text", usage_dict

                validated = []
                for i, ev in enumerate(events):
                    if not isinstance(ev, dict):
                        logger.warning("Event %d is not a dict, skipping", i)
                        continue
                    if "summary" not in ev or "start" not in ev or "end" not in ev:
                        logger.warning("Event %d missing required fields, skipping", i)
                        continue
                    validated.append(ev)

                if not validated:
                    return None, "No valid events extracted", usage_dict

                logger.info("Successfully extracted %d events", len(validated))
                return validated, None, usage_dict

            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON response: %s", e)
                if attempt < config.max_retries:
                    continue
                return None, f"Invalid JSON response: {e}", usage_dict

        except Exception as e:
            last_exception = e
            if attempt < config.max_retries:
                logger.warning(
                    "Batch extraction API call failed (attempt %d/%d): %s",
                    attempt + 1,
                    config.max_retries + 1,
                    e,
                )
            else:
                logger.error(
                    "Batch extraction failed after %d attempts: %s",
                    config.max_retries + 1,
                    e,
                )
                raise

    if last_exception:
        raise last_exception

    return None, "Extraction failed after retries", {}


def delete_batch_calendar_for_item(item) -> None:
    """
    When an IngestItem is deleted: delete linked batch calendar events from
    Google Calendar and remove BatchCalendarRequest/BatchCalendarEvent records.
    """
    from .calendar_client import delete_event
    from .models import BatchCalendarRequest, BatchEventStatus

    config = get_batch_calendar_config()
    calendar_id = config.calendar_id
    try:
        from src.accounts.models import UserFeatureConfig

        tf = UserFeatureConfig.get_for_user(item.user)
        if tf and tf.default_calendar_id:
            calendar_id = tf.default_calendar_id
    except Exception as e:
        logger.warning("Could not load user config for batch calendar delete: %s", e)

    batches = list(
        BatchCalendarRequest.all_objects.filter(ingest_item=item).prefetch_related("events")
    )
    if not batches:
        return

    user = item.user
    for batch in batches:
        for ev in batch.events.all():
            if ev.status == BatchEventStatus.SUCCESS and (ev.google_event_id or "").strip():
                if user:
                    delete_event(user, calendar_id, ev.google_event_id)
                else:
                    logger.warning(
                        "No user for item %s, skipping Google delete for batch event %s",
                        item.id,
                        ev.id,
                    )

    BatchCalendarRequest.all_objects.filter(ingest_item=item).delete()


def get_calendar_webhook_base_url() -> str:
    """Get the HTTPS base URL for the calendar webhook."""
    from urllib.parse import urlparse

    from django.conf import settings

    url = getattr(settings, "CALENDAR_WEBHOOK_BASE_URL", "") or ""
    if url:
        return url.rstrip("/")
    redirect = getattr(settings, "GOOGLE_OAUTH_REDIRECT_URI", "") or ""
    if redirect:
        parsed = urlparse(redirect)
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def delete_calendar_events_for_item(item) -> None:
    """
    For a given IngestItem (e.g. after soft-delete): remove linked events from
    Google Calendar and soft-delete all related CalendarEvent rows.
    """
    from django.utils import timezone as django_timezone

    from .calendar_client import delete_event
    from .models import CalendarEvent, CalendarEventStatus
    from .config_batch_calendar.batch_calendar_config import get_batch_calendar_config

    config = get_batch_calendar_config()
    calendar_id = config.calendar_id
    try:
        from src.accounts.models import UserFeatureConfig

        tf = UserFeatureConfig.get_for_user(item.user)
        if tf and tf.default_calendar_id:
            calendar_id = tf.default_calendar_id
    except Exception as e:
        logger.warning("Could not load user config for calendar delete: %s", e)

    events = list(CalendarEvent.all_objects.filter(source_item=item))
    if not events:
        return

    user = item.user
    for ev in events:
        if ev.status == CalendarEventStatus.SUCCESS and (ev.google_event_id or "").strip():
            if user:
                delete_event(user, calendar_id, ev.google_event_id)
            else:
                logger.warning("No user for item %s, skipping Google delete for event %s", item.id, ev.id)

    now = django_timezone.now()
    CalendarEvent.all_objects.filter(source_item=item).update(
        is_deleted=True,
        deleted_at=now,
        status=CalendarEventStatus.CANCELLED,
    )


def setup_watch_for_user(user, calendar_id: str = "primary"):
    """Create a Google Calendar push notification channel for a user."""
    import uuid
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.common.google_account.auth import GoogleAuthError, get_authenticated_service
    from .models import CalendarWatchChannel

    try:
        service = get_authenticated_service(user, "calendar")
    except GoogleAuthError as e:
        logger.warning("Could not get calendar service for user %s: %s", user.id, e)
        return None

    channel_id = str(uuid.uuid4())
    base_url = get_calendar_webhook_base_url()
    use_webhook = base_url and base_url.startswith("https://")

    if use_webhook:
        webhook_url = f"{base_url}/calendar/webhook/"
        body = {"id": channel_id, "type": "web_hook", "address": webhook_url}
        try:
            response = service.events().watch(calendarId=calendar_id, body=body).execute()
        except Exception as e:
            logger.error("Failed to create calendar watch for user %s: %s", user.id, e)
            return None
        resource_id = response.get("resourceId", "")
        expiration_ms = int(response.get("expiration", 0) or 0)
        expiration_dt = datetime.fromtimestamp(expiration_ms / 1000.0, tz=ZoneInfo("UTC"))
    else:
        logger.warning("Calendar webhook requires HTTPS base URL; creating poll-only channel")
        resource_id = ""
        expiration_dt = None

    try:
        list_resp = service.events().list(calendarId=calendar_id, singleEvents=False).execute()
        sync_token = list_resp.get("nextSyncToken", "")
        while not sync_token and list_resp.get("nextPageToken"):
            list_resp = service.events().list(
                calendarId=calendar_id,
                singleEvents=False,
                pageToken=list_resp["nextPageToken"],
            ).execute()
            sync_token = list_resp.get("nextSyncToken", "")
    except Exception as e:
        logger.warning("Could not get initial sync token for user %s: %s", user.id, e)
        sync_token = ""

    channel = CalendarWatchChannel.objects.create(
        user=user,
        channel_id=channel_id,
        resource_id=resource_id,
        calendar_id=calendar_id,
        sync_token=sync_token,
        expiration=expiration_dt,
        is_active=True,
    )
    logger.info(
        "Created calendar watch channel for user %s%s",
        user.id,
        " (poll-only)" if not use_webhook else "",
    )
    return channel


def stop_watch_channel(channel) -> bool:
    """Stop a Google Calendar notification channel."""
    from src.common.google_account.auth import GoogleAuthError, get_authenticated_service

    if not channel.resource_id:
        return True
    user = channel.user
    try:
        service = get_authenticated_service(user, "calendar")
        service.channels().stop(
            body={"id": channel.channel_id, "resourceId": channel.resource_id}
        ).execute()
        logger.info("Stopped calendar watch channel %s", channel.channel_id)
        return True
    except GoogleAuthError as e:
        logger.warning("Could not stop channel %s: %s", channel.channel_id, e)
        return False
    except Exception as e:
        logger.warning("Failed to stop channel %s: %s", channel.channel_id, e)
        return False


def process_calendar_sync(channel_id: str) -> Dict[str, Any]:
    """
    Process incremental calendar sync for a watch channel.
    For CalendarEvent: soft-deletes IngestItem and CalendarEvent when event deleted on Google.
    For BatchCalendarEvent: only cancels the event, does NOT soft-delete IngestItem.
    """
    from django.utils import timezone as django_timezone

    from .models import CalendarEvent, CalendarEventStatus, CalendarWatchChannel
    from src.common.google_account.auth import GoogleAuthError, get_authenticated_service

    from .models import BatchCalendarEvent, BatchEventStatus

    channel = CalendarWatchChannel.objects.filter(
        channel_id=channel_id,
        is_active=True,
    ).select_related("user").first()
    if not channel:
        return {"success": False, "error": "Channel not found or inactive"}

    user = channel.user
    try:
        service = get_authenticated_service(user, "calendar")
    except GoogleAuthError as e:
        return {"success": False, "error": str(e)}

    deleted_count = 0
    new_sync_token = channel.sync_token
    params = {
        "calendarId": channel.calendar_id,
        "singleEvents": False,
        "showDeleted": True,
    }
    if channel.sync_token:
        params["syncToken"] = channel.sync_token

    try:
        while True:
            req = service.events().list(**params)
            response = req.execute()

            for event in response.get("items", []):
                if event.get("status") == "cancelled":
                    event_id = event.get("id")
                    if not event_id:
                        continue

                    cal_event = CalendarEvent.objects.filter(
                        user=channel.user,
                        google_event_id=event_id,
                        status=CalendarEventStatus.SUCCESS,
                    ).select_related("source_item").first()
                    if cal_event and cal_event.source_item and not cal_event.source_item.is_deleted:
                        item = cal_event.source_item
                        item.is_deleted = True
                        item.deleted_at = django_timezone.now()
                        item.save(update_fields=["is_deleted", "deleted_at"])
                        CalendarEvent.all_objects.filter(source_item=item).update(
                            is_deleted=True,
                            deleted_at=django_timezone.now(),
                            status=CalendarEventStatus.CANCELLED,
                        )
                        deleted_count += 1
                        logger.info("Soft-deleted IngestItem %s (event %s deleted on Google)", item.id, event_id)
                        continue

                    batch_ev = BatchCalendarEvent.objects.filter(
                        batch_request__user=channel.user,
                        google_event_id=event_id,
                        status=BatchEventStatus.SUCCESS,
                    ).select_related("batch_request").first()
                    if batch_ev:
                        batch_ev.status = BatchEventStatus.CANCELLED
                        batch_ev.save(update_fields=["status", "updated_at"])
                        deleted_count += 1
                        logger.info("Cancelled BatchCalendarEvent %s (event %s deleted on Google)", batch_ev.id, event_id)

            new_sync_token = response.get("nextSyncToken", "")
            page_token = response.get("nextPageToken")
            if not page_token:
                break
            params["pageToken"] = page_token

        if new_sync_token:
            channel.sync_token = new_sync_token
            channel.save(update_fields=["sync_token", "updated_at"])

        return {"success": True, "deleted_count": deleted_count, "new_sync_token": new_sync_token}
    except Exception as e:
        err_str = str(e)
        if "410" in err_str or "Gone" in err_str or "Invalid sync token" in err_str:
            params.pop("syncToken", None)
            params.pop("pageToken", None)
            try:
                while True:
                    response = service.events().list(**params).execute()
                    for event in response.get("items", []):
                        if event.get("status") == "cancelled":
                            event_id = event.get("id")
                            if not event_id:
                                continue
                            cal_event = CalendarEvent.objects.filter(
                                user=channel.user,
                                google_event_id=event_id,
                                status=CalendarEventStatus.SUCCESS,
                            ).select_related("source_item").first()
                            if cal_event and cal_event.source_item and not cal_event.source_item.is_deleted:
                                item = cal_event.source_item
                                item.is_deleted = True
                                item.deleted_at = django_timezone.now()
                                item.save(update_fields=["is_deleted", "deleted_at"])
                                CalendarEvent.all_objects.filter(source_item=item).update(
                                    is_deleted=True,
                                    deleted_at=django_timezone.now(),
                                    status=CalendarEventStatus.CANCELLED,
                                )
                                deleted_count += 1
                                logger.info("Soft-deleted IngestItem %s (event %s deleted on Google)", item.id, event_id)
                                continue
                            batch_ev = BatchCalendarEvent.objects.filter(
                                batch_request__user=channel.user,
                                google_event_id=event_id,
                                status=BatchEventStatus.SUCCESS,
                            ).select_related("batch_request").first()
                            if batch_ev:
                                batch_ev.status = BatchEventStatus.CANCELLED
                                batch_ev.save(update_fields=["status", "updated_at"])
                                deleted_count += 1
                                logger.info("Cancelled BatchCalendarEvent %s (event %s deleted on Google)", batch_ev.id, event_id)
                    new_sync_token = response.get("nextSyncToken", "")
                    page_token = response.get("nextPageToken")
                    if not page_token:
                        break
                    params["pageToken"] = page_token
                if new_sync_token:
                    channel.sync_token = new_sync_token
                    channel.save(update_fields=["sync_token", "updated_at"])
                else:
                    channel.sync_token = ""
                    channel.save(update_fields=["sync_token", "updated_at"])
                return {"success": True, "deleted_count": deleted_count, "new_sync_token": new_sync_token}
            except Exception as retry_e:
                return {"success": False, "error": str(retry_e)}
        return {"success": False, "error": err_str}
