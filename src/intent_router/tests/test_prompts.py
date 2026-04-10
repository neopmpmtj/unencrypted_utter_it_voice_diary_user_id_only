"""Tests for intent_router.prompts."""

from django.test import TestCase

from src.intent_router.prompts import TRIAGE_SYSTEM_PROMPT


class TriageSystemPromptTests(TestCase):
    """Tests for TRIAGE_SYSTEM_PROMPT."""

    def test_prompt_contains_all_intents(self):
        for intent in ("task", "event", "collection", "finance", "note", "other"):
            self.assertIn(intent, TRIAGE_SYSTEM_PROMPT)

    def test_prompt_contains_json_schema_keys(self):
        for key in (
            "primary_route",
            "confidence",
            "contains_time_reference",
            "contains_multiple_items",
        ):
            self.assertIn(key, TRIAGE_SYSTEM_PROMPT)
