"""
Text Input Views

Provides the endpoint for text-based entry ingestion.
Supports JSON (no attachments) and multipart/form-data (text + optional files).
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext as _

from src.common.config import get_config
from src.common.google_account.auth import verify_drive_permissions
from src.common.drive_upload import upload_file_to_user_drive_folder
from src.common.storage_local import (
    allocate_unique_attachment_filename,
    ensure_local_storage_tree,
    local_attachments_dir_for_item,
    sanitize_storage_filename,
)
from src.ingestion.models import ItemFile, FileRole

from .services import (
    ingest_text_entry,
    EmptyTextError,
    InvalidTemplateTypeError,
    TextInputError,
)
from src.quotas.services import check_token_quota
from src.accounts.models import UserPreferences
from src.text_rewrite.config_text_rewrite.text_rewrite_config import get_available_templates

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["GET"])
def text_input_page(request):
    """Render the text input page."""
    try:
        prefs = request.user.preferences
        show_inline_rewrite = prefs.show_inline_rewrite
    except UserPreferences.DoesNotExist:
        show_inline_rewrite = True
    cfg = get_config()
    return render(request, 'text_input/index.html', {
        'is_app_admin': getattr(request.user, 'is_app_admin', False),
        'show_inline_rewrite': show_inline_rewrite,
        'save_attachments_to_local_filesystem': cfg.storage.save_attachments_to_local_filesystem,
        'rewrite_templates': get_available_templates() if show_inline_rewrite else [],
        'rewrite_api_url': reverse('text_rewrite:api_rewrite'),
    })


def _parse_request(request):
    """Return (text, template_type, title, occurred_at, files_list)."""
    content_type = request.content_type or ""
    if "multipart/form-data" in content_type:
        text = request.POST.get("text", "")
        template_type = request.POST.get("template_type", "plain")
        title = request.POST.get("title", "")
        occurred_at = request.POST.get("occurred_at") or None
        files_list = request.FILES.getlist("files")
        return text, template_type, title, occurred_at, files_list
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return None, None, None, None, None
    text = payload.get("text", "")
    template_type = payload.get("template_type", "plain")
    title = payload.get("title", "")
    occurred_at = payload.get("occurred_at")
    return text, template_type, title, occurred_at, []


@login_required
@require_POST
def ingest_text(request):
    """
    Ingest a text entry.
    
    POST /text-input/ingest/
    
    JSON body:
    {
        "text": "...",
        "template_type": "plain|list",
        "title": "...",
        "occurred_at": "..."
    }
    
    Or multipart/form-data: text, template_type, title, occurred_at, and files (multiple).
    When files are present, user must have Drive connected; files are uploaded to user's folder.
    
    Response (success - 201):
    {
        "id": "<uuid>",
        "detected_language": "en",
        "translated": false,
        "attachment_count": 0
    }
    """
    text, template_type, title, occurred_at, files_list = _parse_request(request)
    if text is None:
        return JsonResponse(
            {"error": "invalid_json", "message": _("Request body must be valid JSON")},
            status=400,
        )

    if not (text or "").strip():
        return JsonResponse(
            {"error": "empty_text", "message": _("Text is required")},
            status=400,
        )

    # Check token quota
    allowed, remaining, quota_info = check_token_quota(request.user)
    if not allowed:
        return JsonResponse(
            {
                "error": "quota_exceeded",
                "message": _("Daily token quota exceeded. Please try again tomorrow."),
                "quota": {
                    "used_tokens": quota_info.get("used_tokens", 0),
                    "limit_tokens": quota_info.get("limit_tokens", 0),
                    "remaining_tokens": quota_info.get("remaining_tokens", 0),
                },
            },
            status=429,
        )

    if occurred_at is None:
        occurred_at = timezone.now()

    config = get_config()
    use_local_filesystem = config.storage.save_attachments_to_local_filesystem

    if (
        files_list
        and not use_local_filesystem
        and not verify_drive_permissions(request.user)
    ):
        return JsonResponse(
            {
                "error": "drive_not_connected",
                "message": _("Connect Google Drive to attach files to notes."),
            },
            status=403,
        )

    uploaded_file_infos = []
    item = None
    
    if files_list:
        # Create the entry first to get its ID for the subfolder
        try:
            item, metadata = ingest_text_entry(
                user=request.user,
                text=text,
                template_type=template_type,
                title=title,
                occurred_at=occurred_at,
            )
        except EmptyTextError as e:
            logger.warning(f"Empty text error: {e}")
            return JsonResponse(
                {"error": "empty_text", "message": str(e)},
                status=400,
            )
        except InvalidTemplateTypeError as e:
            logger.warning(f"Invalid template type: {e}")
            return JsonResponse(
                {"error": "invalid_template_type", "message": str(e)},
                status=400,
            )
        except TextInputError as e:
            logger.error(f"Text input error: {e}")
            return JsonResponse(
                {"error": "text_input_error", "message": str(e)},
                status=400,
            )
        except Exception as e:
            logger.exception(f"Unexpected error in text ingest: {e}")
            return JsonResponse(
                {"error": "server_error", "message": _("An unexpected error occurred")},
                status=500,
            )
        
        # Persist attachments (Drive or local filesystem)
        try:
            if use_local_filesystem:
                ensure_local_storage_tree(config)
                attach_dir = local_attachments_dir_for_item(
                    config, request.user.id, item.id
                )
                used_local_names: set[str] = set()
                for f in files_list:
                    safe_base = sanitize_storage_filename(f.name or "file")
                    safe_name = allocate_unique_attachment_filename(
                        attach_dir, safe_base, used_local_names
                    )
                    local_path = attach_dir / safe_name
                    with open(local_path, "wb") as out:
                        for chunk in f.chunks():
                            out.write(chunk)
                    uploaded_file_infos.append({
                        "name": safe_name,
                        "storage_url": str(local_path.resolve()),
                        "drive_folder_id": "",
                        "size": getattr(f, "size", None),
                        "content_type": getattr(f, "content_type", "") or "",
                    })
            else:
                for f in files_list:
                    result = upload_file_to_user_drive_folder(
                        request.user, f, subfolder_name=str(item.id)
                    )
                    uploaded_file_infos.append({
                        "name": result["name"],
                        "storage_url": result["webViewLink"],
                        "drive_folder_id": result["parent_folder_id"],
                        "size": getattr(f, "size", None),
                        "content_type": getattr(f, "content_type", "") or "",
                    })
        except Exception as e:
            logger.exception(f"Attachment save failed during text ingest: {e}")
            return JsonResponse(
                {"error": "upload_failed", "message": _("An unexpected error occurred")},
                status=500,
            )
    else:
        # No files: create entry directly
        try:
            item, metadata = ingest_text_entry(
                user=request.user,
                text=text,
                template_type=template_type,
                title=title,
                occurred_at=occurred_at,
            )
        except EmptyTextError as e:
            logger.warning(f"Empty text error: {e}")
            return JsonResponse(
                {"error": "empty_text", "message": str(e)},
                status=400,
            )
        except InvalidTemplateTypeError as e:
            logger.warning(f"Invalid template type: {e}")
            return JsonResponse(
                {"error": "invalid_template_type", "message": str(e)},
                status=400,
            )
        except TextInputError as e:
            logger.error(f"Text input error: {e}")
            return JsonResponse(
                {"error": "text_input_error", "message": str(e)},
                status=400,
            )
        except Exception as e:
            logger.exception(f"Unexpected error in text ingest: {e}")
            return JsonResponse(
                {"error": "server_error", "message": _("An unexpected error occurred")},
                status=500,
            )

    for info in uploaded_file_infos:
        ItemFile.objects.create(
            user=request.user,
            item=item,
            role=FileRole.ATTACHMENT,
            filename=info["name"],
            mime_type=info["content_type"] or "",
            storage_url=info["storage_url"],
            drive_folder_id=info.get("drive_folder_id", ""),
            bytes=info.get("size"),
        )

    response_data = {
        "id": str(item.id),
        "detected_language": metadata["detected_language"],
        "translated": metadata["translated"],
        "content_text": metadata.get("final_text", ""),
        "attachment_count": len(uploaded_file_infos),
    }
    return JsonResponse(response_data, status=201)
