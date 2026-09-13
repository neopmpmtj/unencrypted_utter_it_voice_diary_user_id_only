"""
Service-layer tests for the conversation summarizer.

Every test injects a **fake LLM** (``llm=``), so the whole suite runs offline and
costs nothing: the real ``call_llm`` is never used here.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.conversation_summarizer import services
from src.conversation_summarizer.models import (
    ConversationSummary,
    ConversationType,
    SummaryPromptTemplate,
    SummaryRun,
    SummaryStatus,
)
from src.ingestion.models import IngestItem

MEETING_JSON = (
    '{"type": "meeting", "title": "Deploy split", "summary": "We agreed staging stays on Contabo.", '
    '"key_points": ["staging stays"], "decisions": ["Contabo = staging"], '
    '"action_items": ["update docs"], "people": ["Pedro"], "language": "en"}'
)


def fake_llm(payload=MEETING_JSON, fail=None):
    """A stand-in for services.call_llm that records the messages it was given."""
    calls = []

    def _fake(messages, *, model, temperature, max_output_tokens, api_key=None):
        calls.append({"messages": messages, "model": model, "temperature": temperature})
        if fail:
            raise fail
        return payload, {"input_tokens": 120, "output_tokens": 30}

    _fake.calls = calls
    return _fake


class PureHelperTests(TestCase):
    """No DB, no network — the deterministic 'brain'."""

    def _clip(self, duration_raw=240, duration_audio=None, text="hello", minutes_ago=0):
        return type("Clip", (), {
            "recording_duration_seconds": duration_raw,
            "audio_duration_seconds": duration_audio,
            "content_text": text,
            "occurred_at": timezone.now() - timedelta(minutes=minutes_ago),
        })()

    def test_clip_duration_prefers_raw_value(self):
        # A real clip in this diary is 335s raw but ~59s after silence removal.
        clip = self._clip(duration_raw=335, duration_audio=59.092)
        self.assertEqual(services.clip_duration_seconds(clip), 335)

    def test_clip_duration_falls_back_to_audio(self):
        clip = self._clip(duration_raw=None, duration_audio=59.6)
        self.assertEqual(services.clip_duration_seconds(clip), 60)

    def test_cap_detection_uses_tolerance_not_equality(self):
        self.assertTrue(services.is_cap_clip(self._clip(duration_raw=240)))
        self.assertTrue(services.is_cap_clip(self._clip(duration_raw=335)))  # overrun exists in real data
        self.assertTrue(services.is_cap_clip(self._clip(duration_raw=239.6)))
        self.assertFalse(services.is_cap_clip(self._clip(duration_raw=157)))
        self.assertFalse(services.is_cap_clip(self._clip(duration_raw=239)))

    def test_session_is_long_needs_one_cap_clip(self):
        self.assertTrue(services.session_is_long([self._clip(240), self._clip(157)]))
        self.assertFalse(services.session_is_long([self._clip(157), self._clip(20)]))

    def test_complete_when_tail_arrives(self):
        clips = [self._clip(240, minutes_ago=10), self._clip(157, minutes_ago=5)]
        complete, reason = services.session_is_complete(clips)
        self.assertTrue(complete)
        self.assertEqual(reason, "tail")

    def test_open_when_last_clip_hit_cap_recently(self):
        clips = [self._clip(240, minutes_ago=1), self._clip(240, minutes_ago=0)]
        complete, reason = services.session_is_complete(clips)
        self.assertFalse(complete)
        self.assertEqual(reason, "open")

    def test_complete_when_quiet_after_cap(self):
        # Ended exactly on the cap: no short tail ever arrived.
        clips = [self._clip(240, minutes_ago=40), self._clip(240, minutes_ago=35)]
        complete, reason = services.session_is_complete(clips)
        self.assertTrue(complete)
        self.assertEqual(reason, "quiet")

    def test_build_transcript_has_clip_headers_and_text(self):
        clips = [self._clip(240, text="first part"), self._clip(157, text="second part")]
        transcript = services.build_transcript(clips)
        self.assertIn("--- Clip 1/2 (240s", transcript)
        self.assertIn("--- Clip 2/2 (157s", transcript)
        self.assertIn("first part", transcript)
        self.assertIn("second part", transcript)

    def test_render_template_substitutes_known_placeholders(self):
        rendered = services.render_template(
            "Talk by {{user_name}}: {{clip_count}} clips", {"user_name": "Pedro", "clip_count": 4}
        )
        self.assertEqual(rendered, "Talk by Pedro: 4 clips")

    def test_build_messages_includes_few_shot_examples(self):
        template = SummaryPromptTemplate(
            name="t", system_prompt="SYS {{clip_count}}", user_prompt="USER {{transcript}}"
        )
        example = type("Ex", (), {"input_excerpt": "IN", "expected_output": "OUT"})()
        messages = services.build_messages(
            template, {"clip_count": 2, "transcript": "TEXT"}, [example]
        )
        self.assertEqual([m["role"] for m in messages],
                         ["system", "user", "assistant", "user"])
        self.assertIn("SYS 2", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "IN")
        self.assertEqual(messages[2]["content"], "OUT")
        self.assertIn("TEXT", messages[3]["content"])

    def test_parse_valid_json(self):
        parsed = services.parse_summary(MEETING_JSON)
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["partial"])
        self.assertEqual(parsed["type"], ConversationType.MEETING)
        self.assertEqual(parsed["title"], "Deploy split")
        self.assertIn("staging", parsed["summary"])

    def test_parse_json_inside_markdown_fence(self):
        parsed = services.parse_summary(f"```json\n{MEETING_JSON}\n```")
        self.assertFalse(parsed["partial"])
        self.assertEqual(parsed["title"], "Deploy split")

    def test_parse_garbage_degrades_to_plain_text(self):
        parsed = services.parse_summary("Sorry, I cannot summarize this.")
        self.assertTrue(parsed["partial"])
        self.assertIsNone(parsed["structured"])
        self.assertIn("cannot summarize", parsed["summary"])


class SummarizeSessionTests(TestCase):
    """End-to-end with a fake LLM: DB writes, idempotency, failures."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="summarizer-service@example.com", password="***"
        )
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.enable_conversation_summary = True
        prefs.save(update_fields=["enable_conversation_summary"])
        self.group_id = uuid.uuid4()

    def _make_clip(self, duration, minutes_ago, text=None, group_id=None):
        return IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
            content_text=text or ("This is a long spoken transcript. " * 12),
            recording_duration_seconds=duration,
            recording_group_id=self.group_id if group_id is None else group_id,
        )

    def _session(self, clips):
        return services.Session(user_id=self.user.pk, clips=clips, recording_group_id=self.group_id)

    def test_summarize_creates_row_audit_and_display_copy(self):
        clips = [self._make_clip(240, 30), self._make_clip(240, 25), self._make_clip(157, 20)]
        llm = fake_llm()

        result = services.summarize_session(self._session(clips), llm=llm)

        self.assertEqual(result.action, "created")
        row = ConversationSummary.objects.get(user=self.user, recording_group_id=self.group_id)
        self.assertEqual(row.status, SummaryStatus.READY)
        self.assertEqual(row.revision, 1)
        self.assertEqual(row.clip_count, 3)
        self.assertEqual(row.title, "Deploy split")
        self.assertEqual(row.conversation_type, ConversationType.MEETING)
        self.assertEqual(row.total_duration_seconds, 637)
        self.assertIsNotNone(row.structured_data)
        self.assertEqual(row.template_version, SummaryPromptTemplate.objects.get(name="default").version)

        run = SummaryRun.objects.get(user=self.user)
        self.assertTrue(run.ok)
        self.assertEqual(run.tokens_in, 120)
        self.assertEqual(run.tokens_out, 30)

        for clip in clips:
            clip.refresh_from_db()
            self.assertEqual(clip.summary_text, row.summary_text)

        # The prompt actually carried the transcript and the few-shot examples.
        content = " ".join(m["content"] for m in llm.calls[0]["messages"])
        self.assertIn("long spoken transcript", content)

    def test_second_run_is_skipped(self):
        clips = [self._make_clip(240, 30), self._make_clip(157, 20)]
        services.summarize_session(self._session(clips), llm=fake_llm())

        result = services.summarize_session(self._session(clips), llm=fake_llm())

        self.assertEqual(result.action, "skipped")
        self.assertEqual(result.reason, "up-to-date")
        self.assertEqual(ConversationSummary.objects.count(), 1)
        self.assertEqual(SummaryRun.objects.count(), 1)
        self.assertEqual(ConversationSummary.objects.get().revision, 1)

    def test_late_clip_bumps_revision_on_the_same_row(self):
        clips = [self._make_clip(240, 40), self._make_clip(157, 30)]
        services.summarize_session(self._session(clips), llm=fake_llm())

        # The talk actually continued: another cap clip lands later. It is old
        # enough to be past the quiet window, so the session counts as closed again.
        clips.append(self._make_clip(240, 15))
        result = services.summarize_session(self._session(clips), llm=fake_llm())

        self.assertEqual(result.action, "updated")
        self.assertEqual(ConversationSummary.objects.count(), 1, "must not duplicate the summary")
        row = ConversationSummary.objects.get()
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.clip_count, 3)

    def test_open_session_is_not_summarized(self):
        clips = [self._make_clip(240, 3), self._make_clip(240, 1)]
        result = services.summarize_session(self._session(clips), llm=fake_llm())

        self.assertEqual(result.action, "skipped")
        self.assertEqual(result.reason, "open")
        self.assertFalse(ConversationSummary.objects.exists())

    def test_force_overrides_open_session(self):
        clips = [self._make_clip(240, 3), self._make_clip(240, 1)]
        result = services.summarize_session(self._session(clips), force=True, llm=fake_llm())
        self.assertEqual(result.action, "created")

    def test_dry_run_touches_nothing(self):
        clips = [self._make_clip(240, 30), self._make_clip(157, 20)]
        result = services.summarize_session(self._session(clips), dry_run=True, llm=fake_llm())

        self.assertEqual(result.action, "dry_run")
        self.assertFalse(ConversationSummary.objects.exists())
        self.assertFalse(SummaryRun.objects.exists())

    def test_llm_failure_is_recorded_not_raised(self):
        clips = [self._make_clip(240, 30), self._make_clip(157, 20)]
        llm = fake_llm(fail=RuntimeError("boom"))

        result = services.summarize_session(self._session(clips), llm=llm)

        self.assertEqual(result.action, "failed")
        self.assertIn("boom", result.error)
        row = ConversationSummary.objects.get()
        self.assertEqual(row.status, SummaryStatus.FAILED)
        self.assertEqual(row.attempts, 1)
        self.assertFalse(SummaryRun.objects.get().ok)

    def test_failed_summary_is_retried_on_next_run(self):
        clips = [self._make_clip(240, 30), self._make_clip(157, 20)]
        services.summarize_session(self._session(clips), llm=fake_llm(fail=RuntimeError("boom")))

        result = services.summarize_session(self._session(clips), llm=fake_llm())

        self.assertEqual(result.action, "updated")
        self.assertEqual(ConversationSummary.objects.get().status, SummaryStatus.READY)

    def test_non_json_reply_is_stored_as_partial(self):
        clips = [self._make_clip(240, 30), self._make_clip(157, 20)]
        services.summarize_session(self._session(clips), llm=fake_llm(payload="plain prose summary"))

        row = ConversationSummary.objects.get()
        self.assertEqual(row.status, SummaryStatus.PARTIAL)
        self.assertEqual(row.summary_text, "plain prose summary")

    def test_too_short_session_is_skipped(self):
        clips = [
            self._make_clip(240, 30, text="hi"),
            self._make_clip(157, 20, text="ok"),
        ]
        result = services.summarize_session(self._session(clips), llm=fake_llm())
        self.assertEqual(result.action, "skipped")
        self.assertEqual(result.reason, "too-short")

    def test_disabled_preference_skips_the_sweep_entirely(self):
        self._make_clip(240, 30)
        self._make_clip(157, 20)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.enable_conversation_summary = False
        prefs.save(update_fields=["enable_conversation_summary"])
        llm = fake_llm()

        results = services.summarize_due_sessions(user=self.user, llm=llm)

        self.assertEqual(results, [])
        self.assertEqual(llm.calls, [], "no LLM call may happen when the user opted out")
        self.assertFalse(ConversationSummary.objects.exists())

    def test_sweep_summarizes_closed_but_not_open_sessions(self):
        closed_group, open_group = uuid.uuid4(), uuid.uuid4()
        self._make_clip(240, 40, group_id=closed_group)
        self._make_clip(157, 35, group_id=closed_group)
        self._make_clip(240, 2, group_id=open_group)

        results = services.summarize_due_sessions(user=self.user, llm=fake_llm())

        summarized_groups = {r.recording_group_id for r in results}
        self.assertEqual(summarized_groups, {str(closed_group)})
        self.assertEqual(ConversationSummary.objects.count(), 1)

    def test_summarize_for_user_and_group_helper(self):
        self._make_clip(240, 30)
        self._make_clip(157, 20)
        result = services.summarize_for_user_and_group(self.user, self.group_id, llm=fake_llm())
        self.assertEqual(result.action, "created")
        self.assertEqual(result.clip_count, 2)


class MapReduceTests(TestCase):
    """Marathon conversations: chunk the input, then reduce the notes."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="summarizer-mapreduce@example.com", password="***"
        )
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.enable_conversation_summary = True
        prefs.save(update_fields=["enable_conversation_summary"])
        self.group_id = uuid.uuid4()

    def _long_session(self, clips=3, chars=400):
        items = []
        for index in range(clips):
            items.append(
                IngestItem.objects.create(
                    user=self.user,
                    item_type="audio",
                    status="processed",
                    is_deleted=False,
                    occurred_at=timezone.now() - timedelta(minutes=60 - index * 5),
                    content_text="word " * (chars // 5),
                    recording_duration_seconds=240,
                    recording_group_id=self.group_id,
                )
            )
        return items

    def test_chunk_clips_never_splits_a_clip(self):
        clips = self._long_session(clips=4, chars=200)
        chunks = services.chunk_clips(clips, chunk_chars=250)
        self.assertGreater(len(chunks), 1)
        flattened = [clip for chunk in chunks for clip in chunk]
        self.assertEqual([c.id for c in flattened], [c.id for c in clips], "no clip may be lost or reordered")

    def test_long_transcript_uses_map_reduce(self):
        clips = self._long_session(clips=3, chars=400)
        llm = fake_llm()
        cfg = SimpleNamespace(
            chunk_chars=300, map_reduce_enabled=True,
            model="test-model", temperature=0.0, max_output_tokens=100,
        )

        result = services.summarize_session(
            services.Session(user_id=self.user.pk, clips=clips, recording_group_id=self.group_id),
            llm=llm, cfg=cfg,
        )

        self.assertEqual(result.action, "created")
        # one call per chunk, plus the final reduce over the notes
        self.assertGreaterEqual(len(llm.calls), 2)
        self.assertGreater(
            SummaryRun.objects.filter(kind="map_reduce_chunk").count(), 0,
            "each map call must be audited",
        )
        row = ConversationSummary.objects.get()
        self.assertEqual(row.status, SummaryStatus.READY)
        # the reduce step feeds NOTES, not the raw transcript
        final_messages = llm.calls[-1]["messages"]
        self.assertIn("Part 1", " ".join(m.get("content", "") for m in final_messages))

    def test_map_reduce_disabled_keeps_a_single_call(self):
        clips = self._long_session(clips=3, chars=400)
        llm = fake_llm()
        cfg = SimpleNamespace(
            chunk_chars=300, map_reduce_enabled=False,
            model="test-model", temperature=0.0, max_output_tokens=100,
        )

        services.summarize_session(
            services.Session(user_id=self.user.pk, clips=clips, recording_group_id=self.group_id),
            llm=llm, cfg=cfg,
        )

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(SummaryRun.objects.filter(kind="map_reduce_chunk").count(), 0)

    def test_short_transcript_does_not_map_reduce(self):
        # Enough text to pass min_chars, but far below the chunk budget.
        clips = self._long_session(clips=2, chars=400)
        llm = fake_llm()
        cfg = SimpleNamespace(
            chunk_chars=60_000, map_reduce_enabled=True,
            model="test-model", temperature=0.0, max_output_tokens=100,
        )

        services.summarize_session(
            services.Session(user_id=self.user.pk, clips=clips, recording_group_id=self.group_id),
            llm=llm, cfg=cfg,
        )

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(SummaryRun.objects.filter(kind="map_reduce_chunk").count(), 0)
