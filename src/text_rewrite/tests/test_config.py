"""
Logic-only tests for text_rewrite config.
"""

from django.test import TestCase

from src.text_rewrite.config_text_rewrite.text_rewrite_config import (
    PROMPT_TEMPLATES,
    DEFAULT_TEMPLATE,
    get_rewrite_config,
    get_available_templates,
    _LANGUAGE_INSTRUCTION,
)


class RewriteConfigTests(TestCase):
    """Tests for RewriteConfig and config helpers."""

    def test_default_template_is_grammar(self):
        self.assertEqual(DEFAULT_TEMPLATE, "grammar")

    def test_prompt_templates_has_all_expected_keys(self):
        expected = {"grammar", "professional", "casual", "to-llm", "story", "fairytale"}
        self.assertEqual(set(PROMPT_TEMPLATES.keys()), expected)

    def test_each_template_has_label_and_prompt(self):
        for name, info in PROMPT_TEMPLATES.items():
            self.assertIn("label", info, f"Template {name} missing 'label'")
            self.assertIn("prompt", info, f"Template {name} missing 'prompt'")
            self.assertIsInstance(info["label"], str)
            self.assertIsInstance(info["prompt"], str)

    def test_each_prompt_includes_language_instruction(self):
        for name, info in PROMPT_TEMPLATES.items():
            self.assertIn(
                _LANGUAGE_INSTRUCTION.strip()[:50],
                info["prompt"],
                f"Template {name} prompt missing language instruction",
            )

    def test_each_prompt_formats_with_text_placeholder(self):
        for name, info in PROMPT_TEMPLATES.items():
            formatted = info["prompt"].format(text="Hello world")
            self.assertIn("Hello world", formatted, f"Template {name} did not interpolate {{text}}")

    def test_get_available_templates_returns_list_of_dicts(self):
        templates = get_available_templates()
        self.assertIsInstance(templates, list)
        self.assertEqual(len(templates), len(PROMPT_TEMPLATES))
        for t in templates:
            self.assertIn("name", t)
            self.assertIn("label", t)
            self.assertEqual(t["label"], PROMPT_TEMPLATES[t["name"]]["label"])

    def test_get_rewrite_config_returns_config_object(self):
        config = get_rewrite_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.model, "gpt-4o")
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(config.max_tokens, 800)
