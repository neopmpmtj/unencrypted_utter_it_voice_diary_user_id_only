"""
Management command to permanently delete accounts that have passed the retention period.
Uses GlobalSettings key accounts.deletion_retention_days (default 90).
Run daily via cron or Celery beat: python manage.py delete_expired_accounts
"""

from django.utils import timezone
from django.core.management.base import BaseCommand

from src.accounts.models import CustomUser
from src.accounts.deletion_config import get_deletion_retention_days


class Command(BaseCommand):
    help = 'Permanently delete users whose deletion_requested_at is older than retention_days'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        from datetime import timedelta

        retention_days = get_deletion_retention_days()
        cutoff = timezone.now() - timedelta(days=retention_days)
        to_delete = CustomUser.objects.filter(
            deletion_requested_at__isnull=False,
            deletion_requested_at__lt=cutoff,
        )
        count = to_delete.count()

        if options['dry_run']:
            self.stdout.write(
                self.style.WARNING(f'Dry run: would delete {count} account(s)')
            )
            for user in to_delete[:10]:
                self.stdout.write(f'  - {user.email} (since {user.deletion_requested_at})')
            if count > 10:
                self.stdout.write(f'  ... and {count - 10} more')
            return

        for user in to_delete:
            email = user.email
            user.delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted account: {email}'))

        self.stdout.write(self.style.SUCCESS(f'Deleted {count} expired account(s)'))
