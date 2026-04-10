"""
Google Drive upload helpers: get-or-create folder by path and upload file into user's folder.
Used by text-entry attachments and standalone "Save file to Google Drive" (Voice section).
"""

import logging
import re
from typing import Optional, Dict, Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from django.utils.translation import gettext as _

from src.common.google_account.auth import (
    get_authenticated_service,
    verify_drive_permissions,
    GoogleAuthError,
)

logger = logging.getLogger(__name__)

DEFAULT_FOLDER_PATH = "VoiceDiaryFiles/attachments"


def _sanitize_drive_filename(name: str) -> str:
    """Keep only safe characters for Drive file name; fallback if empty."""
    if not name or not name.strip():
        return "uploaded_file"
    safe = re.sub(r"[^\w.\- ]", "", name.strip())
    if not safe:
        return "uploaded_file"
    if len(safe) <= 200:
        return safe
    # Preserve extension when truncating long names
    dot_pos = safe.rfind('.')
    if dot_pos > 0:
        ext = safe[dot_pos:]  # includes the dot
        stem = safe[:dot_pos]
        max_stem = 200 - len(ext)
        return stem[:max_stem] + ext
    return safe[:200]


def get_or_create_folder_by_path(drive, folder_path: str) -> str:
    """
    Get or create nested folders by path (e.g. VoiceDiaryFiles/attachments).
    Returns the leaf folder ID.
    """
    segments = [s.strip() for s in folder_path.split("/") if s.strip()]
    if not segments:
        return "root"
    parent_id = "root"
    for segment in segments:
        if not segment:
            continue
        escaped_name = segment.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"'{parent_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"name='{escaped_name}' and "
            "trashed = false"
        )
        result = drive.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        files = result.get("files", [])
        if files:
            parent_id = files[0]["id"]
        else:
            body = {
                "name": segment,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder = drive.files().create(body=body, fields="id").execute()
            parent_id = folder["id"]
    return parent_id


def upload_file_to_drive(
    drive,
    uploaded_file,
    parent_folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload a Django UploadedFile to Drive. If parent_folder_id is set, upload into that folder.
    Returns dict with id, name, webViewLink, parent_folder_id.
    """
    name = _sanitize_drive_filename(getattr(uploaded_file, "name", "") or "")
    mime_type = getattr(uploaded_file, "content_type", None) or "application/octet-stream"
    if name.endswith(".txt") and mime_type == "application/octet-stream":
        mime_type = "text/plain"
    uploaded_file.seek(0)
    media = MediaIoBaseUpload(uploaded_file, mimetype=mime_type, resumable=True)
    metadata = {"name": name, "mimeType": mime_type}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]
    file_obj = (
        drive.files()
        .create(body=metadata, media_body=media, fields="id,name,webViewLink")
        .execute()
    )
    return {
        "id": file_obj.get("id"),
        "name": file_obj.get("name", name),
        "webViewLink": file_obj.get("webViewLink", ""),
        "parent_folder_id": parent_folder_id or "",
    }


def upload_file_to_user_drive_folder(
    user, uploaded_file, subfolder_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Upload a file to the user's configured Drive folder (from UserPreferences).
    If subfolder_name is provided (e.g., entry ID), the file is uploaded to:
        <configured_folder>/<subfolder_name>/
    Resolves or creates the folder by path, saves folder_id to preferences if newly resolved.
    Returns dict with id, name, webViewLink, parent_folder_id.
    Raises GoogleAuthError if Drive not connected or auth fails.
    """
    from src.accounts.models import UserPreferences

    if not verify_drive_permissions(user):
        raise GoogleAuthError(_("Connect Google account with Drive access to save files."))
    drive = get_authenticated_service(user, "drive")
    prefs, created = UserPreferences.objects.get_or_create(user=user)
    folder_path = (prefs.drive_attachment_folder_name or "").strip() or DEFAULT_FOLDER_PATH
    folder_id = prefs.drive_attachment_folder_id
    if not folder_id:
        folder_id = get_or_create_folder_by_path(drive, folder_path)
        prefs.drive_attachment_folder_id = folder_id
        prefs.save(update_fields=["drive_attachment_folder_id"])
    
    # If subfolder_name provided (e.g., entry ID), create nested folder and upload there
    if subfolder_name:
        upload_folder_id = get_or_create_folder_by_path(
            drive, f"{folder_path}/{subfolder_name}"
        )
    else:
        upload_folder_id = folder_id
    
    result = upload_file_to_drive(drive, uploaded_file, parent_folder_id=upload_folder_id)
    return result


def upload_local_file_to_user_drive_folder(
    user, local_path: str, filename: str, mime_type: str,
    subfolder_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload a file from the local filesystem to the user's Drive folder.

    Same as upload_file_to_user_drive_folder but reads from a path instead of
    a Django UploadedFile.  Designed for use from Celery tasks where the
    original request object is no longer available.
    """
    from src.accounts.models import UserPreferences

    if not verify_drive_permissions(user):
        raise GoogleAuthError(_("Connect Google account with Drive access to save files."))

    drive = get_authenticated_service(user, "drive")
    prefs, created = UserPreferences.objects.get_or_create(user=user)
    folder_path = (prefs.drive_attachment_folder_name or "").strip() or DEFAULT_FOLDER_PATH
    folder_id = prefs.drive_attachment_folder_id
    if not folder_id:
        folder_id = get_or_create_folder_by_path(drive, folder_path)
        prefs.drive_attachment_folder_id = folder_id
        prefs.save(update_fields=["drive_attachment_folder_id"])

    if subfolder_name:
        upload_folder_id = get_or_create_folder_by_path(
            drive, f"{folder_path}/{subfolder_name}"
        )
    else:
        upload_folder_id = folder_id

    name = _sanitize_drive_filename(filename)
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    metadata = {"name": name, "mimeType": mime_type}
    if upload_folder_id:
        metadata["parents"] = [upload_folder_id]

    file_obj = (
        drive.files()
        .create(body=metadata, media_body=media, fields="id,name,webViewLink")
        .execute()
    )
    return {
        "id": file_obj.get("id"),
        "name": file_obj.get("name", name),
        "webViewLink": file_obj.get("webViewLink", ""),
        "parent_folder_id": upload_folder_id or "",
    }
