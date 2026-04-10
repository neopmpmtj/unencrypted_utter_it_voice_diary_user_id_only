"""
Invoice Parser Celery Tasks

Periodic task to process Gmail invoice PDFs for all users with Gmail connected.
"""

from celery import shared_task

from src.accounts.models import UserSecret
from src.common.logging_utils.logging_config import get_logger

from .pdf_parser.services import process_invoice_messages

logger = get_logger("invoice_parser")


@shared_task
def process_invoices_for_all_users_task():
    """
    Run invoice parsing for every user with Gmail permissions.
    Called by Celery Beat every 10 minutes.
    """
    user_secrets = UserSecret.objects.select_related("user").all()
    processed = 0
    errors = 0

    for user_secret in user_secrets:
        if not user_secret.has_gmail_permission():
            continue
        user = user_secret.user
        if not user or not user.is_active:
            continue
        try:
            result = process_invoice_messages(user)
            pdfs = result.get("summary", {}).get("pdfs_parsed", 0)
            created = result.get("summary", {}).get("ingest_items_created", pdfs)
            skipped = result.get("summary", {}).get("ingest_items_skipped", 0)
            if created:
                processed += created
                logger.info(
                    "Invoice parser: user %s parsed %d PDF(s), created %d IngestItem(s)",
                    user.email,
                    pdfs,
                    created,
                )
            elif pdfs:
                logger.warning(
                    "Invoice parser: user %s parsed %d PDF(s) but created 0 IngestItem(s)",
                    user.email,
                    pdfs,
                )
            if skipped:
                logger.warning(
                    "Invoice parser: user %s skipped persistence for %d parsed PDF(s)",
                    user.email,
                    skipped,
                )
            for err in result.get("errors", []):
                logger.warning("Invoice parser user %s: %s", user.email, err)
                errors += 1
        except Exception as exc:
            logger.exception("Invoice parser failed for user %s: %s", user.email, exc)
            errors += 1

    if processed or errors:
        logger.info(
            "Invoice parser run complete: %d PDFs parsed, %d errors",
            processed,
            errors,
        )
