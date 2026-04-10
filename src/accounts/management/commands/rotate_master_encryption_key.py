"""
Management command to rotate MASTER_ENCRYPTION_KEY (Google OAuth tokens on UserSecret only).

After running, update MASTER_ENCRYPTION_KEY in .env and restart Daphne and Celery.

Usage:
  python manage.py rotate_master_encryption_key --old-key OLD --new-key NEW [--dry-run] [--async]
"""

import os
from django.core.management.base import BaseCommand
from decouple import config, UndefinedValueError


def _validate_fernet_key(key: str) -> bool:
    from cryptography.fernet import Fernet
    try:
        kb = key.encode("utf-8") if isinstance(key, str) else key
        Fernet(kb)
        return True
    except Exception:
        return False


class Command(BaseCommand):
    help = (
        "Rotate MASTER_ENCRYPTION_KEY: re-encrypt OAuth token fields on UserSecret only. "
        "Use --dry-run to count affected records only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--old-key",
            type=str,
            default=None,
            help="Current (compromised) master key. Default: OLD_MASTER_ENCRYPTION_KEY env.",
        )
        parser.add_argument(
            "--new-key",
            type=str,
            default=None,
            help="New master key to rotate to. Default: NEW_MASTER_ENCRYPTION_KEY env.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count affected records only, do not re-encrypt.",
        )
        parser.add_argument(
            "--async",
            dest="async_mode",
            action="store_true",
            help="Enqueue Celery task instead of running inline.",
        )

    def handle(self, *args, **options):
        old_key = options.get("old_key")
        new_key = options.get("new_key")
        if not old_key:
            try:
                old_key = config("OLD_MASTER_ENCRYPTION_KEY")
            except UndefinedValueError:
                self.stderr.write(
                    self.style.ERROR(
                        "Provide --old-key or set OLD_MASTER_ENCRYPTION_KEY in env."
                    )
                )
                return 1
        if not new_key:
            try:
                new_key = config("NEW_MASTER_ENCRYPTION_KEY")
            except UndefinedValueError:
                self.stderr.write(
                    self.style.ERROR(
                        "Provide --new-key or set NEW_MASTER_ENCRYPTION_KEY in env."
                    )
                )
                return 1

        if not _validate_fernet_key(old_key):
            self.stderr.write(self.style.ERROR("--old-key is not a valid Fernet key."))
            return 1
        if not _validate_fernet_key(new_key):
            self.stderr.write(self.style.ERROR("--new-key is not a valid Fernet key."))
            return 1
        if old_key == new_key:
            self.stderr.write(
                self.style.ERROR("Old and new keys must differ.")
            )
            return 1

        if options.get("dry_run"):
            self._dry_run()
            return 0

        if options.get("async_mode"):
            from src.common.encryption_tasks import rotate_master_encryption_key
            t = rotate_master_encryption_key.delay(old_key, new_key)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enqueued rotate_master_encryption_key task: {t.id}. "
                    "Monitor Celery logs. After completion, update MASTER_ENCRYPTION_KEY and restart."
                )
            )
            return 0

        from src.common.encryption_tasks import rotate_master_encryption_key

        rotate_master_encryption_key(old_key, new_key)
        self.stdout.write(
            self.style.SUCCESS(
                "Rotation complete. Update MASTER_ENCRYPTION_KEY in .env and restart Daphne and Celery."
            )
        )
        return 0

    def _dry_run(self):
        from django.db.models import Q

        from src.accounts.models import UserSecret

        us_count = UserSecret.objects.filter(
            Q(encrypted_google_access_token__isnull=False)
            | Q(encrypted_google_refresh_token__isnull=False)
            | Q(encrypted_google_token_expiry__isnull=False)
        ).exclude(
            encrypted_google_access_token="",
            encrypted_google_refresh_token="",
            encrypted_google_token_expiry="",
        ).count()

        self.stdout.write(
            self.style.WARNING("Dry run: would re-encrypt OAuth fields on UserSecret rows:")
        )
        self.stdout.write(f"  UserSecret rows with token data: {us_count}")
        self.stdout.write(
            self.style.WARNING(
                "Run without --dry-run to perform rotation."
            )
        )
