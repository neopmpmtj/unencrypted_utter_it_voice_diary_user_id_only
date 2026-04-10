"""
Classification Celery Tasks — v14

Background task for the 2-pass LLM classification pipeline with
triage-driven downstream parser dispatch (triage result is authoritative;
TaxonomyParserRoute is retained as low-confidence fallback).
"""

import logging

from celery import shared_task
from django.utils import timezone

from src.ingestion.models import IngestItem, IngestJob, JobType, JobStatus
from src.ingestion.tasks import log_api_usage
from src.common.model_picker import get_llm_config

from .services import classify_item, get_parser_routes_for_run

logger = logging.getLogger(__name__)

# ── Triage → parser route mapping ────────────────────────────────────────────
_TRIAGE_TO_PARSER = {
    "task":       ["todo"],
    "event":      ["calendar"],
    "collection": ["list"],
    "finance":    ["financial"],
    "note":       [],
    "other":      [],
}

_TRIAGE_CONFIDENCE_THRESHOLD = 0.60


def get_channel_layer():
    """Get the channel layer for WebSocket broadcasts."""
    try:
        from channels.layers import get_channel_layer as channels_get_layer
        return channels_get_layer()
    except ImportError:
        logger.warning("channels not available, WebSocket broadcasts disabled")
        return None


def broadcast_classification_status(channel_layer, item_id, status, message=''):
    """Send classification status update via WebSocket."""
    if not channel_layer:
        return
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{item_id}",
            {
                "type": "classification.status",
                "status": status,
                "message": message,
            }
        )
    except Exception as e:
        logger.debug("Could not broadcast classification status: %s", e)


@shared_task(bind=True, max_retries=3)
def classify_item_task(self, item_id: str, completion_content: str = '', completion_language: str = ''):
    """
    Celery task to classify an IngestItem with the v14 taxonomy pipeline.

    1. Triage: route_utterance() → primary_route + signals
    2. Taxonomy classification: classify_item() → ItemClassificationRun + entities
    3. Parser dispatch driven by TRIAGE result (not TaxonomyParserRoute).
       Low-confidence fallback: TaxonomyParserRoute matching.
    4. If no parsers triggered, broadcasts pipeline.complete and indexes.
    """
    logger.info("Starting classification task for item %s", item_id)

    try:
        item = IngestItem.objects.select_related("user").get(id=item_id)
    except IngestItem.DoesNotExist:
        logger.error("IngestItem %s not found", item_id)
        return {"success": False, "error": "Item not found"}

    from src.accounts.models import UserFeatureConfig
    try:
        user_config = UserFeatureConfig.get_for_user(item.user)
        if not user_config.enable_auto_classification:
            logger.info("Auto-classification disabled for user %s, skipping", item.user_id)
            return {"success": True, "skipped": True, "reason": "Auto-classification disabled"}
    except Exception as e:
        logger.warning("Could not get user config, proceeding with classification: %s", e)

    job, _created = IngestJob.objects.get_or_create(
        user=item.user,
        item=item,
        job_type=JobType.CLASSIFY_ITEM,
        defaults={"status": JobStatus.QUEUED, "queued_at": timezone.now()},
    )

    if job.status == JobStatus.DONE:
        logger.info("Classification already completed for item %s", item_id)
        return {"success": True, "skipped": True, "reason": "Already classified"}

    channel_layer = get_channel_layer()

    try:
        job.status = JobStatus.RUNNING
        job.started_at = timezone.now()
        job.attempt_count += 1
        job.save(update_fields=["status", "started_at", "attempt_count"])

        broadcast_classification_status(channel_layer, item_id, 'running', 'Classifying content...')

        # ── STEP 1: Triage ───────────────────────────────────────────────────
        from src.intent_router.services import route_utterance
        from src.intent_router.models import ItemTriageResult

        text_for_triage = completion_content or ""
        triage = route_utterance(text_for_triage, item.title or "")

        ItemTriageResult.all_objects.update_or_create(
            item=item,
            defaults={
                "primary_route": triage.primary_route,
                "confidence": triage.confidence,
                "contains_time_reference": triage.contains_time_reference,
                "contains_multiple_items": triage.contains_multiple_items,
                "raw_output": triage.raw_response,
                "is_deleted": False,
                "deleted_at": None,
            },
        )

        logger.info(
            "triage_result",
            extra={
                "item_id": item_id,
                "primary_route": triage.primary_route,
                "confidence": triage.confidence,
            },
        )

        # ── STEP 2: Taxonomy classification ───────────────────────────────────
        result = classify_item(item)
        run_id = result.get("run_id")

        # Log API usage
        usage = result.get("usage", {})
        if item.user:
            _log_classification_usage(item, usage)

        job.status = JobStatus.DONE
        job.finished_at = timezone.now()
        job.checkpoint_data = {"run_id": run_id}
        job.save(update_fields=["status", "finished_at", "checkpoint_data"])

        logger.info("Classification completed for item %s: run=%s", item_id, run_id)
        broadcast_classification_status(channel_layer, item_id, 'complete', 'Classification done')

        # ── STEP 3: Parser dispatch via TRIAGE RESULT (not TaxonomyParserRoute) ──
        parser_actions = _resolve_parser_from_triage(triage, run_id)

        logger.info(
            "triage_dispatch",
            extra={
                "item_id": item_id,
                "primary_route": triage.primary_route,
                "confidence": triage.confidence,
                "parser_selected": parser_actions,
                "routing_source": "triage" if triage.confidence >= _TRIAGE_CONFIDENCE_THRESHOLD else "taxonomy_fallback",
            },
        )

        any_parser_queued = _dispatch_parsers(
            item, parser_actions, completion_content, completion_language,
        )

        if not any_parser_queued:
            from src.ingestion.tasks import broadcast_complete
            broadcast_complete(channel_layer, item_id, completion_content or '', completion_language or '')
            from src.retrieval.tasks import index_entry_prep_task
            index_entry_prep_task.delay(str(item.id))

        return {"success": True, "run_id": run_id}

    except Exception as exc:
        logger.error("Classification failed for item %s: %s", item_id, exc)
        job.status = JobStatus.ERROR
        job.last_error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "last_error", "finished_at"])
        broadcast_classification_status(channel_layer, item_id, 'error', str(exc))
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


def _dispatch_parsers(
    item: IngestItem,
    parser_actions: list,
    completion_content: str,
    completion_language: str,
) -> bool:
    """Dispatch downstream parsers based on resolved actions. Returns True if any queued."""
    any_queued = False

    for action in parser_actions:
        if action == "calendar":
            from src.batch_calendar.tasks import parse_batch_calendar_task
            parse_batch_calendar_task.delay(str(item.id), completion_content, completion_language)
            any_queued = True

        elif action == "list":
            from src.list_parser.tasks import parse_list_task
            parse_list_task.delay(str(item.id), completion_content, completion_language)
            any_queued = True

        elif action == "financial":
            from src.financial_parser.tasks import parse_financial_task
            parse_financial_task.delay(str(item.id), completion_content, completion_language)
            any_queued = True

        elif action == "todo":
            from src.managed_lists.tasks import parse_todo_task
            parse_todo_task.delay(str(item.id), completion_content, completion_language)
            any_queued = True

        else:
            logger.warning("Unknown parser action: %s", action)

    return any_queued


def _resolve_parser_from_triage(triage, run_id) -> list:
    """
    Return parser action list driven by triage result.

    >= 0.60 confidence → use triage primary_route mapping.
    < 0.60 confidence → fall back to TaxonomyParserRoute matching.
    """
    if triage.confidence >= _TRIAGE_CONFIDENCE_THRESHOLD:
        if triage.confidence < 0.80:
            logger.warning(
                "triage_low_confidence primary_route=%s confidence=%.2f",
                triage.primary_route,
                triage.confidence,
            )
        return list(_TRIAGE_TO_PARSER.get(triage.primary_route, []))

    # Low-confidence fallback: use existing TaxonomyParserRoute matching
    logger.warning(
        "triage_very_low_confidence primary_route=%s confidence=%.2f — falling back to taxonomy routes",
        triage.primary_route,
        triage.confidence,
    )
    if run_id:
        from .models import ItemClassificationRun
        try:
            run = ItemClassificationRun.objects.get(id=run_id)
            return get_parser_routes_for_run(run)
        except ItemClassificationRun.DoesNotExist:
            logger.warning("Classification run %s not found for fallback dispatch", run_id)
    return []


def _log_classification_usage(item: IngestItem, usage: dict):
    """Log API token usage for classification LLM calls."""
    user = item.user
    if not user:
        return

    classifier_usage = usage.get("classifier", {})
    if classifier_usage.get("total", 0) > 0:
        model = get_llm_config("taxonomy_classifier").get("model", "")
        if model:
            log_api_usage(user, model, "input_tokens", classifier_usage.get("input", 0),
                          ingest_item=item, origin="classify_item_task")
            log_api_usage(user, model, "output_tokens", classifier_usage.get("output", 0),
                          ingest_item=item, origin="classify_item_task")

    verifier_usage = usage.get("verifier", {})
    if verifier_usage.get("total", 0) > 0:
        model = get_llm_config("taxonomy_verifier").get("model", "")
        if model:
            log_api_usage(user, model, "input_tokens", verifier_usage.get("input", 0),
                          ingest_item=item, origin="classify_item_task_verifier")
            log_api_usage(user, model, "output_tokens", verifier_usage.get("output", 0),
                          ingest_item=item, origin="classify_item_task_verifier")
