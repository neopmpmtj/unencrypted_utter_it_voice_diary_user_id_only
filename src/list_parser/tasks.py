"""
List Parser Celery Tasks

Background tasks for pipeline-triggered list parsing.
All users are routed here when content is tagged with 'lista'.
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

from .models import ListRecord
from .services import parse_list_item

logger = get_logger("list_parser")


def _mark_list_record_failed(item_id: str, error_msg: str):
    from .models import ListRecord, ListRecordStatus
    ListRecord.objects.filter(
        source_item_id=item_id, status=ListRecordStatus.PENDING,
    ).update(status=ListRecordStatus.FAILED, error_message=error_msg[:500])


def get_channel_layer():
    """Get the channel layer for WebSocket broadcasts."""
    try:
        from channels.layers import get_channel_layer as channels_get_layer
        return channels_get_layer()
    except ImportError:
        logger.warning("channels not available, WebSocket broadcasts disabled")
        return None


def broadcast_list_status(channel_layer, item_id, status, message="", extra_data=None):
    """Send list parsing status update via WebSocket."""
    if not channel_layer:
        return
    try:
        from asgiref.sync import async_to_sync
        payload = {
            "type": "list.status",
            "status": status,
            "message": message,
        }
        if extra_data:
            payload.update(extra_data)
        async_to_sync(channel_layer.group_send)(f"pipeline_{item_id}", payload)
    except Exception as e:
        logger.debug("Could not broadcast list status: %s", e)


@shared_task(bind=True, max_retries=3)
def parse_list_task(
    self,
    item_id: str,
    completion_content: str = "",
    completion_language: str = "",
):
    """
    Celery task to parse a list from an IngestItem.

    All tiers are routed here when content is tagged with 'lista'.
    1. Decrypts content, extracts items via Gemini.
    2. Creates ListRecord + ListItem rows in DB.
    3. Broadcasts completion.
    """
    logger.info("Starting list parsing task for item %s", item_id)

    try:
        try:
            item = IngestItem.objects.select_related("user").get(id=item_id)
        except IngestItem.DoesNotExist:
            logger.error("IngestItem %s not found", item_id)
            return {"success": False, "error": "Item not found"}

        job, _ = IngestJob.objects.get_or_create(
            user=item.user,
            item=item,
            job_type=JobType.PARSE_LIST,
            defaults={"status": JobStatus.QUEUED, "queued_at": django_timezone.now()},
        )
        job.status = JobStatus.RUNNING
        job.started_at = django_timezone.now()
        job.attempt_count += 1
        job.save(update_fields=["status", "started_at", "attempt_count"])

        channel_layer = get_channel_layer()
        broadcast_list_status(channel_layer, item_id, "running", "Extracting list items...")

        result = parse_list_item(item)

        usage = result.get("usage", {})
        if usage and item.user and (usage.get("input", 0) + usage.get("output", 0) > 0):
            model = get_llm_config("list_parser").get("model", "")
            if model:
                log_api_usage(
                    item.user,
                    model,
                    "input_tokens",
                    usage.get("input", 0),
                    ingest_item=item,
                    origin="parse_list_task",
                )
                log_api_usage(
                    item.user,
                    model,
                    "output_tokens",
                    usage.get("output", 0),
                    ingest_item=item,
                    origin="parse_list_task",
                )

        display_content = completion_content or ""

        if result.get("success"):
            job.status = JobStatus.DONE
            job.finished_at = django_timezone.now()
            job.checkpoint_data = {
                "list_record_id": result.get("list_record_id"),
                "item_count": result.get("item_count", 0),
            }
            job.save(update_fields=["status", "finished_at", "checkpoint_data"])

            message = f"Extracted {result.get('item_count', 0)} item(s) in list '{result.get('list_name', '')}'"
            broadcast_list_status(channel_layer, item_id, "complete", message)

        else:
            job.status = JobStatus.ERROR
            job.last_error = result.get("error", "Unknown error")
            job.finished_at = django_timezone.now()
            job.save(update_fields=["status", "last_error", "finished_at"])

            broadcast_list_status(channel_layer, item_id, "error", result.get("error", ""))

        broadcast_complete(channel_layer, item_id, display_content, completion_language or "")
        from src.retrieval.tasks import index_entry_prep_task
        index_entry_prep_task.delay(str(item.id))

        logger.info("List parsing task finished for item %s: %s", item_id, result)
        return result

    except Retry:
        raise
    except Exception as exc:
        logger.error("List parsing task failed for item %s: %s", item_id, exc)
        channel_layer = get_channel_layer()
        broadcast_list_status(channel_layer, item_id, "error", str(exc))
        broadcast_complete(channel_layer, item_id, completion_content or "", completion_language or "")
        try:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            _mark_list_record_failed(item_id, str(exc))
            raise
