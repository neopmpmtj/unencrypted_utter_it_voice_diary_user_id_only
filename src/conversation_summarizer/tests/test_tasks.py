"""
Pipeline-hook tests: ``summarizer_on_entry_task``.

The LLM is patched out (``services.call_llm``), so these run offline and cost
nothing. Test settings use ``CELERY_TASK_ALWAYS_EAGER``, and the task is a plain
function here, so it is called directly.
"""

import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.conversation_summarizer.models import (
    ConversationSummary,
    SummaryRun,
    SummaryStatus,
)
from src.conversation_summarizer.tasks import summarizer_on_entry_task
from src.ingestion.models import IngestItem

PAYLOAD = (
    '{"type": "meeting", "title": "T", "summary": "S", "key_points": [], '
    '"decisions": [], "action_items": [], "people": [], "language": "en"}'
)
TRANSCRIPT = "This is a long spoken transcript about the deploy. " * 12


def _fake_llm(*args, **kwargs):
    return PAYLOAD, {"input_tokens": 50, "output_tokens": 10}


def _exploding_llm(*args, **kwargs):
    raise RuntimeError("api down")


class SummarizerHookTaskTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="summarizer-hook@example.com", password="***"
        )
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.enable_conversation_summary = True
        prefs.save(update_fields=["enable_conversation_summary"])
        self.group_id = uuid.uuid4()

    def _clip(self, duration, minutes_ago, group_id=None, deleted=False, text=None):
        return IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=deleted,
            occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
            content_text=text or TRANSCRIPT,
            recording_duration_seconds=duration,
            recording_group_id=self.group_id if group_id is None else group_id,
        )

    # ---------------------------------------------------------------- happy path

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_summarizes_a_closed_session(self):
        self._clip(240, 30)
        tail = self._clip(157, 20)

        result = summarizer_on_entry_task(str(tail.id))

        self.assertEqual(result["action"], "summarized")
        self.assertEqual(len(result["summarized"]), 1)
        row = ConversationSummary.objects.get(user=self.user)
        self.assertEqual(row.status, SummaryStatus.READY)
        self.assertEqual(row.clip_count, 2)
        self.assertEqual(SummaryRun.objects.count(), 1)

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_sweeps_other_closed_sessions_of_the_same_user(self):
        # The entry being finalized sits in a session that is still OPEN ...
        open_group = uuid.uuid4()
        self._clip(240, 3, group_id=open_group)
        trigger = self._clip(240, 1, group_id=open_group)
        # ... while an unrelated session is already closed and unsummarized.
        closed_group = uuid.uuid4()
        self._clip(240, 40, group_id=closed_group)
        self._clip(157, 35, group_id=closed_group)

        result = summarizer_on_entry_task(str(trigger.id))

        self.assertEqual(result["action"], "summarized")
        self.assertEqual(
            [s["recording_group_id"] for s in result["summarized"]], [str(closed_group)]
        )
        self.assertEqual(ConversationSummary.objects.count(), 1)

    # ------------------------------------------------------------------- no-ops

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_noop_for_open_session(self):
        self._clip(240, 3)
        trigger = self._clip(240, 1)

        result = summarizer_on_entry_task(str(trigger.id))

        self.assertEqual(result["action"], "nothing-to-do")
        self.assertFalse(ConversationSummary.objects.exists())

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_noop_when_user_turned_summaries_off(self):
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.enable_conversation_summary = False
        prefs.save(update_fields=["enable_conversation_summary"])
        self._clip(240, 30)
        tail = self._clip(157, 20)

        result = summarizer_on_entry_task(str(tail.id))

        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "disabled-for-user")
        self.assertFalse(ConversationSummary.objects.exists())

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_noop_when_summarized_already(self):
        self._clip(240, 30)
        tail = self._clip(157, 20)
        summarizer_on_entry_task(str(tail.id))

        result = summarizer_on_entry_task(str(tail.id))

        self.assertEqual(result["action"], "nothing-to-do")
        self.assertEqual(ConversationSummary.objects.count(), 1)
        self.assertEqual(SummaryRun.objects.count(), 1)

    def test_missing_item_is_handled(self):
        result = summarizer_on_entry_task(str(uuid.uuid4()))
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "no-item")

    def test_deleted_item_is_handled(self):
        clip = self._clip(240, 30, deleted=True)
        result = summarizer_on_entry_task(str(clip.id))
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "deleted-or-orphan")

    # ---------------------------------------------------------------- resilience

    @patch("src.conversation_summarizer.services.call_llm", _exploding_llm)
    def test_llm_failure_is_reported_not_raised(self):
        self._clip(240, 30)
        tail = self._clip(157, 20)

        result = summarizer_on_entry_task(str(tail.id))  # must not raise

        self.assertEqual(result["action"], "failed")
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(ConversationSummary.objects.get().status, SummaryStatus.FAILED)

    # ------------------------------------------------------------------- wiring

    def test_task_is_registered_with_celery(self):
        from src.utter_it.celery import app

        self.assertIn("src.conversation_summarizer.tasks.summarizer_on_entry_task", app.tasks)

    def test_hook_is_wired_into_both_entry_paths(self):
        """Guard the one-line wiring: audio ingestion and text ingestion both fire it."""
        repo = Path(__file__).resolve().parents[2]
        for relative in ("ingestion/tasks.py", "text_input/services.py"):
            source = (repo / relative).read_text()
            self.assertIn(
                "summarizer_on_entry_task",
                source,
                f"{relative} no longer triggers the conversation summarizer",
            )
