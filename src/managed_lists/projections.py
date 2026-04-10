"""
Managed List Projections

Functions to populate the ManagedListProjection table from concrete
parser records. Called explicitly in each parser's success path.
"""

import logging
from typing import Optional

from src.common.logging_utils.logging_config import get_logger
from .models import (
    ManagedListProjection,
    ManagedListType,
    TodoItem,
    TodoRecord,
)

logger = get_logger("managed_lists.projections")


def refresh_projection_for_todo_record(todo_record: TodoRecord) -> int:
    """
    Create/refresh ManagedListProjection rows for all items in a TodoRecord.
    Deletes existing projection rows for this record first.
    Returns the number of projection rows created.
    """
    ManagedListProjection.objects.filter(record_id=todo_record.id).delete()

    count = 0

    def _project_items(parent: Optional[TodoItem] = None):
        nonlocal count
        items = todo_record.items.filter(parent=parent).order_by("item_index")
        for ti in items:
            text_plain = ti.text or ""
            description_plain = ti.description or ""

            ManagedListProjection.objects.create(
                user=todo_record.user,
                source_ingest_item=todo_record.source_item,
                list_type=ManagedListType.TODO,
                record_id=todo_record.id,
                item_id=ti.id,
                title=text_plain,
                description=description_plain,
                category=todo_record.record_name or "",
                topic=ti.topic or "",
                subtopic=ti.subtopic or "",
                item_status=ti.completion_status,
                priority=ti.priority,
                due_date=ti.due_date,
                entity_name=ti.entity_name or "",
                entity_type=ti.entity_type or "",
                entity_catalog_id=ti.entity_id,
            )
            count += 1
            _project_items(parent=ti)

    _project_items()
    logger.info(
        "Refreshed %d projection rows for TodoRecord %s", count, todo_record.id,
    )
    return count


def refresh_projection_for_list_record(list_record) -> int:
    """
    Create/refresh ManagedListProjection rows for all items in a ListRecord.
    For Phase 1 backfill of existing list_parser records.
    Returns the number of projection rows created.
    """
    ManagedListProjection.objects.filter(record_id=list_record.id).delete()

    count = 0

    def _project_items(parent=None):
        nonlocal count
        items = list_record.items.filter(parent=parent).order_by("item_index")
        for li in items:
            text_plain = li.text or ""
            description_plain = li.description or ""

            list_type = ManagedListType.GENERAL
            list_name_lower = (list_record.list_name or "").lower()
            shopping_keywords = {"compras", "shopping", "supermercado", "groceries", "mercado"}
            if any(kw in list_name_lower for kw in shopping_keywords):
                list_type = ManagedListType.SHOPPING

            ManagedListProjection.objects.create(
                user=list_record.user,
                source_ingest_item=list_record.source_item,
                list_type=list_type,
                record_id=list_record.id,
                item_id=li.id,
                title=text_plain,
                description=description_plain,
                category=list_record.list_name or "",
                quantity=li.quantity,
                unit=li.unit or "",
                due_date=li.due_date,
            )
            count += 1
            _project_items(parent=li)

    _project_items()
    logger.info(
        "Refreshed %d projection rows for ListRecord %s", count, list_record.id,
    )
    return count


def refresh_projection_for_financial_record(financial_record) -> int:
    """
    Create/refresh ManagedListProjection rows for all items in a FinancialRecord.
    For Phase 1 backfill of existing financial_parser records.
    Returns the number of projection rows created.
    """
    ManagedListProjection.objects.filter(record_id=financial_record.id).delete()

    count = 0
    for fi in financial_record.items.all().order_by("item_index"):
        ManagedListProjection.objects.create(
            user=financial_record.user,
            source_ingest_item=financial_record.source_item,
            list_type=ManagedListType.FINANCIAL,
            record_id=financial_record.id,
            item_id=fi.id,
            title=fi.description or fi.merchant or "",
            description=fi.description or "",
            category=fi.category or "",
            item_status=fi.type or "",
            amount=fi.amount,
            currency=fi.currency or "",
            due_date=fi.transaction_date,
            entity_name=fi.merchant or "",
            entity_type="vendor" if fi.merchant else "",
        )
        count += 1

    logger.info(
        "Refreshed %d projection rows for FinancialRecord %s",
        count, financial_record.id,
    )
    return count
