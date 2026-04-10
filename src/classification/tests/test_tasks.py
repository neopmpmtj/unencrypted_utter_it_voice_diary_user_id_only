"""Tests for v14 classification tasks, services, and models."""

import uuid
from unittest.mock import patch

from django.test import TestCase

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem

from src.classification.models import (
    ItemClassificationRun,
    ItemClassificationSelection,
    ItemEntityLink,
    TaxonomyNode,
    TaxonomyParserRoute,
)
from src.classification.services import classify_item
from src.classification.tests.test_services import _create_run_with_selections


def _valid_classifier_output(ingest_item_id: str) -> dict:
    """Minimal valid classifier output for tests (uses seed taxonomy keys)."""
    return {
        "ingest_item_id": ingest_item_id,
        "taxonomy_pack": "personal",
        "primary": {
            "subject_key": "personal.health.appointment.dentist",
            "intent_key": "intent.capture.note.freeform",
            "context_key": "context.self.daily.routine",
            "governance_key": "gov.personal.private.self_only",
        },
        "secondary": {
            "subject_keys": [],
            "intent_keys": [],
            "context_keys": [],
            "time_keys": [],
        },
        "entities": [],
        "actionability": {"is_actionable": False, "recommended_action_type": None, "urgency_level": None},
        "confidence": {"subject": 0.9, "intent": 0.9, "context": 0.9, "time": 0.0, "governance": 0.9, "overall": 0.9},
        "ambiguity": {"has_ambiguity": False, "notes": []},
        "reasoning": {
            "subject_reason": "Health appointment",
            "intent_reason": "Freeform note",
            "context_reason": "Daily routine",
            "time_reason": "",
            "governance_reason": "Personal",
        },
    }


class TaxonomyNodeSeedTests(TestCase):
    """Verify the seed migration created taxonomy nodes."""

    def test_personal_subject_nodes_exist(self):
        self.assertTrue(TaxonomyNode.objects.filter(key="personal.health.appointment.dentist").exists())

    def test_shared_intent_nodes_exist(self):
        self.assertTrue(TaxonomyNode.objects.filter(key="intent.capture.note.freeform").exists())

    def test_governance_nodes_exist(self):
        self.assertTrue(TaxonomyNode.objects.filter(key="gov.personal.private.self_only").exists())

    def test_enterprise_subject_nodes_exist(self):
        self.assertTrue(TaxonomyNode.objects.filter(key="enterprise.finance.accounts_payable.invoice").exists())


class TaxonomyParserRouteTests(TestCase):
    """Verify parser route seed data."""

    def test_calendar_route_exists(self):
        self.assertTrue(TaxonomyParserRoute.objects.filter(
            parser_action="calendar", is_active=True,
        ).exists())

    def test_financial_route_exists(self):
        self.assertTrue(TaxonomyParserRoute.objects.filter(
            parser_action="financial", is_active=True,
        ).exists())

    def test_list_route_exists(self):
        self.assertTrue(TaxonomyParserRoute.objects.filter(
            parser_action="list", is_active=True,
        ).exists())


class ClassifyItemVerifierDisabledTests(TestCase):
    """Tests that verifier is skipped when ENABLE_VERIFIER is False."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="verifier_test@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    @patch("src.classification.services.get_item_content")
    @patch("src.classification.services.call_llm_json")
    @patch("src.classification.config_taxonomy_classifier.ENABLE_VERIFIER", False)
    def test_classify_item_skips_verifier_when_disabled(self, mock_call_llm, mock_get_content):
        mock_get_content.return_value = "Dentist appointment next Tuesday"
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        classifier_output = _valid_classifier_output(str(item.id))
        mock_call_llm.return_value = (
            classifier_output,
            {"input": 100, "output": 50, "total": 150},
        )

        result = classify_item(item)

        self.assertEqual(result["usage"]["verifier"], {})
        mock_call_llm.assert_called_once()

    @patch("src.classification.services.get_item_content")
    @patch("src.classification.services.call_llm_json")
    @patch("src.classification.config_taxonomy_classifier.ENABLE_VERIFIER", False)
    def test_entity_with_role_null_creates_link_with_empty_role(self, mock_call_llm, mock_get_content):
        """Regression: LLM may return role: null; code must coerce to empty string."""
        mock_get_content.return_value = "Had coffee with Ana"
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        output = _valid_classifier_output(str(item.id))
        output["entities"] = [
            {"entity_type": "contacts", "raw_mention": "Ana", "role": None, "confidence": 0.9},
        ]
        mock_call_llm.return_value = (output, {"input": 100, "output": 50, "total": 150})

        result = classify_item(item)

        self.assertIsNotNone(result.get("run_id"))
        link = ItemEntityLink.objects.get(ingest_item=item)
        self.assertEqual(link.role, "")
        self.assertEqual(link.entity_type, "contact")
        self.assertEqual(link.raw_mention, "Ana")

    @patch("src.classification.services.get_item_content")
    @patch("src.classification.services.call_llm_json")
    @patch("src.classification.config_taxonomy_classifier.ENABLE_VERIFIER", False)
    def test_null_secondary_keys_does_not_crash(self, mock_call_llm, mock_get_content):
        """Regression: LLM may return subject_keys: null; code must not iterate over None."""
        mock_get_content.return_value = "Quick note"
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        output = _valid_classifier_output(str(item.id))
        output["secondary"] = {
            "subject_keys": None,
            "intent_keys": [],
            "context_keys": [],
            "time_keys": [],
        }
        mock_call_llm.return_value = (output, {"input": 100, "output": 50, "total": 150})

        result = classify_item(item)

        self.assertIsNotNone(result.get("run_id"))


class ClassifyItemTaskDispatchCalendarTests(TestCase):
    """Tests that classify_item_task dispatches calendar parser when route matches."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="dispatch_calendar@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        self.run = _create_run_with_selections(self.item, [
            ("intent", "intent.task.create.todo"),
            ("subject", "personal.daily.diary"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])

    @patch("src.batch_calendar.tasks.parse_batch_calendar_task")
    @patch("src.classification.tasks.classify_item")
    @patch("src.intent_router.services.route_utterance")
    def test_dispatch_parsers_calendar_queues_task(self, mock_triage, mock_classify, mock_parse_calendar):
        from src.intent_router.schemas import TriageResult
        mock_triage.return_value = TriageResult(
            primary_route="event",
            confidence=0.92,
            contains_time_reference=True,
            contains_multiple_items=False,
            raw_response={"primary_route": "event", "confidence": 0.92,
                          "contains_time_reference": True,
                          "contains_multiple_items": False},
        )
        mock_classify.return_value = {
            "run_id": str(self.run.id),
            "usage": {"classifier": {}, "verifier": {}},
        }
        from src.classification.tasks import classify_item_task

        classify_item_task(str(self.item.id), "plain text", "en")

        mock_parse_calendar.delay.assert_called_once_with(
            str(self.item.id),
            "plain text",
            "en",
        )
