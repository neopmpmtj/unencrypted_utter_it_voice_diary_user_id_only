"""
Invoice persistence: create IngestItem, run intent router, persist FinancialRecord,
FinancialItems, and HypermarketLineItems from parsed invoice JSON.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from src.common.logging_utils.logging_config import get_logger
from src.ingestion.models import IngestItem, IngestStatus, ItemType, Provider
from src.intent_router.services import route_utterance

from src.financial_parser.models import (
    FinancialItem,
    FinancialRecord,
    FinancialRecordStatus,
    HypermarketLineItem,
)

logger = get_logger("invoice_parser.persistence")


def _parse_invoice_date(value: Any) -> Optional[date]:
    """Parse invoice_date from JSON (YYYY-MM-DD)."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except (ValueError, TypeError):
            pass
    return None


def _to_decimal(value: Any) -> Decimal:
    """Convert to Decimal safely."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip().replace(",", "."))
        except Exception:
            return Decimal("0")
    return Decimal("0")


def persist_invoice_to_db(
    user,
    parsed_invoice: Dict[str, Any],
    message_id: str,
    filename: str,
) -> Optional[IngestItem]:
    """
    Persist a parsed invoice to the database.

    Creates IngestItem, runs intent router, creates FinancialRecord + FinancialItems
    + HypermarketLineItems. Skips if parsed_invoice contains an error.

    Returns:
        Created IngestItem, or None if skipped (error in parsed data).
    """
    if parsed_invoice.get("error"):
        return None

    vendor_name = parsed_invoice.get("vendor_name") or "Unknown"
    total_amount = _to_decimal(parsed_invoice.get("total_amount", 0))
    currency = parsed_invoice.get("currency") or "EUR"
    line_items = parsed_invoice.get("line_items") or []
    invoice_date = _parse_invoice_date(parsed_invoice.get("invoice_date"))
    invoice_number = parsed_invoice.get("invoice_number") or ""

    summary = f"{vendor_name} invoice {total_amount} {currency}, {len(line_items)} items"
    title = vendor_name[:255]

    try:
        with transaction.atomic():
            ingest_item = IngestItem.objects.create(
                user=user,
                provider=Provider.GMAIL,
                item_type=ItemType.EMAIL,
                content_text=summary,
                summary_text="",
                title=title,
                external_id=message_id or None,
                source_filename=filename or None,
                occurred_at=timezone.now(),
                status=IngestStatus.PROCESSED,
            )
    except IntegrityError:
        existing = IngestItem.objects.filter(
            user=user,
            provider=Provider.GMAIL,
            external_id=message_id or None,
        ).first()
        if not existing:
            raise
        has_financial_record = FinancialRecord.objects.filter(
            source_item=existing,
            status=FinancialRecordStatus.SUCCESS,
        ).exists()
        if has_financial_record:
            return existing
        ingest_item = existing

    route_utterance(
        json.dumps(parsed_invoice),
        context_hint="This input is a JSON object representing a pre-parsed grocery/food invoice. Route it as finance.",
        user=user,
        ingest_item=ingest_item,
    )

    record_name = vendor_name[:255]
    record_context = f"Invoice {invoice_number}" + (
        f" dated {invoice_date}" if invoice_date else ""
    )

    financial_record = FinancialRecord.objects.create(
        user=user,
        source_item=ingest_item,
        created_by=user,
        record_name=record_name,
        record_context=record_context,
        llm_response=parsed_invoice,
        status=FinancialRecordStatus.SUCCESS,
    )

    for idx, line in enumerate(line_items):
        desc = line.get("description") or ""
        qty = _to_decimal(line.get("quantity", 1))
        unit = _to_decimal(line.get("unit_price", 0))
        total = _to_decimal(line.get("total", 0))
        if total == 0 and unit and qty:
            total = unit * qty

        FinancialItem.objects.create(
            financial_record=financial_record,
            item_index=idx,
            type="expense",
            amount=total,
            currency=currency,
            merchant=vendor_name[:255],
            description=desc[:500] if isinstance(desc, str) else str(desc)[:500],
            transaction_date=invoice_date,
            item_data=line,
        )

        HypermarketLineItem.objects.create(
            financial_record=financial_record,
            line_index=idx,
            description=desc[:1000] if isinstance(desc, str) else str(desc)[:1000],
            quantity=qty,
            unit_price=unit,
            total=total,
            gmail_message_id=message_id or None,
            gmail_filename=filename or None,
        )

    return ingest_item
