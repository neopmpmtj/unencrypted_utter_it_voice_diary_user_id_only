"""Tests for intent_router.services.route_utterance."""

from unittest.mock import patch

from django.test import TestCase

from src.intent_router.services import route_utterance


class RouteUtteranceTests(TestCase):
    """Tests for route_utterance."""

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_llm_success_returns_triage_result(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "event",
                "confidence": 0.9,
                "contains_time_reference": True,
                "contains_multiple_items": False,
            },
            {},
        )
        result = route_utterance("Meeting tomorrow at 3pm")
        self.assertEqual(result.primary_route, "event")
        self.assertEqual(result.confidence, 0.9)
        self.assertTrue(result.contains_time_reference)
        self.assertFalse(result.contains_multiple_items)
        self.assertIn("primary_route", result.raw_response)
        self.assertEqual(result.raw_response["primary_route"], "event")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_all_valid_routes_accepted(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        for route in ("task", "event", "collection", "finance", "note", "other"):
            mock_call_llm.return_value = (
                {
                    "primary_route": route,
                    "confidence": 0.8,
                    "contains_time_reference": False,
                    "contains_multiple_items": False,
                },
                {},
            )
            result = route_utterance("sample text")
            self.assertEqual(result.primary_route, route)

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_invalid_route_fallback_to_note(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "unknown",
                "confidence": 0.9,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        result = route_utterance("sample text")
        self.assertEqual(result.primary_route, "note")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_exception_fallback_to_note(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.side_effect = RuntimeError("API error")
        result = route_utterance("sample text")
        self.assertEqual(result.primary_route, "note")
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("error", result.raw_response)
        self.assertEqual(result.raw_response["error"], "API error")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_title_prepended_to_user_prompt(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "note",
                "confidence": 0.5,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        route_utterance("body", title="My Title")
        call_args = mock_call_llm.call_args
        user_prompt = call_args[0][1]
        self.assertEqual(user_prompt, "Title: My Title\n\nbody")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_empty_title_no_title_line(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "note",
                "confidence": 0.5,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        route_utterance("body", title="")
        call_args = mock_call_llm.call_args
        user_prompt = call_args[0][1]
        self.assertEqual(user_prompt, "body")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_text_stripped(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "note",
                "confidence": 0.5,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        route_utterance("  hello  ")
        call_args = mock_call_llm.call_args
        user_prompt = call_args[0][1]
        self.assertEqual(user_prompt, "hello")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_missing_optional_fields_defaults(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = ({"primary_route": "note"}, {})
        result = route_utterance("sample text")
        self.assertEqual(result.confidence, 0.5)
        self.assertFalse(result.contains_time_reference)
        self.assertFalse(result.contains_multiple_items)

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_primary_route_case_normalized(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "TASK",
                "confidence": 0.9,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        result = route_utterance("sample text")
        self.assertEqual(result.primary_route, "task")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_empty_text(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "note",
                "confidence": 0.5,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        result = route_utterance("")
        self.assertEqual(result.primary_route, "note")

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_unicode_text_passed_through(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "note",
                "confidence": 0.5,
                "contains_time_reference": False,
                "contains_multiple_items": False,
            },
            {},
        )
        text = "Reunião amanhã às 15h"
        route_utterance(text)
        call_args = mock_call_llm.call_args
        user_prompt = call_args[0][1]
        self.assertEqual(user_prompt, text)

    @patch("src.intent_router.services.call_llm_json")
    @patch("src.intent_router.services.get_llm_config")
    def test_context_hint_prepended_to_prompt(self, mock_get_cfg, mock_call_llm):
        mock_get_cfg.return_value = {"model": "gpt-4o-mini"}
        mock_call_llm.return_value = (
            {
                "primary_route": "finance",
                "confidence": 0.95,
                "contains_time_reference": False,
                "contains_multiple_items": True,
            },
            {"input": 50, "output": 20, "total": 70},
        )
        result = route_utterance(
            '{"vendor_name": "Test"}',
            context_hint="This is a JSON grocery invoice. Route as finance.",
        )
        self.assertEqual(result.primary_route, "finance")
        call_args = mock_call_llm.call_args
        user_prompt = call_args[0][1]
        self.assertIn("This is a JSON grocery invoice", user_prompt)
        self.assertIn('{"vendor_name": "Test"}', user_prompt)
