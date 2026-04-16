"""
Entries Views

Handles the entries list page and API endpoint for viewing user utterances.
"""

import json
import logging
import uuid
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.csrf import csrf_protect

from src.common.utils import ensure_directory
from src.common.google_account.auth import verify_drive_permissions, GoogleAuthError
from src.common.drive_upload import upload_local_file_to_user_drive_folder
from src.common.storage_local import (
    allocate_unique_attachment_filename,
    ensure_local_storage_tree,
    local_attachments_dir_for_item,
    sanitize_storage_filename,
)

from src.accounts.models import GlobalSettings
from src.batch_calendar.services import delete_batch_calendar_for_item, delete_calendar_events_for_item
from src.list_parser.services import delete_list_records_for_item, save_list_from_formatted_text
from src.financial_parser.services import delete_financial_records_for_item, save_financial_from_formatted_text
from src.managed_lists.models import ManagedListProjection
from src.managed_lists.services import delete_todo_records_for_item
from src.batch_calendar.tasks import parse_batch_calendar_task
from src.classification.models import (
    ItemClassificationRun,
    ItemClassificationSelection,
    ItemEntityLink,
)
from src.intent_router.models import ItemTriageResult
from src.classification.services import has_calendar_classification, has_list_classification, has_financial_classification, has_todo_classification
from src.classification.tasks import classify_item_task
from src.common.config import get_config
from src.ingestion.models import IngestItem, FileRole, IngestItemEditLog, IngestJob, IngestStatus, JobType, ItemFile

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_SEARCH_BATCH_SIZE = 500
DEFAULT_MAX_BROWSE_ENTRIES = 0  # 0 = unlimited browse; users can scroll to load all entries
CONTENT_PREVIEW_MAX_LENGTH = 40
CONTENT_PREVIEW_MIN_LENGTH = 20


def get_max_search_batch_size() -> int:
    """Get the max search batch size from GlobalSettings or use default."""
    return GlobalSettings.get_value('entries.max_search_batch_size', DEFAULT_MAX_SEARCH_BATCH_SIZE)


def get_max_browse_entries() -> int:
    """Get the max browse entries limit from GlobalSettings or use default. 0 = unlimited."""
    return GlobalSettings.get_value('entries.max_browse_entries', DEFAULT_MAX_BROWSE_ENTRIES)


def truncate_preview(text: str) -> str:
    """
    Truncate text to 20-40 characters for preview.
    Tries to break at a word boundary if possible.
    """
    if not text:
        return ""
    
    if len(text) <= CONTENT_PREVIEW_MAX_LENGTH:
        return text
    
    # Try to find a word boundary between min and max length
    truncated = text[:CONTENT_PREVIEW_MAX_LENGTH]
    
    # Look for last space within the range
    last_space = truncated.rfind(' ', CONTENT_PREVIEW_MIN_LENGTH, CONTENT_PREVIEW_MAX_LENGTH)
    
    if last_space > CONTENT_PREVIEW_MIN_LENGTH:
        return truncated[:last_space] + "..."
    
    return truncated[:CONTENT_PREVIEW_MAX_LENGTH - 3] + "..."


def _get_item_classification_labels(item) -> list[str]:
    """Return human-readable taxonomy labels for display from the latest classification."""
    try:
        proj = item.retrieval_projection
        labels = []
        if proj.primary_subject_key:
            from src.classification.models import TaxonomyNode
            node = TaxonomyNode.objects.filter(key=proj.primary_subject_key).values_list("label", flat=True).first()
            labels.append(node or proj.primary_subject_key.rsplit(".", 1)[-1])
        if proj.primary_intent_key:
            from src.classification.models import TaxonomyNode
            node = TaxonomyNode.objects.filter(key=proj.primary_intent_key).values_list("label", flat=True).first()
            labels.append(node or proj.primary_intent_key.rsplit(".", 1)[-1])
        return labels
    except Exception:
        return []


def _build_entry_response(item, content_text, title):
    """Build the entry dict used in API responses."""
    display_text = content_text
    attachments = [
        {'filename': f.filename, 'storage_url': f.storage_url}
        for f in item.files.filter(role=FileRole.ATTACHMENT)
    ]
    return {
        'id': str(item.id),
        'title': title or (f"Entry {item.occurred_at.strftime('%Y-%m-%d %H:%M') if item.occurred_at else 'Unknown'}"),
        'content_preview': truncate_preview(display_text),
        'content_full': display_text,
        'occurred_at': item.occurred_at.isoformat() if item.occurred_at else None,
        'item_type': item.item_type,
        'tags': _get_item_classification_labels(item),
        'attachments': attachments,
    }


@login_required
def entries_page(request):
    """
    Render the entries list page.
    All users have access to edit and rewrite.
    """
    from src.text_rewrite.config_text_rewrite.text_rewrite_config import get_available_templates

    context = {
        'can_edit': True,
        'can_rewrite': True,
        'is_app_admin': getattr(request.user, 'is_app_admin', False),
        'entries_js_config': {
            'can_edit': True,
            'can_rewrite': True,
            'record_label': _('Record'),
            'stop_label': _('Stop'),
            'save_failed': _('Save failed'),
            'could_not_save_entry': _('Could not save entry. Please try again.'),
        },
    }

    config = get_config()
    context['recorder_config'] = {
        'uploadUrl': reverse('recordings:upload'),
        'maxDuration': config.recorder.max_duration,
        'maxFileSize': config.recorder.max_file_size_mb * 1024 * 1024,
        'showTimer': True,
    }
    context['rewrite_templates'] = get_available_templates()

    return render(request, 'entries/list.html', context)


@login_required
@require_GET
def entries_list_api(request):
    """
    API endpoint for fetching entries with pagination, search, and date filtering.

    Query Parameters:
        cursor: Pagination cursor (occurred_at timestamp in ISO format)
        search: Search query string (searches content and title)
        date_preset: Date filter preset (today, week, month, all)
        date_from: Custom date range start (ISO format)
        date_to: Custom date range end (ISO format)
        page_size: Number of entries per page (default 20, max 50)
        ids: Comma-separated entry UUIDs (max 20); returns only those entries in order

    Returns:
        JSON response with entries, pagination info, and search feedback.
    """
    user = request.user

    # Parse parameters
    cursor = request.GET.get('cursor')
    search_query = request.GET.get('search', '').strip()
    date_preset = request.GET.get('date_preset', 'all')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    ids_param = request.GET.get('ids', '').strip()
    
    try:
        page_size = min(int(request.GET.get('page_size', DEFAULT_PAGE_SIZE)), 50)
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    
    # Build base queryset (most recent first; null occurred_at last)
    queryset = IngestItem.objects.filter(
        user=user,
        is_deleted=False,
    ).prefetch_related('files').select_related('retrieval_projection').order_by(F('occurred_at').desc(nulls_last=True), '-ingested_at')
    
    # Apply date filter
    now = timezone.now()
    if date_preset == 'today':
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        queryset = queryset.filter(
            Q(occurred_at__gte=start_of_day) | Q(occurred_at__isnull=True, ingested_at__gte=start_of_day)
        )
    elif date_preset == 'week':
        start_of_week = now - timedelta(days=7)
        queryset = queryset.filter(
            Q(occurred_at__gte=start_of_week) | Q(occurred_at__isnull=True, ingested_at__gte=start_of_week)
        )
    elif date_preset == 'month':
        start_of_month = now - timedelta(days=30)
        queryset = queryset.filter(
            Q(occurred_at__gte=start_of_month) | Q(occurred_at__isnull=True, ingested_at__gte=start_of_month)
        )
    elif date_from or date_to:
        if date_from:
            queryset = queryset.filter(occurred_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(occurred_at__lte=date_to)
    
    # Apply cursor pagination
    if cursor:
        try:
            from django.utils.dateparse import parse_datetime
            cursor_datetime = parse_datetime(cursor)
            if cursor_datetime:
                queryset = queryset.filter(occurred_at__lt=cursor_datetime)
        except (ValueError, TypeError):
            pass
    
    # Get total count (before search filtering, for display)
    total_count = queryset.count()
    
    # ids mode: fetch specific entries by ID (e.g. for chat sources modal)
    if ids_param:
        parsed_ids = []
        for part in ids_param.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                parsed_ids.append(uuid.UUID(part))
            except (ValueError, TypeError):
                continue
        parsed_ids = parsed_ids[:20]
        if parsed_ids:
            items = list(
                IngestItem.objects.filter(
                    user=user,
                    is_deleted=False,
                    id__in=parsed_ids,
                )
                .prefetch_related('files')
                .select_related('retrieval_projection')
            )
            id_to_item = {item.id: item for item in items}
            entries = []
            for eid in parsed_ids:
                item = id_to_item.get(eid)
                if not item:
                    continue
                content_text = item.content_text or ""
                summary_text = item.summary_text or ""
                title = item.title or ""
                attachments = [
                    {'filename': f.filename, 'storage_url': f.storage_url}
                    for f in item.files.filter(role=FileRole.ATTACHMENT)
                ]
                entries.append({
                    'id': str(item.id),
                    'title': title or (f"Entry {item.occurred_at.strftime('%Y-%m-%d %H:%M') if item.occurred_at else 'Unknown'}"),
                    'content_preview': truncate_preview(content_text),
                    'content_full': content_text,
                    'occurred_at': item.occurred_at.isoformat() if item.occurred_at else None,
                    'item_type': item.item_type,
                    'tags': _get_item_classification_labels(item),
                    'attachments': attachments,
                })
            return JsonResponse({
                'entries': entries,
                'has_more': False,
                'next_cursor': None,
                'total_count': len(entries),
                'searched_count': None,
                'filters_active': False,
            })
        return JsonResponse({
            'entries': [],
            'has_more': False,
            'next_cursor': None,
            'total_count': 0,
            'searched_count': None,
            'filters_active': False,
        })

    # Fetch and process entries
    entries = []
    searched_count = 0
    has_more = False
    next_cursor = None
    
    if search_query:
        # Search mode: need to decrypt and filter
        max_batch_size = get_max_search_batch_size()
        search_lower = search_query.lower()
        
        batch_size = 100  # Fetch in batches for efficiency
        offset = 0
        
        while len(entries) < page_size and searched_count < max_batch_size:
            batch = list(queryset[offset:offset + batch_size])
            
            if not batch:
                break
            
            for item in batch:
                searched_count += 1
                
                if searched_count > max_batch_size:
                    break
                
                # Decrypt content
                content_text = item.content_text or ""
                summary_text = item.summary_text or ""
                title = item.title or ""
                
                # Check if search query matches
                if (search_lower in (content_text or '').lower() or 
                    search_lower in (title or '').lower()):
                    display_text = content_text
                    attachments = [
                        {'filename': f.filename, 'storage_url': f.storage_url}
                        for f in item.files.filter(role=FileRole.ATTACHMENT)
                    ]
                    entries.append({
                        'id': str(item.id),
                        'title': title or f"Entry {item.occurred_at.strftime('%Y-%m-%d %H:%M') if item.occurred_at else 'Unknown'}",
                        'content_preview': truncate_preview(display_text),
                        'content_full': display_text,
                        'occurred_at': item.occurred_at.isoformat() if item.occurred_at else None,
                        'item_type': item.item_type,
                        'tags': _get_item_classification_labels(item),
                        'attachments': attachments,
                    })
                    
                    if len(entries) >= page_size:
                        # Check if there might be more
                        next_cursor = item.occurred_at.isoformat() if item.occurred_at else None
                        break
            
            offset += batch_size
        
        # Check if there are more results
        has_more = searched_count < max_batch_size and offset < total_count
        
    else:
        # No search: simple pagination
        max_browse = get_max_browse_entries()
        if max_browse > 0:
            queryset = queryset[:max_browse]
        items = list(queryset[:page_size + 1])
        has_more = len(items) > page_size
        items = items[:page_size]
        
        for item in items:
            content_text = item.content_text or ""
            title = item.title or ""
            display_text = content_text
            attachments = [
                {'filename': f.filename, 'storage_url': f.storage_url}
                for f in item.files.filter(role=FileRole.ATTACHMENT)
            ]
            entries.append({
                'id': str(item.id),
                'title': title or f"Entry {item.occurred_at.strftime('%Y-%m-%d %H:%M') if item.occurred_at else 'Unknown'}",
                'content_preview': truncate_preview(display_text),
                'content_full': display_text,
                'occurred_at': item.occurred_at.isoformat() if item.occurred_at else None,
                'item_type': item.item_type,
                'tags': _get_item_classification_labels(item),
                'attachments': attachments,
            })
        
        if items:
            next_cursor = items[-1].occurred_at.isoformat() if items[-1].occurred_at else None
    
    # Determine if filters are active
    filters_active = bool(search_query or date_preset != 'all' or date_from or date_to)
    
    return JsonResponse({
        'entries': entries,
        'has_more': has_more,
        'next_cursor': next_cursor,
        'total_count': total_count,
        'searched_count': searched_count if search_query else None,
        'filters_active': filters_active,
    })


def _collect_descendant_item_ids(root_item):
    """
    Collect all descendant IDs via parent_item and split_parent.
    Same user only. Excludes root_item (caller handles it).
    """
    user_id = root_item.user_id
    to_visit = [root_item.id]
    visited = set()
    while to_visit:
        current_id = to_visit.pop()
        if current_id in visited:
            continue
        visited.add(current_id)
        children = list(
            IngestItem.objects.filter(
                user_id=user_id,
                is_deleted=False,
            )
            .filter(Q(parent_item_id=current_id) | Q(split_parent_id=current_id))
            .values_list("id", flat=True)
        )
        to_visit.extend(c for c in children if c not in visited)
    visited.discard(root_item.id)
    return visited


def _soft_delete_item_and_cleanup(item):
    """Soft-delete an IngestItem and run cleanup for calendar, list, financial, todo, retrieval."""
    item.is_deleted = True
    item.deleted_at = timezone.now()
    item.save(update_fields=["is_deleted", "deleted_at"])
    delete_calendar_events_for_item(item)
    delete_batch_calendar_for_item(item)
    delete_list_records_for_item(item)
    delete_financial_records_for_item(item)
    delete_todo_records_for_item(item)
    now = timezone.now()
    ItemTriageResult.all_objects.filter(item=item).update(is_deleted=True, deleted_at=now)
    ItemClassificationRun.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
    ItemClassificationSelection.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
    ItemEntityLink.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
    ManagedListProjection.objects.filter(source_ingest_item=item).delete()
    from src.retrieval.models import ItemRetrievalProjection

    ItemRetrievalProjection.objects.filter(ingest_item=item).delete()


@login_required
@require_http_methods(["POST", "DELETE"])
def entry_delete_api(request, entry_id):
    """
    Soft-delete an entry (IngestItem) for the current user.
    Cascades to all descendants (child_items and split_children) recursively.
    Returns 204 on success, 404 if not found or not owned.
    """
    user = request.user

    item = IngestItem.objects.filter(
        id=entry_id,
        user=user,
        is_deleted=False,
    ).first()

    if not item:
        return JsonResponse({"error": _("Not found")}, status=404)

    descendant_ids = _collect_descendant_item_ids(item)
    _soft_delete_item_and_cleanup(item)

    if descendant_ids:
        descendants = IngestItem.objects.filter(
            id__in=descendant_ids,
            user=user,
        )
        for desc in descendants:
            _soft_delete_item_and_cleanup(desc)

    return HttpResponse(status=204)


def _process_edit_attachments(request, item) -> int:
    """Process file attachments from edit request. Returns count of attachments saved."""
    files_list = request.FILES.getlist('files')
    if not files_list:
        return 0
    config = get_config()
    storage_base = ensure_directory(config.storage.audio_temp_path)
    use_local_filesystem = config.storage.save_attachments_to_local_filesystem
    if use_local_filesystem:
        ensure_local_storage_tree(config)
        attach_dir = local_attachments_dir_for_item(config, request.user.id, item.id)
    elif not verify_drive_permissions(request.user):
        logger.warning(
            "User %s has no Drive access, skipping edit attachment upload",
            request.user.id,
        )
        return 0
    else:
        attach_dir = storage_base / str(request.user.id) / 'attachments' / str(item.id)
        ensure_directory(attach_dir)
    attachment_count = 0
    used_local_names: set[str] = set()
    for uploaded_file in files_list:
        if not uploaded_file:
            continue
        try:
            if use_local_filesystem:
                safe_base = sanitize_storage_filename(uploaded_file.name or "file")
                safe_name = allocate_unique_attachment_filename(
                    attach_dir, safe_base, used_local_names
                )
            else:
                safe_name = uploaded_file.name or "file"
            local_path = attach_dir / safe_name
            with open(local_path, 'wb') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)
            if use_local_filesystem:
                resolved = str(local_path.resolve())
                ItemFile.objects.create(
                    user=request.user,
                    item=item,
                    role=FileRole.ATTACHMENT,
                    filename=safe_name,
                    mime_type=uploaded_file.content_type or 'application/octet-stream',
                    storage_url=resolved,
                    drive_folder_id='',
                    bytes=uploaded_file.size,
                )
                logger.info(
                    "Saved edit attachment on local disk for item %s: %s",
                    item.id,
                    safe_name,
                )
            else:
                itemfile = ItemFile.objects.create(
                    user=request.user,
                    item=item,
                    role=FileRole.ATTACHMENT,
                    filename=safe_name,
                    mime_type=uploaded_file.content_type or 'application/octet-stream',
                    storage_url='',
                    bytes=uploaded_file.size,
                )
                try:
                    result = upload_local_file_to_user_drive_folder(
                        request.user,
                        str(local_path),
                        safe_name,
                        uploaded_file.content_type or 'application/octet-stream',
                        subfolder_name=str(item.id),
                    )
                    itemfile.storage_url = result.get('webViewLink', '')
                    itemfile.drive_folder_id = result.get('parent_folder_id', '')
                    itemfile.filename = result.get('name', safe_name)
                    itemfile.save(update_fields=['storage_url', 'drive_folder_id', 'filename'])
                    logger.info("Uploaded edit attachment to Drive for item %s: %s", item.id, safe_name)
                except GoogleAuthError as e:
                    logger.warning("Drive auth failed for edit attachment %s: %s", safe_name, e)
                except Exception as e:
                    logger.error("Failed to upload edit attachment %s to Drive: %s", safe_name, e)
                finally:
                    local_path.unlink(missing_ok=True)
            attachment_count += 1
        except Exception as e:
            logger.error("Failed to save edit attachment locally: %s", e)
    return attachment_count


@login_required
@require_POST
@csrf_protect
def entry_edit_api(request, entry_id):
    """
    Edit an entry: overwrite in-place (log change) or create linked copy.
    Accepts JSON or multipart/form-data (with optional file attachments).
    POST body (JSON): {"content_text": str, "title": str, "tags": [str], "create_new": bool}
    POST body (multipart): content_text, title, create_new, files[]
    """
    user = request.user

    item = IngestItem.objects.filter(
        id=entry_id,
        user=user,
        is_deleted=False,
    ).prefetch_related('files').select_related('retrieval_projection').first()

    if not item:
        return JsonResponse({'error': _('Not found')}, status=404)

    content_type = request.content_type or ''
    is_multipart = 'multipart/form-data' in content_type or list(request.FILES.keys())

    if is_multipart:
        content_text = request.POST.get('content_text', '')
        title = (request.POST.get('title') or '').strip()
        create_new = request.POST.get('create_new', 'false').lower() in ('true', '1', 'yes')
    else:
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': _('Invalid JSON')}, status=400)
        content_text = body.get('content_text', '')
        title = (body.get('title') or '').strip()
        create_new = bool(body.get('create_new', False))

    if create_new:
        new_item = IngestItem.objects.create(
            user=user,
            parent_item=item,
            provider=item.provider,
            item_type=item.item_type,
            template_type=item.template_type,
            status=IngestStatus.PROCESSED,
            occurred_at=item.occurred_at,
            title=title or "",
            content_text=content_text or "",
            summary_text=item.summary_text or "",
        )
        source_item = new_item.parent_item or new_item
        if has_calendar_classification(source_item):
            detected_lang = item.detected_language or ""
            parse_batch_calendar_task.delay(str(new_item.id), content_text or "", detected_lang)
        elif has_list_classification(source_item):
            save_list_from_formatted_text(new_item, content_text or "")
        elif has_financial_classification(source_item):
            save_financial_from_formatted_text(new_item, content_text or "")
        else:
            detected_lang = item.detected_language or ""
            classify_item_task.delay(str(new_item.id), content_text or "", detected_lang)
        from src.retrieval.tasks import index_entry_prep_task
        index_entry_prep_task.delay(str(new_item.id))
        try:
            from src.gigo.services import record_entry
            record_entry(
                user=user,
                item=new_item,
                content_text=content_text or "",
                item_type=item.item_type or "text",
            )
        except Exception as e:
            logger.warning(f"Could not record GIGO entry: {e}")
        attachment_count = _process_edit_attachments(request, new_item)
        response_data = {
            'entry': _build_entry_response(new_item, content_text or "", title or ""),
            'created_new': True,
        }
        if has_calendar_classification(source_item):
            response_data['calendar_parsing_queued'] = True
        if attachment_count:
            response_data['attachment_count'] = attachment_count
        return JsonResponse(response_data)
    else:
        old_content = item.content_text or ""
        old_title = item.title or ""
        fields_changed = []
        if (content_text or '') != (old_content or ''):
            fields_changed.append('content_text')
        if (title or '') != (old_title or ''):
            fields_changed.append('title')

        item.content_text = content_text or ""
        item.summary_text = item.summary_text or ""
        item.title = title or ""
        item.save(update_fields=['content_text', 'summary_text', 'title'])

        if fields_changed:
            IngestItemEditLog.objects.create(
                item=item,
                edited_by=user,
                fields_changed=fields_changed,
            )

        if has_calendar_classification(item):
            delete_calendar_events_for_item(item)
            delete_batch_calendar_for_item(item)
            IngestJob.objects.filter(item=item, job_type=JobType.PARSE_CALENDAR).delete()
            detected_lang = item.detected_language or ""
            parse_batch_calendar_task.delay(str(item.id), content_text or "", detected_lang)

        if has_list_classification(item):
            save_list_from_formatted_text(item, content_text or "")
        elif has_financial_classification(item):
            save_financial_from_formatted_text(item, content_text or "")

        if not (has_calendar_classification(item) or has_list_classification(item) or has_financial_classification(item) or has_todo_classification(item)):
            now = timezone.now()
            ItemClassificationRun.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
            ItemClassificationSelection.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
            ItemEntityLink.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
            IngestJob.objects.filter(item=item, job_type__in=[JobType.CLASSIFY_ITEM, JobType.PARSE_CALENDAR]).delete()
            detected_lang = item.detected_language or ""
            classify_item_task.delay(str(item.id), content_text or "", detected_lang)

        from src.retrieval.tasks import index_entry_prep_task
        index_entry_prep_task.delay(str(item.id))
        attachment_count = _process_edit_attachments(request, item)
        response_data = {
            'entry': _build_entry_response(item, content_text or "", title or ""),
            'created_new': False,
        }
        if has_calendar_classification(item):
            response_data['calendar_parsing_queued'] = True
        if attachment_count:
            response_data['attachment_count'] = attachment_count
        return JsonResponse(response_data)
