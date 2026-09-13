"""
================================================================================
 summarize_recording_groups — Voice Diary conversation summarizer
================================================================================

WHAT THIS FILE IS
-----------------
A **Django management command**: the standard, idiomatic way to give a Django
app a custom CLI entry point. It is NOT a microservice (no separate process,
port, or systemd unit) and it is NOT bolted onto an existing script. It is one
self-contained module that does ONE job, end to end:

    find unsplit conversations  ->  group their clips  ->  AI-summarize  ->  store

WHY THIS JOB EXISTS (the domain problem)
----------------------------------------
The voice diary app caps recordings at 240 seconds. When Pedro talks longer,
the app saves the current clip, starts a new recording automatically, and keeps
going. One continuous conversation is therefore stored as SEVERAL clips:

    [240s cap] -> [240s cap] -> ... -> [final non-240s tail]
                    |________ same conversation ________|

The final clip is shorter than 240s — that is the "tail" that proves the
conversation ended (the cap forced the splits; the tail is where he stopped).

THE PIPELINE (data flows through this file top to bottom)
---------------------------------------------------------
    Postgres clips
        │
        ▼
    _unsummarized_items()   🔍 DB lookup: which clips still need summarizing?
        │
        ▼
    _find_groups()          🧩 Grouping engine (pure code, NO AI):
        │                      • primary: client-provided recording_group_id
        │                      • fallback: time-based 240s-cap chain + tail
        ▼
    _is_group_complete()    ⏳ Decision: is the conversation finished?
        │
        ▼
    _build_transcript()     📄 Join all clip texts into one transcript
        │
        ▼
    _summarize_with_ai()    🤖 THE ONLY AI CALL — one LLM call per group
        │
        ▼
    _mark_done()            💾 Write canonical row + mark clips done
        │
        ▼
    ingestion_recordinggroupsummary table (source of truth)

DESIGN PRINCIPLES (why it's shaped this way)
--------------------------------------------
1. **Deterministic grouping, AI only for text.** Grouping is business logic
   that must be reproducible and testable — so it's plain Python. The LLM is
   used ONLY to compress the transcript into a summary. This keeps costs low
   (one call per conversation) and behavior predictable.

2. **Idempotent (safe to re-run).** A group is "done" when a row exists in
   `ingestion_recordinggroupsummary` for its (user, recording_group_id).
   Re-running never duplicates work or double-charges the LLM.

3. **Single-responsibility module.** All the special-job logic lives in THIS
   file. The rest of the app doesn't know it exists. To move or delete the
   feature, you touch one file.

4. **Backlog-friendly.** Only clips carrying the new recording metadata
   (`recording_duration_seconds`) are considered — old pre-migration data is
   ignored ("starting from now onwards").

HOW DJANGO DISCOVERS THIS FILE (architecture hook)
--------------------------------------------------
Django auto-discovers any file named like a command inside an app's
`management/commands/` directory:

    src/ingestion/
      management/
        __init__.py
        commands/
          __init__.py
          summarize_recording_groups.py   <-- you are here
          (any other command .py files)

Django's `manage.py` scans those directories, loads each module, and the
`Command` class inside becomes a CLI verb. That's why:

    python manage.py summarize_recording_groups

"just works" — no registration, no config, no service restart needed.

USAGE
-----
    python manage.py summarize_recording_groups            # all users
    python manage.py summarize_recording_groups --user-id 1
    python manage.py summarize_recording_groups --dry-run  # preview only
    python manage.py summarize_recording_groups --backfill # fill missing summary rows for already-summarized clips
    python manage.py summarize_recording_groups --json     # machine output

Exit code 0 on success (even if nothing to summarize), 1 on error.
================================================================================
"""

import json
import uuid

from django.core.management.base import BaseCommand
from django.utils import timezone
from openai import OpenAI

from src.common.config.settings import get_config
from src.ingestion.models import IngestItem, RecordingGroupSummary

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# A clip "hit the cap" when the raw recording reached ~240s. The client stores
# the raw duration in recording_duration_seconds (whole seconds) and the
# post-processing duration in audio_duration_seconds (float). We accept either,
# with a small tolerance because "240.0s" can be stored as 239.7–240.3 after
# silence removal / rounding.
CAP_SECONDS = 240
CAP_TOLERANCE = 0.5


# ---------------------------------------------------------------------------
# Pure helper functions (no I/O) — the "brain" of the feature
# ---------------------------------------------------------------------------

def _is_cap(item) -> bool:
    """
    True when this audio clip ran into the 240s recording cap.

    This is the single source of truth for "did the timer force a split?"
    Prefer the raw client-reported duration; fall back to the processed one.
    """
    rd = item.recording_duration_seconds
    if rd is not None:
        return rd >= CAP_SECONDS
    ad = item.audio_duration_seconds
    return ad is not None and ad >= CAP_SECONDS - CAP_TOLERANCE


def _build_transcript(group) -> str:
    """
    Concatenate all clip texts into ONE transcript, in chronological order.

    Each clip is wrapped in a header (clip number, duration, timestamp) so the
    LLM can see the boundaries even though it's one continuous conversation.
    """
    parts = []
    for i, item in enumerate(group, start=1):
        ts = item.occurred_at.strftime("%Y-%m-%d %H:%M UTC") if item.occurred_at else "?"
        dur = item.recording_duration_seconds or item.audio_duration_seconds or 0
        text = (item.content_text or "").strip() or "[no transcript]"
        parts.append(f"--- Clip {i}/{len(group)} ({dur}s, {ts}) ---\n{text}")
    return "\n\n".join(parts)


def _find_groups(items, gap_seconds=600):
    """
    THE GROUPING ENGINE — pure deterministic code, no AI.

    Goal: turn a flat list of clips into a list of CONVERSATIONS, where each
    conversation is a list of clips that belong together.

    Two strategies, in order of trust:

    1) PRIMARY — client-provided ``recording_group_id``:
       The mobile client already stamps every clip of one recording session
       with the same UUID. If clips share a UUID, they ARE one conversation —
       no guessing needed. (We still require at least one 240s-cap clip inside
       so standalone short notes don't become "conversations".)

    2) FALLBACK — time-based cap-chain detection (for clips with NULL group id):
       Walk chronologically. A conversation starts at a cap clip (240s). While
       the next clip is ALSO a cap, the timer kept running out — same talk.
       The first NON-cap clip after the cap chain is the tail that closes the
       conversation. A large time gap also closes it (new session).

    Returns a list of groups; each group is a list of IngestItem ordered by
    occurred_at ASC.
    """
    # Sort per user by time — grouping must never mix two users.
    items = sorted(items, key=lambda i: (i.user_id, i.occurred_at or timezone.now()))

    # 1) Primary path: bucket by (user, recording_group_id).
    by_group_id = {}
    for item in items:
        if item.recording_group_id:
            by_group_id.setdefault((item.user_id, item.recording_group_id), []).append(item)

    # 2) Fallback path: entries without a group id, still chronological.
    fallback = [i for i in items if not i.recording_group_id]
    fallback.sort(key=lambda i: i.occurred_at or timezone.now())

    groups = []

    # Primary: one group per client UUID (if it contains a cap clip).
    for key, members in by_group_id.items():
        members.sort(key=lambda i: i.occurred_at or timezone.now())
        if any(_is_cap(m) for m in members):
            groups.append(members)

    # Fallback: the state machine below is the heart of the grouping logic.
    #
    #   current = clips accumulated for the conversation we're building
    #   prev    = previous clip (to measure the time gap)
    #
    # Transitions:
    #   • no group open + cap clip        -> start a group
    #   • gap too big                     -> close current group, start fresh
    #   • next clip is also cap           -> same conversation, keep appending
    #   • next clip is NON-cap after caps -> it's the tail -> group is complete
    current = []
    prev = None
    for item in fallback:
        # Measure the gap since the previous clip (0.0 if unknown).
        if prev is not None and item.occurred_at and prev.occurred_at:
            gap = (item.occurred_at - prev.occurred_at).total_seconds()
        else:
            gap = 0.0
        gap_ok = gap <= gap_seconds
        prev = item

        if not current:
            # Nothing open yet: only a cap clip can START a conversation.
            # (Standalone short notes are deliberately ignored here.)
            if _is_cap(item):
                current = [item]
            continue

        if not gap_ok:
            # A big time gap means the previous conversation is over.
            if any(_is_cap(x) for x in current):
                groups.append(current)
            current = [item] if _is_cap(item) else []
        elif _is_cap(item):
            # Timer ran out again -> same conversation continues.
            current.append(item)
        else:
            # NON-cap right after a cap chain = the tail that ends the talk.
            current.append(item)
            if any(_is_cap(x) for x in current):
                groups.append(current)
            current = []

    # Flush any group still open at the end of the data.
    if current and any(_is_cap(x) for x in current):
        groups.append(current)

    # De-duplicate: an entry could theoretically appear via both paths.
    seen = set()
    deduped = []
    for group in groups:
        ids = frozenset(i.id for i in group)
        if ids not in seen:
            seen.add(ids)
            deduped.append(group)
    return deduped


def _is_group_complete(group, now, min_tail_age_seconds) -> tuple[bool, str]:
    """
    Decision: is this conversation finished enough to summarize?

    Two ways to be complete:
      - "tail"     : the last clip is non-cap -> user clearly stopped talking.
      - "aged-cap" : the last clip hit the cap BUT it's old enough that no
                     follow-up clip appeared (session truly ended on a cap).

    If the last clip is a cap and very recent ("open-cap"), the user may still
    be recording — we skip and retry next run rather than cut a talk in half.
    """
    last = group[-1]
    if not _is_cap(last):
        return True, "tail"
    if last.occurred_at and (now - last.occurred_at).total_seconds() >= min_tail_age_seconds:
        return True, "aged-cap"
    return False, "open-cap"


def _summarize_with_ai(api_key, model, transcript, user_email=""):
    """
    THE ONLY AI DECISION POINT — compress a transcript into a concise summary.

    One call per conversation group. The prompt is carefully worded to tell
    the model the clips are ONE continuous talk (so it doesn't treat them as
    separate topics) and to reply in the same language as the content.
    """
    client = OpenAI(api_key=api_key, timeout=120.0)
    system = (
        "You summarize voice-diary conversations that were recorded as several "
        "audio clips because a 4-minute recording cap forced the app to split "
        "one continuous conversation. The clips are consecutive parts of a "
        "single conversation. Produce a concise summary in the same language "
        "as the content, covering: main topic(s), key points, decisions, and "
        "any action items. Use short bullets, no preamble, under 150 words."
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Conversation transcript:\n\n{transcript}"},
        ],
    )
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# The Django command class — the thin orchestrator that ties it all together
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    """
    Django wraps this class and exposes it as:  manage.py summarize_recording_groups

    The class has two responsibilities only:
      1. `add_arguments` — declare the CLI flags (argparse-style).
      2. `handle`        — run the pipeline in order and print results.

    Everything else is delegated to the pure functions above — that keeps the
    command readable and the logic testable without Django.
    """

    help = "Group cap-split audio clips into conversations, summarize each, mark done."

    def add_arguments(self, parser):
        """Declare every CLI flag the command accepts."""
        parser.add_argument("--user-id", type=int, default=None, help="Only this user (default: all).")
        parser.add_argument("--dry-run", action="store_true", help="Detect groups but do not call AI or write DB.")
        parser.add_argument("--backfill", action="store_true", help="Create RecordingGroupSummary rows for clips already carrying summary_text.")
        parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
        parser.add_argument("--model", default=None, help="LLM model for summaries (default: gpt-4.1-mini).")
        parser.add_argument("--gap-seconds", type=int, default=600, help="Max gap between clips of one conversation (default 600).")
        parser.add_argument("--min-tail-age-seconds", type=int, default=300, help="A trailing cap clip older than this counts as session end (default 300).")

    def handle(self, *args, **options):
        """
        The pipeline, in order:

          1. resolve settings from CLI flags
          2. query candidate clips (audio, new client, not deleted)
          3. filter to unsummarized groups
          4. group them into conversations
          5. keep only COMPLETE conversations
          6. dry-run?  -> print and stop
          7. nothing?  -> report and stop
          8. else: for each group: build transcript -> AI summary -> mark done
        """
        self._gap_seconds = options["gap_seconds"]
        self._min_tail_age = options["min_tail_age_seconds"]
        self._model = options["model"] or "gpt-4.1-mini"
        now = timezone.now()

        # Only audio clips from the NEW client (they carry recording duration
        # metadata). Old pre-migration backlog is intentionally ignored.
        qs = IngestItem.objects.filter(
            item_type="audio",
            is_deleted=False,
            recording_duration_seconds__isnull=False,
        )
        if options["user_id"]:
            qs = qs.filter(user_id=options["user_id"])
        qs = qs.prefetch_related("user")

        # Special mode: backfill summary rows for clips that were summarized
        # before the summary table existed (one-time migration helper).
        if options["backfill"]:
            self._backfill_summary_rows(qs, options)
            return

        # Candidate items = clips whose group has no summary row yet.
        items = self._unsummarized_items(qs)

        # Group them (pure function — see _find_groups for the algorithm).
        groups = _find_groups(items, gap_seconds=self._gap_seconds)
        if options["json"]:
            print(json.dumps({"groups_found": len(groups)}))

        # Keep only conversations that are safely finished.
        complete = []
        for g in groups:
            ok, reason = _is_group_complete(g, now, self._min_tail_age)
            if ok:
                complete.append(g)
            elif not options["json"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"  skip group (starts {g[0].occurred_at}): still open "
                        f"(last clip hit the cap recently, no tail yet) — run again later"
                    )
                )

        if options["json"]:
            print(json.dumps({"groups_complete": len(complete)}))

        # Dry run: report what WOULD happen, touch nothing.
        if options["dry_run"]:
            self._print_groups_dry(complete)
            return

        if not complete:
            if not options["json"]:
                self.stdout.write(self.style.SUCCESS("Nothing to summarize."))
            return

        # Real run: load the API key once, then process every group.
        config = get_config()
        api_key = config.ai.openai_api_key
        if not api_key:
            self.stderr.write("No OpenAI API key configured (AI_OPENAI_API_KEY). Aborting.")
            raise SystemExit(1)

        results = []
        for group in complete:
            transcript = _build_transcript(group)
            summary = _summarize_with_ai(api_key, self._model, transcript)
            group_uuid = self._mark_done(group, summary)
            results.append(
                {
                    "user_id": group[0].user_id,
                    "recording_group_id": str(group_uuid),
                    "clips": len(group),
                    "start": group[0].occurred_at.isoformat() if group[0].occurred_at else None,
                    "end": group[-1].occurred_at.isoformat() if group[-1].occurred_at else None,
                    "summary": summary,
                }
            )
            if not options["json"]:
                self._print_group(group, summary)

        if options["json"]:
            print(json.dumps(results, indent=2))

    # ------------------------------------------------------------------ utils

    def _unsummarized_items(self, qs):
        """
        DB lookup: which clips still need summarizing?

        A clip is "done" when a RecordingGroupSummary row exists for its
        (user, recording_group_id). Clips without a group id fall back to the
        legacy per-clip marker (summary_text already filled).
        """
        items = list(qs)
        done_group_ids = set(
            RecordingGroupSummary.objects.filter(
                user_id__in={i.user_id for i in items}
            ).values_list("user_id", "recording_group_id")
        )
        out = []
        for i in items:
            if i.recording_group_id:
                key = (i.user_id, i.recording_group_id)
                if key in done_group_ids:
                    continue  # group already summarized
            elif (i.summary_text or "").strip():
                continue  # legacy marker on clip itself
            out.append(i)
        return out

    def _backfill_summary_rows(self, qs, options):
        """
        One-off maintenance helper: clips already carrying summary_text (e.g.
        summarized before the summary table existed) get their canonical
        RecordingGroupSummary row created. Idempotent — safe to re-run.
        """
        items = [i for i in qs if (i.summary_text or "").strip()]
        groups = _find_groups(items, gap_seconds=self._gap_seconds)
        created = 0
        for group in groups:
            group_uuid = self._mark_done(group, group[-1].summary_text, create_summary_row=True)
            if group_uuid:
                created += 1
        if not options["json"]:
            self.stdout.write(self.style.SUCCESS(f"Backfill: created {created} summary row(s)."))
        elif created:
            print(json.dumps({"backfilled": created}))

    def _print_groups_dry(self, complete):
        """Human-readable dry-run report: one line per candidate group."""
        if not complete:
            self.stdout.write(self.style.SUCCESS("Dry run: no complete groups to summarize."))
            return
        self.stdout.write(self.style.SUCCESS(f"Dry run: {len(complete)} group(s) would be summarized:\n"))
        for g in complete:
            first = g[0].occurred_at.strftime("%H:%M") if g[0].occurred_at else "?"
            last = g[-1].occurred_at.strftime("%H:%M") if g[-1].occurred_at else "?"
            durations = ",".join(str(i.recording_duration_seconds or round(i.audio_duration_seconds or 0)) for i in g)
            self.stdout.write(f"  Group user={g[0].user_id} clips={len(g)} {first}-{last} UTC dur=[{durations}]s")

    def _print_group(self, group, summary):
        """Human-readable result block for one summarized conversation."""
        first = group[0].occurred_at.strftime("%H:%M") if group[0].occurred_at else "?"
        last = group[-1].occurred_at.strftime("%H:%M") if group[-1].occurred_at else "?"
        self.stdout.write("=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"Group user={group[0].user_id} | {len(group)} clips | {first}-{last} UTC"
            )
        )
        self.stdout.write("-" * 60)
        self.stdout.write(summary)
        self.stdout.write("")

    def _mark_done(self, group, summary, create_summary_row=True):
        """
        Persist the result (idempotent):

        1. Decide the shared group UUID: reuse the client's UUID when all
           clips agree on it; otherwise generate a fresh one.
        2. Stamp every clip with the summary + the shared UUID (display copy).
        3. Create/update ONE canonical RecordingGroupSummary row — this is
           the source of truth AND the "done" marker for future runs.
        """
        existing = {i.recording_group_id for i in group if i.recording_group_id}
        group_uuid = existing.pop() if len(existing) == 1 else uuid.uuid4()

        for item in group:
            item.summary_text = summary
            item.recording_group_id = group_uuid
            item.save(update_fields=["summary_text", "recording_group_id"])

        if create_summary_row:
            user = group[0].user
            total_duration = sum(
                (i.recording_duration_seconds or i.audio_duration_seconds or 0) for i in group
            )
            RecordingGroupSummary.objects.update_or_create(
                user=user,
                recording_group_id=group_uuid,
                defaults={
                    "summary_text": summary,
                    "clip_count": len(group),
                    "total_duration_seconds": int(total_duration),
                    "started_at": group[0].occurred_at,
                    "ended_at": group[-1].occurred_at,
                    "model_used": self._model,
                },
            )
        return group_uuid
