"""
Management command to backfill ManagedListProjection from existing parser records.

Usage:
    python manage.py backfill_managed_list_projections
    python manage.py backfill_managed_list_projections --list-only
    python manage.py backfill_managed_list_projections --financial-only
    python manage.py backfill_managed_list_projections --todo-only
"""

from django.core.management.base import BaseCommand

from src.managed_lists.models import ManagedListProjection
from src.managed_lists.projections import (
    refresh_projection_for_financial_record,
    refresh_projection_for_list_record,
    refresh_projection_for_todo_record,
)


class Command(BaseCommand):
    help = "Backfill ManagedListProjection from existing ListRecord, FinancialRecord, and TodoRecord rows."

    def add_arguments(self, parser):
        parser.add_argument("--list-only", action="store_true", help="Only backfill ListRecord projections")
        parser.add_argument("--financial-only", action="store_true", help="Only backfill FinancialRecord projections")
        parser.add_argument("--todo-only", action="store_true", help="Only backfill TodoRecord projections")

    def handle(self, *args, **options):
        do_all = not (options["list_only"] or options["financial_only"] or options["todo_only"])

        total = 0

        if do_all or options["list_only"]:
            total += self._backfill_list_records()

        if do_all or options["financial_only"]:
            total += self._backfill_financial_records()

        if do_all or options["todo_only"]:
            total += self._backfill_todo_records()

        self.stdout.write(self.style.SUCCESS(f"Backfill complete. {total} projection rows created."))

    def _backfill_list_records(self) -> int:
        from src.list_parser.models import ListRecord
        records = ListRecord.objects.filter(status="success").prefetch_related("items")
        count = 0
        for record in records.iterator():
            count += refresh_projection_for_list_record(record)
        self.stdout.write(f"  ListRecord: {count} projection rows")
        return count

    def _backfill_financial_records(self) -> int:
        from src.financial_parser.models import FinancialRecord
        records = FinancialRecord.objects.filter(status="success").prefetch_related("items")
        count = 0
        for record in records.iterator():
            count += refresh_projection_for_financial_record(record)
        self.stdout.write(f"  FinancialRecord: {count} projection rows")
        return count

    def _backfill_todo_records(self) -> int:
        from src.managed_lists.models import TodoRecord
        records = TodoRecord.objects.filter(status="success").prefetch_related("items")
        count = 0
        for record in records.iterator():
            count += refresh_projection_for_todo_record(record)
        self.stdout.write(f"  TodoRecord: {count} projection rows")
        return count
