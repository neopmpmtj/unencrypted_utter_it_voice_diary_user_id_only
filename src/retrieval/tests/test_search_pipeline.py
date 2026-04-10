"""
Tests for the hybrid search pipeline: vector + token retrieval, context building,
embedding text composition, token index construction, and the reindex command.

Covers changes from:
  - Plan 1: embedding always includes content, context includes content_text_searchable,
            entries edit re-classifies, token index includes content words
  - Plan 2: _token_retrieval as independent search, merged results in query_diary,
            dead FTS code removed
"""

import uuid
from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem
from src.retrieval.utils import hmac_token


class BuildTokenIndexTests(TestCase):
    """Tests for _build_token_index including content_text tokens."""

    def test_keywords_are_indexed(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index(["shopping", "store"], [], [])
        self.assertIn(hmac_token("shopping"), result)
        self.assertIn(hmac_token("store"), result)

    def test_short_keywords_are_indexed(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index(["ok"], [], [])
        self.assertIn(hmac_token("ok"), result)

    def test_list_item_words_longer_than_2_chars(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index([], ["buy some milk"], [])
        self.assertIn(hmac_token("buy"), result)
        self.assertIn(hmac_token("some"), result)
        self.assertIn(hmac_token("milk"), result)

    def test_list_item_short_words_excluded(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index([], ["I am ok"], [])
        self.assertNotIn(hmac_token("I"), result)
        self.assertNotIn(hmac_token("am"), result)
        self.assertNotIn(hmac_token("ok"), result)

    def test_financial_merchant_and_category(self):
        from src.retrieval.tasks import _build_token_index

        fin = [{"merchant": "Lidl", "category": "groceries"}]
        result = _build_token_index([], [], fin)
        self.assertIn(hmac_token("lidl"), result)
        self.assertIn(hmac_token("groceries"), result)

    def test_content_text_words_are_indexed(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index([], [], [], content_text="anexei a minha foto")
        self.assertIn(hmac_token("anexei"), result)
        self.assertIn(hmac_token("minha"), result)
        self.assertIn(hmac_token("foto"), result)

    def test_content_text_short_words_excluded(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index([], [], [], content_text="eu vi a foto")
        self.assertNotIn(hmac_token("eu"), result)
        self.assertNotIn(hmac_token("vi"), result)
        self.assertNotIn(hmac_token("a"), result)
        self.assertIn(hmac_token("foto"), result)

    def test_content_text_limited_to_500_chars(self):
        from src.retrieval.tasks import _build_token_index

        long_text = "word " * 200
        result = _build_token_index([], [], [], content_text=long_text)
        self.assertTrue(len(result) <= 101)

    def test_empty_content_text_no_error(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index([], [], [], content_text="")
        self.assertEqual(result, [])

    def test_none_content_text_no_error(self):
        from src.retrieval.tasks import _build_token_index

        result = _build_token_index([], [], [], content_text=None)
        self.assertEqual(result, [])


class EmbeddingTextCompositionTests(TestCase):
    """Tests that embedding text always includes content regardless of summary."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="embed@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )
        self.cache_data = {
            "entry_id": str(self.item.id),
            "user_id": str(self.user.id),
            "content_text": "anexei a minha foto ao diario",
            "title": "Recording",
            "classification": "personal.diary",
            "list_items": [],
            "financial_items": [],
            "occurred_at": timezone.now().isoformat(),
            "has_attachment": True,
            "attachment_types": ["image/png"],
        }

    @patch("src.retrieval.tasks.delete_prep_cache")
    @patch("src.retrieval.tasks._compute_embedding")
    @patch("src.retrieval.tasks.read_prep_cache")
    def test_content_text_in_embedding_when_summary_exists(self, mock_read, mock_embed, _del):
        mock_read.return_value = self.cache_data
        mock_embed.return_value = ([0.1] * 1536, {"total": 10})

        from src.retrieval.tasks import index_entry_process_task

        with patch("src.summarizer.services.summarize_for_search") as mock_sum:
            mock_sum.return_value = {
                "summary": "User recorded a diary entry about attachments",
                "keywords": ["diary", "attachment"],
            }
            index_entry_process_task(str(self.item.id), "/tmp/test.tmp")

        call_args = mock_embed.call_args[0][0]
        self.assertIn("anexei a minha foto", call_args)
        self.assertIn("User recorded a diary entry", call_args)

    @patch("src.retrieval.tasks.delete_prep_cache")
    @patch("src.retrieval.tasks._compute_embedding")
    @patch("src.retrieval.tasks.read_prep_cache")
    def test_content_text_in_embedding_when_summary_empty(self, mock_read, mock_embed, _del):
        mock_read.return_value = self.cache_data
        mock_embed.return_value = ([0.1] * 1536, {"total": 10})

        from src.retrieval.tasks import index_entry_process_task

        with patch("src.summarizer.services.summarize_for_search", side_effect=RuntimeError("fail")):
            index_entry_process_task(str(self.item.id), "/tmp/test.tmp")

        call_args = mock_embed.call_args[0][0]
        self.assertIn("anexei a minha foto", call_args)


class BuildContextTests(TestCase):
    """Tests for _build_context including content_text_searchable."""

    def test_empty_results_returns_no_entries_message(self):
        from src.retrieval.services import _build_context

        result = _build_context([])
        self.assertIn("No relevant diary entries found", result)

    def test_includes_content_text_searchable(self):
        from src.retrieval.services import _build_context

        proj = MagicMock()
        proj.occurred_at = datetime(2026, 3, 12, 9, 48)
        proj.primary_subject_key = "personal.diary.freeform"
        proj.primary_intent_key = "personal.record.note"
        proj.summary = "A quick recording"
        proj.keywords = ["recording", "attachment"]
        proj.content_text_searchable = "Ok, isto e uma gravacao rapida. anexei a minha foto"
        proj.entity_names_normalized = []
        proj.list_items_flat = ""
        proj.financial_items_flat = ""

        result = _build_context([proj])
        self.assertIn("Content: Ok, isto e uma gravacao rapida. anexei a minha foto", result)
        self.assertIn("Summary: A quick recording", result)
        self.assertIn("Keywords: recording, attachment", result)

    def test_content_text_searchable_truncated_to_800(self):
        from src.retrieval.services import _build_context

        proj = MagicMock()
        proj.occurred_at = datetime(2026, 1, 1)
        proj.primary_subject_key = "general"
        proj.primary_intent_key = ""
        proj.summary = ""
        proj.keywords = []
        proj.content_text_searchable = "x" * 2000
        proj.entity_names_normalized = []
        proj.list_items_flat = ""
        proj.financial_items_flat = ""

        result = _build_context([proj])
        content_line = [l for l in result.split("\n") if l.startswith("Content:")][0]
        content_value = content_line.replace("Content: ", "")
        self.assertEqual(len(content_value), 800)

    def test_empty_content_text_searchable_not_shown(self):
        from src.retrieval.services import _build_context

        proj = MagicMock()
        proj.occurred_at = datetime(2026, 1, 1)
        proj.primary_subject_key = "general"
        proj.primary_intent_key = ""
        proj.summary = "summary"
        proj.keywords = []
        proj.content_text_searchable = ""
        proj.entity_names_normalized = []
        proj.list_items_flat = ""
        proj.financial_items_flat = ""

        result = _build_context([proj])
        self.assertNotIn("Content:", result)


class TokenRetrievalTests(TestCase):
    """Tests for _token_retrieval as independent search channel."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="tokenret@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

        self.item1 = IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )
        self.item2 = IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )

        from src.retrieval.models import ItemRetrievalProjection

        self.proj1 = ItemRetrievalProjection.objects.create(
            ingest_item=self.item1,
            user=self.user,
            token_index=[hmac_token("foto"), hmac_token("anexei"), hmac_token("gravacao")],
        )
        self.proj2 = ItemRetrievalProjection.objects.create(
            ingest_item=self.item2,
            user=self.user,
            token_index=[hmac_token("compras"), hmac_token("lista")],
        )

    def test_returns_matching_entries(self):
        from src.retrieval.services import _token_retrieval

        query_hmacs = {hmac_token("foto"), hmac_token("anexei")}
        results = _token_retrieval(str(self.user.id), query_hmacs, set())
        item_ids = {r.ingest_item_id for r in results}
        self.assertIn(self.item1.id, item_ids)
        self.assertNotIn(self.item2.id, item_ids)

    def test_excludes_specified_ids(self):
        from src.retrieval.services import _token_retrieval

        query_hmacs = {hmac_token("foto")}
        results = _token_retrieval(
            str(self.user.id), query_hmacs, {self.item1.id},
        )
        self.assertEqual(len(results), 0)

    def test_empty_hmacs_returns_empty(self):
        from src.retrieval.services import _token_retrieval

        results = _token_retrieval(str(self.user.id), set(), set())
        self.assertEqual(results, [])

    def test_ranks_by_overlap_count(self):
        from src.retrieval.services import _token_retrieval

        item3 = IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )
        from src.retrieval.models import ItemRetrievalProjection

        ItemRetrievalProjection.objects.create(
            ingest_item=item3,
            user=self.user,
            token_index=[hmac_token("foto")],
        )

        query_hmacs = {hmac_token("foto"), hmac_token("anexei")}
        results = _token_retrieval(str(self.user.id), query_hmacs, set())
        self.assertEqual(results[0].ingest_item_id, self.item1.id)

    def test_user_isolation(self):
        from src.retrieval.services import _token_retrieval

        other_user = CustomUser.objects.create_user(
            email="other_tok@example.com", password="Pass123",
        )
        other_user.is_email_verified = True
        other_user.save()

        query_hmacs = {hmac_token("foto")}
        results = _token_retrieval(str(other_user.id), query_hmacs, set())
        self.assertEqual(len(results), 0)


class QueryDiaryTokenRetrievalIntegrationTests(TestCase):
    """Tests that query_diary uses token retrieval when vector search finds nothing."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="qdtok@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def _mock_openai(self, answer="Found it"):
        mock_cls = MagicMock()
        mock_client = mock_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = answer
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed
        return mock_cls

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services._token_retrieval")
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_token_retrieval_called_when_vector_empty(self, _key, mock_oai, mock_tok_ret, _vs):
        mock_oai.return_value = self._mock_openai().return_value
        mock_tok_ret.return_value = []

        from src.retrieval.services import query_diary

        query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="onde eu anexei a minha foto",
        )
        mock_tok_ret.assert_called_once()
        call_args = mock_tok_ret.call_args
        self.assertEqual(call_args[0][0], self.user.id)
        self.assertIsInstance(call_args[0][1], set)
        self.assertTrue(len(call_args[0][1]) > 0)

    @patch("src.retrieval.services._vector_search")
    @patch("src.retrieval.services._token_retrieval")
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_token_results_merged_with_vector(self, _key, mock_oai, mock_tok_ret, mock_vs):
        vec_proj = MagicMock()
        vec_proj.ingest_item_id = uuid.uuid4()
        vec_proj.distance = 0.05
        vec_proj.summary = "vec entry"
        vec_proj.keywords = []
        vec_proj.entity_names_normalized = []
        vec_proj.entity_roles = []
        vec_proj.list_items_flat = ""
        vec_proj.financial_items_flat = ""
        vec_proj.content_text_searchable = ""
        vec_proj.summary_text_searchable = ""
        vec_proj.occurred_at = datetime(2026, 3, 12)
        vec_proj.primary_subject_key = "general"
        vec_proj.primary_intent_key = ""
        vec_proj.token_index = []
        mock_vs.return_value = [vec_proj]

        tok_proj = MagicMock()
        tok_proj.ingest_item_id = uuid.uuid4()
        tok_proj.distance = None
        tok_proj.summary = "tok entry"
        tok_proj.keywords = []
        tok_proj.entity_names_normalized = []
        tok_proj.entity_roles = []
        tok_proj.list_items_flat = ""
        tok_proj.financial_items_flat = ""
        tok_proj.content_text_searchable = "anexei a minha foto"
        tok_proj.summary_text_searchable = ""
        tok_proj.occurred_at = datetime(2026, 3, 12)
        tok_proj.primary_subject_key = "general"
        tok_proj.primary_intent_key = ""
        tok_proj.token_index = [hmac_token("foto")]
        mock_tok_ret.return_value = [tok_proj]

        mock_oai.return_value = self._mock_openai().return_value

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="foto",
        )
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["entry_id"], str(vec_proj.ingest_item_id))

    @patch("src.retrieval.services.TOKEN_INDEX_ENABLED", False)
    @patch("src.retrieval.services._vector_search")
    @patch("src.retrieval.services._token_retrieval")
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_token_index_overlap_ignored_when_token_index_disabled(
        self, _key, mock_oai, mock_tok_ret, mock_vs,
    ):
        """Token HMAC overlap must not affect ranking when token index is disabled."""
        strong_vec = MagicMock()
        strong_vec.ingest_item_id = uuid.uuid4()
        strong_vec.distance = 0.1
        strong_vec.summary = "alpha"
        strong_vec.keywords = []
        strong_vec.entity_names_normalized = []
        strong_vec.entity_roles = []
        strong_vec.list_items_flat = ""
        strong_vec.financial_items_flat = ""
        strong_vec.content_text_searchable = ""
        strong_vec.summary_text_searchable = ""
        strong_vec.occurred_at = datetime(2026, 3, 10)
        strong_vec.primary_subject_key = "general"
        strong_vec.primary_intent_key = ""
        strong_vec.token_index = []

        token_only_boost = MagicMock()
        token_only_boost.ingest_item_id = uuid.uuid4()
        token_only_boost.distance = 0.35
        token_only_boost.summary = "beta"
        token_only_boost.keywords = []
        token_only_boost.entity_names_normalized = []
        token_only_boost.entity_roles = []
        token_only_boost.list_items_flat = ""
        token_only_boost.financial_items_flat = ""
        token_only_boost.content_text_searchable = ""
        token_only_boost.summary_text_searchable = ""
        token_only_boost.occurred_at = datetime(2026, 3, 10)
        token_only_boost.primary_subject_key = "general"
        token_only_boost.primary_intent_key = ""
        token_only_boost.token_index = [hmac_token("foto"), hmac_token("attach")]

        mock_vs.return_value = [strong_vec, token_only_boost]
        mock_tok_ret.return_value = []
        mock_oai.return_value = self._mock_openai().return_value

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="foto attach extra words",
        )
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["entry_id"], str(strong_vec.ingest_item_id))

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services._token_retrieval", return_value=[])
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_no_results_from_either_channel(self, _key, mock_oai, _tok, _vs):
        mock_oai.return_value = self._mock_openai("No entries found").return_value

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="something random",
        )
        self.assertEqual(result["sources"], [])


class DeadFTSCodeRemovedTests(TestCase):
    """Verify dead FTS code has been removed from services module."""

    def test_fulltext_search_not_in_services(self):
        import src.retrieval.services as svc
        self.assertFalse(hasattr(svc, "_fulltext_search"))

    def test_rerank_by_hybrid_not_in_services(self):
        import src.retrieval.services as svc
        self.assertFalse(hasattr(svc, "_rerank_by_hybrid"))

    def test_fts_config_not_imported(self):
        import src.retrieval.services as svc
        module_source = open(svc.__file__).read()
        self.assertNotIn("FTS_ENABLED", module_source)
        self.assertNotIn("FTS_WEIGHT", module_source)
        self.assertNotIn("FTS_SKIP_WHEN_ENTITY_MATCH", module_source)


class ReindexEntriesCommandTests(TestCase):
    """Tests for the reindex_entries management command."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="reindex@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    @patch("src.retrieval.management.commands.reindex_entries.index_entry_prep_task")
    def test_dry_run_does_not_queue(self, mock_prep):
        IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )
        out = StringIO()
        call_command("reindex_entries", "--dry-run", stdout=out)
        mock_prep.delay.assert_not_called()
        self.assertIn("Dry run", out.getvalue())

    @patch("src.retrieval.management.commands.reindex_entries.index_entry_prep_task")
    def test_queues_non_deleted_items(self, mock_prep):
        item1 = IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )
        IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=True,
        )
        out = StringIO()
        call_command("reindex_entries", stdout=out)
        mock_prep.delay.assert_called_once_with(str(item1.id))
        self.assertIn("Queued re-index for 1", out.getvalue())

    @patch("src.retrieval.management.commands.reindex_entries.index_entry_prep_task")
    def test_user_filter(self, mock_prep):
        IngestItem.objects.create(
            user=self.user,
            item_type="voice", is_deleted=False,
        )
        other_user = CustomUser.objects.create_user(
            email="reindex_other@example.com", password="Pass123",
        )
        other_user.is_email_verified = True
        other_user.save()
        IngestItem.objects.create(
            user=other_user,
            item_type="voice", is_deleted=False,
        )
        out = StringIO()
        call_command("reindex_entries", "--user", str(self.user.id), stdout=out)
        self.assertIn("Queued re-index for 1", out.getvalue())


class EntryEditReclassificationTests(TestCase):
    """Tests that entries edit path deletes classification jobs before re-queuing."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="editreclass@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_entries_view_imports_classification_run(self):
        """Verify entries/views.py imports ItemClassificationRun."""
        from src.entries import views
        module_source = open(views.__file__).read()
        self.assertIn("ItemClassificationRun", module_source)
        self.assertIn("from src.classification.models import", module_source)

    def test_entries_view_deletes_jobs_before_classify(self):
        """Verify the edit path soft-deletes classification runs and jobs before re-queue."""
        from src.entries import views
        module_source = open(views.__file__).read()
        self.assertIn("ItemClassificationRun.all_objects.filter(ingest_item=item).update", module_source)
        self.assertIn("IngestJob.objects.filter(item=item, job_type__in=", module_source)
        self.assertIn("classify_item_task.delay", module_source)
