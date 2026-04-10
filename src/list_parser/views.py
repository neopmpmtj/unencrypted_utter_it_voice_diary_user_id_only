"""
List Parser Views — My Lists Web Interface

Provides the Lists page and JSON API endpoints for viewing,
creating, editing, and deleting list records and items via the web UI.
"""

import json
from collections import defaultdict
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Max as MaxAgg, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from django.utils import timezone as django_timezone
from django.utils.translation import gettext as _

from src.list_parser.models import ListItem, ListRecord, ListRecordStatus
from src.list_parser.services import (
    soft_delete_list_item_and_descendants,
    soft_delete_list_items_for_records,
)

PAGE_SIZE_DEFAULT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_qs_for_user(request):
    """Base queryset: active ListRecords visible to this user."""
    user = request.user
    return ListRecord.objects.filter(
        user=user,
        is_deleted=False,
        status=ListRecordStatus.SUCCESS,
    ).filter(
        Q(source_item__user=user)
        | Q(created_by=user)
    )


def _serialize_item(li: ListItem, _user_id: int) -> dict:
    """Return a JSON-safe dict for a single ListItem."""
    text = li.text or ""
    description = li.description or ""
    return {
        "id": str(li.id),
        "text": text,
        "description": description,
        "due_date": li.due_date.isoformat() if li.due_date else None,
        "quantity": str(li.quantity) if li.quantity is not None else None,
        "unit": li.unit or "",
        "parent_id": str(li.parent_id) if li.parent_id else None,
        "item_index": li.item_index,
    }


def _serialize_record(record: ListRecord, items: list, user_id: int) -> dict:
    """Return a JSON-safe dict for a ListRecord with its items."""
    top_level = [i for i in items if i.parent_id is None]
    sub_items_map = defaultdict(list)
    for i in items:
        if i.parent_id is not None:
            sub_items_map[str(i.parent_id)].append(_serialize_item(i, user_id))
    return {
        "id": str(record.id),
        "name": record.list_name or _("Untitled List"),
        "context": record.list_context or "",
        "item_count": len(top_level),
        "items": [_serialize_item(i, user_id) for i in top_level],
        "sub_items": dict(sub_items_map),
        "is_manual": record.source_item_id is None,
    }


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

@login_required
@require_GET
def lists_page(request):
    lists_i18n = {
        "delete_list_confirm": _("Delete this list?"),
        "delete_list_failed": _("Failed to delete list."),
        "delete_item_confirm": _("Delete this item?"),
        "delete_item_failed": _("Failed to delete item."),
        "save_failed": _("Failed to save."),
        "save_item_failed": _("Failed to save item."),
        "create_failed": _("Failed to create list."),
        "add_item_failed": _("Failed to add item."),
        "no_items_yet": _("No items yet."),
        "more": _("more"),
        "item": _("item"),
        "items": _("items"),
        "page_of": _("Page %(page)s of %(total)s (%(count)s lists)"),
        "placeholder_item_text": _("Item text..."),
        "placeholder_qty": _("Qty"),
        "placeholder_unit": _("Unit"),
        "untitled_list": _("Untitled List"),
        "manual": _("Manual"),
    }
    return render(request, "list_parser/lists.html", {"lists_i18n": lists_i18n})


# ---------------------------------------------------------------------------
# List API
# ---------------------------------------------------------------------------

@login_required
@require_GET
def lists_list_api(request):
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(1, min(100, int(request.GET.get("page_size", PAGE_SIZE_DEFAULT))))
    except (ValueError, TypeError):
        page_size = PAGE_SIZE_DEFAULT

    record_qs = _record_qs_for_user(request).order_by("-updated_at", "-created_at")

    total_records = record_qs.count()
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    records_page = list(record_qs[offset: offset + page_size])
    record_ids = [r.id for r in records_page]

    # Batch-fetch all items for these records
    all_items_qs = ListItem.objects.filter(
        list_record_id__in=record_ids,
    ).order_by("list_record_id", "item_index")

    items_by_record = defaultdict(list)
    for li in all_items_qs:
        items_by_record[li.list_record_id].append(li)

    user_id = request.user.id
    records_data = []
    for record in records_page:
        items = items_by_record[record.id]
        records_data.append(_serialize_record(record, items, user_id))

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
def list_create_api(request):
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

    record = ListRecord.objects.create(
        user=user,
        source_item=None,
        created_by=user,
        list_name=name,
        list_context=context,
        status=ListRecordStatus.SUCCESS,
    )

    created_items = []
    for idx, item_data in enumerate(items_data):
        text = (item_data.get("text") or "").strip()
        if not text:
            continue
        description = (item_data.get("description") or "").strip()
        quantity_raw = item_data.get("quantity")
        unit = (item_data.get("unit") or "").strip()[:30]

        quantity = None
        if quantity_raw not in (None, ""):
            try:
                from decimal import Decimal
                quantity = Decimal(str(quantity_raw))
            except Exception:
                quantity = None

        li = ListItem.objects.create(
            list_record=record,
            parent=None,
            item_index=idx,
            text=text,
            description=description,
            quantity=quantity,
            unit=unit,
        )
        created_items.append(li)

    return JsonResponse(
        {"success": True, "record": _serialize_record(record, created_items, user.id)},
        status=201,
    )


# ---------------------------------------------------------------------------
# Record API (PATCH / DELETE)
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["PATCH", "DELETE"])
def list_record_api(request, record_id):
    qs = _record_qs_for_user(request)
    try:
        record = qs.get(id=record_id)
    except ListRecord.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "DELETE":
        now = django_timezone.now()
        record.is_deleted = True
        record.deleted_at = now
        record.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        soft_delete_list_items_for_records([record.id], now)
        return JsonResponse({"success": True})

    # PATCH
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    update_fields = []
    if "name" in body:
        record.list_name = (body["name"] or "").strip()
        update_fields.append("list_name")
    if "context" in body:
        record.list_context = (body["context"] or "").strip()
        update_fields.append("list_context")

    if update_fields:
        update_fields.append("updated_at")
        record.save(update_fields=update_fields)

    items = list(ListItem.objects.filter(list_record=record).order_by("item_index"))
    return JsonResponse(_serialize_record(record, items, request.user.id))


# ---------------------------------------------------------------------------
# Item API (PATCH / DELETE)
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["PATCH", "DELETE"])
def list_item_api(request, item_id):
    user = request.user
    try:
        li = ListItem.objects.get(
            id=item_id,
            list_record__in=_record_qs_for_user(request),
        )
    except ListItem.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "DELETE":
        record = li.list_record
        now = django_timezone.now()
        soft_delete_list_item_and_descendants(li, now)
        if not ListItem.objects.filter(list_record=record, parent=None).exists():
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

    if "text" in body:
        text = (body["text"] or "").strip()
        if not text:
            return JsonResponse({"error": "text cannot be empty"}, status=400)
        li.text = text
        update_fields.append("text")

    if "description" in body:
        li.description = (body["description"] or "").strip()
        update_fields.append("description")

    if "due_date" in body:
        due_date_str = (body["due_date"] or "").strip()
        if due_date_str:
            try:
                li.due_date = date.fromisoformat(due_date_str)
            except ValueError:
                li.due_date = None
        else:
            li.due_date = None
        update_fields.append("due_date")

    if "quantity" in body:
        q = body["quantity"]
        if q in (None, ""):
            li.quantity = None
        else:
            try:
                from decimal import Decimal
                li.quantity = Decimal(str(q))
            except Exception:
                li.quantity = None
        update_fields.append("quantity")

    if "unit" in body:
        li.unit = (body["unit"] or "").strip()[:30]
        update_fields.append("unit")

    if update_fields:
        li.save(update_fields=update_fields)

    return JsonResponse(_serialize_item(li, user.id))


# ---------------------------------------------------------------------------
# Add item to existing record
# ---------------------------------------------------------------------------

@login_required
@require_POST
def list_items_add_api(request, record_id):
    qs = _record_qs_for_user(request)
    try:
        record = qs.get(id=record_id)
    except ListRecord.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "text is required"}, status=400)

    description = (body.get("description") or "").strip()
    quantity_raw = body.get("quantity")
    unit = (body.get("unit") or "").strip()[:30]

    quantity = None
    if quantity_raw not in (None, ""):
        try:
            from decimal import Decimal
            quantity = Decimal(str(quantity_raw))
        except Exception:
            quantity = None

    max_idx = ListItem.objects.filter(
        list_record=record, parent=None
    ).aggregate(m=MaxAgg("item_index"))["m"]
    item_index = (max_idx if max_idx is not None else -1) + 1

    user = request.user
    li = ListItem.objects.create(
        list_record=record,
        parent=None,
        item_index=item_index,
        text=text,
        description=description,
        quantity=quantity,
        unit=unit,
    )

    return JsonResponse({"success": True, "item": _serialize_item(li, user.id)}, status=201)
