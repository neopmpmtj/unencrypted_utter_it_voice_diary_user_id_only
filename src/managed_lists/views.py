"""
Managed Lists Views — To-Do Web Interface

Provides the To-Do list page and JSON API endpoints for viewing,
creating, editing, and deleting to-do items via the web UI.
"""

import json
import uuid
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.utils import timezone as tz
from django.utils.translation import gettext as _
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from src.managed_lists.models import (
    ManagedListProjection,
    ManagedListType,
    ManagedRecordStatus,
    TodoCompletionStatus,
    TodoItem,
    TodoPriority,
    TodoRecord,
)
from src.managed_lists.projections import refresh_projection_for_todo_record
from src.managed_lists.services import (
    get_item_and_descendant_ids,
    soft_delete_todo_items_for_records,
    soft_delete_todo_record_if_last_item,
)

PAGE_SIZE_DEFAULT = 30

_STATUS_CYCLE = {
    TodoCompletionStatus.OPEN: TodoCompletionStatus.IN_PROGRESS,
    TodoCompletionStatus.IN_PROGRESS: TodoCompletionStatus.ON_HOLD,
    TodoCompletionStatus.ON_HOLD: TodoCompletionStatus.DONE,
    TodoCompletionStatus.DONE: TodoCompletionStatus.OPEN,
    TodoCompletionStatus.CANCELLED: TodoCompletionStatus.OPEN,
}

VALID_STATUSES = {c.value for c in TodoCompletionStatus}
VALID_PRIORITIES = {c.value for c in TodoPriority}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_qs_for_user(request):
    """Base queryset: active TodoRecords visible to this user (with at least one top-level item)."""
    user = request.user
    return TodoRecord.objects.filter(
        user=user,
        is_deleted=False,
        status=ManagedRecordStatus.SUCCESS,
    ).filter(
        Q(source_item__user=user)
        | Q(created_by=user)
    ).annotate(
        _top_level_count=Count("items", filter=Q(items__parent=None))
    ).filter(
        _top_level_count__gt=0
    )


def _item_qs_for_user(request):
    """Base queryset: active top-level TodoItems visible to this user."""
    user = request.user
    return TodoItem.objects.filter(
        todo_record__user=user,
        todo_record__is_deleted=False,
        todo_record__status=ManagedRecordStatus.SUCCESS,
        parent=None,
    ).filter(
        Q(todo_record__source_item__user=user)
        | Q(todo_record__created_by=user)
    )


def _serialize_item(ti: TodoItem, _user_id: int) -> dict:
    """Return a JSON-safe dict for a single TodoItem."""
    text = ti.text or ""
    description = ti.description or ""
    return {
        "id": str(ti.id),
        "text": text,
        "description": description,
        "priority": ti.priority,
        "completion_status": ti.completion_status,
        "due_date": ti.due_date.isoformat() if ti.due_date else None,
        "topic": ti.topic,
        "subtopic": ti.subtopic,
        "entity_name": ti.entity_name,
        "created_at": ti.created_at.isoformat(),
    }


def _get_or_create_manual_record(user) -> TodoRecord:
    """Return the single manual TodoRecord for this user, creating if needed."""
    record, _ = TodoRecord.objects.get_or_create(
        user=user,
        source_item=None,
        created_by=user,
        defaults={
            "status": ManagedRecordStatus.SUCCESS,
            "record_name": "Manual",
        },
    )
    # Ensure the manual record stays in success state
    if record.status != ManagedRecordStatus.SUCCESS:
        record.status = ManagedRecordStatus.SUCCESS
        record.save(update_fields=["status", "updated_at"])
    return record


def _update_projection_for_item(ti: TodoItem, _user_id: int) -> None:
    """Upsert the ManagedListProjection row for a single TodoItem."""
    text = ti.text or ""
    description = ti.description or ""
    ManagedListProjection.objects.update_or_create(
        item_id=ti.id,
        defaults={
            "user": ti.todo_record.user,
            "source_ingest_item": ti.todo_record.source_item,
            "list_type": ManagedListType.TODO,
            "record_id": ti.todo_record.id,
            "title": text,
            "description": description,
            "category": ti.todo_record.record_name or "",
            "topic": ti.topic or "",
            "subtopic": ti.subtopic or "",
            "item_status": ti.completion_status,
            "priority": ti.priority,
            "due_date": ti.due_date,
            "entity_name": ti.entity_name or "",
            "entity_type": ti.entity_type or "",
        },
    )


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

@login_required
@require_GET
def todos_page(request):
    todos_i18n = {
        "status_open": _("Open"),
        "status_in_progress": _("In Progress"),
        "status_on_hold": _("On Hold"),
        "status_done": _("Done"),
        "status_cancelled": _("Cancelled"),
        "status_summary_open": _("open"),
        "status_summary_done": _("done"),
        "status_summary_in_progress": _("in progress"),
        "status_summary_on_hold": _("on hold"),
        "status_summary_cancelled": _("cancelled"),
        "priority_1": _("Lowest"),
        "priority_2": _("Low"),
        "priority_3": _("Medium"),
        "priority_4": _("High"),
        "priority_5": _("Urgent"),
        "more": _("more"),
        "item": _("item"),
        "items": _("items"),
        "manual": _("Manual"),
        "untitled_list": _("Untitled List"),
        "no_items": _("No items"),
        "no_items_to_show": _("No items to show."),
        "priority_title": _("Priority"),
        "delete_tasks_confirm": _("Delete %(count)s task(s)?"),
        "delete_task_confirm": _("Delete this task?"),
        "delete_list_confirm": _("Delete this entire list and all its tasks?"),
        "failed_delete_list": _("Failed to delete list."),
        "failed_update_status": _("Failed to update status."),
        "failed_delete_task": _("Failed to delete task."),
        "failed_load_task": _("Failed to load task."),
        "failed_save_task": _("Failed to save task."),
        "bulk_action_failed": _("Bulk action failed."),
        "page_of": _("Page %(page)s of %(total)s (%(count)s lists)"),
        "selected": _("selected"),
        "new_task": _("New Task"),
        "edit_task": _("Edit Task"),
    }
    return render(request, "managed_lists/todos.html", {"todos_i18n": todos_i18n})


# ---------------------------------------------------------------------------
# List API
# ---------------------------------------------------------------------------

@login_required
@require_GET
def todos_list_api(request):
    status_filter = request.GET.get("status", "all")
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(1, min(100, int(request.GET.get("page_size", PAGE_SIZE_DEFAULT))))
    except (ValueError, TypeError):
        page_size = PAGE_SIZE_DEFAULT

    record_qs = _record_qs_for_user(request)

    # Filter to records that have at least one item matching the status filter
    if status_filter != "all" and status_filter in VALID_STATUSES:
        record_qs = record_qs.filter(
            items__completion_status=status_filter,
            items__parent=None,
        ).distinct()

    record_qs = record_qs.order_by("-updated_at", "-created_at")

    total_records = record_qs.count()
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    records_page = list(record_qs[offset: offset + page_size])
    record_ids = [r.id for r in records_page]

    # Batch-fetch all top-level items for these records
    all_items_qs = TodoItem.objects.filter(
        todo_record_id__in=record_ids,
        parent=None,
    ).order_by(
        F("due_date").asc(nulls_last=True),
        "-priority",
        "created_at",
    )

    # Group items by record, applying status filter for display
    items_by_record = defaultdict(list)
    all_items_by_record = defaultdict(list)
    for ti in all_items_qs:
        all_items_by_record[ti.todo_record_id].append(ti)
        if status_filter == "all" or ti.completion_status == status_filter:
            items_by_record[ti.todo_record_id].append(ti)

    user_id = request.user.id
    records_data = []
    for record in records_page:
        all_items = all_items_by_record[record.id]
        display_items = items_by_record[record.id]
        per_record_counts = {s: 0 for s in VALID_STATUSES}
        for ti in all_items:
            per_record_counts[ti.completion_status] = per_record_counts.get(ti.completion_status, 0) + 1
        records_data.append({
            "id": str(record.id),
            "name": record.record_name or "Untitled List",
            "context": record.record_context or "",
            "is_manual": record.source_item_id is None,
            "item_count": len(all_items),
            "status_counts": per_record_counts,
            "items": [_serialize_item(ti, user_id) for ti in display_items],
        })

    # Global status counts across all items for this user (ignoring filter)
    status_counts = {s: 0 for s in VALID_STATUSES}
    for row in _item_qs_for_user(request).values("completion_status").annotate(cnt=Count("id")):
        status_counts[row["completion_status"]] = row["cnt"]

    return JsonResponse({
        "records": records_data,
        "total_records": total_records,
        "page": page,
        "total_pages": total_pages,
        "status_counts": status_counts,
    })


# ---------------------------------------------------------------------------
# Create API
# ---------------------------------------------------------------------------

@login_required
@require_POST
def todo_create_api(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "text is required"}, status=400)

    description = (body.get("description") or "").strip()
    priority_raw = body.get("priority", TodoPriority.MEDIUM)
    try:
        priority = int(priority_raw)
        if priority not in VALID_PRIORITIES:
            priority = TodoPriority.MEDIUM
    except (ValueError, TypeError):
        priority = TodoPriority.MEDIUM

    due_date_str = (body.get("due_date") or "").strip()
    due_date = None
    if due_date_str:
        from datetime import date
        try:
            due_date = date.fromisoformat(due_date_str)
        except ValueError:
            pass

    topic = (body.get("topic") or "").strip()[:255]

    user = request.user

    todo_record = _get_or_create_manual_record(user)

    # item_index = max existing + 1 within this record (top-level only)
    from django.db.models import Max as MaxAgg
    max_idx = TodoItem.objects.filter(
        todo_record=todo_record, parent=None
    ).aggregate(m=MaxAgg("item_index"))["m"]
    item_index = (max_idx if max_idx is not None else -1) + 1

    ti = TodoItem.objects.create(
        todo_record=todo_record,
        parent=None,
        item_index=item_index,
        text=text,
        description=description,
        priority=priority,
        completion_status=TodoCompletionStatus.OPEN,
        due_date=due_date,
        topic=topic,
        item_data={},
    )

    _update_projection_for_item(ti, user.id)

    return JsonResponse({"success": True, "item": _serialize_item(ti, user.id)}, status=201)


# ---------------------------------------------------------------------------
# Record API (DELETE)
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["DELETE"])
def todo_record_api(request, record_id):
    qs = _record_qs_for_user(request)
    try:
        record = qs.get(id=record_id)
    except TodoRecord.DoesNotExist:
        # Also allow deleting ghost records (0 items) that _record_qs_for_user now hides
        try:
            record = TodoRecord.objects.filter(
                is_deleted=False,
                status=ManagedRecordStatus.SUCCESS,
            ).filter(
                Q(source_item__user=request.user) | Q(created_by=request.user)
            ).get(id=record_id)
        except TodoRecord.DoesNotExist:
            return JsonResponse({"error": "Not found"}, status=404)

    item_ids = list(TodoItem.all_objects.filter(todo_record=record).values_list("id", flat=True))
    ManagedListProjection.objects.filter(item_id__in=item_ids).delete()
    now = tz.now()
    soft_delete_todo_items_for_records([record.id], now)
    TodoRecord.all_objects.filter(pk=record.pk).update(
        is_deleted=True,
        deleted_at=now,
        status=ManagedRecordStatus.FAILED,
    )
    return JsonResponse({"success": True})


# ---------------------------------------------------------------------------
# Single item API (GET / PATCH / DELETE)
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def todo_item_api(request, item_id):
    qs = _item_qs_for_user(request)
    try:
        ti = qs.get(id=item_id)
    except TodoItem.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    user_id = request.user.id

    if request.method == "GET":
        return JsonResponse(_serialize_item(ti, user_id))

    if request.method == "DELETE":
        ids_to_delete = get_item_and_descendant_ids(ti)
        soft_delete_todo_record_if_last_item(ti.todo_record, ids_to_delete)
        ManagedListProjection.objects.filter(item_id__in=ids_to_delete).delete()
        TodoItem.all_objects.filter(id__in=ids_to_delete).update(
            is_deleted=True, deleted_at=tz.now()
        )
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
        ti.text = text
        update_fields.append("text")

    if "description" in body:
        ti.description = (body["description"] or "").strip()
        update_fields.append("description")

    if "priority" in body:
        try:
            p = int(body["priority"])
            if p in VALID_PRIORITIES:
                ti.priority = p
                update_fields.append("priority")
        except (ValueError, TypeError):
            pass

    if "completion_status" in body:
        s = body["completion_status"]
        if s in VALID_STATUSES:
            ti.completion_status = s
            update_fields.append("completion_status")

    if "due_date" in body:
        due_date_str = (body["due_date"] or "").strip()
        if due_date_str:
            from datetime import date
            try:
                ti.due_date = date.fromisoformat(due_date_str)
            except ValueError:
                ti.due_date = None
        else:
            ti.due_date = None
        update_fields.append("due_date")

    if "topic" in body:
        ti.topic = (body["topic"] or "").strip()[:255]
        update_fields.append("topic")

    if update_fields:
        update_fields.append("item_index")  # no-op, keeps save minimal
        ti.save(update_fields=update_fields)
        _update_projection_for_item(ti, user_id)

    return JsonResponse(_serialize_item(ti, user_id))


# ---------------------------------------------------------------------------
# Bulk API
# ---------------------------------------------------------------------------

@login_required
@require_POST
def todos_bulk_api(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    action = body.get("action")
    raw_ids = body.get("item_ids", [])
    if not isinstance(raw_ids, list) or not raw_ids:
        return JsonResponse({"error": "item_ids must be a non-empty list"}, status=400)

    # Validate UUIDs
    try:
        item_ids = [uuid.UUID(str(i)) for i in raw_ids]
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Invalid item_ids"}, status=400)

    qs = _item_qs_for_user(request).filter(id__in=item_ids)
    allowed_ids = list(qs.values_list("id", flat=True))

    if action == "delete":
        by_record = defaultdict(list)
        for item_id, record_id in qs.values_list("id", "todo_record_id"):
            by_record[record_id].append(item_id)
        ManagedListProjection.objects.filter(item_id__in=allowed_ids).delete()
        affected = TodoItem.all_objects.filter(id__in=allowed_ids).update(
            is_deleted=True, deleted_at=tz.now()
        )
        for record_id, ids_for_record in by_record.items():
            record = TodoRecord.all_objects.get(pk=record_id)
            soft_delete_todo_record_if_last_item(record, ids_for_record)
        return JsonResponse({"success": True, "affected": affected})

    if action == "status":
        value = body.get("value")
        if value not in VALID_STATUSES:
            return JsonResponse({"error": "Invalid status value"}, status=400)
        affected = qs.update(completion_status=value)
        ManagedListProjection.objects.filter(item_id__in=allowed_ids).update(item_status=value)
        return JsonResponse({"success": True, "affected": affected})

    if action == "priority":
        try:
            value = int(body.get("value", 0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid priority value"}, status=400)
        if value not in VALID_PRIORITIES:
            return JsonResponse({"error": "Invalid priority value"}, status=400)
        affected = qs.update(priority=value)
        ManagedListProjection.objects.filter(item_id__in=allowed_ids).update(priority=value)
        return JsonResponse({"success": True, "affected": affected})

    return JsonResponse({"error": "Unknown action"}, status=400)
