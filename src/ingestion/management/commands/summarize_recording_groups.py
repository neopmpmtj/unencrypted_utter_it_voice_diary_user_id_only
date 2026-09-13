"""
================================================================================
 summarize_recording_groups — CLI wrapper for the conversation summarizer
================================================================================

WHAT THIS FILE IS
-----------------
A **thin command-line wrapper**. All the real logic (grouping, prompt building,
the LLM call, parsing, persistence) lives in the ``conversation_summarizer`` app:

    src/conversation_summarizer/services.py   ← the engine
    src/conversation_summarizer/models.py     ← prompt / examples / summary / audit

This file only: parses flags → calls the service → prints the result. It exists so
the work can also be run by hand (dry runs, backfills, one-off repairs) without
going through the pipeline.

UNLIKE BEFORE
-------------
Grouping is no longer re-implemented here. The session key is the client-provided
``recording_group_id`` (present on every clip in live data), so the old
"cap-chain + gap" heuristic is now only a fallback for clips that lack it.

USAGE
-----
    python manage.py summarize_recording_groups                  # sweep, all users
    python manage.py summarize_recording_groups --user-id 1
    python manage.py summarize_recording_groups --dry-run        # preview only
    python manage.py summarize_recording_groups --json           # machine output
    python manage.py summarize_recording_groups --force          # re-summarize now
    python manage.py summarize_recording_groups --backfill       # create rows from existing text, no LLM
    python manage.py summarize_recording_groups --min-tail-age-seconds 900

Exit code 0 on success (even when there is nothing to do), 1 on configuration error.
================================================================================
"""

import json
from types import SimpleNamespace

from django.core.management.base import BaseCommand
from django.utils import timezone

from src.accounts.models import CustomUser
from src.conversation_summarizer.models import ConversationSummary, SummaryStatus
from src.conversation_summarizer.services import (
    SummarizeResult,
    find_sessions,
    summarize_due_sessions,
    summarize_session,
)


class Command(BaseCommand):
    help = (
        "Summarize long (cap-split) recording sessions. Thin wrapper over "
        "conversation_summarizer.services."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None, help="Only this user (default: all).")
        parser.add_argument("--dry-run", action="store_true", help="Detect sessions but do not call AI or write DB.")
        parser.add_argument("--backfill", action="store_true", help="Create summary rows for sessions whose clips already carry a summary, without calling the AI.")
        parser.add_argument("--force", action="store_true", help="Re-summarize even if up to date (also ignores the per-user preference).")
        parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
        parser.add_argument("--model", default=None, help="Override the LLM model for this run.")
        parser.add_argument(
            "--min-tail-age-seconds",
            type=int,
            default=None,
            help="Quiet window: a trailing cap clip older than this closes the session (default 600).",
        )
        parser.add_argument(
            "--gap-seconds",
            type=int,
            default=None,
            help="Deprecated/ignored: grouping uses the client-provided recording_group_id.",
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options["user_id"])
        cfg = self._config_override(user, options)

        if options["backfill"]:
            results = self._backfill(user, options)
            self._report(results, options["json"], empty_message="Backfill: nothing to create.")
            return

        if options["force"]:
            results = self._force_all(user, cfg, options)
        else:
            results = summarize_due_sessions(
                user=user, dry_run=options["dry_run"], cfg=cfg
            )

        self._report(results, options["json"], empty_message="Nothing to summarize.")

    # ------------------------------------------------------------------ helpers

    def _resolve_user(self, user_id):
        if not user_id:
            return None
        user = CustomUser.objects.filter(pk=user_id).first()
        if user is None:
            self.stderr.write(self.style.ERROR(f"No user with id {user_id}"))
            raise SystemExit(1)
        return user

    def _config_override(self, user, options):
        """
        Build a lightweight config object when CLI flags override stored settings.

        Returns ``None`` when nothing was overridden, so the service falls back to
        the user's SummaryAgentConfig (or the global default, or field defaults).
        """
        overrides = {}
        if options.get("model"):
            overrides["model"] = options["model"]
        if options.get("min_tail_age_seconds"):
            overrides["quiet_seconds"] = options["min_tail_age_seconds"]
        return SimpleNamespace(**overrides) if overrides else None

    def _force_all(self, user, cfg, options):
        """Re-summarize every session, ignoring completeness and the user preference."""
        results = []
        for session in find_sessions(user=user, cfg=cfg):
            if options["dry_run"]:
                results.append(
                    SummarizeResult(
                        session.user_id,
                        str(session.recording_group_id) if session.recording_group_id else None,
                        "dry_run",
                        clip_count=session.clip_count,
                    )
                )
                continue
            results.append(
                summarize_session(session, force=True, cfg=cfg)
            )
        return results

    def _backfill(self, user, options):
        """
        Create the canonical row for sessions whose clips already carry a summary
        text (e.g. summarized before this table existed). No AI call, idempotent.
        """
        results = []
        sessions = find_sessions(user=user, cfg=None)
        for session in sessions:
            if session.recording_group_id is None:
                continue
            if ConversationSummary.objects.filter(
                user_id=session.user_id, recording_group_id=session.recording_group_id
            ).exists():
                continue

            existing_text = next(
                (c.summary_text.strip() for c in session.clips if (c.summary_text or "").strip()),
                "",
            )
            if not existing_text:
                continue

            if options["dry_run"]:
                results.append(
                    SummarizeResult(
                        session.user_id, str(session.recording_group_id), "dry_run",
                        reason="backfill", clip_count=session.clip_count,
                    )
                )
                continue

            row = ConversationSummary.objects.create(
                user_id=session.user_id,
                recording_group_id=session.recording_group_id,
                summary_text=existing_text,
                clip_count=session.clip_count,
                total_duration_seconds=session.total_duration_seconds,
                started_at=session.started_at,
                ended_at=session.ended_at,
                status=SummaryStatus.READY,
                revision=1,
                last_error="",
            )
            results.append(
                SummarizeResult(
                    session.user_id, str(session.recording_group_id), "created",
                    reason="backfill", summary_id=str(row.id),
                    clip_count=session.clip_count, summary_text=row.summary_text,
                )
            )
        return results

    def _report(self, results, as_json, empty_message):
        if as_json:
            print(json.dumps([r.as_dict() for r in results], indent=2))
            return

        if not results:
            self.stdout.write(self.style.SUCCESS(empty_message))
            return

        created = [r for r in results if r.action in ("created", "updated")]
        failed = [r for r in results if r.action == "failed"]
        other = [r for r in results if r not in created and r not in failed]

        for result in created:
            self.stdout.write("=" * 60)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Group user={result.user_id} | {result.clip_count} clips | {result.action} ({result.reason})"
                )
            )
            self.stdout.write("-" * 60)
            self.stdout.write(result.summary_text or "(no text)")
            self.stdout.write("")

        for result in failed:
            self.stderr.write(
                self.style.ERROR(f"Group {result.recording_group_id} failed: {result.error}")
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {len(created)} summarized, {len(failed)} failed, {len(other)} skipped."
            )
        )
