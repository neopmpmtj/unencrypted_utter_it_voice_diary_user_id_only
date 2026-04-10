"""
Logic-only tests for text_rewrite services.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.text_rewrite.config_text_rewrite.text_rewrite_config import PROMPT_TEMPLATES
from src.text_rewrite.services import rewrite_text, get_openai_client


class RewriteTextLogicTests(TestCase):
    """Tests for rewrite_text logic (no real LLM calls)."""

    def test_rewrite_text_raises_for_unknown_template(self):
        with self.assertRaises(ValueError) as ctx:
            rewrite_text("Hello", template_name="nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("Available:", str(ctx.exception))

    def test_rewrite_text_uses_default_template_when_none_given(self):
        with patch("src.text_rewrite.services.get_openai_client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "Polished text"
            mock_resp.usage.prompt_tokens = 10
            mock_resp.usage.completion_tokens = 5
            mock_resp.usage.total_tokens = 15

            mock_client.return_value.chat.completions.create.return_value = mock_resp

            with patch("src.text_rewrite.services.get_rewrite_config") as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.model = "gpt-4o"
                mock_cfg.temperature = 0.0
                mock_cfg.max_tokens = 800
                mock_cfg.max_retries = 0
                mock_config.return_value = mock_cfg

                result, tokens = rewrite_text("Hello world", template_name=None)

        self.assertEqual(result, "Polished text")
        self.assertEqual(tokens["input"], 10)
        self.assertEqual(tokens["output"], 5)
        self.assertEqual(tokens["total"], 15)

    def test_rewrite_text_passes_correct_prompt_to_openai(self):
        with patch("src.text_rewrite.services.get_openai_client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "Result"
            mock_resp.usage.prompt_tokens = 1
            mock_resp.usage.completion_tokens = 1
            mock_resp.usage.total_tokens = 2

            mock_client.return_value.chat.completions.create.return_value = mock_resp

            with patch("src.text_rewrite.services.get_rewrite_config") as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.model = "gpt-4o"
                mock_cfg.temperature = 0.0
                mock_cfg.max_tokens = 800
                mock_cfg.max_retries = 0
                mock_config.return_value = mock_cfg

                rewrite_text("Olá mundo", template_name="grammar")

        call_kwargs = mock_client.return_value.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        prompt = messages[0]["content"]
        self.assertIn("Olá mundo", prompt)
        self.assertIn("IMPORTANT: Respond in the same language", prompt)
