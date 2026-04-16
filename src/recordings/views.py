"""
Recording Views

Handles audio file uploads and the recording UI page.
"""

import json
import logging
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.utils import timezone
from django.utils.translation import gettext as _

from src.common.config import get_config
from src.common.utils import ensure_directory
from src.common.google_account.auth import verify_drive_permissions, GoogleAuthError
from src.common.drive_upload import upload_file_to_user_drive_folder, upload_local_file_to_user_drive_folder
from src.common.storage_local import (
    allocate_unique_attachment_filename,
    ensure_local_storage_tree,
    local_attachments_dir_for_item,
    local_recording_user_dir,
    sanitize_storage_filename,
)
from src.accounts.models import GlobalSettings, UserPreferences
from src.text_rewrite.config_text_rewrite.text_rewrite_config import get_available_templates
from src.ingestion.models import (
    IngestItem,
    IngestJob,
    ItemFile,
    JobType,
    JobStatus,
    ItemType,
    Provider,
    TemplateType,
    IngestStatus,
    FileRole,
)
from src.ingestion.tasks import (
    broadcast_complete,
    get_channel_layer,
    get_pending_transcription,
    process_audio_ingest,
    transcribe_only_task,
)
from src.batch_calendar.services import delete_batch_calendar_for_item, delete_calendar_events_for_item
from src.batch_calendar.tasks import parse_batch_calendar_task
from src.classification.services import has_calendar_classification, has_list_classification
from src.list_parser.services import save_list_from_formatted_text
from src.classification.tasks import classify_item_task
from src.ingestion.audio_services import AudioChunker
logger = logging.getLogger(__name__)


@login_required
def recording_page(request):
    """
    Render the recording page UI.
    
    Provides the audio recorder interface with real-time status updates.
    """
    config = get_config()
    try:
        prefs = request.user.preferences
        show_timer = prefs.show_recording_timer
        show_inline_rewrite = prefs.show_inline_rewrite
    except UserPreferences.DoesNotExist:
        show_timer = True
        show_inline_rewrite = True

    context = {
        'max_duration': config.recorder.max_duration,
        'max_file_size_mb': config.recorder.max_file_size_mb,
        'allow_unlimited': config.recorder.allow_unlimited,
        'is_app_admin': getattr(request.user, 'is_app_admin', False),
        'show_inline_rewrite': show_inline_rewrite,
        'save_attachments_to_local_filesystem': config.storage.save_attachments_to_local_filesystem,
        'rewrite_templates': get_available_templates() if show_inline_rewrite else [],
        'rewrite_api_url': reverse('text_rewrite:api_rewrite'),
        'recorder_config': {
            'uploadUrl': reverse('recordings:upload'),
            'updateEntryUrlTemplate': reverse('recordings:update_entry', args=['00000000-0000-0000-0000-000000000000']).replace('00000000-0000-0000-0000-000000000000', '{id}'),
            'maxDuration': config.recorder.max_duration,
            'maxFileSize': config.recorder.max_file_size_mb * 1024 * 1024,
            'swipeVoiceUrl': reverse('recordings:record'),
            'swipeTextUrl': reverse('text_input:page'),
            'serviceWorkerUrl': reverse('recordings:service-worker'),
            'showTimer': show_timer,
            'isAppAdmin': getattr(request.user, 'is_app_admin', False),
        },
    }

    return render(request, 'recordings/index.html', context)


@login_required
@require_http_methods(["GET"])
def upload_to_drive_page(request):
    """
    Render the Google Drive file upload page.
    """
    cfg = get_config()
    return render(
        request,
        "recordings/upload.html",
        {
            "save_attachments_to_local_filesystem": cfg.storage.save_attachments_to_local_filesystem,
        },
    )


@login_required
@require_http_methods(["POST"])
@csrf_protect
def upload_file_to_drive(request):
    """
    Upload files to the authenticated user's Google Drive folder and create
    an IngestItem for tracking. Accepts multiple files; one IngestItem per batch.
    Files go to VoiceDiaryFiles/attachments/<item.id>/.
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {"error": "unauthorized", "message": _("Authentication required")},
            status=401,
        )

    files_list = request.FILES.getlist("files")
    if not files_list:
        return JsonResponse(
            {"error": "no_file", "message": _("No files provided")},
            status=400,
        )

    config = get_config()
    use_local_filesystem = config.storage.save_attachments_to_local_filesystem

    if not use_local_filesystem:
        if not verify_drive_permissions(request.user):
            return JsonResponse(
                {
                    "error": "drive_not_connected",
                    "message": _("Connect Google account with Drive access to save files."),
                },
                status=403,
            )

    occurred_at = timezone.now()
    title = f"File Upload {occurred_at.strftime('%Y-%m-%d %H:%M')}"
    filenames_text = "\n".join(
        getattr(f, "name", "file") or "file" for f in files_list
    )

    try:
        with transaction.atomic():
            item = IngestItem.objects.create(
                user=request.user,
                provider=Provider.MANUAL,
                item_type=ItemType.FILE,
                template_type=TemplateType.PLAIN,
                status=IngestStatus.PROCESSED,
                occurred_at=occurred_at,
                title=title,
                content_text=filenames_text,
                summary_text="",
            )

            uploaded_infos = []
            if use_local_filesystem:
                ensure_local_storage_tree(config)
                attach_dir = local_attachments_dir_for_item(
                    config, request.user.id, item.id
                )
                used_local_names: set[str] = set()
                for uploaded in files_list:
                    safe_base = sanitize_storage_filename(
                        getattr(uploaded, "name", "") or "file"
                    )
                    safe_name = allocate_unique_attachment_filename(
                        attach_dir, safe_base, used_local_names
                    )
                    local_path = attach_dir / safe_name
                    with open(local_path, "wb") as f:
                        for chunk in uploaded.chunks():
                            f.write(chunk)
                    resolved = str(local_path.resolve())
                    ItemFile.objects.create(
                        user=request.user,
                        item=item,
                        role=FileRole.ATTACHMENT,
                        filename=safe_name,
                        mime_type=getattr(uploaded, "content_type", "") or "",
                        storage_url=resolved,
                        drive_folder_id="",
                        bytes=getattr(uploaded, "size", None),
                    )
                    uploaded_infos.append(
                        {
                            "id": "",
                            "name": safe_name,
                            "webViewLink": resolved,
                        }
                    )
            else:
                for uploaded in files_list:
                    result = upload_file_to_user_drive_folder(
                        request.user,
                        uploaded,
                        subfolder_name=str(item.id),
                    )
                    ItemFile.objects.create(
                        user=request.user,
                        item=item,
                        role=FileRole.ATTACHMENT,
                        filename=result.get("name", getattr(uploaded, "name", "")),
                        mime_type=getattr(uploaded, "content_type", "") or "",
                        storage_url=result.get("webViewLink", ""),
                        drive_folder_id=result.get("parent_folder_id", ""),
                        bytes=getattr(uploaded, "size", None),
                    )
                    uploaded_infos.append(
                        {
                            "id": result.get("id"),
                            "name": result.get("name"),
                            "webViewLink": result.get("webViewLink"),
                        }
                    )
    except GoogleAuthError as e:
        logger.warning(f"Drive auth failed for user {request.user.id}: {e}")
        return JsonResponse(
            {
                "error": "drive_auth_failed",
                "message": _("Could not connect to Google Drive. Try reconnecting your account in settings."),
            },
            status=503,
        )
    except Exception as e:
        logger.exception(f"Drive upload failed for user {request.user.id}: {e}")
        return JsonResponse(
            {"error": "upload_failed", "message": _("An unexpected error occurred")},
            status=500,
        )

    return JsonResponse(
        {"item_id": str(item.id), "files": uploaded_infos, "count": len(uploaded_infos)},
        status=201,
    )


@login_required
@require_http_methods(["POST"])
@csrf_protect
def upload_audio(request):
    """
    Handle audio file upload.
    
    Accepts audio file upload (WebM or WAV). Two modes:
    - Normal: Creates IngestItem/IngestJob, enqueues full pipeline
    - Transcribe-only: Saves audio, enqueues transcription-only task (used by edit mode recorder)
    """
    config = get_config()

    # Get uploaded file
    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'error': _('No audio file provided')}, status=400)
    
    # Get template_type (defaults to 'plain')
    template_type = request.POST.get('template_type', 'plain')
    if template_type not in (TemplateType.PLAIN, TemplateType.LIST):
        template_type = TemplateType.PLAIN
    
    # Validate file size
    max_size_bytes = config.recorder.max_file_size_mb * 1024 * 1024
    if audio_file.size > max_size_bytes:
        return JsonResponse({
            'error': _('File too large. Maximum size is %(max_size)sMB') % {'max_size': config.recorder.max_file_size_mb}
        }, status=400)

    # Determine file format
    content_type = audio_file.content_type or 'audio/webm'
    if 'webm' in content_type:
        extension = 'webm'
    elif 'wav' in content_type:
        extension = 'wav'
    elif 'mp3' in content_type or 'mpeg' in content_type:
        extension = 'mp3'
    else:
        extension = content_type.split('/')[-1] if '/' in content_type else 'webm'
    
    # Temp audio base (transcribe-only / processing); permanent recordings use local root when enabled
    storage_base = ensure_directory(config.storage.audio_temp_path)
    use_local_filesystem = config.storage.save_attachments_to_local_filesystem
    if use_local_filesystem:
        ensure_local_storage_tree(config)

    transcribe_only = request.POST.get('transcribe_only', '').lower() in ('1', 'true', 'yes')

    if transcribe_only:
        # === TRANSCRIBE-ONLY MODE (used by edit-mode recorder) ===
        temp_id = str(uuid.uuid4())
        filename = f"{temp_id}.{extension}"
        file_path = storage_base / str(request.user.id) / 'pending' / filename
        ensure_directory(file_path.parent)
        
        # Save file
        with open(file_path, 'wb') as f:
            for chunk in audio_file.chunks():
                f.write(chunk)
        
        logger.info(f"Saved audio file for transcribe-only: {file_path} ({audio_file.size} bytes)")

        duration = AudioChunker().get_audio_duration(file_path)
        if not duration or duration <= 0:
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass
            return JsonResponse({
                'error': _('Could not determine audio duration. Please try again.'),
            }, status=400)

        # Enqueue transcription-only task
        transcribe_only_task.delay(
            temp_id,
            str(file_path),
            request.user.id,
            str(request.user.id),
            template_type,
        )
        
        logger.info(f"Enqueued transcription-only task: temp_id={temp_id}, template_type={template_type}")
        
        return JsonResponse({
            'temp_id': temp_id,
            'status': 'transcribing',
            'message': 'Audio uploaded, transcription in progress',
        })
    
    else:
        # === NORMAL MODE: Full pipeline ===
        item_id = uuid.uuid4()
        filename = f"{item_id}.{extension}"
        if use_local_filesystem:
            rec_dir = local_recording_user_dir(config, request.user.id)
            file_path = rec_dir / filename
        else:
            file_path = storage_base / str(request.user.id) / filename
        ensure_directory(file_path.parent)
        
        # Save file
        with open(file_path, 'wb') as f:
            for chunk in audio_file.chunks():
                f.write(chunk)
        
        logger.info(f"Saved audio file: {file_path} ({audio_file.size} bytes)")

        duration = AudioChunker().get_audio_duration(file_path)
        if not duration or duration <= 0:
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass
            return JsonResponse({
                'error': _('Could not determine audio duration. Please try again.'),
            }, status=400)

        # Create IngestItem (duration already known from quota check)
        item = IngestItem.objects.create(
            id=item_id,
            user=request.user,
            provider=Provider.MANUAL,
            item_type=ItemType.AUDIO,
            template_type=template_type,
            occurred_at=timezone.now(),
            title=f"Voice Recording {timezone.now().strftime('%Y-%m-%d %H:%M')}",
            original_file_size=audio_file.size,
            audio_format=extension,
            audio_duration_seconds=duration,
        )
        
        # Create ItemFile for audio
        ItemFile.objects.create(
            user=request.user,
            item=item,
            role='original',
            filename=filename,
            mime_type=content_type,
            storage_url=str(file_path.resolve()),
            bytes=audio_file.size,
        )
        
        # Handle attached files (auto-save mode with attachments).
        # Upload to Drive synchronously (same as text_input) so files reliably
        # reach the user's Drive. Celery task was unreliable when worker cannot
        # access the same filesystem as the web process.
        # Only create attachments dir when we have actual attachment files (not just audio).
        attachment_count = 0
        attachment_files = request.FILES.getlist('files')
        if attachment_files:
            if use_local_filesystem:
                attach_dir = local_attachments_dir_for_item(
                    config, request.user.id, item_id
                )
                used_local_names: set[str] = set()
                for uploaded_file in attachment_files:
                    if not uploaded_file:
                        continue
                    try:
                        safe_base = sanitize_storage_filename(
                            uploaded_file.name or "file"
                        )
                        safe_name = allocate_unique_attachment_filename(
                            attach_dir, safe_base, used_local_names
                        )
                        local_path = attach_dir / safe_name
                        with open(local_path, "wb") as f:
                            for chunk in uploaded_file.chunks():
                                f.write(chunk)
                        resolved = str(local_path.resolve())
                        ItemFile.objects.create(
                            user=request.user,
                            item=item,
                            role=FileRole.ATTACHMENT,
                            filename=safe_name,
                            mime_type=uploaded_file.content_type
                            or "application/octet-stream",
                            storage_url=resolved,
                            drive_folder_id="",
                            bytes=uploaded_file.size,
                        )
                        logger.info(
                            f"Saved attachment on local disk for item {item.id}: {safe_name}"
                        )
                        attachment_count += 1
                    except Exception as e:
                        logger.error(f"Failed to save attachment locally: {e}")
            elif not verify_drive_permissions(request.user):
                logger.warning(
                    f"User {request.user.id} has no Drive access, skipping file attachments"
                )
            else:
                attach_dir = storage_base / str(request.user.id) / 'attachments' / str(item_id)
                ensure_directory(attach_dir)

                for uploaded_file in attachment_files:
                    if not uploaded_file:
                        continue
                    try:
                        safe_name = uploaded_file.name or 'file'
                        local_path = attach_dir / safe_name
                        with open(local_path, 'wb') as f:
                            for chunk in uploaded_file.chunks():
                                f.write(chunk)
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
                            logger.info(f"Uploaded attachment to Drive for item {item.id}: {safe_name}")
                        except GoogleAuthError as e:
                            logger.warning(f"Drive auth failed for attachment {safe_name}: {e}")
                        except Exception as e:
                            logger.error(f"Failed to upload attachment {safe_name} to Drive: {e}")
                        finally:
                            local_path.unlink(missing_ok=True)
                        attachment_count += 1
                    except Exception as e:
                        logger.error(f"Failed to save attachment locally: {e}")
        
        # Create IngestJob
        job = IngestJob.objects.create(
            user=request.user,
            item=item,
            job_type=JobType.PROCESS_AUDIO,
            status=JobStatus.QUEUED,
        )
        
        # Enqueue Celery task
        process_audio_ingest.delay(str(job.id))
        
        logger.info(f"Enqueued audio processing job: {job.id} for item: {item.id} with {attachment_count} attachments")
        
        return JsonResponse({
            'item_id': str(item.id),
            'job_id': str(job.id),
            'status': 'processing',
            'message': 'Audio uploaded and processing started',
            'attachment_count': attachment_count,
        })


@login_required
@require_http_methods(["GET"])
def get_pending_status(request, temp_id):
    """
    Get status for a transcribe-only (pending) recording by temp_id.
    
    Used by polling fallback when WebSocket is unavailable.
    Checks Redis for pending transcription result.
    """
    data = get_pending_transcription(str(temp_id))
    if not data:
        return JsonResponse({'status': 'in_progress', 'message': _('Processing...')})

    status = data.get('status')
    if status == 'discarded':
        return JsonResponse({
            'status': 'discarded',
            'reason': data.get('reason', _('No speech detected')),
        })
    if status == 'error':
        return JsonResponse({
            'status': 'error',
            'error': data.get('error', _('Transcription failed')),
        })

    transcription = data.get('transcription', '')
    detected_language = data.get('detected_language', '')
    return JsonResponse({
        'status': 'ready',
        'transcribed_text': transcription,
        'content_text': transcription,
        'detected_language': detected_language,
    })


@login_required
@require_http_methods(["GET"])
def get_status(request, item_id):
    """
    Get current processing status for an item.
    
    Returns the current status and progress for polling clients.
    """
    item = get_object_or_404(IngestItem, id=item_id, user=request.user)

    content_text_plain = None
    if item.status in ('processed', 'tagged'):
        content_text_plain = item.content_text

    # Get latest job
    job = item.jobs.order_by('-queued_at').first()

    response_data = {
        'item_id': str(item.id),
        'item_status': item.status,
        'content_text': content_text_plain if item.status in ('processed', 'tagged') else None,
        'detected_language': item.detected_language,
    }
    
    if job:
        response_data.update({
            'job_id': str(job.id),
            'job_status': job.status,
            'checkpoint': job.checkpoint,
            'last_error': job.last_error if job.status == 'error' else None,
        })
    
    # Check for calendar conflict (from PARSE_CALENDAR job)
    calendar_job = item.jobs.filter(job_type='parse_calendar').order_by('-queued_at').first()
    if calendar_job and calendar_job.checkpoint_data:
        if calendar_job.checkpoint_data.get('conflict'):
            response_data['calendar_conflict'] = True
            response_data['confirmation_url'] = calendar_job.checkpoint_data.get('confirmation_url', '')
            response_data['calendar_event_id'] = calendar_job.checkpoint_data.get('calendar_event_id', '')
    
    return JsonResponse(response_data)


@login_required
@require_http_methods(["PATCH", "POST"])
@csrf_protect
def update_entry_content(request, item_id):
    """
    Update an existing processed IngestItem's content_text after user edit.

    Accepts:
    - POST  multipart/form-data  (text + optional file attachments)
    - PATCH application/json     (text-only, backward compatible)

    Django 5.0 only parses multipart/form-data for POST (request.POST and
    request.FILES are empty for PATCH), so file uploads MUST use POST.

    Re-encrypts, saves, clears tags, resets classification/calendar jobs,
    and re-queues classify_item_task (which may trigger parse_batch_calendar_task).
    When files are present they are uploaded to the user's Google Drive and
    stored as ItemFile records.
    """
    item = get_object_or_404(IngestItem, id=item_id, user=request.user)
    if item.status not in (IngestStatus.PROCESSED, IngestStatus.TAGGED):
        return JsonResponse({'error': _('Item is not in a state that allows editing')}, status=400)

    # Parse request: support both JSON and multipart/form-data
    content_type = request.content_type or ''
    files_list = []
    is_multipart = 'multipart/form-data' in content_type or list(request.FILES.keys())

    if is_multipart:
        content_text = request.POST.get('content_text', '')
        files_list = request.FILES.getlist('files')
    elif 'application/json' in content_type:
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({'error': _('Invalid JSON')}, status=400)
        content_text = data.get('content_text', '')
    else:
        return JsonResponse({'error': _('Unsupported Content-Type')}, status=400)

    if not isinstance(content_text, str):
        return JsonResponse({'error': _('content_text must be a string')}, status=400)
    if not content_text.strip():
        return JsonResponse({'error': _('Content cannot be empty')}, status=400)

    user = item.user
    if not user:
        return JsonResponse({'error': _('Item has no associated user')}, status=400)

    summary_plain = item.summary_text or ""
    title_plain = item.title or ""

    is_calendar_entry = has_calendar_classification(item)
    is_list_entry = has_list_classification(item)

    with transaction.atomic():
        if is_calendar_entry:
            delete_calendar_events_for_item(item)
            delete_batch_calendar_for_item(item)
        item.content_text = content_text
        item.summary_text = summary_plain or ""
        item.title = title_plain or ""
        item.save(update_fields=['content_text', 'summary_text', 'title'])

        if is_calendar_entry:
            IngestJob.objects.filter(item=item, job_type=JobType.PARSE_CALENDAR).delete()
        elif not is_list_entry:
            from django.utils import timezone as tz
            from src.classification.models import (
                ItemClassificationRun,
                ItemClassificationSelection,
                ItemEntityLink,
            )
            now = tz.now()
            ItemClassificationRun.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
            ItemClassificationSelection.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
            ItemEntityLink.all_objects.filter(ingest_item=item).update(is_deleted=True, deleted_at=now)
            IngestJob.objects.filter(item=item, job_type__in=[JobType.CLASSIFY_ITEM, JobType.PARSE_CALENDAR]).delete()

    detected_lang = item.detected_language or ''
    if is_calendar_entry:
        parse_batch_calendar_task.delay(str(item.id), content_text, detected_lang)
        logger.info(f"Updated and re-queued calendar parsing for item {item.id}")
    elif is_list_entry:
        save_list_from_formatted_text(item, content_text)
        logger.info(f"Updated list record for item {item.id}")
    else:
        classify_item_task.delay(str(item.id), content_text, detected_lang)
        logger.info(f"Updated and re-queued classification for item {item.id}")

    # Handle file attachments from the edit session
    attachment_count = 0
    if files_list:
        config = get_config()
        storage_base = ensure_directory(config.storage.audio_temp_path)
        use_local_filesystem = config.storage.save_attachments_to_local_filesystem
        if use_local_filesystem:
            ensure_local_storage_tree(config)
            attach_dir = local_attachments_dir_for_item(
                config, request.user.id, item.id
            )
            used_local_names: set[str] = set()
            for uploaded_file in files_list:
                if not uploaded_file:
                    continue
                try:
                    safe_base = sanitize_storage_filename(
                        uploaded_file.name or "file"
                    )
                    safe_name = allocate_unique_attachment_filename(
                        attach_dir, safe_base, used_local_names
                    )
                    local_path = attach_dir / safe_name
                    with open(local_path, "wb") as f:
                        for chunk in uploaded_file.chunks():
                            f.write(chunk)
                    resolved = str(local_path.resolve())
                    ItemFile.objects.create(
                        user=request.user,
                        item=item,
                        role=FileRole.ATTACHMENT,
                        filename=safe_name,
                        mime_type=uploaded_file.content_type
                        or "application/octet-stream",
                        storage_url=resolved,
                        drive_folder_id="",
                        bytes=uploaded_file.size,
                    )
                    logger.info(
                        f"Saved edit-mode attachment on local disk for item {item.id}: {safe_name}"
                    )
                    attachment_count += 1
                except Exception as e:
                    logger.error(f"Failed to save edit attachment locally: {e}")
        elif not verify_drive_permissions(request.user):
            logger.warning(
                f"User {request.user.id} has no Drive access, skipping edit-mode file attachments"
            )
        else:
            attach_dir = storage_base / str(request.user.id) / 'attachments' / str(item.id)
            ensure_directory(attach_dir)

            for uploaded_file in files_list:
                if not uploaded_file:
                    continue
                try:
                    safe_name = uploaded_file.name or 'file'
                    local_path = attach_dir / safe_name
                    with open(local_path, 'wb') as f:
                        for chunk in uploaded_file.chunks():
                            f.write(chunk)
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
                        logger.info(f"Uploaded edit-mode attachment to Drive for item {item.id}: {safe_name}")
                    except GoogleAuthError as e:
                        logger.warning(f"Drive auth failed for edit attachment {safe_name}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to upload edit attachment {safe_name} to Drive: {e}")
                    finally:
                        local_path.unlink(missing_ok=True)
                    attachment_count += 1
                except Exception as e:
                    logger.error(f"Failed to save edit attachment locally: {e}")

    channel_layer = get_channel_layer()
    if channel_layer:
        broadcast_complete(channel_layer, str(item.id), content_text, detected_lang)

    from src.retrieval.tasks import index_entry_prep_task
    index_entry_prep_task.delay(str(item.id))

    return JsonResponse({'success': True, 'attachment_count': attachment_count})


def manifest(request):
    """Serve the PWA manifest.json."""
    global_allowed = GlobalSettings.get_value('pwa.standalone_ui_allowed', True)
    user_prefers_standalone = True
    if request.user.is_authenticated:
        try:
            user_prefers_standalone = request.user.preferences.standalone_app_ui
        except Exception:
            pass
    use_standalone = bool(global_allowed) and user_prefers_standalone

    manifest_content = {
        "name": "Voice Diary",
        "short_name": "VoiceDiary",
        "description": "Record voice notes with automatic transcription and translation",
        "start_url": "/voice/",
        "display": "standalone" if use_standalone else "browser",
        "background_color": "#ffffff",
        "theme_color": "#4a90d9",
        "icons": [
            {
                "src": "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    }
    return JsonResponse(manifest_content)


def service_worker(request):
    """Serve the service worker JavaScript."""
    sw_path = Path(settings.BASE_DIR) / 'src' / 'static' / 'recordings' / 'js' / 'service-worker.js'
    
    if sw_path.exists():
        with open(sw_path, 'r') as f:
            content = f.read()
    else:
        # Minimal service worker if file doesn't exist
        content = """
// Voice Diary Service Worker
const CACHE_NAME = 'voicediary-v1';

self.addEventListener('install', (event) => {
    console.log('[ServiceWorker] Install');
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('[ServiceWorker] Activate');
    event.waitUntil(clients.claim());
});

self.addEventListener('fetch', (event) => {
    // Pass through all requests
    event.respondWith(fetch(event.request));
});
"""
    
    return HttpResponse(content, content_type='application/javascript')
