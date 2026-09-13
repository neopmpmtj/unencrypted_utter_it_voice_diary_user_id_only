"""
purge_chat_entry_records — soft-delete chat-experiment entries and everything derived from them.

The Telegram ``voice-diary-logger`` Gateway hook (disabled 2026-09-13) piped every
message from Pedro's chat with Neo into the Voice Diary as a text entry titled
"Pedro Julio to Neo" (originally "Message from Pedro Julio").  Those entries were
classified like instructions, which produced to-do records and managed-list
projections.  This command removes the entries *and* everything derived from
them, using the same soft-delete path as deleting a single entry in the entries
UI (``src.entries.views._soft_delete_item_and_cleanup``): items, to-do records,
triage/classification rows and entity links are soft-deleted (recoverable);
managed-list and retrieval projections are removed (they are rebuilt from the
source records by the usual backfill commands if ever needed).

Idempotent — safe to re-run.  ALWAYS run ``--dry-run`` first and read the numbers.

Usage:
    python manage.py purge_chat_entry_records --dry-run
    python manage.py purge_chat_entry_records --dry-run --user-id 1
    python manage.py purge_chat_entry_records
    python manage.py purge_chat_entry_records --title-prefix "Some other title"
"""

from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Q

from src.ingestion.models import IngestItem

DEFAULT_TITLE_PREFIXES = ("Pedro Julio to Neo", "Message from Pedro Julio")


class Command(BaseCommand):
    help = "Soft-delete chat-experiment entries (by title prefix) and their derived records."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be removed; delete nothing.")
        parser.add_argument("--user-id", type=int, default=None, help="Restrict to a single user.")
        parser.add_argument(
            "--title-prefix",
            action="append",
            dest="title_prefixes",
            default=None,
            metavar="PREFIX",
            help="Title prefix to match (repeatable). Defaults to the two known chat titles: "
            + ", ".join(DEFAULT_TITLE_PREFIXES),
        )

    def handle(self, *args, **options):
        from src.batch_calendar.models import BatchCalendarRequest, CalendarEvent
        from src.classification.models import ItemClassificationRun
        from src.entries.views import _soft_delete_item_and_cleanup
        from src.financial_parser.models import FinancialRecord
        from src.ingestion.models import ItemFile
        from src.managed_lists.models import ManagedListProjection, TodoRecord
        from src.retrieval.models import ItemRetrievalProjection

        prefixes = options["title_prefixes"] or list(DEFAULT_TITLE_PREFIXES)
        dry = options["dry_run"]

        q = Q()
        for prefix in prefixes:
            q |= Q(title__startswith=prefix)
        qs = IngestItem.objects.filter(is_deleted=False).filter(q)
        if options["user_id"]:
            qs = qs.filter(user_id=options["user_id"])
        items = list(qs.order_by("user_id", "occurred_at"))
        ids = [item.id for item in items]

        if not items:
            self.stdout.write("no matching entries found — nothing to do")
            return

        # ---- what exists now (this is also the dry-run report) ----------------
        todo_qs = TodoRecord.all_objects.filter(source_item_id__in=ids, is_deleted=False)
        proj_qs = ManagedListProjection.objects.filter(source_ingest_item_id__in=ids)
        cal_qs = CalendarEvent.all_objects.filter(source_item_id__in=ids, is_deleted=False)
        fin_qs = FinancialRecord.all_objects.filter(source_item_id__in=ids, is_deleted=False)
        batch_qs = BatchCalendarRequest.all_objects.filter(ingest_item_id__in=ids)
        run_qs = ItemClassificationRun.all_objects.filter(ingest_item_id__in=ids, is_deleted=False)
        retr_qs = ItemRetrievalProjection.objects.filter(ingest_item_id__in=ids)
        file_qs = ItemFile.objects.filter(item_id__in=ids)

        derived_ids = set(todo_qs.values_list("source_item_id", flat=True))
        derived_ids |= set(proj_qs.values_list("source_ingest_item_id", flat=True))
        derived_ids |= set(cal_qs.values_list("source_item_id", flat=True))
        derived_ids |= set(fin_qs.values_list("source_item_id", flat=True))

        self.stdout.write(f"chat entries matched       : {len(items)}")
        self.stdout.write(f"entries with derived rows  : {sum(1 for i in ids if i in derived_ids)}")
        self.stdout.write(f"to-do records              : {todo_qs.count()}")
        self.stdout.write(f"managed-list projections   : {proj_qs.count()}")
        self.stdout.write(f"calendar events            : {cal_qs.count()}")
        self.stdout.write(f"financial records          : {fin_qs.count()}")
        self.stdout.write(f"batch calendar requests    : {batch_qs.count()}")
        self.stdout.write(f"classification runs        : {run_qs.count()}")
        self.stdout.write(f"retrieval projections      : {retr_qs.count()}")
        self.stdout.write(f"attachments (kept on disk) : {file_qs.count()}")

        if dry:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing was deleted."))
            return

        # ---- apply -------------------------------------------------------------
        counts = Counter()
        for idx, item in enumerate(items, 1):
            try:
                _soft_delete_item_and_cleanup(item)
                counts["deleted"] += 1
            except Exception as exc:  # keep going; report at the end
                counts["errors"] += 1
                self.stderr.write(f"ERROR deleting {item.id}: {exc}")
            if idx % 100 == 0:
                self.stdout.write(f"  ... {idx}/{len(items)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"soft-deleted {counts['deleted']} entr(ies) (errors: {counts['errors']}); "
                "re-run with --dry-run to confirm zero."
            )
        )
