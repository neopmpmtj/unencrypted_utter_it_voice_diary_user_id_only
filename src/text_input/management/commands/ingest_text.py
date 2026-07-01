"""
Ingest a text diary entry from the shell (same service path as /text-input/ingest/).

Requires a running Celery worker so classification and downstream pipeline tasks execute.

Examples:

  python manage.py ingest_text --email user@example.com --text "Meeting notes"
  echo "Longer entry" | python manage.py ingest_text --email user@example.com
  python manage.py ingest_text --user-id <uuid> --template-type list --title "Groceries" --text "milk, eggs"
"""

import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from src.quotas.services import check_token_quota
from src.text_input.services import (
    EmptyTextError,
    InvalidTemplateTypeError,
    TextInputError,
    ingest_text_entry,
)

User = get_user_model()


def _resolve_occurred_at(raw: str) -> timezone.datetime:
    dt = parse_datetime(raw)
    if dt is None:
        raise CommandError(
            "Invalid --occurred-at; use an ISO 8601 datetime (e.g. 2026-04-19T14:30:00)."
        )
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _resolve_user(*, email: str | None, user_id: str | None):
    if email and user_id:
        raise CommandError("Specify only one of --email or --user-id.")
    if not email and not user_id:
        raise CommandError("Provide --email or --user-id.")

    if email:
        try:
            return User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {email!r}.") from exc

    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist as exc:
        raise CommandError(f"No user with id {user_id!r}.") from exc


def _resolve_body(options: dict) -> str:
    text = options.get("text")
    if text is not None:
        return text
    if sys.stdin.isatty():
        raise CommandError(
            "Provide --text '...' or pipe the entry body on stdin (non-interactive)."
        )
    return sys.stdin.read()


class Command(BaseCommand):
    help = (
        "Create a text IngestItem via ingest_text_entry (language detect, optional "
        "translation, queue classification). Requires Celery worker for the async pipeline."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default=None,
            help="Account email (same as login). Mutually exclusive with --user-id.",
        )
        parser.add_argument(
            "--user-id",
            type=str,
            default=None,
            dest="user_id",
            help="User UUID primary key. Mutually exclusive with --email.",
        )
        parser.add_argument(
            "--text",
            type=str,
            default=None,
            help="Entry body. If omitted, body is read from stdin.",
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
        user = _resolve_user(
            email=options.get("email"),
            user_id=options.get("user_id"),
        )

        allowed, _remaining, quota_info = check_token_quota(user)
        if not allowed:
            raise CommandError(
                "Daily token quota exceeded for this user. "
                f"Used {quota_info.get('used_tokens', 0)} / "
                f"{quota_info.get('limit_tokens', 0)}."
            )

        body = _resolve_body(options)
        occurred_raw = options.get("occurred_at")
        occurred_at = (
            _resolve_occurred_at(occurred_raw) if occurred_raw else timezone.now()
        )

        try:
            item, metadata = ingest_text_entry(
                user=user,
                text=body,
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

        self.stdout.write(
            self.style.SUCCESS(
                f"Created ingest item {item.id} "
                f"(language={metadata.get('detected_language')}, "
                f"translated={metadata.get('translated')}). "
                "Classification queued; ensure a Celery worker is running."
            )
        )
