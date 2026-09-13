"""
The journal gate: a long (cap-length) recording must never be classified.

Two layers are covered:
* the guard inside ``classify_item_task`` (defensive — covers every call site,
  including the edit API which re-classifies items)
* the wiring in ``process_audio_ingest`` (the pipeline that enqueues it)
"""

import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.classification.tasks import classify_item_task
from src.ingestion.models import IngestItem, IngestJob, JobStatus, JobType


class JournalGateTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="journal-gate@example.com", password="***"
        )

    def _clip(self, duration, minutes_ago=0, group_id=None):
        return IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
            content_text="rambling about my day",
            recording_duration_seconds=duration,
            recording_group_id=group_id,
        )

    @patch("src.retrieval.tasks.index_entry_prep_task")
    @patch("src.ingestion.tasks.broadcast_complete")
    def test_journal_tail_is_never_classified(self, mock_broadcast, mock_index):
        """Pedro's confirmation: the sub-cap tail of a journal session is not classified either."""
        gid = uuid.uuid4()
        self._clip(240, minutes_ago=5, group_id=gid)
        tail = self._clip(16, minutes_ago=1, group_id=gid)

        result = classify_item_task(str(tail.id))

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "journal-session")
        tail.refresh_from_db()
        self.assertEqual(tail.status, "tagged")  # terminal status, without classification
        mock_broadcast.assert_called_once()
        mock_index.delay.assert_called_once_with(str(tail.id))
        self.assertFalse(
            IngestJob.objects.filter(item=tail, job_type=JobType.CLASSIFY_ITEM).exists(),
            "no classification job may be created for a journal item",
        )

    @patch("src.retrieval.tasks.index_entry_prep_task")
    @patch("src.ingestion.tasks.broadcast_complete")
    def test_cap_tranche_itself_is_never_classified(self, mock_broadcast, mock_index):
        clip = self._clip(240, group_id=uuid.uuid4())

        result = classify_item_task(str(clip.id))

        self.assertEqual(result.get("reason"), "journal-session")
        self.assertFalse(
            IngestJob.objects.filter(item=clip, job_type=JobType.CLASSIFY_ITEM).exists()
        )

    @patch("src.retrieval.tasks.index_entry_prep_task")
    @patch("src.ingestion.tasks.broadcast_complete")
    def test_subcap_entry_still_reaches_classification(self, mock_broadcast, mock_index):
        """Instruction mode must pass the gate (proven by reaching the next check: a DONE job)."""
        clip = self._clip(55, group_id=uuid.uuid4())
        IngestJob.objects.create(
            user=self.user,
            item=clip,
            job_type=JobType.CLASSIFY_ITEM,
            status=JobStatus.DONE,
            queued_at=timezone.now(),
        )

        result = classify_item_task(str(clip.id))

        self.assertEqual(result.get("reason"), "Already classified")  # gate did not fire
        mock_broadcast.assert_not_called()
        mock_index.delay.assert_not_called()

    @patch("src.ingestion.session_mode.resolve_session_mode", return_value="instruction")
    @patch("src.retrieval.tasks.index_entry_prep_task")
    @patch("src.ingestion.tasks.broadcast_complete")
    def test_only_the_mode_decides(self, mock_broadcast, mock_index, _mock_mode):
        """The same cap-length clip proceeds when the mode resolves to instruction."""
        clip = self._clip(240, group_id=uuid.uuid4())
        IngestJob.objects.create(
            user=self.user,
            item=clip,
            job_type=JobType.CLASSIFY_ITEM,
            status=JobStatus.DONE,
            queued_at=timezone.now(),
        )

        result = classify_item_task(str(clip.id))

        self.assertEqual(result.get("reason"), "Already classified")

    def test_gate_is_wired_into_pipeline_and_classifier(self):
        src = Path(__file__).resolve().parents[2]
        for relative in ("ingestion/tasks.py", "classification/tasks.py"):
            self.assertIn(
                "resolve_session_mode",
                (src / relative).read_text(),
                f"{relative} no longer consults the session mode",
            )
