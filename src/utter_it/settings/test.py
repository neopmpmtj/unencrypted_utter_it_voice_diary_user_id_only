"""Test settings: English locale, eager Celery tasks."""
from .dev import *  # noqa: F401, F403

LANGUAGE_CODE = 'en'

# Run Celery tasks synchronously so upload_attachments_to_drive_task runs in-process
CELERY_TASK_ALWAYS_EAGER = True
