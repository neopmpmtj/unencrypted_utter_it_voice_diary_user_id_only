"""Tests for retrieval services: query_diary RAG pipeline."""

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.retrieval.utils import hmac_token

from src.accounts.models import CustomUser, UserPreferences
from src.retrieval.models import AssistantChatMessage, ChatSession, UserChatMessage


def _mock_openai_chat(answer_text="Test answer"):
    """Create a mock OpenAI client that returns a chat completion."""
    mock_client_cls = MagicMock()
    mock_client = mock_client_cls.return_value
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = answer_text
    mock_client.chat.completions.create.return_value = mock_resp
    mock_embed_resp = MagicMock()
    mock_embed_resp.data = [MagicMock()]
    mock_embed_resp.data[0].embedding = [0.1] * 1536
    mock_client.embeddings.create.return_value = mock_embed_resp
    return mock_client_cls


class QueryDiaryTests(TestCase):
    """Tests for the query_diary() function."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="qd@example.com", password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    @patch("src.retrieval.services._get_api_key", return_value="")
    def test_no_api_key_returns_error(self, _mock_key):
        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="hello",
        )
        self.assertIn("API key", result["answer"])
        self.assertEqual(result["sources"], [])

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_creates_session_when_none(self, _key, mock_oai_cls, _vs):
        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Answer"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="What happened today?",
        )
        self.assertIsNotNone(result["session_id"])
        self.assertTrue(ChatSession.objects.filter(id=result["session_id"]).exists())

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_reuses_existing_session(self, _key, mock_oai_cls, _vs):
        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Answer"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        session = ChatSession.objects.create(
            user=self.user, title="Existing",
        )

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=str(session.id),
            user_message="follow up",
        )
        self.assertEqual(result["session_id"], str(session.id))

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_returns_answer_and_sources(self, _key, mock_oai_cls, _vs):
        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Here is the answer"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="test",
        )
        self.assertEqual(result["answer"], "Here is the answer")
        self.assertIsInstance(result["sources"], list)

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_llm_failure_returns_fallback(self, _key, mock_oai_cls, _vs):
        mock_client = mock_oai_cls.return_value
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="test",
        )
        self.assertIn("could not generate", result["answer"].lower())

    @patch("src.retrieval.services._vector_search", return_value=[])
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_stores_user_and_assistant_messages(self, _key, mock_oai_cls, _vs):
        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Reply"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="question",
        )
        session = ChatSession.objects.get(id=result["session_id"])
        from src.retrieval.services import get_session_messages_ordered
        ordered = get_session_messages_ordered(session, _user_id=self.user.id)
        self.assertEqual(len(ordered), 2)
        self.assertEqual(ordered[0][0], "user")
        self.assertEqual(ordered[0][1]["content"], "question")
        self.assertEqual(ordered[1][0], "assistant")
        self.assertEqual(ordered[1][1]["content"], "Reply")

    @patch("src.retrieval.services.ChatSession")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_db_error_on_session_create_returns_error(self, _key, mock_session_cls):
        mock_session_cls.objects.filter.return_value.first.return_value = None
        mock_session_cls.objects.create.side_effect = RuntimeError("DB down")

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="hi",
        )
        self.assertIn("database error", result["answer"].lower())
        self.assertIsNone(result["session_id"])

    @patch("src.retrieval.services._vector_search")
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_source_refs_structure(self, _key, mock_oai_cls, mock_vs):
        mock_proj = MagicMock()
        mock_proj.ingest_item_id = uuid.uuid4()
        mock_proj.summary = "A test summary"
        mock_proj.occurred_at = datetime(2026, 3, 7, 20, 0)
        mock_proj.primary_subject_key = "general.calendar.appointment"
        mock_proj.primary_intent_key = "general.scheduling.reminder"
        mock_vs.return_value = [mock_proj]

        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Answer"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="test",
        )
        self.assertEqual(len(result["sources"]), 1)
        src = result["sources"][0]
        self.assertIn("entry_id", src)
        self.assertIn("summary", src)
        self.assertIn("occurred_at", src)
        self.assertIn("subject", src)
        self.assertIn("intent", src)
        self.assertIn("classification", src)
        self.assertEqual(src["summary"], "A test summary")
        self.assertEqual(src["subject"], "general.calendar.appointment")
        self.assertEqual(src["intent"], "general.scheduling.reminder")

    @patch("src.retrieval.services._vector_search")
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_source_refs_classification_display(self, _key, mock_oai_cls, mock_vs):
        mock_proj = MagicMock()
        mock_proj.ingest_item_id = uuid.uuid4()
        mock_proj.summary = "Summary"
        mock_proj.occurred_at = datetime(2026, 3, 7, 20, 0)
        mock_proj.primary_subject_key = "general.calendar.appointment"
        mock_proj.primary_intent_key = "general.scheduling.reminder"
        mock_vs.return_value = [mock_proj]

        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Answer"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="test",
        )
        src = result["sources"][0]
        self.assertEqual(src["classification"], "appointment | reminder")

    @patch("src.retrieval.services._vector_search")
    @patch("src.retrieval.services.OpenAI")
    @patch("src.retrieval.services._get_api_key", return_value="sk-test")
    def test_source_refs_empty_taxonomy(self, _key, mock_oai_cls, mock_vs):
        mock_proj = MagicMock()
        mock_proj.ingest_item_id = uuid.uuid4()
        mock_proj.summary = "Summary"
        mock_proj.occurred_at = datetime(2026, 3, 7, 20, 0)
        mock_proj.primary_subject_key = ""
        mock_proj.primary_intent_key = ""
        mock_vs.return_value = [mock_proj]

        mock_client = mock_oai_cls.return_value
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Answer"
        mock_client.chat.completions.create.return_value = mock_resp
        mock_embed = MagicMock()
        mock_embed.data = [MagicMock()]
        mock_embed.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = mock_embed

        from src.retrieval.services import query_diary

        result = query_diary(
            user_id=str(self.user.id),
            user=self.user,
            session_id=None,
            user_message="test",
        )
        src = result["sources"][0]
        self.assertIsNone(src["classification"])


class SelectBestProjectionsTests(TestCase):
    """Unit tests for composite ranking / single-best selection."""

    def test_select_best_prefers_higher_composite_score(self):
        from src.retrieval.services import _select_best_projections

        weaker = MagicMock()
        weaker.ingest_item_id = uuid.uuid4()
        weaker.distance = 0.4
        weaker.summary = "other"
        weaker.keywords = []
        weaker.entity_names_normalized = []
        weaker.list_items_flat = ""
        weaker.financial_items_flat = ""
        weaker.content_text_searchable = ""
        weaker.secondary_subject_keys = []
        weaker.secondary_intent_keys = []
        weaker.secondary_context_keys = []
        weaker.primary_context_key = ""
        weaker.occurred_at = datetime(2026, 1, 1)
        weaker.token_index = []
        weaker.primary_subject_key = "a.b"
        weaker.primary_intent_key = ""

        stronger = MagicMock()
        stronger.ingest_item_id = uuid.uuid4()
        stronger.distance = 0.1
        stronger.summary = "meeting notes about quarterly planning"
        stronger.keywords = ["planning", "quarterly"]
        stronger.entity_names_normalized = []
        stronger.list_items_flat = ""
        stronger.financial_items_flat = ""
        stronger.content_text_searchable = "quarterly planning discussion"
        stronger.secondary_subject_keys = []
        stronger.secondary_intent_keys = []
        stronger.secondary_context_keys = []
        stronger.primary_context_key = ""
        stronger.occurred_at = datetime(2026, 1, 2)
        stronger.token_index = []
        stronger.primary_subject_key = "work.meeting"
        stronger.primary_intent_key = ""

        out = _select_best_projections(
            [weaker, stronger],
            user_message="What about quarterly planning?",
            query_entities=set(),
            query_token_hmacs=set(),
            n=1,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].ingest_item_id, stronger.ingest_item_id)

    @patch("src.retrieval.services.TOKEN_INDEX_ENABLED", False)
    def test_token_overlap_ignored_when_token_index_disabled(self):
        from src.retrieval.services import _select_best_projections

        vec_better = MagicMock()
        vec_better.ingest_item_id = uuid.uuid4()
        vec_better.distance = 0.12
        vec_better.summary = "s"
        vec_better.keywords = []
        vec_better.entity_names_normalized = []
        vec_better.list_items_flat = ""
        vec_better.financial_items_flat = ""
        vec_better.content_text_searchable = ""
        vec_better.secondary_subject_keys = []
        vec_better.secondary_intent_keys = []
        vec_better.secondary_context_keys = []
        vec_better.primary_context_key = ""
        vec_better.occurred_at = datetime(2026, 2, 1)
        vec_better.token_index = []
        vec_better.primary_subject_key = ""
        vec_better.primary_intent_key = ""

        would_win_on_token = MagicMock()
        would_win_on_token.ingest_item_id = uuid.uuid4()
        would_win_on_token.distance = 0.4
        would_win_on_token.summary = "s"
        would_win_on_token.keywords = []
        would_win_on_token.entity_names_normalized = []
        would_win_on_token.list_items_flat = ""
        would_win_on_token.financial_items_flat = ""
        would_win_on_token.content_text_searchable = ""
        would_win_on_token.secondary_subject_keys = []
        would_win_on_token.secondary_intent_keys = []
        would_win_on_token.secondary_context_keys = []
        would_win_on_token.primary_context_key = ""
        would_win_on_token.occurred_at = datetime(2026, 2, 1)
        would_win_on_token.token_index = [hmac_token("banana")]
        would_win_on_token.primary_subject_key = ""
        would_win_on_token.primary_intent_key = ""

        q_hmacs = {hmac_token("banana")}
        out = _select_best_projections(
            [would_win_on_token, vec_better],
            user_message="banana",
            query_entities=set(),
            query_token_hmacs=q_hmacs,
            n=1,
        )
        self.assertEqual(out[0].ingest_item_id, vec_better.ingest_item_id)
