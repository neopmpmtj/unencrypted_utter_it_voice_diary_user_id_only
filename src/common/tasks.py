"""
Background tasks for operations that must not block the web process.
"""

import logging
import shutil
from pathlib import Path

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def upload_attachments_to_drive_task(
    self, item_id: str, user_id: int, attachment_infos: list
):
    """
    Upload locally-saved attachment files to Google Drive and update ItemFile
    records.  Called from the upload_audio view so that Drive I/O never blocks
    a Daphne request thread.  ItemFile records are created in the view; this
    task updates storage_url and drive_folder_id when Drive upload completes.

    Args:
        item_id: UUID of the IngestItem these attachments belong to.
        user_id: PK of the uploading user.
        attachment_infos: list of dicts, each with keys
            ``itemfile_id``, ``local_path``, ``filename``, ``mime_type``, ``size``.
    """
    from src.accounts.models import CustomUser
    from src.common.drive_upload import upload_local_file_to_user_drive_folder
    from src.common.google_account.auth import GoogleAuthError
    from src.ingestion.models import IngestItem, ItemFile

    try:
        user = CustomUser.objects.get(id=user_id)
        item = IngestItem.objects.get(id=item_id)
    except Exception as e:
        logger.error(f"Drive upload task setup failed: {e}")
        return

    uploaded_count = 0
    for info in attachment_infos:
        itemfile_id = info.get("itemfile_id")
        if not itemfile_id:
            logger.warning("Attachment info missing itemfile_id, skipping")
            continue
        local_path = Path(info["local_path"])
        if not local_path.exists():
            logger.warning(f"Attachment file missing, skipping: {local_path}")
            continue

        try:
            result = upload_local_file_to_user_drive_folder(
                user,
                str(local_path),
                info["filename"],
                info["mime_type"],
                subfolder_name=str(item.id),
            )
            ItemFile.objects.filter(id=itemfile_id).update(
                filename=result.get("name", info["filename"]),
                storage_url=result.get("webViewLink", ""),
                drive_folder_id=result.get("parent_folder_id", ""),
            )
            uploaded_count += 1
            local_path.unlink(missing_ok=True)
            logger.info(f"Uploaded attachment to Drive for item {item_id}: {info['filename']}")
        except GoogleAuthError as e:
            logger.warning(f"Drive auth failed for attachment upload: {e}")
        except Exception as e:
            logger.error(f"Failed to upload attachment {info['filename']} for item {item_id}: {e}")

    # Clean up the temp attachment directory if it is now empty
    if attachment_infos:
        first_path = Path(attachment_infos[0]["local_path"])
        attach_dir = first_path.parent
        if attach_dir.exists() and not any(attach_dir.iterdir()):
            shutil.rmtree(attach_dir, ignore_errors=True)

    logger.info(f"Drive attachment upload complete for item {item_id}: {uploaded_count}/{len(attachment_infos)} files")
