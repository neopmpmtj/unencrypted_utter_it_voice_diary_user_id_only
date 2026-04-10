"""Tests for v14 retrieval Celery tasks: prep and process."""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem


class IndexEntryPrepTaskTests(TestCase):
    """Tests for index_entry_prep_task."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="prep@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="voice",
            is_deleted=False,
        )

    @patch("src.retrieval.tasks.index_entry_process_task")
    @patch("src.retrieval.tasks.write_prep_cache", return_value="/tmp/test.tmp")
    def test_success_enqueues_process_task(self, _cache, mock_process):
        from src.retrieval.tasks import index_entry_prep_task

        index_entry_prep_task(str(self.item.id))
        mock_process.delay.assert_called_once_with(str(self.item.id), "/tmp/test.tmp")

    def test_missing_item_returns_early(self):
        from src.retrieval.tasks import index_entry_prep_task

        index_entry_prep_task(str(uuid.uuid4()))

    def test_deleted_item_returns_early(self):
        self.item.is_deleted = True
        self.item.save()

        from src.retrieval.tasks import index_entry_prep_task

        index_entry_prep_task(str(self.item.id))

    @patch("src.retrieval.tasks.write_prep_cache", side_effect=RuntimeError("cache write fail"))
    def test_prep_failure_retries(self, _cache):
        from src.retrieval.tasks import index_entry_prep_task

        with self.assertRaises(RuntimeError):
            index_entry_prep_task(str(self.item.id))


class IndexEntryProcessTaskTests(TestCase):
    """Tests for index_entry_process_task."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="proc@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="voice",
            is_deleted=False,
        )
        self.cache_data = {
            "entry_id": str(self.item.id),
            "user_id": str(self.user.id),
            "content_text": "I went to the store today.",
            "title": "Shopping",
            "classification": "personal.daily.diary",
            "list_items": ["milk", "bread"],
            "financial_items": [
                {"merchant": "Store", "category": "groceries", "amount": "10", "currency": "EUR", "description": "food"},
            ],
            "occurred_at": timezone.now().isoformat(),
            "has_attachment": False,
            "attachment_types": [],
        }

    @patch("src.retrieval.tasks.delete_prep_cache")
    @patch("src.retrieval.tasks._compute_embedding", return_value=([0.1] * 1536, {"total": 10}))
    @patch("src.retrieval.tasks.read_prep_cache")
    def test_success_creates_retrieval_projection(self, mock_read, _embed, _del):
        mock_read.return_value = self.cache_data

        from src.retrieval.tasks import index_entry_process_task

        with patch("src.summarizer.services.summarize_for_search") as mock_sum:
            mock_sum.return_value = {
                "summary": "Went shopping for groceries",
                "keywords": ["shopping", "store"],
            }
            index_entry_process_task(str(self.item.id), "/tmp/test.tmp")

        from src.retrieval.models import ItemRetrievalProjection
        proj = ItemRetrievalProjection.objects.get(ingest_item=self.item)
        self.assertEqual(proj.summary, "Went shopping for groceries")

    @patch("src.retrieval.tasks.index_entry_prep_task")
    @patch("src.retrieval.tasks.read_prep_cache", return_value=None)
    def test_cache_miss_reenqueues_prep(self, _read, mock_prep):
        from src.retrieval.tasks import index_entry_process_task

        index_entry_process_task(str(self.item.id), "/tmp/gone.tmp")
        mock_prep.delay.assert_called_once_with(str(self.item.id))

    @patch("src.retrieval.tasks.delete_prep_cache")
    @patch("src.retrieval.tasks._compute_embedding", return_value=([0.1] * 1536, {"total": 10}))
    @patch("src.retrieval.tasks.read_prep_cache")
    def test_summarizer_failure_stores_partial(self, mock_read, _embed, _del):
        mock_read.return_value = self.cache_data

        from src.retrieval.tasks import index_entry_process_task

        with patch("src.summarizer.services.summarize_for_search", side_effect=RuntimeError("LLM down")):
            index_entry_process_task(str(self.item.id), "/tmp/test.tmp")

        from src.retrieval.models import ItemRetrievalProjection
        proj = ItemRetrievalProjection.objects.get(ingest_item=self.item)
        self.assertEqual(proj.summary or "", "")
