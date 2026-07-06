"""
Ingest media (image/video/file) diary entries from the shell.

Designed for Telegram-side ingestion: when a message contains "Diary" + media,
download the file and run this command to create the entry with attachment(s).

Examples:

  # Photo-only diary entry (text auto-filled)
  python manage.py ingest_media --user-id 1 --files /tmp/photo.jpg

  # With caption text
  python manage.py ingest_media --user-id 1 --text "Fridge status" --files /tmp/fridge.jpg

  # Multiple files
  python manage.py ingest_media --user-id 1 --files /tmp/img.jpg /tmp/video.mp4

  # With custom timestamp
  python manage.py ingest_media --user-id 1 --files /tmp/photo.jpg --occurred-at "2026-07-01T17:00:00"
"""

import logging
import mimetypes
import shutil
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from src.common.config import get_config
from src.common.storage_local import (
    allocate_unique_attachment_filename,
    ensure_local_storage_tree,
    local_attachments_dir_for_item,
    sanitize_storage_filename,
)
from src.ingestion.models import FileRole, ItemFile
from src.quotas.services import check_token_quota
from src.text_input.services import (
    EmptyTextError,
    InvalidTemplateTypeError,
    TextInputError,
    ingest_text_entry,
)

User = get_user_model()
logger = logging.getLogger(__name__)

DEFAULT_MEDIA_TEXT = "Media diary entry"


class Command(BaseCommand):
    help = (
        "Create a diary entry with attached media files (images, videos, etc.). "
        "Designed for Telegram-side ingestion. Requires a Celery worker for classification."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=str,
            required=True,
            help="User UUID primary key.",
        )
        parser.add_argument(
            "--files",
            type=str,
            nargs="+",
            required=True,
            help="One or more file paths to attach (images, videos, etc.).",
        )
        parser.add_argument(
            "--text",
            type=str,
            default=None,
            help=(
                "Optional entry body. "
                f"Defaults to '{DEFAULT_MEDIA_TEXT}' if omitted."
            ),
        )
        parser.add_argument(
            "--template-type",
            type=str,
            default="plain",
            choices=("plain", "list"),
            help="Template: plain or list (default: plain).",
        )
        parser.add_argument(
            "--title",
            type=str,
            default="",
            help="Optional title.",
        )
        parser.add_argument(
            "--occurred-at",
            type=str,
            default=None,
            dest="occurred_at",
            help="Optional ISO 8601 datetime for when the entry occurred (default: now).",
        )

    def handle(self, *args, **options):
        # ── Resolve user ──────────────────────────────────────────────
        user_id = options["user_id"]
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with id {user_id!r}.") from exc

        # ── Check quota ───────────────────────────────────────────────
        allowed, _remaining, quota_info = check_token_quota(user)
        if not allowed:
            raise CommandError(
                "Daily token quota exceeded for this user. "
                f"Used {quota_info.get('used_tokens', 0)} / "
                f"{quota_info.get('limit_tokens', 0)}."
            )

        # ── Resolve text ──────────────────────────────────────────────
        text = options.get("text") or DEFAULT_MEDIA_TEXT

        # ── Resolve occurred_at ───────────────────────────────────────
        occurred_raw = options.get("occurred_at")
        if occurred_raw:
            occurred_at = parse_datetime(occurred_raw)
            if occurred_at is None:
                raise CommandError(
                    "Invalid --occurred-at; use ISO 8601 "
                    "(e.g. 2026-07-01T17:00:00)."
                )
            if timezone.is_naive(occurred_at):
                occurred_at = timezone.make_aware(
                    occurred_at, timezone.get_current_timezone()
                )
        else:
            occurred_at = timezone.now()

        # ── Validate file paths ───────────────────────────────────────
        file_paths: list[Path] = []
        for raw in options["files"]:
            fp = Path(raw).expanduser().resolve()
            if not fp.exists():
                raise CommandError(f"File not found: {fp}")
            if not fp.is_file():
                raise CommandError(f"Not a regular file: {fp}")
            file_paths.append(fp)

        # ── Create the entry (text ingest pipeline) ───────────────────
        try:
            item, metadata = ingest_text_entry(
                user=user,
                text=text,
                template_type=options["template_type"],
                title=options.get("title") or "",
                occurred_at=occurred_at,
            )
        except EmptyTextError as e:
            raise CommandError(str(e)) from e
        except InvalidTemplateTypeError as e:
            raise CommandError(str(e)) from e
        except TextInputError as e:
            raise CommandError(str(e)) from e

        # ── Save attached files to local storage ──────────────────────
        config = get_config()
        ensure_local_storage_tree(config)
        attach_dir = local_attachments_dir_for_item(config, user.id, item.id)
        used_names: set[str] = set()
        saved_count = 0

        for fp in file_paths:
            safe_base = sanitize_storage_filename(fp.name)
            safe_name = allocate_unique_attachment_filename(
                attach_dir, safe_base, used_names
            )
            dest = attach_dir / safe_name

            shutil.copy2(fp, dest)
            file_size = dest.stat().st_size

            mime_type, _ = mimetypes.guess_type(str(fp))
            if mime_type is None:
                mime_type = "application/octet-stream"

            ItemFile.objects.create(
                user=user,
                item=item,
                role=FileRole.ATTACHMENT,
                filename=safe_name,
                mime_type=mime_type,
                storage_url=str(dest.resolve()),
                bytes=file_size,
            )
            saved_count += 1
            logger.info(
                "Attached file: %s (%s, %d bytes)", safe_name, mime_type, file_size
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Created ingest item {item.id} "
                f"(language={metadata.get('detected_language')}, "
                f"translated={metadata.get('translated')}, "
                f"attachments={saved_count}). "
                "Classification queued; ensure a Celery worker is running."
            )
        )
