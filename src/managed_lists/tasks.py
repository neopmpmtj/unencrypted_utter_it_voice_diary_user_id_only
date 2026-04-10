"""
Managed Lists / To-Do Celery Tasks

Background tasks for pipeline-triggered to-do parsing.
Routed here when content is tagged with a todo intent taxonomy key.
"""

import logging

from celery import shared_task
from celery.exceptions import Retry
from django.utils import timezone as django_timezone

from src.common.logging_utils.logging_config import get_logger
from src.common.model_picker import get_llm_config
from src.ingestion.tasks import log_api_usage
from src.ingestion.models import IngestItem, IngestJob, JobType, JobStatus
from src.ingestion.tasks import broadcast_complete

from .services import parse_todo_item

logger = get_logger("managed_lists")


def _mark_todo_record_failed(item_id: str, error_msg: str):
    from .models import TodoRecord, ManagedRecordStatus
    TodoRecord.objects.filter(
        source_item_id=item_id, status=ManagedRecordStatus.PENDING,
    ).update(status=ManagedRecordStatus.FAILED, error_message=error_msg[:500])


def get_channel_layer():
    """Get the channel layer for WebSocket broadcasts."""
    try:
        from channels.layers import get_channel_layer as channels_get_layer
        return channels_get_layer()
    except ImportError:
        logger.warning("channels not available, WebSocket broadcasts disabled")
        return None


def broadcast_todo_status(channel_layer, item_id, status, message="", extra_data=None):
    """Send to-do parsing status update via WebSocket."""
    if not channel_layer:
        return
    try:
        from asgiref.sync import async_to_sync
        payload = {
            "type": "todo.status",
            "status": status,
            "message": message,
        }
        if extra_data:
            payload.update(extra_data)
        async_to_sync(channel_layer.group_send)(f"pipeline_{item_id}", payload)
    except Exception as e:
        logger.debug("Could not broadcast todo status: %s", e)


@shared_task(bind=True, max_retries=3)
def parse_todo_task(
    self,
    item_id: str,
    completion_content: str = "",
    completion_language: str = "",
):
    """
    Celery task to parse to-do items from an IngestItem.

    1. Decrypts content, extracts tasks via Gemini.
    2. Creates TodoRecord + TodoItem rows in DB.
    3. Populates ManagedListProjection rows.
    4. Broadcasts completion.
    """
    logger.info("Starting todo parsing task for item %s", item_id)

    try:
        try:
            item = IngestItem.objects.select_related("user").get(id=item_id)
        except IngestItem.DoesNotExist:
            logger.error("IngestItem %s not found", item_id)
            return {"success": False, "error": "Item not found"}

        job, _ = IngestJob.objects.get_or_create(
            user=item.user,
            item=item,
            job_type=JobType.PARSE_TODO,
            defaults={"status": JobStatus.QUEUED, "queued_at": django_timezone.now()},
        )
        job.status = JobStatus.RUNNING
        job.started_at = django_timezone.now()
        job.attempt_count += 1
        job.save(update_fields=["status", "started_at", "attempt_count"])

        channel_layer = get_channel_layer()
        broadcast_todo_status(channel_layer, item_id, "running", "Extracting to-do items...")

        result = parse_todo_item(item)

        usage = result.get("usage", {})
        if usage and item.user and (usage.get("input", 0) + usage.get("output", 0) > 0):
            model = get_llm_config("todo_parser").get("model", "")
            if model:
                log_api_usage(
                    item.user,
                    model,
                    "input_tokens",
                    usage.get("input", 0),
                    ingest_item=item,
                    origin="parse_todo_task",
                )
                log_api_usage(
                    item.user,
                    model,
                    "output_tokens",
                    usage.get("output", 0),
                    ingest_item=item,
                    origin="parse_todo_task",
                )

        display_content = completion_content or ""

        if result.get("success"):
            job.status = JobStatus.DONE
            job.finished_at = django_timezone.now()
            job.checkpoint_data = {
                "todo_record_id": result.get("todo_record_id"),
                "item_count": result.get("item_count", 0),
            }
            job.save(update_fields=["status", "finished_at", "checkpoint_data"])

            message = f"Extracted {result.get('item_count', 0)} task(s) in '{result.get('record_name', '')}'"
            broadcast_todo_status(channel_layer, item_id, "complete", message)

        else:
            job.status = JobStatus.ERROR
            job.last_error = result.get("error", "Unknown error")
            job.finished_at = django_timezone.now()
            job.save(update_fields=["status", "last_error", "finished_at"])

            broadcast_todo_status(channel_layer, item_id, "error", result.get("error", ""))

        broadcast_complete(channel_layer, item_id, display_content, completion_language or "")
        from src.retrieval.tasks import index_entry_prep_task
        index_entry_prep_task.delay(str(item.id))

        logger.info("Todo parsing task finished for item %s: %s", item_id, result)
        return result

    except Retry:
        raise
    except Exception as exc:
        logger.error("Todo parsing task failed for item %s: %s", item_id, exc)
        channel_layer = get_channel_layer()
        broadcast_todo_status(channel_layer, item_id, "error", str(exc))
        broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
        try:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            _mark_todo_record_failed(item_id, str(exc))
            raise
