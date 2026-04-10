"""
Management command to re-index all diary entries for retrieval search.

Use after schema or indexing logic changes (e.g. embedding text, token index).
Queues index_entry_prep_task for each non-deleted IngestItem.

Run: python manage.py reindex_entries
"""

from django.core.management.base import BaseCommand

from src.ingestion.models import IngestItem
from src.retrieval.tasks import index_entry_prep_task


class Command(BaseCommand):
    help = "Re-index all diary entries for retrieval (queues index_entry_prep_task per item)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            help="Re-index only entries for this user ID (UUID)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show count of entries that would be re-indexed without queuing",
        )

    def handle(self, *args, **options):
        qs = IngestItem.objects.filter(is_deleted=False)
        if options.get("user"):
            qs = qs.filter(user_id=options["user"])
        count = qs.count()

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(f"Dry run: would re-index {count} {'entry' if count == 1 else 'entries'}")
            )
            return

        for item in qs.only("id").iterator():
            index_entry_prep_task.delay(str(item.id))

        self.stdout.write(
            self.style.SUCCESS(f"Queued re-index for {count} {'entry' if count == 1 else 'entries'}")
        )
