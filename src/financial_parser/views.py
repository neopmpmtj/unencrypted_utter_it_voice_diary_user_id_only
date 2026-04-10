"""
Financial Parser Views — Financial Entries Web Interface

Provides the Financials page and JSON API endpoints for viewing,
creating, editing, and deleting financial records and items via the web UI.
"""

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Max as MaxAgg, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone as django_timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from src.financial_parser.models import FinancialItem, FinancialRecord, FinancialRecordStatus
from src.financial_parser.services import soft_delete_financial_items_for_records

PAGE_SIZE_DEFAULT = 30

VALID_TYPES = {"expense", "income"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_qs_for_user(request):
    """Base queryset: active FinancialRecords visible to this user."""
    user = request.user
    return FinancialRecord.objects.filter(
        user=user,
        is_deleted=False,
        status=FinancialRecordStatus.SUCCESS,
    ).filter(
        Q(source_item__user=user)
        | Q(created_by=user)
    )


def _serialize_item(fi: FinancialItem) -> dict:
    """Return a JSON-safe dict for a single FinancialItem (no encryption)."""
    return {
        "id": str(fi.id),
        "type": fi.type,
        "amount": str(fi.amount),
        "currency": fi.currency,
        "category": fi.category,
        "merchant": fi.merchant,
        "transaction_date": fi.transaction_date.isoformat() if fi.transaction_date else None,
        "description": fi.description,
        "payment_method": fi.payment_method,
        "item_index": fi.item_index,
    }


def _serialize_record(record: FinancialRecord, items: list) -> dict:
    """Return a JSON-safe dict for a FinancialRecord with its items."""
    expenses = [i for i in items if i.type == "expense"]
    incomes = [i for i in items if i.type == "income"]
    total_expense = sum(i.amount for i in expenses if i.amount)
    total_income = sum(i.amount for i in incomes if i.amount)
    return {
        "id": str(record.id),
        "name": record.record_name or "Untitled Record",
        "context": record.record_context or "",
        "item_count": len(items),
        "total_expense": str(total_expense),
        "total_income": str(total_income),
        "items": [_serialize_item(i) for i in items],
        "is_manual": record.source_item_id is None,
    }


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

@login_required
@require_GET
def financials_page(request):
    financials_i18n = {
        "expense": _("Expense"),
        "income": _("Income"),
        "expenses_label": _("Expenses"),
        "income_label": _("Income"),
        "more": _("more"),
        "item": _("item"),
        "items": _("items"),
        "manual": _("Manual"),
        "no_items": _("No items"),
        "no_items_yet": _("No items yet."),
        "amount": _("Amount"),
        "merchant": _("Merchant"),
        "delete_entry_confirm": _("Delete this financial entry?"),
        "delete_item_confirm": _("Delete this item?"),
        "failed_delete_entry": _("Failed to delete entry."),
        "failed_create_entry": _("Failed to create entry."),
        "failed_save": _("Failed to save."),
        "failed_add_item": _("Failed to add item."),
        "failed_save_item": _("Failed to save item."),
        "failed_delete_item": _("Failed to delete item."),
        "page_of": _("Page %(page)s of %(total)s (%(count)s entries)"),
    }
    return render(request, "financial_parser/financials.html", {"financials_i18n": financials_i18n})


# ---------------------------------------------------------------------------
# List API
# ---------------------------------------------------------------------------

@login_required
@require_GET
def financials_list_api(request):
    type_filter = request.GET.get("type", "all")
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(1, min(100, int(request.GET.get("page_size", PAGE_SIZE_DEFAULT))))
    except (ValueError, TypeError):
        page_size = PAGE_SIZE_DEFAULT

    record_qs = _record_qs_for_user(request)

    if type_filter in VALID_TYPES:
        record_qs = record_qs.filter(items__type=type_filter).distinct()

    record_qs = record_qs.order_by("-updated_at", "-created_at")

    total_records = record_qs.count()
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    records_page = list(record_qs[offset: offset + page_size])
    record_ids = [r.id for r in records_page]

    all_items_qs = FinancialItem.objects.filter(
        financial_record_id__in=record_ids,
    ).order_by("financial_record_id", "item_index")

    items_by_record = defaultdict(list)
    for fi in all_items_qs:
        items_by_record[fi.financial_record_id].append(fi)

    records_data = []
    for record in records_page:
        items = items_by_record[record.id]
        records_data.append(_serialize_record(record, items))

    return JsonResponse({
        "records": records_data,
        "total_records": total_records,
        "page": page,
        "total_pages": total_pages,
    })


# ---------------------------------------------------------------------------
# Create API
# ---------------------------------------------------------------------------

@login_required
@require_POST
def financial_create_api(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = (body.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)

    context = (body.get("context") or "").strip()
    items_data = body.get("items") or []

    user = request.user

    record = FinancialRecord.objects.create(
        user=user,
        source_item=None,
        created_by=user,
        record_name=name,
        record_context=context,
        status=FinancialRecordStatus.SUCCESS,
    )

    created_items = []
    for idx, item_data in enumerate(items_data):
        item_type = (item_data.get("type") or "expense").lower()
        if item_type not in VALID_TYPES:
            item_type = "expense"

        amount_raw = item_data.get("amount")
        try:
            amount = Decimal(str(amount_raw))
            if amount <= 0:
                continue
        except (InvalidOperation, TypeError):
            continue

        currency = (item_data.get("currency") or "EUR").strip()[:10]
        category = (item_data.get("category") or "").strip()[:100]
        merchant = (item_data.get("merchant") or "").strip()[:255]
        description = (item_data.get("description") or "").strip()
        payment_method = (item_data.get("payment_method") or "").strip()[:50]

        transaction_date = None
        date_str = (item_data.get("transaction_date") or "").strip()
        if date_str:
            try:
                transaction_date = date.fromisoformat(date_str)
            except ValueError:
                pass

        fi = FinancialItem.objects.create(
            financial_record=record,
            item_index=idx,
            type=item_type,
            amount=amount,
            currency=currency,
            category=category,
            merchant=merchant,
            description=description,
            payment_method=payment_method,
            transaction_date=transaction_date,
        )
        created_items.append(fi)

    return JsonResponse(
        {"success": True, "record": _serialize_record(record, created_items)},
        status=201,
    )


# ---------------------------------------------------------------------------
# Record API (PATCH / DELETE)
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["PATCH", "DELETE"])
def financial_record_api(request, record_id):
    qs = _record_qs_for_user(request)
    try:
        record = qs.get(id=record_id)
    except FinancialRecord.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "DELETE":
        now = django_timezone.now()
        record.is_deleted = True
        record.deleted_at = now
        record.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        soft_delete_financial_items_for_records([record.id], now)
        return JsonResponse({"success": True})

    # PATCH
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    update_fields = []
    if "name" in body:
        record.record_name = (body["name"] or "").strip()
        update_fields.append("record_name")
    if "context" in body:
        record.record_context = (body["context"] or "").strip()
        update_fields.append("record_context")

    if update_fields:
        update_fields.append("updated_at")
        record.save(update_fields=update_fields)

    items = list(FinancialItem.objects.filter(financial_record=record).order_by("item_index"))
    return JsonResponse(_serialize_record(record, items))


# ---------------------------------------------------------------------------
# Item API (PATCH / DELETE)
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["PATCH", "DELETE"])
def financial_item_api(request, item_id):
    try:
        fi = FinancialItem.objects.get(
            id=item_id,
            financial_record__in=_record_qs_for_user(request),
        )
    except FinancialItem.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "DELETE":
        record = fi.financial_record
        now = django_timezone.now()
        fi.deleted_at = now
        fi.save(update_fields=["deleted_at"])
        if not FinancialItem.objects.filter(financial_record=record).exists():
            record.is_deleted = True
            record.deleted_at = now
            record.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        return JsonResponse({"success": True})

    # PATCH
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    update_fields = []

    if "type" in body:
        t = (body["type"] or "expense").lower()
        if t in VALID_TYPES:
            fi.type = t
            update_fields.append("type")

    if "amount" in body:
        try:
            amount = Decimal(str(body["amount"]))
            if amount > 0:
                fi.amount = amount
                update_fields.append("amount")
        except (InvalidOperation, TypeError):
            pass

    if "currency" in body:
        fi.currency = (body["currency"] or "EUR").strip()[:10]
        update_fields.append("currency")

    if "category" in body:
        fi.category = (body["category"] or "").strip()[:100]
        update_fields.append("category")

    if "merchant" in body:
        fi.merchant = (body["merchant"] or "").strip()[:255]
        update_fields.append("merchant")

    if "description" in body:
        fi.description = (body["description"] or "").strip()
        update_fields.append("description")

    if "payment_method" in body:
        fi.payment_method = (body["payment_method"] or "").strip()[:50]
        update_fields.append("payment_method")

    if "transaction_date" in body:
        date_str = (body["transaction_date"] or "").strip()
        if date_str:
            try:
                fi.transaction_date = date.fromisoformat(date_str)
            except ValueError:
                fi.transaction_date = None
        else:
            fi.transaction_date = None
        update_fields.append("transaction_date")

    if update_fields:
        fi.save(update_fields=update_fields)

    return JsonResponse(_serialize_item(fi))


# ---------------------------------------------------------------------------
# Add item to existing record
# ---------------------------------------------------------------------------

@login_required
@require_POST
def financial_items_add_api(request, record_id):
    qs = _record_qs_for_user(request)
    try:
        record = qs.get(id=record_id)
    except FinancialRecord.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    item_type = (body.get("type") or "expense").lower()
    if item_type not in VALID_TYPES:
        item_type = "expense"

    amount_raw = body.get("amount")
    try:
        amount = Decimal(str(amount_raw))
        if amount <= 0:
            return JsonResponse({"error": "amount must be positive"}, status=400)
    except (InvalidOperation, TypeError):
        return JsonResponse({"error": "invalid amount"}, status=400)

    currency = (body.get("currency") or "EUR").strip()[:10]
    category = (body.get("category") or "").strip()[:100]
    merchant = (body.get("merchant") or "").strip()[:255]
    description = (body.get("description") or "").strip()
    payment_method = (body.get("payment_method") or "").strip()[:50]

    transaction_date = None
    date_str = (body.get("transaction_date") or "").strip()
    if date_str:
        try:
            transaction_date = date.fromisoformat(date_str)
        except ValueError:
            pass

    max_idx = FinancialItem.objects.filter(
        financial_record=record
    ).aggregate(m=MaxAgg("item_index"))["m"]
    item_index = (max_idx if max_idx is not None else -1) + 1

    fi = FinancialItem.objects.create(
        financial_record=record,
        item_index=item_index,
        type=item_type,
        amount=amount,
        currency=currency,
        category=category,
        merchant=merchant,
        description=description,
        payment_method=payment_method,
        transaction_date=transaction_date,
    )

    return JsonResponse({"success": True, "item": _serialize_item(fi)}, status=201)
