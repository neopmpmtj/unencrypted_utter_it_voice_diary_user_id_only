"""Model-level tests for the conversation summarizer app skeleton."""

import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.conversation_summarizer.models import (
    ConversationSummary,
    ConversationType,
    SummaryAgentConfig,
    SummaryExample,
    SummaryPromptTemplate,
    SummaryRun,
    SummaryStatus,
)


class SummaryAgentConfigTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="summarizer-config@example.com", password="pw"
        )

    def test_global_config_and_per_user_override_resolution(self):
        global_cfg = SummaryAgentConfig.objects.create(is_global=True, model="global-model")
        self.assertEqual(SummaryAgentConfig.get_for_user(self.user).pk, global_cfg.pk)

        override = SummaryAgentConfig.objects.create(user=self.user, model="user-model")
        self.assertEqual(SummaryAgentConfig.get_for_user(self.user).pk, override.pk)
        # A different user still falls back to the global row.
        other = CustomUser.objects.create_user(email="other@example.com", password="pw")
        self.assertEqual(SummaryAgentConfig.get_for_user(other).pk, global_cfg.pk)

    def test_defaults_are_sane(self):
        cfg = SummaryAgentConfig.objects.create(is_global=True)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.cap_seconds, 240)
        self.assertEqual(cfg.quiet_seconds, 600)
        self.assertAlmostEqual(cfg.cap_tolerance, 0.5)

    def test_only_one_global_row(self):
        SummaryAgentConfig.objects.create(is_global=True)
        with self.assertRaises(Exception):
            SummaryAgentConfig.objects.create(is_global=True)


class SummaryPromptTemplateTests(TestCase):
    def test_seeded_default_template_and_examples_exist(self):
        """The data migration ships an editable default prompt + few-shot examples."""
        template = SummaryPromptTemplate.objects.get(name="default")
        self.assertTrue(template.is_default)
        self.assertTrue(template.is_active)
        self.assertEqual(template.version, 1)
        self.assertIn("JSON", template.system_prompt)
        self.assertIn("{{transcript}}", template.user_prompt)
        labels = list(template.examples.values_list("label", flat=True))
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("meeting" in label for label in labels))
        self.assertTrue(any("brainstorm" in label for label in labels))

    def test_version_increments_only_when_prompt_text_changes(self):
        template = SummaryPromptTemplate.objects.create(
            name="version-test", system_prompt="sys", user_prompt="{{transcript}}"
        )
        self.assertEqual(template.version, 1)

        template.is_active = False
        template.save()
        template.refresh_from_db()
        self.assertEqual(template.version, 1, "non-prompt edits must not bump the version")

        template.system_prompt = "sys v2"
        template.save()
        template.refresh_from_db()
        self.assertEqual(template.version, 2)

    def test_unknown_placeholder_is_rejected(self):
        template = SummaryPromptTemplate(
            name="bad", system_prompt="sys", user_prompt="{{transcript}} {{nonsense}}"
        )
        with self.assertRaises(ValidationError):
            template.full_clean()

    def test_allowed_placeholders_pass(self):
        template = SummaryPromptTemplate(
            name="good",
            system_prompt="Summarize {{user_name}}'s talk",
            user_prompt="{{transcript}} ({{clip_count}} clips, {{duration_minutes}} min, "
                        "{{started_at}} → {{ended_at}}, {{language}})",
        )
        template.full_clean()  # must not raise

    def test_get_for_type_falls_back_to_default(self):
        # The seed migration ships a default template into every test transaction;
        # remove it so "fall back to the default" is deterministic.
        SummaryPromptTemplate.objects.all().delete()
        default = SummaryPromptTemplate.objects.create(
            name="fallback-default", is_default=True, system_prompt="s", user_prompt="{{transcript}}"
        )
        SummaryPromptTemplate.objects.create(
            name="meeting", conversation_type=ConversationType.MEETING,
            system_prompt="s", user_prompt="{{transcript}}",
        )
        self.assertEqual(
            SummaryPromptTemplate.get_for_type(ConversationType.BRAINSTORM).pk, default.pk
        )
        self.assertEqual(
            SummaryPromptTemplate.get_for_type(ConversationType.MEETING).name, "meeting"
        )

    def test_examples_are_ordered(self):
        template = SummaryPromptTemplate.objects.create(
            name="t", system_prompt="s", user_prompt="{{transcript}}"
        )
        SummaryExample.objects.create(template=template, input_excerpt="b", expected_output="{}", sort_order=2)
        SummaryExample.objects.create(template=template, input_excerpt="a", expected_output="{}", sort_order=1)
        self.assertEqual([e.input_excerpt for e in template.examples.all()], ["a", "b"])


class ConversationSummaryTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="summarizer-summary@example.com", password="pw"
        )

    def test_one_summary_per_user_and_group(self):
        group = uuid.uuid4()
        ConversationSummary.objects.create(user=self.user, recording_group_id=group)
        with self.assertRaises(Exception):
            ConversationSummary.objects.create(user=self.user, recording_group_id=group)

    def test_revision_and_status_lifecycle(self):
        group = uuid.uuid4()
        summary = ConversationSummary.objects.create(
            user=self.user,
            recording_group_id=group,
            clip_count=4,
            total_duration_seconds=877,
            started_at=timezone.now(),
            ended_at=timezone.now(),
            summary_text="A summary",
            conversation_type=ConversationType.MEETING,
        )
        self.assertEqual(summary.status, SummaryStatus.PENDING)
        self.assertEqual(summary.revision, 1)

        summary.status = SummaryStatus.READY
        summary.revision = 2
        summary.save()
        summary.refresh_from_db()
        self.assertEqual(summary.status, SummaryStatus.READY)
        self.assertEqual(summary.revision, 2)

    def test_summary_run_audit_row(self):
        run = SummaryRun.objects.create(
            user=self.user,
            model="gpt-4.1-mini",
            input_chars=1234,
            tokens_in=300,
            tokens_out=120,
            latency_ms=2100,
        )
        self.assertTrue(run.ok)
