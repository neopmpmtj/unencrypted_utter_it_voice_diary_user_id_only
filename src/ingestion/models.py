import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Provider(models.TextChoices):
    GMAIL = "gmail", _("Gmail")
    GDRIVE = "gdrive", _("Google Drive")
    FILESYSTEM = "filesystem", _("Filesystem")
    MANUAL = "manual", _("Manual")
    OTHER = "other", _("Other")


class ItemType(models.TextChoices):
    AUDIO = "audio", _("Audio")
    TEXT = "text", _("Text")
    EMAIL = "email", _("Email")
    FILE = "file", _("File")
    OTHER = "other", _("Other")


class IngestStatus(models.TextChoices):
    NEW = "new", _("New")
    PROCESSED = "processed", _("Processed")
    ERROR = "error", _("Error")
    TAGGED = "tagged", _("Tagged")


class TemplateType(models.TextChoices):
    PLAIN = "plain", _("Plain")
    LIST = "list", _("List")


class IngestRun(models.Model):
    """
    A batch or unit of ingestion work (e.g., 'import today’s recordings').
    Facts-only: not pipeline config.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ingest_runs")

    started_at = models.DateTimeField(default=timezone.now, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True)

    note = models.CharField(max_length=255, blank=True, default="")

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.started_at.isoformat()}"


class IngestItem(models.Model):
    """
    The backbone fact table: one row per 'thing' you ingested (utterance, email, file, etc).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ingest_items"
    )

    ingest_run = models.ForeignKey(IngestRun, on_delete=models.SET_NULL, null=True, blank=True)

    parent_item = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_items",
        help_text="Set when this entry was created as an edited copy of another",
    )

    split_parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="split_children",
        help_text="Set when this entry was created by splitting a mixed utterance",
    )

    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.MANUAL)
    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.OTHER)
    template_type = models.CharField(max_length=20, choices=TemplateType.choices, default=TemplateType.PLAIN)
    status = models.CharField(max_length=20, choices=IngestStatus.choices, default=IngestStatus.NEW)

    # External identifiers (for dedupe)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    external_thread_id = models.CharField(max_length=255, null=True, blank=True)
    source_filename = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Original filename when item originates from attachment (e.g. Gmail PDF)",
    )

    # When it happened vs when we ingested it
    occurred_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(default=timezone.now, editable=False)

    title = models.CharField(max_length=255, blank=True, default="")
    content_text = models.TextField(blank=True, default="")      # transcript or extracted text (plaintext)
    summary_text = models.TextField(blank=True, default="")      # later step; still a "fact" output (plaintext)

    # Soft delete (fact lifecycle)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Audio metadata (preserved after audio deletion)
    audio_duration_seconds = models.FloatField(
        null=True, blank=True,
        help_text="Original audio duration in seconds"
    )
    audio_format = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Original audio format (webm, wav, mp3)"
    )
    original_file_size = models.BigIntegerField(
        null=True, blank=True,
        help_text="Original file size in bytes"
    )
    detected_language = models.CharField(
        max_length=10, blank=True, default="",
        help_text="Detected language ISO code"
    )

    # Retention tracking
    audio_deletion_scheduled_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the audio file is scheduled for deletion"
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "item_type"]),
            models.Index(fields=["audio_deletion_scheduled_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_user_provider_external_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.item_type} {self.id}"


class FileRole(models.TextChoices):
    ORIGINAL = "original", _("Original")
    ATTACHMENT = "attachment", _("Attachment")
    TRANSCRIPT = "transcript", _("Transcript")
    PROCESSED = "processed", _("Processed")
    THUMBNAIL = "thumbnail", _("Thumbnail")
    EXPORT = "export", _("Export")
    OTHER = "other", _("Other")


class ItemFile(models.Model):
    """
    A file linked to an ingest item (audio file, attachment, transcript export, etc).
    Store a path/URL (e.g., GDrive URL) rather than blob data.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="item_files")

    item = models.ForeignKey(IngestItem, on_delete=models.CASCADE, related_name="files")

    role = models.CharField(max_length=20, choices=FileRole.choices, default=FileRole.ORIGINAL)
    filename = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=120, blank=True, default="")

    # Where the file lives (GDrive URL, local path, S3 URL, etc.)
    storage_url = models.TextField()

    bytes = models.BigIntegerField(null=True, blank=True)

    # Google Drive folder ID (for files organized in UUID-based subfolders)
    # Used to enable future syncing of folder deletions from Google Drive
    drive_folder_id = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=["user", "role"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.role} {self.filename}"


class IngestItemEditLog(models.Model):
    """Minimal audit log for entry edits (overwrite mode)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(IngestItem, on_delete=models.CASCADE, related_name="edit_logs")
    edited_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        related_name="ingest_item_edit_logs",
    )
    edited_at = models.DateTimeField(auto_now_add=True)
    fields_changed = models.JSONField(default=list)  # e.g. ["content_text", "title", "tags"]

    class Meta:
        db_table = "ingestion_ingestitemeditlog"
        indexes = [models.Index(fields=["item", "edited_at"])]


class JobType(models.TextChoices):
    FETCH_GMAIL = "fetch_gmail", _("Fetch Gmail message")
    PROCESS_AUDIO = "process_audio", _("Process audio metadata")
    CLASSIFY_ITEM = "classify_item", _("Classify item with LLM")
    PARSE_CALENDAR = "parse_calendar", _("Parse calendar event")
    PARSE_LIST = "parse_list", _("Parse item list")
    PARSE_FINANCIAL = "parse_financial", _("Parse financial entries")
    PARSE_TODO = "parse_todo", _("Parse to-do items")


class JobStatus(models.TextChoices):
    QUEUED = "queued", _("Queued")
    RUNNING = "running", _("Running")
    DONE = "done", _("Done")
    ERROR = "error", _("Error")


class IngestJob(models.Model):
    """
    Tracks async ingestion work. This is NOT pipeline config.
    It's just: "did we fetch the raw stuff / attach files / extract email body?"
    
    For audio processing, also tracks pipeline checkpoints for resume capability.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ingest_jobs")
    item = models.ForeignKey("ingestion.IngestItem", on_delete=models.CASCADE, related_name="jobs")

    job_type = models.CharField(max_length=40, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=JobStatus.choices, default=JobStatus.QUEUED)

    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    queued_at = models.DateTimeField(default=timezone.now, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Pipeline checkpoint tracking (for resume-from-checkpoint retry logic)
    checkpoint = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Current/last completed pipeline step"
    )
    checkpoint_data = models.JSONField(
        default=dict, blank=True,
        help_text="Checkpoint state data for resume (e.g., transcription text, chunk paths)"
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "job_type"]),
            models.Index(fields=["user", "queued_at"]),
        ]


class GmailRawMessage(models.Model):
    """
    Stores a raw snapshot of a Gmail message fetch.
    (A for Gmail: yes, store raw payload for debugging / reprocessing.)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gmail_raw_messages")
    item = models.OneToOneField("ingestion.IngestItem", on_delete=models.CASCADE, related_name="gmail_raw")

    payload_json = models.JSONField()

    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]
