"""Tests for intent_router.schemas."""

from django.test import TestCase

from src.intent_router.schemas import TriageResult


class TriageResultSchemaTests(TestCase):
    """Tests for TriageResult dataclass."""

    def test_triage_result_dataclass_creation(self):
        result = TriageResult(
            primary_route="event",
            confidence=0.9,
            contains_time_reference=True,
            contains_multiple_items=False,
            raw_response={"primary_route": "event"},
        )
        self.assertEqual(result.primary_route, "event")
        self.assertEqual(result.confidence, 0.9)
        self.assertTrue(result.contains_time_reference)
        self.assertFalse(result.contains_multiple_items)
        self.assertEqual(result.raw_response, {"primary_route": "event"})

    def test_triage_result_equality(self):
        a = TriageResult(
            primary_route="note",
            confidence=0.5,
            contains_time_reference=False,
            contains_multiple_items=False,
            raw_response={},
        )
        b = TriageResult(
            primary_route="note",
            confidence=0.5,
            contains_time_reference=False,
            contains_multiple_items=False,
            raw_response={},
        )
        self.assertEqual(a, b)
