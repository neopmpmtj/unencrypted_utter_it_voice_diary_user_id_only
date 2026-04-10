"""
Batch Calendar Celery Tasks

Background tasks for pipeline-triggered batch calendar parsing.
Pro/Ultra users are routed here when content is tagged with calendar.
"""

import copy
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from celery import shared_task
from celery.exceptions import Retry
from django.utils import timezone as django_timezone

from src.common.google_account.auth import GoogleAuthError
from src.common.logging_utils.logging_config import get_logger
from src.common.model_picker import get_llm_config
from src.ingestion.models import IngestItem, IngestJob, JobType, JobStatus
from src.ingestion.tasks import broadcast_complete, log_api_usage
from src.accounts.models import UserFeatureConfig

from .calendar_client import insert_event
from .conflict_resolution import check_batch_availability
from .config_batch_calendar.batch_calendar_config import get_batch_calendar_config
from .models import BatchCalendarEvent, BatchCalendarRequest, BatchEventStatus, BatchRequestStatus
from .services import extract_batch_events

logger = get_logger("batch_calendar")


def get_channel_layer():
    """Get the channel layer for WebSocket broadcasts."""
    try:
        from channels.layers import get_channel_layer as channels_get_layer
        return channels_get_layer()
    except ImportError:
        logger.warning("channels not available, WebSocket broadcasts disabled")
        return None


def _get_calendar_id_for_user(user):
    """Get calendar_id from user config or default."""
    config = get_batch_calendar_config()
    calendar_id = config.calendar_id
    if user:
        try:
            uf = UserFeatureConfig.get_for_user(user)
            if uf.default_calendar_id:
                calendar_id = uf.default_calendar_id
        except Exception:
            pass
    return calendar_id


def _ensure_aware(dt, tz_str):
    """Make datetime timezone-aware."""
    if dt is None:
        return None
    if dt.tzinfo:
        return dt
    try:
        tz = ZoneInfo(tz_str)
        return django_timezone.make_aware(dt, tz)
    except Exception:
        return dt


def _event_data_to_google_format(event_data, start_dt, end_dt, tz_str):
    """Build Google Calendar event body with updated start/end."""
    body = copy.deepcopy(event_data)
    body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_str}
    body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_str}
    return body


def broadcast_batch_calendar_status(channel_layer, item_id, status, message="", extra_data=None):
    """Send batch calendar parsing status update via WebSocket."""
    if not channel_layer:
        return
    try:
        from asgiref.sync import async_to_sync
        payload = {
            "type": "calendar.status",
            "status": status,
            "message": message,
        }
        if extra_data:
            payload.update(extra_data)
        async_to_sync(channel_layer.group_send)(f"pipeline_{item_id}", payload)
    except Exception as e:
        logger.debug("Could not broadcast batch calendar status: %s", e)


@shared_task(bind=True, max_retries=3)
def parse_batch_calendar_task(
    self,
    item_id: str,
    completion_content: str = "",
    completion_language: str = "",
):
    """
    Celery task to parse batch calendar events from an IngestItem.

    For pro/ultra users when content is tagged with calendar.
    1. Decrypts content, extracts events via Gemini
    2. Checks availability for each event
    3. If no conflicts: auto-inserts all events, broadcasts complete
    4. If conflicts: creates BatchCalendarRequest, broadcasts conflict with confirmation_url
    """
    logger.info("Starting batch calendar parsing task for item %s", item_id)

    try:
        try:
            item = IngestItem.objects.select_related("user").get(id=item_id)
        except IngestItem.DoesNotExist:
            logger.error("IngestItem %s not found", item_id)
            return {"success": False, "error": "Item not found"}

        # Check for existing successful batch
        existing = BatchCalendarRequest.objects.filter(
            ingest_item=item,
            status__in=[BatchRequestStatus.CONFIRMED, BatchRequestStatus.PARTIAL],
        ).exists()
        if existing:
            logger.info("Batch already processed for item %s", item_id)
            channel_layer = get_channel_layer()
            broadcast_batch_calendar_status(channel_layer, item_id, "complete", "Events already created")
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            return {"success": True, "skipped": True, "reason": "Already processed"}

        pending = BatchCalendarRequest.objects.filter(
            ingest_item=item, status=BatchRequestStatus.PENDING,
        ).exists()
        if pending:
            logger.info("Calendar parsing already in progress for item %s", item_id)
            channel_layer = get_channel_layer()
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            return {"success": True, "skipped": True, "reason": "In progress"}

        try:
            user_config = UserFeatureConfig.get_for_user(item.user)
        except Exception as e:
            logger.warning("Could not get user config: %s", e)
            user_config = None

        if user_config and not user_config.enable_calendar_integration:
            logger.info("Calendar integration disabled for user %s", item.user_id)
            channel_layer = get_channel_layer()
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            return {"success": True, "skipped": True, "reason": "Calendar integration disabled"}

        user = item.user
        if not user:
            logger.error("Item %s has no associated user", item_id)
            channel_layer = get_channel_layer()
            broadcast_batch_calendar_status(channel_layer, item_id, "error", "No user for authentication")
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            return {"success": False, "error": "No user for authentication"}

        from src.common.utils.content import get_item_title_and_content

        title, content = get_item_title_and_content(item)
        content_text = (title or "") + "\n" + (content or "") if title else (content or "")
        content_text = content_text.strip()

        if not content_text:
            logger.warning("Item %s has no content to parse", item_id)
            channel_layer = get_channel_layer()
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            return {"success": False, "error": "No content to parse"}

        channel_layer = get_channel_layer()
        broadcast_batch_calendar_status(channel_layer, item_id, "running", "Extracting calendar events...")

        # Resolve user timezone
        config = get_batch_calendar_config()
        try:
            user_timezone = user.preferences.timezone
        except Exception:
            user_timezone = config.default_timezone
        if not user_timezone:
            user_timezone = config.default_timezone
        logger.debug("parse_batch_calendar_task: user_timezone=%s for item %s", user_timezone, item_id)

        # Extract events
        events, error_msg, usage = extract_batch_events(content_text, user_timezone=user_timezone)
        if usage and item.user and (usage.get("input", 0) + usage.get("output", 0) > 0):
            model = get_llm_config("batch_calendar").get("model", "")
            if model:
                log_api_usage(
                    item.user,
                    model,
                    "input_tokens",
                    usage.get("input", 0),
                    ingest_item=item,
                    origin="parse_batch_calendar_task",
                )
                log_api_usage(
                    item.user,
                    model,
                    "output_tokens",
                    usage.get("output", 0),
                    ingest_item=item,
                    origin="parse_batch_calendar_task",
                )
        if error_msg:
            logger.error("Batch extraction failed for item %s: %s", item_id, error_msg)
            broadcast_batch_calendar_status(channel_layer, item_id, "error", error_msg)
            raise Exception(error_msg)

        if not events:
            logger.warning("No events extracted for item %s", item_id)
            broadcast_batch_calendar_status(channel_layer, item_id, "error", "No events could be extracted")
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            return {"success": False, "error": "No events extracted"}

        calendar_id = _get_calendar_id_for_user(item.user)

        try:
            conflict_results = check_batch_availability(
                user,
                events,
                calendar_id=calendar_id,
                default_timezone=user_timezone,
            )
        except GoogleAuthError as e:
            logger.warning("Google auth error during batch conflict check: %s", e)
            broadcast_batch_calendar_status(
                channel_layer,
                item_id,
                "error",
                "Google Calendar authentication failed. Please reconnect your Google account.",
            )
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            return {"success": False, "error": "Google Calendar authentication failed"}

        # Create IngestJob for tracking
        job, _ = IngestJob.objects.get_or_create(
            user=item.user,
            item=item,
            job_type=JobType.PARSE_CALENDAR,
            defaults={"status": JobStatus.QUEUED, "queued_at": django_timezone.now()},
        )
        job.status = JobStatus.RUNNING
        job.started_at = django_timezone.now()
        job.attempt_count += 1
        job.save(update_fields=["status", "started_at", "attempt_count"])

        has_conflicts = any(not r["is_available"] for r in conflict_results)

        batch = BatchCalendarRequest.objects.create(
            user=user,
            ingest_item=item,
            input_text=content_text,
            parsed_events_json=events,
            status=BatchRequestStatus.PENDING,
        )

        for cr in conflict_results:
            ev_data = cr["event_data"]
            tz_str = ev_data.get("start", {}).get("timeZone") or user_timezone
            BatchCalendarEvent.objects.create(
                batch_request=batch,
                event_index=cr["event_index"],
                event_data=ev_data,
                summary=(ev_data.get("summary") or "")[:255],
                start_datetime=cr.get("start_datetime"),
                end_datetime=cr.get("end_datetime"),
                timezone=tz_str,
                status=(
                    BatchEventStatus.PENDING_CONFIRMATION
                    if not cr["is_available"]
                    else BatchEventStatus.PENDING
                ),
                conflicting_events=cr.get("conflicting_events", []),
                alternative_slots=cr.get("alternative_slots", []),
                alternative_slots_by_day=cr.get("alternative_slots_by_day", []),
            )

        if not has_conflicts:
            # Auto-insert all events
            tz_str = user_timezone
            success_count = 0
            for batch_ev in batch.events.all().order_by("event_index"):
                ev_data = batch_ev.event_data
                ev_tz = ev_data.get("start", {}).get("timeZone") or tz_str
                start_dt = batch_ev.start_datetime
                end_dt = batch_ev.end_datetime
                if not start_dt or not end_dt:
                    batch_ev.mark_failed("Invalid datetime")
                    continue
                body_ev = _event_data_to_google_format(ev_data, start_dt, end_dt, ev_tz)
                try:
                    api_resp = insert_event(user, body_ev, calendar_id)
                except GoogleAuthError:
                    batch_ev.mark_failed("Google Calendar authentication failed")
                    continue
                if api_resp:
                    batch_ev.mark_success(api_resp.get("id", ""), api_resp)
                    success_count += 1
                else:
                    batch_ev.mark_failed("Google Calendar API error")

            batch.status = BatchRequestStatus.CONFIRMED
            batch.save()

            job.status = JobStatus.DONE
            job.finished_at = django_timezone.now()
            job.checkpoint_data = {"batch_id": str(batch.id), "inserted_count": success_count}
            job.save(update_fields=["status", "finished_at", "checkpoint_data"])

            message = f"Created {success_count} calendar event(s)"
            broadcast_batch_calendar_status(channel_layer, item_id, "complete", message)
            broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            logger.info("Batch calendar parsing completed for item %s: %d events created", item_id, success_count)
            return {"success": True, "batch_id": str(batch.id), "inserted_count": success_count}
        else:
            # Conflicts: broadcast and redirect to confirmation page
            job.status = JobStatus.DONE
            job.finished_at = django_timezone.now()
            job.checkpoint_data = {
                "batch_id": str(batch.id),
                "conflict": True,
                "confirmation_url": f"/batch-calendar/confirm/{batch.id}/",
            }
            job.save(update_fields=["status", "finished_at", "checkpoint_data"])

            broadcast_batch_calendar_status(
                channel_layer,
                item_id,
                "calendar_conflict",
                "Time slot conflict detected. Please confirm or choose alternative times.",
                extra_data={
                    "conflict": True,
                    "confirmation_url": f"/batch-calendar/confirm/{batch.id}/",
                    "batch_id": str(batch.id),
                },
            )
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))
            logger.info("Batch conflict detected for item %s, needs confirmation", item_id)
            return {"success": False, "conflict": True, "batch_id": str(batch.id)}

    except Retry:
        raise
    except Exception as exc:
        logger.error("Batch calendar parsing failed for item %s: %s", item_id, exc)
        channel_layer = get_channel_layer()
        broadcast_batch_calendar_status(channel_layer, item_id, "error", str(exc))
        broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
        try:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            BatchCalendarRequest.objects.filter(
                ingest_item_id=item_id, status=BatchRequestStatus.PENDING,
            ).update(status=BatchRequestStatus.FAILED)
            raise


@shared_task
def sync_calendar_changes_task(channel_id: str):
    """
    Process incremental calendar sync when Google sends a push notification.
    """
    from .services import process_calendar_sync

    result = process_calendar_sync(channel_id)
    if result.get("success"):
        deleted = result.get("deleted_count", 0)
        if deleted:
            logger.info("Calendar sync for channel %s: processed %d entries", channel_id, deleted)
    else:
        logger.warning("Calendar sync failed for channel %s: %s", channel_id, result.get("error"))


@shared_task
def poll_calendar_changes_task():
    """
    Poll all active calendar watch channels for changes (deletions).
    Used when webhooks are unavailable (e.g. localhost) or as a fallback.
    """
    from .models import CalendarWatchChannel

    from .services import process_calendar_sync

    channels = list(
        CalendarWatchChannel.objects.filter(is_active=True).values_list("channel_id", flat=True)
    )
    for channel_id in channels:
        result = process_calendar_sync(channel_id)
        if result.get("success"):
            deleted = result.get("deleted_count", 0)
            if deleted:
                logger.info("Calendar poll for channel %s: processed %d entries", channel_id, deleted)
        else:
            logger.warning("Calendar poll failed for channel %s: %s", channel_id, result.get("error"))


@shared_task
def renew_calendar_watches_task():
    """
    Renew expiring watch channels and set up watches for new users with calendar access.
    Run periodically (e.g. every 6 hours) via Celery Beat.
    """
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone as django_timezone

    from src.accounts.models import UserFeatureConfig as _UserFeatureConfig, UserSecret
    from .models import CalendarWatchChannel

    from .services import setup_watch_for_user, stop_watch_channel

    now = django_timezone.now()
    renew_threshold = now + timedelta(hours=12)

    channels_to_renew = list(
        CalendarWatchChannel.objects.filter(
            is_active=True,
            expiration__isnull=False,
            expiration__lte=renew_threshold,
        ).select_related("user")
    )

    for channel in channels_to_renew:
        user = channel.user
        calendar_id = channel.calendar_id
        stop_watch_channel(channel)
        channel.is_active = False
        channel.save(update_fields=["is_active", "updated_at"])
        new_channel = setup_watch_for_user(user, calendar_id)
        if new_channel:
            logger.info("Renewed calendar watch for user %s", user.id)

    users_with_calendar = set()
    for us in UserSecret.objects.filter(
        encrypted_google_access_token__isnull=False,
    ).exclude(encrypted_google_access_token=""):
        try:
            if us.has_calendar_permission():
                users_with_calendar.add(us.user_id)
        except Exception:
            pass

    users_with_channel = set(
        CalendarWatchChannel.objects.filter(is_active=True).values_list("user_id", flat=True)
    )
    users_needing_channel = users_with_calendar - users_with_channel

    for user_id in users_needing_channel:
        try:
            User = get_user_model()
            user = User.objects.get(id=user_id)
            user_config = _UserFeatureConfig.get_for_user(user)
            if not user_config.enable_calendar_integration:
                continue
            calendar_id = getattr(user_config, "default_calendar_id", None) or "primary"
            new_channel = setup_watch_for_user(user, calendar_id)
            if new_channel:
                logger.info("Created calendar watch for user %s", user_id)
            else:
                logger.warning("Watch setup failed for user %s", user_id)
        except Exception as e:
            logger.debug("Could not setup watch for user %s: %s", user_id, e)
