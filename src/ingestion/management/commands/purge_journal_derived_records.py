"""
purge_journal_derived_records — remove derived records produced by JOURNAL sessions.

Long recordings ("journal" talks) must never be classified.  Before the
journal/instruction gate existed they were, and this command removes what that
produced: to-do records, managed-list projections, calendar entries and financial
records whose source clip belongs to a journal session.

WHat it KEEPS:
  * ``ItemClassificationRun`` / selections — audit of what the old behaviour did
  * the entries themselves — nothing is deleted from the diary; only the derived
    records the classifier created are removed

Idempotent and safe to re-run.  ALWAYS run with ``--dry-run`` first and read the
numbers before applying.

Usage:
    python manage.py purge_journal_derived_records --dry-run
    python manage.py purge_journal_derived_records --dry-run --user-id 1
    python manage.py purge_journal_derived_records --user-id 1
"""

from collections import Counter

from django.core.management.base import BaseCommand

from src.ingestion.models import IngestItem
from src.ingestion.session_mode import JOURNAL, cap_groups_for, resolve_session_mode


class Command(BaseCommand):
    help = "Delete derived records (todos, lists, calendar, financial) created from journal recordings."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be removed; delete nothing.")
        parser.add_argument("--user-id", type=int, default=None, help="Restrict to a single user.")

    def handle(self, *args, **options):
        from src.batch_calendar.models import CalendarEvent
        from src.batch_calendar.services import (
            delete_batch_calendar_for_item,
            delete_calendar_events_for_item,
        )
        from src.financial_parser.services import delete_financial_records_for_item
        from src.list_parser.services import delete_list_records_for_item
        from src.managed_lists.models import ManagedListProjection, TodoRecord
        from src.managed_lists.services import delete_todo_records_for_item

        dry = options["dry_run"]
        qs = IngestItem.objects.filter(item_type="audio", is_deleted=False)
        if options["user_id"]:
            qs = qs.filter(user_id=options["user_id"])
        clips = list(qs.order_by("user_id", "occurred_at"))

        # Journal clips: grouped sessions containing a cap tranche (batch per user),
        # plus the legacy no-group fallback evaluated per clip.
        journal_clips = []
        by_user: dict = {}
        for clip in clips:
            by_user.setdefault(clip.user_id, []).append(clip)
        for user_id, user_clips in by_user.items():
            groups = {c.recording_group_id for c in user_clips if c.recording_group_id}
            journal_groups = cap_groups_for(user_id, groups)
            for clip in user_clips:
                if clip.recording_group_id:
                    if str(clip.recording_group_id) in journal_groups:
                        journal_clips.append(clip)
                elif resolve_session_mode(user_id, clip) == JOURNAL:
                    journal_clips.append(clip)

        counts = Counter()
        affected = 0
        for clip in journal_clips:
            todo_n = TodoRecord.objects.filter(source_item=clip).count()
            proj_n = ManagedListProjection.objects.filter(source_ingest_item=clip).count()
            cal_n = CalendarEvent.all_objects.filter(source_item=clip).count()
            if not (todo_n or proj_n or cal_n):
                continue
            affected += 1
            counts["todo_records"] += todo_n
            counts["managed_list_projections"] += proj_n
            counts["calendar_events"] += cal_n

            if not dry:
                delete_todo_records_for_item(clip)
                delete_list_records_for_item(clip)
                delete_financial_records_for_item(clip)
                delete_calendar_events_for_item(clip)
                delete_batch_calendar_for_item(clip)
                ManagedListProjection.objects.filter(source_ingest_item=clip).delete()

        self.stdout.write(f"journal clips scanned : {len(journal_clips)}")
        self.stdout.write(f"clips with derived rows: {affected}")
        self.stdout.write(f"to-do records           : {counts['todo_records']}")
        self.stdout.write(f"managed-list projections: {counts['managed_list_projections']}")
        self.stdout.write(f"calendar events         : {counts['calendar_events']}")
        if dry:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing was deleted."))
        else:
            self.stdout.write(self.style.SUCCESS("Purge complete (classification runs kept for audit)."))
