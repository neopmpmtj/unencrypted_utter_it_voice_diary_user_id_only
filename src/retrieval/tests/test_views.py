"""Tests for retrieval views: chat page + API endpoints."""

import json
import uuid
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.retrieval.models import AssistantChatMessage, ChatSession, UserChatMessage


class _AuthenticatedTestCase(TestCase):
    """Base class that creates a logged-in user."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="chattest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()


class ChatPageTests(_AuthenticatedTestCase):
    """Tests for the chat_page view."""

    def test_renders_200(self):
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "retrieval/chat.html")

    def test_latest_session_in_context(self):
        session = ChatSession.objects.create(
            user=self.user, title="Session A",
        )
        UserChatMessage.objects.create(
            session=session, content="Hello", sequence_index=1,
        )
        AssistantChatMessage.objects.create(
            session=session,
            content="Hi there",
            source_entries=[],
            sequence_index=2,
        )
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("latest_session", resp.context)
        latest = resp.context["latest_session"]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], str(session.id))
        self.assertEqual(len(latest["messages"]), 2)
        self.assertEqual(latest["messages"][0]["role"], "user")
        self.assertEqual(latest["messages"][0]["content"], "Hello")
        self.assertEqual(latest["messages"][1]["role"], "assistant")
        self.assertEqual(latest["messages"][1]["content"], "Hi there")

    def test_latest_session_null_when_none_exist(self):
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("latest_session", resp.context)
        self.assertIsNone(resp.context["latest_session"])

    def test_latest_session_returns_last_10_messages_only(self):
        session = ChatSession.objects.create(
            user=self.user, title="Many messages",
        )
        for i in range(12):
            if i % 2 == 0:
                UserChatMessage.objects.create(
                    session=session, content=f"Msg {i}", sequence_index=i + 1,
                )
            else:
                AssistantChatMessage.objects.create(
                    session=session, content=f"Msg {i}", source_entries=[], sequence_index=i + 1,
                )
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        latest = resp.context["latest_session"]
        self.assertIsNotNone(latest)
        self.assertEqual(len(latest["messages"]), 10)
        self.assertEqual(latest["messages"][0]["content"], "Msg 2")
        self.assertEqual(latest["messages"][-1]["content"], "Msg 11")

    def test_chat_page_loads_last_10_questions_and_answers_on_load(self):
        """Assert that the last 10 Q&A (messages) are loaded into the chat upon page load."""
        session = ChatSession.objects.create(
            user=self.user, title="Q&A session",
        )
        for i in range(5):
            UserChatMessage.objects.create(
                session=session,
                content=f"Question {i}",
                sequence_index=i * 2 + 1,
            )
            AssistantChatMessage.objects.create(
                session=session,
                content=f"Answer {i}",
                source_entries=[],
                sequence_index=i * 2 + 2,
            )
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        latest = resp.context["latest_session"]
        self.assertIsNotNone(latest)
        self.assertEqual(len(latest["messages"]), 10)
        for i in range(5):
            self.assertEqual(latest["messages"][i * 2]["role"], "user")
            self.assertEqual(latest["messages"][i * 2]["content"], f"Question {i}")
            self.assertEqual(latest["messages"][i * 2 + 1]["role"], "assistant")
            self.assertEqual(latest["messages"][i * 2 + 1]["content"], f"Answer {i}")
        script = resp.content.decode()
        self.assertIn('id="chat-latest-session"', script)
        self.assertIn("Question 0", script)
        self.assertIn("Answer 0", script)
        self.assertIn("Question 4", script)
        self.assertIn("Answer 4", script)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 302)


class ChatMessageApiTests(_AuthenticatedTestCase):
    """Tests for chat_message_api (POST /chat/api/chat/)."""

    url = None

    def setUp(self):
        super().setUp()
        self.url = reverse("retrieval:api_chat")

    @patch("src.retrieval.views.query_diary")
    def test_success(self, mock_qd):
        mock_qd.return_value = {
            "answer": "Hello",
            "sources": [],
            "session_id": str(uuid.uuid4()),
        }
        resp = self.client.post(
            self.url,
            data=json.dumps({"message": "hi"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["answer"], "Hello")
        mock_qd.assert_called_once()

    def test_empty_message_400(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({"message": "  "}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_bad_json_400(self):
        resp = self.client.post(
            self.url,
            data="not json{",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    @patch("src.retrieval.views.query_diary", side_effect=RuntimeError("boom"))
    def test_query_diary_exception_500(self, _mock):
        resp = self.client.post(
            self.url,
            data=json.dumps({"message": "hello"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", resp.json())

    def test_requires_post(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)


class SessionsApiTests(_AuthenticatedTestCase):
    """Tests for sessions_api (GET/POST /chat/api/sessions/)."""

    url = None

    def setUp(self):
        super().setUp()
        self.url = reverse("retrieval:api_sessions")

    def test_get_empty_list(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sessions"], [])

    def test_get_returns_sessions(self):
        ChatSession.objects.create(
            user=self.user, title="My chat",
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        sessions = resp.json()["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["title"], "My chat")

    def test_post_creates_session(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({"title": "New chat"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["title"], "New chat")
        self.assertTrue(ChatSession.objects.filter(id=data["id"]).exists())

    def test_post_default_title(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()["title"])

    def test_user_isolation(self):
        other_user = CustomUser.objects.create_user(
            email="other@example.com", password="Pass123",
        )
        other_user.is_email_verified = True
        other_user.save()
        ChatSession.objects.create(
            user=other_user, title="Other",
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.json()["sessions"], [])


class SessionDetailApiTests(_AuthenticatedTestCase):
    """Tests for session_detail_api (DELETE /chat/api/sessions/<id>/)."""

    def test_delete_success(self):
        session = ChatSession.objects.create(
            user=self.user, title="To delete",
        )
        url = reverse("retrieval:api_session_detail", args=[session.id])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])
        self.assertFalse(ChatSession.objects.filter(id=session.id).exists())

    def test_delete_nonexistent_404(self):
        url = reverse("retrieval:api_session_detail", args=[uuid.uuid4()])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 404)

    def test_delete_other_users_session_404(self):
        other_user = CustomUser.objects.create_user(
            email="other2@example.com", password="Pass123",
        )
        other_user.is_email_verified = True
        other_user.save()
        session = ChatSession.objects.create(
            user=other_user, title="Private",
        )
        url = reverse("retrieval:api_session_detail", args=[session.id])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 404)


class ChatSourcesModalTests(_AuthenticatedTestCase):
    """Tests for chat sources modal: page structure, source data, entries API integration."""

    def test_chat_page_includes_sources_modal_elements(self):
        """Chat page template includes modal dialog and attachment preview modal."""
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="chat-sources-modal"', html)
        self.assertIn('id="chat-sources-prev"', html)
        self.assertIn('id="chat-sources-next"', html)
        self.assertIn('id="chat-sources-close"', html)
        self.assertIn('id="chat-attachment-preview-modal"', html)
        self.assertIn("retrieval/js/chat.js", html)

    def test_chat_page_with_sources_renders_data_entry_ids_for_modal(self):
        """When assistant message has source_entries, latest_session JSON includes entry_ids for modal."""
        session = ChatSession.objects.create(
            user=self.user, title="Sources test",
        )
        UserChatMessage.objects.create(
            session=session, content="When is coffee with Paulo?", sequence_index=1,
        )
        source_entries = [
            {"entry_id": "e1111111-1111-1111-1111-111111111111", "summary": "Coffee with Ana", "occurred_at": "2026-03-07T20:00:00"},
            {"entry_id": "e2222222-2222-2222-2222-222222222222", "summary": "Dinner with Paulo", "occurred_at": "2026-03-07T20:00:00"},
        ]
        AssistantChatMessage.objects.create(
            session=session,
            content="No coffee with Paulo. Dinner on March 7.",
            source_entries=json.dumps(source_entries),
            sequence_index=2,
        )
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("chat-latest-session", html)
        self.assertIn("e1111111-1111-1111-1111-111111111111", html)
        self.assertIn("e2222222-2222-2222-2222-222222222222", html)
        self.assertIn("Coffee with Ana", html)
        self.assertIn("Dinner with Paulo", html)

    def test_entries_api_ids_returns_same_count_as_chat_sources(self):
        """Entries API ?ids= returns entries matching chatbot source_entries for modal with full text."""
        from src.ingestion.models import IngestItem

        full_content_1 = "Full diary text about coffee with Ana"
        full_content_2 = "Full diary text about dinner with Paulo at Calypso"
        item1 = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Coffee with Ana",
            content_text=full_content_1,
        )
        item2 = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Dinner with Paulo",
            content_text=full_content_2,
        )
        source_ids = [str(item1.id), str(item2.id)]
        url = reverse("entries:api_list") + "?ids=" + ",".join(source_ids)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["entries"]), 2)
        self.assertEqual(data["total_count"], 2)
        ids_returned = [e["id"] for e in data["entries"]]
        self.assertEqual(set(ids_returned), set(source_ids))
        contents = {e["content_full"] for e in data["entries"]}
        self.assertIn(full_content_1, contents)
        self.assertIn(full_content_2, contents)
        for entry in data["entries"]:
            self.assertIn("content_full", entry)
            self.assertGreater(len(entry["content_full"]), 0)

    def test_chat_page_modal_close_is_button_not_link(self):
        """Modal close button is a button (no href); closing modal keeps user on chat page."""
        resp = self.client.get(reverse("retrieval:chat"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="chat-sources-close"', html)
        self.assertIn('type="button"', html)


class SessionMessagesApiTests(_AuthenticatedTestCase):
    """Tests for session_messages_api (GET /chat/api/sessions/<id>/messages/)."""

    def test_returns_messages(self):
        session = ChatSession.objects.create(
            user=self.user, title="Chat",
        )
        UserChatMessage.objects.create(
            session=session, content="Hello", sequence_index=1,
        )
        AssistantChatMessage.objects.create(
            session=session,
            content="Hi there",
            source_entries=json.dumps([{"entry_id": "abc"}]),
            sequence_index=2,
        )
        url = reverse("retrieval:api_session_messages", args=[session.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["messages"][0]["role"], "user")
        self.assertEqual(data["messages"][1]["role"], "assistant")

    def test_nonexistent_session_404(self):
        url = reverse("retrieval:api_session_messages", args=[uuid.uuid4()])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_other_users_session_404(self):
        other_user = CustomUser.objects.create_user(
            email="other3@example.com", password="Pass123",
        )
        other_user.is_email_verified = True
        other_user.save()
        session = ChatSession.objects.create(
            user=other_user, title="Private",
        )
        url = reverse("retrieval:api_session_messages", args=[session.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
