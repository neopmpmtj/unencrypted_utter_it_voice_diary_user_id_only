"""Tests for src.common.ai_client — JSON fence stripping, retry logic, usage extraction."""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.common.ai_client import _strip_json_fences, call_llm_json


class StripJsonFencesTests(TestCase):
    """Tests for _strip_json_fences."""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        self.assertEqual(_strip_json_fences(raw), raw)

    def test_fenced_json(self):
        raw = '```json\n{"key": "value"}\n```'
        self.assertEqual(_strip_json_fences(raw), '{"key": "value"}')

    def test_fenced_no_lang_tag(self):
        raw = '```\n{"key": "value"}\n```'
        self.assertEqual(_strip_json_fences(raw), '{"key": "value"}')

    def test_whitespace_around_fences(self):
        raw = '  ```json\n{"a": 1}\n```  '
        self.assertEqual(_strip_json_fences(raw), '{"a": 1}')

    def test_nested_backticks_in_content(self):
        raw = '```json\n{"code": "use `backticks`"}\n```'
        self.assertEqual(_strip_json_fences(raw), '{"code": "use `backticks`"}')


class CallLlmJsonTests(TestCase):
    """Tests for call_llm_json."""

    def _mock_response(self, content: str, prompt_tokens=10, completion_tokens=5, total_tokens=15):
        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        usage.total_tokens = total_tokens
        choice = MagicMock()
        choice.message.content = content
        response = MagicMock()
        response.choices = [choice]
        response.usage = usage
        return response

    @patch("src.common.ai_client.OpenAI")
    def test_returns_parsed_json_and_usage(self, mock_openai_cls):
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.return_value = self._mock_response('{"result": "ok"}')

        parsed, usage = call_llm_json("sys", "usr", {"model": "gpt-4o-mini"}, api_key="key")

        self.assertEqual(parsed, {"result": "ok"})
        self.assertEqual(usage["input"], 10)
        self.assertEqual(usage["output"], 5)
        self.assertEqual(usage["total"], 15)

    @patch("src.common.ai_client.OpenAI")
    def test_strips_fences_before_parsing(self, mock_openai_cls):
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.return_value = self._mock_response(
            '```json\n{"fenced": true}\n```'
        )

        parsed, _ = call_llm_json("sys", "usr", {}, api_key="key")
        self.assertEqual(parsed, {"fenced": True})

    @patch("src.common.ai_client.time.sleep")
    @patch("src.common.ai_client.OpenAI")
    def test_retries_on_json_error(self, mock_openai_cls, mock_sleep):
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.side_effect = [
            self._mock_response("not json"),
            self._mock_response('{"ok": true}'),
        ]

        parsed, _ = call_llm_json("sys", "usr", {}, api_key="key", max_retries=1)
        self.assertEqual(parsed, {"ok": True})
        mock_sleep.assert_called_once()

    @patch("src.common.ai_client.time.sleep")
    @patch("src.common.ai_client.OpenAI")
    def test_raises_after_exhausted_retries(self, mock_openai_cls, mock_sleep):
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.return_value = self._mock_response("bad")

        with self.assertRaises(json.JSONDecodeError):
            call_llm_json("sys", "usr", {}, api_key="key", max_retries=1)

    @patch("src.common.ai_client.get_openai_api_key", return_value="from-config")
    @patch("src.common.ai_client.OpenAI")
    def test_uses_get_openai_api_key_when_none(self, mock_openai_cls, mock_get_key):
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.return_value = self._mock_response('{}')

        call_llm_json("sys", "usr", {})

        mock_get_key.assert_called_once()
        mock_openai_cls.assert_called_once_with(api_key="from-config", timeout=60.0)
