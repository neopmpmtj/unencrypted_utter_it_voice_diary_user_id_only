"""
Financial Parser Services

OpenAI LLM-based expense and income extraction from natural language.
Extracts financial entries and stores them in DB.
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

from django.utils.translation import gettext as _

from src.common.logging_utils.logging_config import get_logger
from src.ingestion.models import IngestItem

from .config_financial_parser.financial_parser_config import (
    FinancialParserConfig,
    get_financial_parser_config,
)
from .models import FinancialItem, FinancialRecord, FinancialRecordStatus

logger = get_logger("financial_parser")


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


def _parse_transaction_date(value: Any) -> Optional[date]:
    """Parse transaction_date from LLM response."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except (ValueError, TypeError):
            logger.debug("Could not parse transaction_date: %r", value)
    return None


def _parse_amount(value: Any) -> Optional[Decimal]:
    """Parse amount from LLM response. Returns None on failure."""
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
            cleaned = value.strip().replace(",", ".")
            d = Decimal(cleaned)
            return d if d > 0 else None
        except (InvalidOperation, ValueError):
            logger.debug("Could not parse amount: %r", value)
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


def _validate_and_normalize_financial_item(item_dict: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """Validate a single financial item. Returns normalized dict or None if invalid."""
    if not isinstance(item_dict, dict):
        logger.warning("Financial item %d is not a dict, skipping", index)
        return None
    amount = _parse_amount(item_dict.get("amount"))
    if amount is None:
        logger.warning("Financial item %d missing or invalid 'amount', skipping", index)
        return None
    item_type = (item_dict.get("type") or "expense").strip().lower()
    if item_type not in ("expense", "income"):
        item_type = "expense"
    currency = (item_dict.get("currency") or "EUR").strip().upper()[:10] or "EUR"
    return {
        "type": item_type,
        "amount": amount,
        "currency": currency,
        "category": (item_dict.get("category") or "")[:100],
        "merchant": (item_dict.get("merchant") or "")[:255],
        "transaction_date": item_dict.get("transaction_date"),
        "description": item_dict.get("description") or "",
        "payment_method": (item_dict.get("payment_method") or "")[:50],
        "item_data": item_dict,
    }


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


def extract_financial_items(
    title: str,
    content_text: str,
    config: Optional[FinancialParserConfig] = None,
) -> Tuple[Optional[str], Optional[str], Optional[List[Dict[str, Any]]], Optional[str], Dict[str, int]]:
    """
    Extract financial entries from natural language using Gemini.

    Returns:
        Tuple of (record_name, record_context, items_list, error_message, usage_dict).
    """
    if config is None:
        config = get_financial_parser_config()

    if not config.openai_api_key:
        raise ValueError(
            "OpenAI API key not configured. Set OPENAI_API_KEY."
        )

    logger.debug("extract_financial_items input: title=%r, content=%r", title[:100], content_text[:200])

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
            logger.debug("Financial extraction API call completed in %.3fs", api_duration)
            usage = response.usage
            usage_dict = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total": getattr(usage, "total_tokens", 0) if usage else 0,
            }

            response_text = (response.choices[0].message.content or "").strip()
            logger.debug("extract_financial_items raw LLM response:\n%s", response_text[:500])
            response_text = _strip_markdown_json_fences(response_text)

            try:
                result = json.loads(response_text)

                if "error" in result:
                    err_msg = result["error"]
                    logger.warning("LLM returned error: %s", err_msg)
                    return None, None, None, err_msg, usage_dict

                record_name = result.get("record_name") or "Despesas"
                record_context = result.get("record_context") or ""
                items = result.get("items")
                if not items or not isinstance(items, list):
                    logger.warning("LLM response missing or invalid 'items' array")
                    return None, None, None, "No financial items extracted from text", usage_dict

                validated: List[Dict[str, Any]] = []
                for i, item_dict in enumerate(items):
                    valid_item = _validate_and_normalize_financial_item(item_dict, i)
                    if valid_item is not None:
                        validated.append(valid_item)

                if not validated:
                    return None, None, None, "No valid financial items extracted", usage_dict

                logger.info("Successfully extracted %d financial items in '%s'", len(validated), record_name)
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
                    "Financial extraction API call failed (attempt %d/%d): %s",
                    attempt + 1,
                    config.max_retries + 1,
                    e,
                )
            else:
                logger.error(
                    "Financial extraction failed after %d attempts: %s",
                    config.max_retries + 1,
                    e,
                )
                raise

    if last_exception:
        raise last_exception

    return None, None, None, "Extraction failed after retries", {}


def _persist_financial_items(
    financial_record: FinancialRecord,
    items_list: List[Dict[str, Any]],
    user_id: int,
) -> int:
    """Persist financial items to DB. Returns total count of created items."""
    count = 0
    for idx, item_dict in enumerate(items_list):
        FinancialItem.objects.create(
            financial_record=financial_record,
            item_index=idx,
            type=item_dict.get("type", "expense"),
            amount=item_dict["amount"],
            currency=item_dict.get("currency", "EUR"),
            category=item_dict.get("category", ""),
            merchant=item_dict.get("merchant", ""),
            transaction_date=_parse_transaction_date(item_dict.get("transaction_date")),
            description=item_dict.get("description", ""),
            payment_method=item_dict.get("payment_method", ""),
            item_data=_to_json_safe(item_dict.get("item_data", item_dict)),
        )
        count += 1
    return count


def parse_financial_item(
    item: IngestItem,
    config: Optional[FinancialParserConfig] = None,
) -> Dict[str, Any]:
    """
    Orchestrate financial extraction for a single IngestItem.

    1. Check for existing successful FinancialRecord (skip if found).
    2. Decrypt content.
    3. Call extract_financial_items() via Gemini.
    4. Create FinancialRecord + FinancialItem rows.

    Returns:
        Dict with result details.
    """
    if config is None:
        config = get_financial_parser_config()

    user = item.user
    if not user:
        return {"success": False, "error": _("Item has no associated user for financial parsing")}

    existing = FinancialRecord.objects.filter(
        user=item.user,
        source_item=item,
        status=FinancialRecordStatus.SUCCESS,
    ).first()
    if existing:
        logger.info("Financial record already exists for item %s", item.id)
        return {
            "success": True,
            "financial_record_id": str(existing.id),
            "skipped": True,
            "message": _("Financial record already exists"),
            "usage": {},
        }

    title, content = get_item_title_and_content(item)
    logger.debug("parse_financial_item input: title=%r, content=%r", title, content)

    if not content or not content.strip():
        error_msg = _("Item has no content to parse")
        logger.warning("Item %s: %s", item.id, error_msg)
        return {"success": False, "error": error_msg, "usage": {}}

    record, created = FinancialRecord.objects.get_or_create(
        user=item.user,
        source_item=item,
        defaults={"status": FinancialRecordStatus.PENDING},
    )

    if not created:
        if record.status == FinancialRecordStatus.SUCCESS:
            return {"success": True, "financial_record_id": str(record.id),
                    "skipped": True, "message": _("Financial record already exists"), "usage": {}}
        if record.status == FinancialRecordStatus.PENDING:
            return {"success": True, "financial_record_id": str(record.id),
                    "skipped": True, "message": _("Financial parsing in progress"), "usage": {}}
        # FAILED → re-attempt
        record.status = FinancialRecordStatus.PENDING
        record.error_message = ""
        record.save(update_fields=["status", "error_message", "updated_at"])

    try:
        record_name, record_context, items_list, error_msg, extract_usage = extract_financial_items(title, content, config)

        if error_msg:
            record.mark_failed(error_msg)
            return {
                "success": False,
                "financial_record_id": str(record.id),
                "error": error_msg,
                "usage": extract_usage,
            }

        record.record_name = (record_name or _("Despesas"))[:255]
        record.record_context = record_context or ""
        record.llm_response = _to_json_safe(
            {"record_name": record_name, "record_context": record_context or "", "items": items_list}
        )
        record.save(update_fields=["record_name", "record_context", "llm_response", "updated_at"])

        created_items = _persist_financial_items(record, items_list, user_id=user.id)

        record.mark_success()

        logger.info(
            "Financial parsing completed for item %s: %d items in '%s'",
            item.id, created_items, record.record_name,
        )
        return {
            "success": True,
            "financial_record_id": str(record.id),
            "record_name": record.record_name,
            "item_count": created_items,
            "usage": extract_usage,
        }

    except Exception as e:
        logger.error("Financial parsing failed for item %s: %s", item.id, e)
        record.mark_failed(str(e))
        return {
            "success": False,
            "financial_record_id": str(record.id),
            "error": str(e),
            "usage": {},
        }


def format_financial_for_display(record: FinancialRecord) -> str:
    """Format a successful FinancialRecord as bullet list for display."""
    lines = [record.record_name or _("Despesas")]
    if record.record_context:
        lines.append(record.record_context)
    items = record.items.order_by("item_index")
    for fi in items:
        desc = fi.description or fi.merchant or fi.category or "item"
        lines.append(f"- {desc}: {fi.amount} {fi.currency}")
    return "\n".join(lines)


def get_financial_display_content(record: FinancialRecord) -> tuple[str, dict]:
    """Format financial record and enhance via LLM. Fallback to raw format on error. Returns (display_text, usage_dict)."""
    from src.financial_parser.financial_formatter.services import enhance_financial_display
    return enhance_financial_display(record)


def _parse_financial_bullet(stripped: str) -> Optional[Tuple[str, Decimal, str]]:
    """Parse bullet line into (description, amount, currency). Returns None if invalid."""
    stripped = stripped.strip()
    if not stripped:
        return None
    match = re.match(
        r"^-\s*(.+?)\s*[:]\s*([\d.,]+)\s+(\w+)\s*$",
        stripped,
        re.IGNORECASE,
    )
    if match:
        desc, amt_str, curr = match.group(1).strip(), match.group(2), match.group(3).upper()
        try:
            amount = Decimal(amt_str.replace(",", "."))
            if amount > 0:
                return (desc, amount, curr)
        except (InvalidOperation, ValueError):
            pass
    match2 = re.match(
        r"^-\s*(.+?)\s+([\d.,]+)\s+(\w+)\s*$",
        stripped,
        re.IGNORECASE,
    )
    if match2:
        desc, amt_str, curr = match2.group(1).strip(), match2.group(2), match2.group(3).upper()
        try:
            amount = Decimal(amt_str.replace(",", "."))
            if amount > 0:
                return (desc, amount, curr)
        except (InvalidOperation, ValueError):
            pass
    return None


def parse_formatted_financial_text(text: str) -> Tuple[str, str, List[Dict[str, Any]]]:
    """
    Parse user-edited financial text back into (record_name, record_context, items).

    Expected format:
        Record Name
        [record_context - optional, line 2 if not a bullet]
        - desc: amount currency
        - desc: amount currency
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "Despesas", "", []

    record_name = lines[0].strip().lstrip("- ").strip() or "Despesas"
    record_context = ""
    start_idx = 1
    if len(lines) > 1 and not lines[1].strip().startswith("- "):
        record_context = lines[1].strip()
        start_idx = 2

    items: List[Dict[str, Any]] = []
    for ln in lines[start_idx:]:
        stripped = ln.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        parsed = _parse_financial_bullet("- " + stripped) if stripped else None
        if parsed:
            desc, amount, currency = parsed
            items.append({
                "type": "expense",
                "amount": amount,
                "currency": currency,
                "category": "",
                "merchant": "",
                "transaction_date": None,
                "description": desc,
                "payment_method": "",
            })
    return record_name, record_context, items


def save_financial_from_formatted_text(
    item: IngestItem,
    formatted_text: str,
) -> Optional[FinancialRecord]:
    """
    Parse formatted financial text and persist as FinancialRecord + FinancialItems.
    Deletes any existing FinancialRecord for the item first.
    Returns the new FinancialRecord on success, None if no items parsed.
    """
    if not item.user_id:
        logger.warning("save_financial_from_formatted_text: item %s has no user", item.id)
        return None

    record_name, record_context, items = parse_formatted_financial_text(formatted_text)
    if not items:
        return None

    delete_financial_records_for_item(item)

    from src.ingestion.models import IngestJob, JobType
    IngestJob.objects.filter(item=item, job_type=JobType.PARSE_FINANCIAL).delete()

    record = FinancialRecord.objects.create(
        user=item.user,
        source_item=item,
        record_name=(record_name or "Despesas")[:255],
        record_context=record_context or "",
        llm_response={"record_name": record_name, "record_context": record_context or "", "items": _to_json_safe(items)},
        status=FinancialRecordStatus.SUCCESS,
    )
    count = _persist_financial_items(record, items, user_id=item.user_id)
    logger.info(
        "Saved %d financial items in '%s' for item %s (from formatted text)",
        count, record.record_name, item.id,
    )
    return record


def soft_delete_financial_items_for_records(record_ids: List[Any], now) -> None:
    """Soft-delete all FinancialItems belonging to the given record IDs."""
    if not record_ids:
        return
    FinancialItem.all_objects.filter(financial_record_id__in=record_ids).update(deleted_at=now)


def delete_financial_records_for_item(item: IngestItem) -> None:
    """Soft-delete all FinancialRecords (and their items) linked to the given IngestItem."""
    from django.utils import timezone as tz

    records = FinancialRecord.all_objects.filter(source_item=item, is_deleted=False)
    record_ids = list(records.values_list("id", flat=True))
    count = len(record_ids)
    if count == 0:
        return

    now = tz.now()
    records.update(is_deleted=True, deleted_at=now, status=FinancialRecordStatus.FAILED)
    soft_delete_financial_items_for_records(record_ids, now)
    logger.info("Soft-deleted %d financial record(s) for item %s", count, item.id)
