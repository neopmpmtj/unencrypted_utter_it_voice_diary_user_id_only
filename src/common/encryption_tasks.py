"""
Master encryption key rotation task (OAuth tokens on UserSecret only).

Re-wraps encrypted_google_* fields when MASTER_ENCRYPTION_KEY is rotated.
"""

import logging

from celery import shared_task
from django.db import transaction

from src.common.utils.encryption import decrypt_value_with_master, encrypt_value_with_master

logger = logging.getLogger(__name__)


def _reencrypt_field(value: str, old_key: str, new_key: str):
    if not value or not str(value).strip():
        return None
    plain = decrypt_value_with_master(value, old_key)
    if plain is None:
        return None
    return encrypt_value_with_master(plain, new_key)


@shared_task(bind=True, max_retries=0)
def rotate_master_encryption_key(self, old_master_key: str, new_master_key: str):
    """
    Re-encrypt Google OAuth token fields from old Fernet key to new Fernet key.

    After completion, set MASTER_ENCRYPTION_KEY to the new key in the environment
    and restart application processes.
    """
    from src.accounts.models import UserSecret

    items_processed = 0
    for secret in UserSecret.objects.iterator():
        with transaction.atomic():
            locked = UserSecret.objects.select_for_update().get(pk=secret.pk)
            updated = False
            for field in (
                "encrypted_google_access_token",
                "encrypted_google_refresh_token",
                "encrypted_google_token_expiry",
            ):
                val = getattr(locked, field)
                if not val:
                    continue
                new_val = _reencrypt_field(val, old_master_key, new_master_key)
                if new_val is not None:
                    setattr(locked, field, new_val)
                    updated = True
            if updated:
                locked.save()
                items_processed += 1

    logger.info(
        "rotate_master_encryption_key (OAuth only) completed. Updated %s UserSecret row(s).",
        items_processed,
    )
