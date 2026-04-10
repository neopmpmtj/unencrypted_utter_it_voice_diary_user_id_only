"""
Management command to test real batch calendar insertion.

Usage:
  python manage.py test_batch_insert "Make a calendar entry for this week, Wednesday, Thursday and Friday at 3 o'clock: Go to the gym."
  python manage.py test_batch_insert "..." --user pro@example.com

Requires: Pro/Ultra user with Google Calendar connected. GOOGLE_GEMINI_API_KEY in .env.
"""

import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from src.accounts.models import UserFeatureConfig
from src.batch_calendar.calendar_client import insert_event
from src.batch_calendar.conflict_resolution import check_batch_availability
from src.batch_calendar.config_batch_calendar.batch_calendar_config import get_batch_calendar_config
from src.batch_calendar.services import extract_batch_events

User = get_user_model()


def _event_data_to_google_format(event_data, start_dt, end_dt, tz_str):
    body = copy.deepcopy(event_data)
    body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_str}
    body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_str}
    return body


class Command(BaseCommand):
    help = "Test real batch calendar insertion via Gemini and Google Calendar API"

    def add_arguments(self, parser):
        parser.add_argument("text", nargs="?", default="Make a calendar entry for this week, Wednesday, Thursday and Friday at 3 o'clock: Go to the gym.")
        parser.add_argument("--user", type=str, help="User email (default: first pro/ultra user)")

    def handle(self, *args, **options):
        text = options["text"]
        user_email = options.get("user")

        if user_email:
            try:
                user = User.objects.get(email=user_email)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"User {user_email} not found"))
                return
        else:
            user = User.objects.filter(tier__in=["pro", "ultra"]).first()
            if not user:
                self.stderr.write(self.style.ERROR("No pro/ultra user found. Create one or use --user EMAIL"))
                return

        self.stdout.write(f"Using user: {user.email} (tier={user.tier})")

        try:
            tf = UserFeatureConfig.get_for_user(user)
            calendar_id = tf.default_calendar_id or "primary"
        except Exception:
            calendar_id = "primary"

        config = get_batch_calendar_config()
        self.stdout.write("Extracting events via Gemini...")
        events, error_msg, _ = extract_batch_events(text)

        if error_msg:
            self.stderr.write(self.style.ERROR(f"Extraction failed: {error_msg}"))
            return

        if not events:
            self.stderr.write(self.style.ERROR("No events extracted"))
            return

        self.stdout.write(self.style.SUCCESS(f"Extracted {len(events)} event(s)"))
        for i, ev in enumerate(events):
            self.stdout.write(f"  {i+1}. {ev.get('summary')} @ {ev.get('start')} - {ev.get('end')}")

        self.stdout.write("Checking availability...")
        try:
            conflict_results = check_batch_availability(
                user, events, calendar_id=calendar_id, default_timezone=config.default_timezone
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Availability check failed: {e}"))
            return

        has_conflicts = any(not r["is_available"] for r in conflict_results)
        if has_conflicts:
            self.stdout.write(self.style.WARNING("Some slots have conflicts:"))
            for r in conflict_results:
                if not r["is_available"]:
                    self.stdout.write(f"  Event {r['event_index']}: conflict, {len(r.get('alternative_slots', []))} alternatives")
        else:
            self.stdout.write("All slots available")

        self.stdout.write("Inserting events into Google Calendar...")
        tz_str = config.default_timezone
        success_count = 0
        for r in conflict_results:
            ev_data = r["event_data"]
            ev_tz = ev_data.get("start", {}).get("timeZone") or tz_str
            start_dt = r.get("start_datetime")
            end_dt = r.get("end_datetime")
            if not start_dt or not end_dt:
                self.stdout.write(self.style.WARNING(f"  Event {r['event_index']}: skip (invalid datetime)"))
                continue
            body = _event_data_to_google_format(ev_data, start_dt, end_dt, ev_tz)
            try:
                resp = insert_event(user, body, calendar_id)
                if resp:
                    success_count += 1
                    self.stdout.write(self.style.SUCCESS(f"  Created: {ev_data.get('summary')} -> {resp.get('htmlLink', resp.get('id'))}"))
                else:
                    self.stdout.write(self.style.WARNING(f"  Event {r['event_index']}: insert failed"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Event {r['event_index']}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Done. Inserted {success_count}/{len(events)} events"))
