"""
Managed Lists / To-Do Services

OpenAI LLM-based to-do item extraction from natural language.
Extracts actionable tasks and stores them as TodoRecord + TodoItem rows.
"""

import json
import logging
import time
from datetime import date, datetime, time as time_type
from typing import Any, Dict, List, Optional, Tuple

# [Google Gemini API — google-genai library imports]
# from google import genai
# from google.genai import types
from openai import OpenAI

from src.common.logging_utils.logging_config import get_logger
from src.ingestion.models import IngestItem

from .config_todo_parser.todo_parser_config import get_todo_parser_config, TodoParserConfig
from .models import (
    ManagedListProjection,
    ManagedListType,
    ManagedRecordStatus,
    TodoCompletionStatus,
    TodoItem,
    TodoPriority,
    TodoRecord,
)

logger = get_logger("managed_lists")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

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


def _parse_due_date(value: Any) -> Optional[date]:
    """Parse a due_date value from LLM response."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except (ValueError, TypeError):
            logger.debug("Could not parse due_date: %r", value)
    return None


def _parse_due_time(value: Any) -> Optional[time_type]:
    """Parse a due_time value from LLM response (HH:MM or HH:MM:SS)."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        try:
            parts = value.strip().split(":")
            if len(parts) >= 2:
                return time_type(int(parts[0]), int(parts[1]))
        except (ValueError, TypeError):
            logger.debug("Could not parse due_time: %r", value)
    return None


def _parse_priority(value: Any) -> int:
    """Parse a priority value (1-5). Defaults to MEDIUM (3)."""
    if value is None:
        return TodoPriority.MEDIUM
    try:
        p = int(value)
        if 1 <= p <= 5:
            return p
    except (ValueError, TypeError):
        pass
    return TodoPriority.MEDIUM


def _to_json_safe(obj: Any) -> Any:
    """Convert object to JSON-serializable form."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    return obj


# [Google Gemini API — Gemini usage metadata extractor]
# def _gemini_usage_dict(response) -> Dict[str, int]:
#     """Extract usage dict from Gemini response."""
#     um = getattr(response, "usage_metadata", None)
#     if not um:
#         return {"input": 0, "output": 0, "total": 0}
#     inp = getattr(um, "prompt_token_count", 0) or getattr(um, "input_token_count", 0)
#     out = getattr(um, "candidates_token_count", 0) or getattr(um, "output_token_count", 0)
#     total = getattr(um, "total_token_count", 0) or (inp + out)
#     return {"input": inp or 0, "output": out or 0, "total": total or 0}


VALID_ENTITY_TYPES = {
    "person", "organization", "vendor", "location", "project",
    "contact", "client", "product", "device", "account", "document", "unknown",
}

VALID_RECURRENCE_RULES = {"daily", "weekly", "monthly"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_and_normalize_item(item_dict: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """Validate a single todo item. Returns normalized dict or None if invalid."""
    if not isinstance(item_dict, dict):
        logger.warning("Item %d is not a dict, skipping", index)
        return None
    text = item_dict.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        logger.warning("Item %d missing 'text' field, skipping", index)
        return None

    entity_type_raw = (item_dict.get("entity_type") or "").strip().lower()
    entity_type = entity_type_raw if entity_type_raw in VALID_ENTITY_TYPES else ""

    recurrence = (item_dict.get("recurrence_rule") or "").strip().lower()
    recurrence = recurrence if recurrence in VALID_RECURRENCE_RULES else ""

    normalized: Dict[str, Any] = {
        "text": text.strip(),
        "description": (item_dict.get("description") or "").strip(),
        "priority": _parse_priority(item_dict.get("priority")),
        "due_date": item_dict.get("due_date"),
        "due_time": item_dict.get("due_time"),
        "topic": (item_dict.get("topic") or "").strip()[:255],
        "subtopic": (item_dict.get("subtopic") or "").strip()[:255],
        "recurrence_rule": recurrence,
        "entity_name": (item_dict.get("entity_name") or "").strip()[:255],
        "entity_type": entity_type,
    }

    children = item_dict.get("children")
    if children is not None and isinstance(children, list):
        validated_children: List[Dict[str, Any]] = []
        for j, child_dict in enumerate(children):
            valid_child = _validate_and_normalize_item(child_dict, j)
            if valid_child is not None:
                validated_children.append(valid_child)
        normalized["children"] = validated_children

    return normalized


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

def _resolve_entity(user, entity_name: str, entity_type: str):
    """
    Look up or create an EntityCatalog entry for the given entity.
    Returns (EntityCatalog instance, canonical_name, entity_type) or (None, name, type).
    """
    if not entity_name:
        return None, "", ""

    from src.classification.models import EntityCatalog, EntityType

    normalized = entity_name.strip().lower()

    type_map = {v.lower(): v for v in EntityType.values}
    resolved_type = type_map.get(entity_type, EntityType.UNKNOWN)

    try:
        entity = EntityCatalog.objects.filter(
            user=user,
            normalized_name=normalized,
        ).first()

        if entity:
            return entity, entity.canonical_name, entity.entity_type

        entity = EntityCatalog.objects.create(
            user=user,
            entity_type=resolved_type,
            canonical_name=entity_name.strip(),
            normalized_name=normalized,
        )
        return entity, entity.canonical_name, entity.entity_type
    except Exception as exc:
        logger.warning("Entity resolution failed for %r: %s", entity_name, exc)
        return None, entity_name.strip(), entity_type


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _persist_items_recursive(
    todo_record: TodoRecord,
    items_list: List[Dict[str, Any]],
    parent: Optional[TodoItem],
    user=None,
) -> int:
    """
    Recursively persist items to TodoItem rows (plaintext).
    Returns total count of created items.
    """
    count = 0
    for idx, item_dict in enumerate(items_list):
        item_data = _to_json_safe(item_dict)
        text_plain = (item_dict.get("text") or "")[:500]
        description_plain = item_dict.get("description") or ""

        entity_name = item_dict.get("entity_name", "")
        entity_type = item_dict.get("entity_type", "")
        entity_obj = None
        if entity_name and user:
            entity_obj, entity_name, entity_type = _resolve_entity(user, entity_name, entity_type)

        ti = TodoItem.objects.create(
            todo_record=todo_record,
            parent=parent,
            item_index=idx,
            text=text_plain,
            description=description_plain,
            priority=_parse_priority(item_dict.get("priority")),
            completion_status=TodoCompletionStatus.OPEN,
            due_date=_parse_due_date(item_dict.get("due_date")),
            due_time=_parse_due_time(item_dict.get("due_time")),
            topic=(item_dict.get("topic") or "")[:255],
            subtopic=(item_dict.get("subtopic") or "")[:255],
            recurrence_rule=(item_dict.get("recurrence_rule") or "")[:100],
            entity=entity_obj,
            entity_name=entity_name[:255] if entity_name else "",
            entity_type=entity_type[:30] if entity_type else "",
            item_data=item_data,
        )
        count += 1

        children = item_dict.get("children")
        if children and isinstance(children, list):
            count += _persist_items_recursive(todo_record, children, parent=ti, user=user)
    return count


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def get_item_title_and_content(item: IngestItem) -> Tuple[str, str]:
    """Get plaintext title and content from an IngestItem."""
    from src.common.utils.content import get_item_title_and_content as _get_content
    return _get_content(item)


def get_todo_item_data(ti: TodoItem, _user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get text, description, item_data from a TodoItem (plaintext)."""
    item_data = ti.item_data if isinstance(ti.item_data, dict) else {}
    return {
        "text": ti.text or "",
        "description": ti.description or "",
        "item_data": item_data,
    }


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

def extract_todo_items(
    title: str,
    content_text: str,
    config: Optional[TodoParserConfig] = None,
) -> Tuple[Optional[str], Optional[str], Optional[List[Dict[str, Any]]], Optional[str], Dict[str, int]]:
    """
    Extract to-do items from natural language using Gemini.

    Returns:
        Tuple of (record_name, record_context, items_list, error_message, usage_dict).
    """
    if config is None:
        config = get_todo_parser_config()

    if not config.openai_api_key:
        raise ValueError(
            "OpenAI API key not configured. Set OPENAI_API_KEY."
        )

    logger.debug("extract_todo_items input: title=%r, content=%r", title[:100], content_text[:200])

    now = datetime.now()
    system_date = now.strftime("%Y-%m-%d")
    system_time = now.strftime("%H:%M:%S")
    prompt = config.get_prompt(title, content_text, system_date, system_time)

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
                    attempt, config.max_retries, delay,
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
            logger.debug("Todo extraction API call completed in %.3fs", api_duration)
            usage = response.usage
            usage_dict = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total": getattr(usage, "total_tokens", 0) if usage else 0,
            }

            response_text = (response.choices[0].message.content or "").strip()
            logger.debug("extract_todo_items raw LLM response:\n%s", response_text[:500])
            response_text = _strip_markdown_json_fences(response_text)

            try:
                result = json.loads(response_text)

                if "error" in result:
                    err_msg = result["error"]
                    logger.warning("LLM returned error: %s", err_msg)
                    return None, None, None, err_msg, usage_dict

                record_name = result.get("record_name") or "tasks"
                record_context = result.get("record_context") or ""
                items = result.get("items")
                if not items or not isinstance(items, list):
                    logger.warning("LLM response missing or invalid 'items' array")
                    return None, None, None, "No tasks extracted from text", usage_dict

                validated: List[Dict[str, Any]] = []
                for i, item_dict in enumerate(items):
                    valid_item = _validate_and_normalize_item(item_dict, i)
                    if valid_item is not None:
                        validated.append(valid_item)

                if not validated:
                    return None, None, None, "No valid tasks extracted", usage_dict

                logger.info("Successfully extracted %d tasks in '%s'", len(validated), record_name)
                return record_name, record_context, validated, None, usage_dict

            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON response: %s", e)
                if attempt < config.max_retries:
                    continue
                return None, None, None, f"Invalid JSON response: {e}", usage_dict

        except Exception as e:
            last_exception = e
            if attempt < config.max_retries:
                logger.warning(
                    "Todo extraction API call failed (attempt %d/%d): %s",
                    attempt + 1, config.max_retries + 1, e,
                )
            else:
                logger.error(
                    "Todo extraction failed after %d attempts: %s",
                    config.max_retries + 1, e,
                )
                raise

    if last_exception:
        raise last_exception

    return None, None, None, "Extraction failed after retries", {}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def parse_todo_item(
    item: IngestItem,
    config: Optional[TodoParserConfig] = None,
) -> Dict[str, Any]:
    """
    Orchestrate to-do extraction for a single IngestItem.

    1. Check for existing successful TodoRecord (skip if found).
    2. Decrypt content.
    3. Call extract_todo_items() via Gemini.
    4. Create TodoRecord + TodoItem rows.
    5. Populate ManagedListProjection rows.

    Returns:
        Dict with result details.
    """
    if config is None:
        config = get_todo_parser_config()

    user = item.user
    if not user:
        return {"success": False, "error": "Item has no associated user for todo parsing"}

    existing = TodoRecord.objects.filter(
        user=item.user,
        source_item=item,
        status=ManagedRecordStatus.SUCCESS,
    ).first()
    if existing:
        logger.info("Todo record already exists for item %s", item.id)
        return {
            "success": True,
            "todo_record_id": str(existing.id),
            "skipped": True,
            "message": "Todo record already exists",
            "usage": {},
        }

    title, content = get_item_title_and_content(item)
    logger.debug("parse_todo_item input: title=%r, content=%r", title, content)

    if not content or not content.strip():
        error_msg = "Item has no content to parse"
        logger.warning("Item %s: %s", item.id, error_msg)
        return {"success": False, "error": error_msg, "usage": {}}

    todo_record, created = TodoRecord.objects.get_or_create(
        user=item.user,
        source_item=item,
        defaults={"status": ManagedRecordStatus.PENDING, "created_by": user},
    )

    if not created:
        if todo_record.status == ManagedRecordStatus.SUCCESS:
            return {"success": True, "todo_record_id": str(todo_record.id),
                    "skipped": True, "message": "Todo record already exists", "usage": {}}
        if todo_record.status == ManagedRecordStatus.PENDING:
            return {"success": True, "todo_record_id": str(todo_record.id),
                    "skipped": True, "message": "Todo parsing in progress", "usage": {}}
        # FAILED → re-attempt
        todo_record.status = ManagedRecordStatus.PENDING
        todo_record.error_message = ""
        todo_record.save(update_fields=["status", "error_message", "updated_at"])

    try:
        record_name, record_context, items_list, error_msg, extract_usage = extract_todo_items(
            title, content, config,
        )

        if error_msg:
            todo_record.mark_failed(error_msg)
            return {
                "success": False,
                "todo_record_id": str(todo_record.id),
                "error": error_msg,
                "usage": extract_usage,
            }

        todo_record.record_name = (record_name or "tasks")[:255]
        todo_record.record_context = record_context or ""
        todo_record.llm_response = _to_json_safe(
            {"record_name": record_name, "record_context": record_context or "", "items": items_list}
        )
        todo_record.save(update_fields=["record_name", "record_context", "llm_response", "updated_at"])

        created_items = _persist_items_recursive(
            todo_record, items_list, parent=None, user=item.user,
        )

        todo_record.mark_success()

        # Populate ManagedListProjection
        from .projections import refresh_projection_for_todo_record
        refresh_projection_for_todo_record(todo_record)

        logger.info(
            "Todo parsing completed for item %s: %d tasks in '%s'",
            item.id, created_items, todo_record.record_name,
        )
        return {
            "success": True,
            "todo_record_id": str(todo_record.id),
            "record_name": todo_record.record_name,
            "item_count": created_items,
            "usage": extract_usage,
        }

    except Exception as e:
        logger.error("Todo parsing failed for item %s: %s", item.id, e)
        todo_record.mark_failed(str(e))
        return {
            "success": False,
            "todo_record_id": str(todo_record.id),
            "error": str(e),
            "usage": {},
        }


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def get_item_and_descendant_ids(item: TodoItem) -> list:
    """
    Return the item's ID plus all descendant IDs (recursive).
    Used when deleting a parent: CASCADE will remove children, so we must
    account for them in soft_delete_todo_record_if_last_item.
    """
    ids = [item.id]
    frontier = [item.id]
    while frontier:
        children = list(
            TodoItem.all_objects.filter(parent_id__in=frontier).values_list("id", flat=True)
        )
        ids.extend(children)
        frontier = children
    return ids


def soft_delete_todo_record_if_last_item(todo_record: TodoRecord, item_ids_being_deleted: list) -> None:
    """
    Soft-delete TodoRecord if we are removing its last remaining item(s).
    item_ids_being_deleted: IDs of TodoItems we are about to delete that belong to this record.
    """
    from django.utils import timezone as tz

    remaining = todo_record.items.exclude(id__in=item_ids_being_deleted).count()
    if remaining > 0:
        return
    now = tz.now()
    TodoRecord.all_objects.filter(pk=todo_record.pk).update(
        is_deleted=True, deleted_at=now, status=ManagedRecordStatus.FAILED
    )
    logger.info("Soft-deleted todo record %s (last item removed)", todo_record.id)


def soft_delete_todo_items_for_records(record_ids: list, now) -> None:
    """Soft-delete all TodoItems belonging to the given record IDs."""
    if not record_ids:
        return
    TodoItem.all_objects.filter(todo_record_id__in=record_ids).update(
        is_deleted=True, deleted_at=now
    )


def delete_todo_records_for_item(item: IngestItem) -> None:
    """Soft-delete all TodoRecords (and their TodoItems) linked to the given IngestItem."""
    from django.utils import timezone as tz

    records = TodoRecord.all_objects.filter(source_item=item, is_deleted=False)
    record_ids = list(records.values_list("id", flat=True))
    count = len(record_ids)
    if count == 0:
        return

    now = tz.now()
    records.update(is_deleted=True, deleted_at=now, status=ManagedRecordStatus.FAILED)
    soft_delete_todo_items_for_records(record_ids, now)
    logger.info("Soft-deleted %d todo record(s) for item %s", count, item.id)


# ---------------------------------------------------------------------------
# Unified search
# ---------------------------------------------------------------------------

def search_managed_items(
    user,
    user_id: int,
    list_type: Optional[str] = None,
    topic: Optional[str] = None,
    subtopic: Optional[str] = None,
    status: Optional[str] = None,
    due_before=None,
    due_after=None,
    query: Optional[str] = None,
    entity_name: Optional[str] = None,
    entity_type: Optional[str] = None,
):
    """
    Search ManagedListProjection with optional filters.
    Returns a QuerySet of ManagedListProjection rows.
    """
    qs = ManagedListProjection.objects.filter(user=user)

    if list_type:
        qs = qs.filter(list_type=list_type)
    if topic:
        qs = qs.filter(topic__icontains=topic)
    if subtopic:
        qs = qs.filter(subtopic__icontains=subtopic)
    if status:
        qs = qs.filter(item_status=status)
    if due_before:
        qs = qs.filter(due_date__lte=due_before)
    if due_after:
        qs = qs.filter(due_date__gte=due_after)
    if entity_name:
        qs = qs.filter(entity_name__icontains=entity_name)
    if entity_type:
        qs = qs.filter(entity_type=entity_type)
    if query:
        from django.db.models import Q
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(category__icontains=query)
            | Q(entity_name__icontains=query)
        )

    return qs.order_by("-created_at")
