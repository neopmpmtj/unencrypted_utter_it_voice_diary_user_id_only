"""
List Parser Services

OpenAI LLM-based item list extraction from natural language.
Extracts exactly one named list per input and stores items in DB.
"""

import json
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

# [Google Gemini API — google-genai library imports]
# from google import genai
# from google.genai import types
from openai import OpenAI

from src.common.logging_utils.logging_config import get_logger
from src.ingestion.models import IngestItem

from .config_list_parser.list_parser_config import get_list_parser_config, ListParserConfig
from .models import ListItem, ListRecord, ListRecordStatus

logger = get_logger("list_parser")


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
    """Parse a due_date value from LLM response. Returns None on failure or null."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except (ValueError, TypeError):
            logger.debug("Could not parse due_date: %r", value)
    return None


def _normalize_unit(value: Any) -> str:
    """Resolve a unit string to its canonical name via the DB-backed alias map."""
    if not value or not isinstance(value, str):
        return ""
    from .unit_utils import get_unit_alias_map
    alias_map = get_unit_alias_map()
    v = value.strip().lower()
    return alias_map.get(v, "")


def _parse_quantity(value: Any) -> Optional[Decimal]:
    """Parse a quantity value from LLM response. Returns None on failure or null."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value if value > 0 else None
    if isinstance(value, (int, float)) and value > 0:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            pass
    if isinstance(value, str) and value.strip():
        try:
            d = Decimal(value.strip())
            return d if d > 0 else None
        except (InvalidOperation, ValueError):
            logger.debug("Could not parse quantity: %r", value)
    return None


def _to_json_safe(obj: Any) -> Any:
    """Convert object to JSON-serializable form (Decimal -> float)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    return obj


def _persist_items_recursive(
    list_record: ListRecord,
    items_list: List[Dict[str, Any]],
    parent: Optional[ListItem],
) -> int:
    """
    Recursively persist items (flat or hierarchical) to ListItem rows (plaintext).
    Returns total count of created items.
    """
    count = 0
    for idx, item_dict in enumerate(items_list):
        unit_val = _normalize_unit(item_dict.get("unit"))
        item_data = _to_json_safe(item_dict)
        text_plain = (item_dict.get("text") or "")[:500]
        description_plain = item_dict.get("description") or ""
        li = ListItem.objects.create(
            list_record=list_record,
            parent=parent,
            item_index=idx,
            text=text_plain,
            description=description_plain,
            due_date=_parse_due_date(item_dict.get("due_date")),
            quantity=_parse_quantity(item_dict.get("quantity")),
            unit=(unit_val or "")[:30],
            item_data=item_data,
        )
        count += 1
        children = item_dict.get("children")
        if children and isinstance(children, list):
            count += _persist_items_recursive(list_record, children, parent=li)
    return count


def _validate_and_normalize_item(item_dict: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """
    Validate a single item (flat or with children). Returns normalized dict or None if invalid.
    Preserves hierarchy: children are recursively validated.
    """
    if not isinstance(item_dict, dict):
        logger.warning("Item %d is not a dict, skipping", index)
        return None
    text = item_dict.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        logger.warning("Item %d missing 'text' field, skipping", index)
        return None
    normalized: Dict[str, Any] = {
        "text": (text or "").strip(),
        "description": item_dict.get("description") or "",
        "due_date": item_dict.get("due_date"),
        "quantity": item_dict.get("quantity"),
        "unit": item_dict.get("unit"),
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


def get_item_title_and_content(item: IngestItem) -> Tuple[str, str]:
    """Get plaintext title and content from an IngestItem."""
    from src.common.utils.content import get_item_title_and_content as _get_content
    return _get_content(item)


# [Google Gemini API — Gemini usage metadata extractor]
# def _gemini_usage_dict(response) -> Dict[str, int]:
#     """Extract usage dict from Gemini response. Keys: input, output, total."""
#     um = getattr(response, "usage_metadata", None)
#     if not um:
#         return {"input": 0, "output": 0, "total": 0}
#     inp = getattr(um, "prompt_token_count", 0) or getattr(um, "input_token_count", 0)
#     out = getattr(um, "candidates_token_count", 0) or getattr(um, "output_token_count", 0)
#     total = getattr(um, "total_token_count", 0) or (inp + out)
#     return {"input": inp or 0, "output": out or 0, "total": total or 0}


def extract_list_items(
    title: str,
    content_text: str,
    config: Optional[ListParserConfig] = None,
) -> Tuple[Optional[str], Optional[str], Optional[List[Dict[str, Any]]], Optional[str], Dict[str, int]]:
    """
    Extract a named list of items from natural language using Gemini.

    Args:
        title: Title of the source item.
        content_text: Body text to parse.
        config: Optional ListParserConfig instance.

    Returns:
        Tuple of (list_name, list_context, items_list, error_message, usage_dict).
        - list_name: Inferred list name, or None on failure.
        - list_context: Optional context/occasion for the list, or None on failure.
        - items_list: List of item dicts, or None on failure.
        - error_message: Error string if extraction failed, else None.
        - usage_dict: Token usage from API (input, output, total).
    """
    if config is None:
        config = get_list_parser_config()

    if not config.openai_api_key:
        raise ValueError(
            "OpenAI API key not configured. Set OPENAI_API_KEY."
        )

    logger.debug("extract_list_items input: title=%r, content=%r", title[:100], content_text[:200])

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
            logger.debug("List extraction API call completed in %.3fs", api_duration)
            usage = response.usage
            usage_dict = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total": getattr(usage, "total_tokens", 0) if usage else 0,
            }

            response_text = (response.choices[0].message.content or "").strip()
            logger.debug("extract_list_items raw LLM response:\n%s", response_text[:500])
            response_text = _strip_markdown_json_fences(response_text)

            try:
                result = json.loads(response_text)

                if "error" in result:
                    err_msg = result["error"]
                    logger.warning("LLM returned error: %s", err_msg)
                    return None, None, None, err_msg, usage_dict

                list_name = result.get("list_name") or "itens"
                list_context = result.get("list_context") or result.get("list_description") or ""
                items = result.get("items")
                if not items or not isinstance(items, list):
                    logger.warning("LLM response missing or invalid 'items' array")
                    return None, None, None, "No items extracted from text", usage_dict

                validated: List[Dict[str, Any]] = []
                for i, item_dict in enumerate(items):
                    valid_item = _validate_and_normalize_item(item_dict, i)
                    if valid_item is not None:
                        validated.append(valid_item)

                if not validated:
                    return None, None, None, "No valid items extracted", usage_dict

                logger.info("Successfully extracted %d items in list '%s'", len(validated), list_name)
                return list_name, list_context, validated, None, usage_dict

            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON response: %s", e)
                if attempt < config.max_retries:
                    continue
                return None, None, None, f"Invalid JSON response: {e}", usage_dict

        except Exception as e:
            last_exception = e
            if attempt < config.max_retries:
                logger.warning(
                    "List extraction API call failed (attempt %d/%d): %s",
                    attempt + 1,
                    config.max_retries + 1,
                    e,
                )
            else:
                logger.error(
                    "List extraction failed after %d attempts: %s",
                    config.max_retries + 1,
                    e,
                )
                raise

    if last_exception:
        raise last_exception

    return None, None, None, "Extraction failed after retries", {}


def parse_list_item(
    item: IngestItem,
    config: Optional[ListParserConfig] = None,
) -> Dict[str, Any]:
    """
    Orchestrate list extraction for a single IngestItem.

    1. Check for existing successful ListRecord (skip if found).
    2. Decrypt content.
    3. Call extract_list_items() via Gemini.
    4. Create ListRecord + ListItem rows.

    Returns:
        Dict with result details.
    """
    if config is None:
        config = get_list_parser_config()

    user = item.user
    if not user:
        return {"success": False, "error": "Item has no associated user for list parsing"}

    existing = ListRecord.objects.filter(
        user=item.user,
        source_item=item,
        status=ListRecordStatus.SUCCESS,
    ).first()
    if existing:
        logger.info("List record already exists for item %s", item.id)
        return {
            "success": True,
            "list_record_id": str(existing.id),
            "skipped": True,
            "message": "List already exists",
            "usage": {},
        }

    title, content = get_item_title_and_content(item)
    logger.debug("parse_list_item input: title=%r, content=%r", title, content)

    if not content or not content.strip():
        error_msg = "Item has no content to parse"
        logger.warning("Item %s: %s", item.id, error_msg)
        return {"success": False, "error": error_msg, "usage": {}}

    list_record, created = ListRecord.objects.get_or_create(
        user=item.user,
        source_item=item,
        defaults={"status": ListRecordStatus.PENDING},
    )

    if not created:
        if list_record.status == ListRecordStatus.SUCCESS:
            return {"success": True, "list_record_id": str(list_record.id),
                    "skipped": True, "message": "List already exists", "usage": {}}
        if list_record.status == ListRecordStatus.PENDING:
            return {"success": True, "list_record_id": str(list_record.id),
                    "skipped": True, "message": "List parsing in progress", "usage": {}}
        # FAILED → re-attempt
        list_record.status = ListRecordStatus.PENDING
        list_record.error_message = ""
        list_record.save(update_fields=["status", "error_message", "updated_at"])

    try:
        list_name, list_context, items_list, error_msg, extract_usage = extract_list_items(title, content, config)

        if error_msg:
            list_record.mark_failed(error_msg)
            return {
                "success": False,
                "list_record_id": str(list_record.id),
                "error": error_msg,
                "usage": extract_usage,
            }

        list_record.list_name = (list_name or "itens")[:255]
        list_record.list_context = list_context or ""
        list_record.llm_response = _to_json_safe(
            {"list_name": list_name, "list_context": list_context or "", "items": items_list}
        )
        list_record.save(update_fields=["list_name", "list_context", "llm_response", "updated_at"])

        created_items = _persist_items_recursive(list_record, items_list, parent=None)

        list_record.mark_success()

        logger.info(
            "List parsing completed for item %s: %d items in list '%s'",
            item.id, created_items, list_record.list_name,
        )
        return {
            "success": True,
            "list_record_id": str(list_record.id),
            "list_name": list_record.list_name,
            "item_count": created_items,
            "usage": extract_usage,
        }

    except Exception as e:
        logger.error("List parsing failed for item %s: %s", item.id, e)
        list_record.mark_failed(str(e))
        return {
            "success": False,
            "list_record_id": str(list_record.id),
            "error": str(e),
            "usage": {},
        }


def get_list_item_data(li: ListItem, _user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get text, description, item_data from a ListItem (plaintext)."""
    item_data = li.item_data if isinstance(li.item_data, dict) else {}
    return {
        "text": li.text or "",
        "description": li.description or "",
        "item_data": item_data,
    }


def _format_quantity(q: Decimal) -> str:
    """Format quantity for display: '2' for whole numbers, '1.5' for decimals."""
    if q == int(q):
        return str(int(q))
    return str(q)


def _format_item_line(li: ListItem, text: str) -> str:
    """Format a single ListItem as a bullet line (without indent)."""
    if li.quantity is not None and li.quantity > 1:
        if li.unit:
            return f"- {_format_quantity(li.quantity)} {li.unit} {text}"
        return f"- {_format_quantity(li.quantity)} x {text}"
    if li.quantity is not None and li.quantity == 1 and li.unit:
        return f"- 1 {li.unit} {text}"
    return f"- {text}"


def _format_items_hierarchical(
    list_record: ListRecord,
    parent: Optional[ListItem],
    indent: int,
    lines: List[str],
    user_id: Optional[int],
) -> None:
    """Append formatted items (and their children) to lines. indent is spaces before '-'."""
    prefix = " " * indent
    items = list_record.items.filter(parent=parent).order_by("item_index")
    for li in items:
        item_data = get_list_item_data(li, user_id)
        lines.append(prefix + _format_item_line(li, item_data["text"]))
        _format_items_hierarchical(list_record, li, indent + 2, lines, user_id)


def format_list_for_display(list_record: ListRecord) -> str:
    """Format a successful ListRecord as 'List Name\\n[list_context]\\n- item1\\n  - child1\\n...'."""
    user_id = list_record.source_item.user_id if list_record.source_item else None
    lines = [list_record.list_name or "itens"]
    if list_record.list_context:
        lines.append(list_record.list_context)
    _format_items_hierarchical(list_record, parent=None, indent=0, lines=lines, user_id=user_id)
    return "\n".join(lines)


def get_list_display_content(list_record: ListRecord) -> tuple[str, dict]:
    """Format list and enhance via LLM. Fallback to raw format on error. Returns (display_text, usage_dict)."""
    raw = format_list_for_display(list_record)
    from src.list_parser.list_formatter.services import enhance_list_display
    return enhance_list_display(raw)


def _parse_bullet_content(stripped: str) -> Tuple[str, Optional[Decimal], str]:
    """Parse bullet content into (text, quantity, unit)."""
    qty_val = None
    unit_val = ""
    item_text = stripped

    from .unit_utils import get_unit_alias_map
    all_unit_keys = sorted(get_unit_alias_map().keys(), key=len, reverse=True)
    unit_pattern = "|".join(re.escape(k) for k in all_unit_keys) if all_unit_keys else "kg|litre|unit"
    match_qty_unit = re.match(
        rf"^(?P<qty>\d+(?:[.,]\d+)?)\s+(?P<unit>{unit_pattern})\s+(?P<text>.+)$",
        stripped,
        re.IGNORECASE,
    )
    if match_qty_unit:
        try:
            qty_str = match_qty_unit.group("qty").replace(",", ".")
            qty_val = Decimal(qty_str) if float(qty_str) > 0 else None
        except (ValueError, InvalidOperation):
            pass
        unit_val = _normalize_unit(match_qty_unit.group("unit"))
        item_text = match_qty_unit.group("text").strip()
    else:
        match_x = re.match(
            r"^(?P<qty>\d+(?:[.,]\d+)?)\s*x\s+(?P<text>.+)$",
            stripped,
            re.IGNORECASE,
        )
        if match_x:
            try:
                qty_str = match_x.group("qty").replace(",", ".")
                qty_val = Decimal(qty_str) if float(qty_str) > 0 else None
            except (ValueError, InvalidOperation):
                pass
            item_text = match_x.group("text").strip()
        else:
            paren_match = re.search(
                r"\s*\((?P<qty>\d+(?:[.,]\d+)?)\)\s*$", stripped
            )
            if paren_match:
                try:
                    qty_str = paren_match.group("qty").replace(",", ".")
                    qty_val = Decimal(qty_str) if float(qty_str) > 0 else None
                except (ValueError, InvalidOperation):
                    pass
                item_text = re.sub(
                    r"\s*\(\d+(?:[.,]\d+)?\)\s*$", "", stripped
                ).strip()
    return item_text, qty_val, unit_val


def parse_formatted_list_text(text: str) -> Tuple[str, str, List[Dict[str, Any]]]:
    """
    Parse user-edited bullet-point text back into (list_name, list_context, items).

    Expected format:
        List Name
        [list_context - optional, line 2 if not a bullet]
        - item one
        - 2 x item two
        - parent
          - child one
          - child two

    Indentation: 2 spaces per nesting level. Returns hierarchical items with optional "children".
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "itens", "", []

    list_name = lines[0].strip().lstrip("- ").strip() or "itens"
    list_context = ""
    start_idx = 1
    if len(lines) > 1 and not lines[1].strip().startswith("- "):
        list_context = lines[1].strip()
        start_idx = 2

    items: List[Dict[str, Any]] = []
    stack: List[Tuple[int, List[Dict[str, Any]]]] = [(0, items)]

    for ln in lines[start_idx:]:
        indent = len(ln) - len(ln.lstrip())
        stripped = ln.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if not stripped:
            continue

        level = indent // 2
        while stack and stack[-1][0] >= level:
            stack.pop()
        if not stack:
            stack.append((0, items))

        item_text, qty_val, unit_val = _parse_bullet_content(stripped)
        item_dict: Dict[str, Any] = {"text": item_text}
        if qty_val is not None:
            item_dict["quantity"] = qty_val
        if unit_val:
            item_dict["unit"] = unit_val

        parent_list = stack[-1][1]
        parent_list.append(item_dict)
        children = item_dict.setdefault("children", [])
        stack.append((level, children))

    def _strip_empty_children(obj: Any) -> None:
        if isinstance(obj, dict):
            if "children" in obj and obj["children"] == []:
                del obj["children"]
            for v in obj.values():
                _strip_empty_children(v)
        elif isinstance(obj, list):
            for v in obj:
                _strip_empty_children(v)

    _strip_empty_children(items)
    return list_name, list_context, items


def save_list_from_formatted_text(
    item: IngestItem,
    formatted_text: str,
) -> Optional[ListRecord]:
    """
    Parse formatted bullet-point text and persist as ListRecord + ListItems.
    Deletes any existing ListRecord for the item first.
    Returns the new ListRecord on success, None if no items parsed.
    """
    if not item.user_id:
        logger.warning("save_list_from_formatted_text: item %s has no user", item.id)
        return None

    list_name, list_context, items = parse_formatted_list_text(formatted_text)
    if not items:
        return None

    delete_list_records_for_item(item)

    from src.ingestion.models import IngestJob, JobType
    IngestJob.objects.filter(item=item, job_type=JobType.PARSE_LIST).delete()

    items_for_json = _to_json_safe(items)

    record = ListRecord.objects.create(
        user=item.user,
        source_item=item,
        list_name=(list_name or "itens")[:255],
        list_context=list_context or "",
        llm_response={"list_name": list_name, "list_context": list_context or "", "items": items_for_json},
        status=ListRecordStatus.SUCCESS,
    )
    count = _persist_items_recursive(record, items, parent=None)
    logger.info(
        "Saved %d items in list '%s' for item %s (from formatted text)",
        count, record.list_name, item.id,
    )
    return record


def soft_delete_list_item_and_descendants(li: ListItem, now) -> None:
    """
    Soft-delete a ListItem and all its descendants (children, grandchildren, etc.).
    """
    to_delete = [li.id]
    current_level = [li.id]
    while current_level:
        children = list(
            ListItem.all_objects.filter(parent_id__in=current_level).values_list("id", flat=True)
        )
        to_delete.extend(children)
        current_level = children
    ListItem.all_objects.filter(id__in=to_delete).update(deleted_at=now)


def soft_delete_list_items_for_records(record_ids: List[Any], now) -> None:
    """Soft-delete all ListItems belonging to the given record IDs."""
    if not record_ids:
        return
    ListItem.all_objects.filter(list_record_id__in=record_ids).update(deleted_at=now)


def delete_list_records_for_item(item: IngestItem) -> None:
    """
    Soft-delete all ListRecords (and their items) linked to the given IngestItem.
    Called when an entry is deleted.
    """
    from django.utils import timezone as tz

    records = ListRecord.all_objects.filter(source_item=item, is_deleted=False)
    record_ids = list(records.values_list("id", flat=True))
    count = len(record_ids)
    if count == 0:
        return

    now = tz.now()
    records.update(is_deleted=True, deleted_at=now, status=ListRecordStatus.FAILED)
    soft_delete_list_items_for_records(record_ids, now)
    logger.info("Soft-deleted %d list record(s) for item %s", count, item.id)
