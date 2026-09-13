"""
Admin tests for the conversation summarizer.

Covers the "Preview on my last session" action: it must show the rendered prompt
and the model's reply, audit the call, and — critically — write NO
ConversationSummary (it is for iterating on prompts, not for producing data).
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.conversation_summarizer.models import (
    ConversationSummary,
    SummaryPromptTemplate,
    SummaryRun,
)
from src.ingestion.models import IngestItem

PAYLOAD = (
    '{"type": "brainstorm", "title": "Preview title", "summary": "Preview summary text", '
    '"key_points": ["one"], "decisions": [], "action_items": [], "people": [], "language": "en"}'
)


def _fake_llm(*args, **kwargs):
    return PAYLOAD, {"input_tokens": 11, "output_tokens": 22}


class PromptPreviewActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = CustomUser.objects.create_superuser(
            email="summarizer-admin@example.com", password="***"
        )
        prefs = UserPreferences.objects.get(user=self.admin_user)
        prefs.enable_conversation_summary = True
        prefs.save(update_fields=["enable_conversation_summary"])
        self.client.force_login(self.admin_user)
        self.template = SummaryPromptTemplate.objects.get(name="default")
        self.changelist_url = reverse(
            "admin:conversation_summarizer_summaryprompttemplate_changelist"
        )

    def _make_session(self):
        group_id = uuid.uuid4()
        IngestItem.objects.create(
            user=self.admin_user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now() - timedelta(minutes=30),
            content_text="A long spoken transcript about planning. " * 10,
            recording_duration_seconds=240,
            recording_group_id=group_id,
        )
        IngestItem.objects.create(
            user=self.admin_user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now() - timedelta(minutes=25),
            content_text="And then we wrapped up with a tail clip.",
            recording_duration_seconds=157,
            recording_group_id=group_id,
        )
        return group_id

    def _run_action(self):
        return self.client.post(
            self.changelist_url,
            {
                "action": "preview_on_last_session",
                "_selected_action": [str(self.template.id)],
            },
            follow=True,
        )

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_preview_shows_prompt_and_reply_without_writing_a_summary(self):
        self._make_session()

        response = self._run_action()

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Preview summary text", content)
        self.assertIn("What the model received", content)
        self.assertIn("--- Clip 1/2", content, "the rendered prompt must be shown")

        self.assertFalse(
            ConversationSummary.objects.exists(),
            "a preview must never create a summary row",
        )
        run = SummaryRun.objects.get()
        self.assertEqual(run.kind, "preview")
        self.assertTrue(run.ok)
        self.assertEqual(run.tokens_in, 11)
        self.assertEqual(run.tokens_out, 22)

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_preview_without_any_session_is_handled_gracefully(self):
        response = self._run_action()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SummaryRun.objects.exists())
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("no recorded conversations" in m for m in messages),
            f"expected a warning message, got: {messages}",
        )

    @patch("src.conversation_summarizer.services.call_llm", _fake_llm)
    def test_preview_requires_exactly_one_template(self):
        self._make_session()
        other = SummaryPromptTemplate.objects.create(
            name="second-template", system_prompt="s", user_prompt="{{transcript}}"
        )

        response = self.client.post(
            self.changelist_url,
            {
                "action": "preview_on_last_session",
                "_selected_action": [str(self.template.id), str(other.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SummaryRun.objects.exists())
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("exactly one template" in m for m in messages), messages)

    @patch(
        "src.conversation_summarizer.services.call_llm",
        side_effect=RuntimeError("model unreachable"),
    )
    def test_preview_surfaces_model_errors(self, _mock):
        self._make_session()

        response = self._run_action()

        self.assertEqual(response.status_code, 200)
        self.assertIn("model unreachable", response.content.decode())
        self.assertFalse(SummaryRun.objects.get().ok)
        self.assertFalse(ConversationSummary.objects.exists())


class AdminRegistrationTests(TestCase):
    def test_all_models_are_registered(self):
        from django.contrib import admin as django_admin

        from src.conversation_summarizer.models import (
            ConversationSummary,
            SummaryAgentConfig,
            SummaryExample,
            SummaryPromptTemplate,
            SummaryRun,
        )

        for model in (
            SummaryAgentConfig,
            SummaryPromptTemplate,
            SummaryExample,
            ConversationSummary,
            SummaryRun,
        ):
            self.assertIn(model, django_admin.site._registry, f"{model.__name__} is not in admin")
