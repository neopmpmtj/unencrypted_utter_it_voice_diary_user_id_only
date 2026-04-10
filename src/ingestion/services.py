from .models import (
    IngestItem,
    IngestJob,
    ItemFile,
    JobType,
    IngestStatus,
)
from .tasks import fetch_gmail_ingest, process_audio_ingest


def create_audio_entry(*, user, storage_url, title="", occurred_at=None):
    item = IngestItem.objects.create(
        user=user,
        item_type="audio",
        title=title,
        occurred_at=occurred_at,
        status=IngestStatus.NEW,
    )

    ItemFile.objects.create(
        user=user,
        item=item,
        role="original",
        storage_url=storage_url,
    )

    job = IngestJob.objects.create(
        user=user,
        item=item,
        job_type=JobType.PROCESS_AUDIO,
    )

    process_audio_ingest.delay(str(job.id))
    return item


def create_gmail_entry(*, user, gmail_message_id):
    item = IngestItem.objects.create(
        user=user,
        provider="gmail",
        item_type="email",
        external_id=gmail_message_id,
        status=IngestStatus.NEW,
    )

    job = IngestJob.objects.create(
        user=user,
        item=item,
        job_type=JobType.FETCH_GMAIL,
    )

    fetch_gmail_ingest.delay(str(job.id))
    return item
