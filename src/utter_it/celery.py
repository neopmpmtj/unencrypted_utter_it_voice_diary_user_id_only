import os

from celery import Celery
from celery.schedules import crontab
from kombu import Queue
from decouple import config

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    config("DJANGO_SETTINGS_MODULE", default="src.utter_it.settings.dev"),
)

app = Celery("utter_it")

# Reads CELERY_* settings from Django settings.py
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in installed apps
app.autodiscover_tasks()

# Queue definitions for the retrieval indexing pipeline.
# "celery" is Celery's built-in default queue; all tasks without explicit
# routing go there. index_prep/index_process are dedicated retrieval queues.
app.conf.task_queues = [
    Queue("celery"),
    Queue("index_prep"),
    Queue("index_process"),
]

app.conf.task_routes = {
    "src.retrieval.tasks.index_entry_prep_task": {"queue": "index_prep"},
    "src.retrieval.tasks.index_entry_process_task": {"queue": "index_process"},
}

# Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    'cleanup-expired-audio': {
        'task': 'src.ingestion.tasks.cleanup_expired_audio_files',
        'schedule': crontab(minute=0),  # Run every hour, on the hour
    },
    'cleanup-expired-pending-transcriptions': {
        'task': 'src.ingestion.tasks.cleanup_expired_pending_transcriptions',
        'schedule': crontab(minute='*/5'),  # Run every 5 minutes
    },
    'delete-expired-accounts': {
        'task': 'src.accounts.tasks.delete_expired_accounts_task',
        'schedule': crontab(hour=4, minute=0),  # Run at 4 AM daily
    },
    'renew-calendar-watches': {
        'task': 'src.batch_calendar.tasks.renew_calendar_watches_task',
        'schedule': crontab(minute=0, hour='*/6'),  # Every 6 hours
    },
    'poll-calendar-changes': {
        'task': 'src.batch_calendar.tasks.poll_calendar_changes_task',
        'schedule': crontab(minute='*/3'),
    },
    'process-invoices-for-all-users': {
        'task': 'src.invoice_parser.tasks.process_invoices_for_all_users_task',
        'schedule': crontab(minute='*/10'),
    },
}
