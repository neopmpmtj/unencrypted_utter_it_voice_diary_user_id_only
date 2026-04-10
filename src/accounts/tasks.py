"""
Accounts Celery Tasks
"""

import logging
from io import StringIO

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger(__name__)


@shared_task
def delete_expired_accounts_task():
    """
    Permanently delete users whose deletion_requested_at is older than retention_days.
    Runs daily via Celery beat. Uses GlobalSettings accounts.deletion_retention_days.
    """
    out = StringIO()
    try:
        call_command('delete_expired_accounts', stdout=out)
        logger.info(f"delete_expired_accounts_task: {out.getvalue().strip()}")
    except Exception as e:
        logger.exception(f"delete_expired_accounts_task failed: {e}")
