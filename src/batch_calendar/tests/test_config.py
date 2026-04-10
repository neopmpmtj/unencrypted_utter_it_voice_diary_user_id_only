"""Tests for batch_calendar config."""

from django.test import TestCase

from src.batch_calendar.config_batch_calendar.batch_calendar_config import (
    BATCH_PROMPT_TEMPLATE,
    BatchCalendarConfig,
    get_batch_calendar_config,
)


class BatchCalendarConfigTests(TestCase):
    """Tests for BatchCalendarConfig and config helpers."""

    def test_prompt_template_includes_events_key(self):
        self.assertIn("events", BATCH_PROMPT_TEMPLATE)
        self.assertIn("{content_text}", BATCH_PROMPT_TEMPLATE)
        self.assertIn("{system_date}", BATCH_PROMPT_TEMPLATE)
        self.assertIn("{system_time}", BATCH_PROMPT_TEMPLATE)

    def test_get_batch_calendar_config_returns_config_object(self):
        config = get_batch_calendar_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.model, "gpt-4.1-mini")
        self.assertEqual(config.temperature, 0.3)
        self.assertEqual(config.max_tokens, 4096)
        self.assertEqual(config.default_timezone, "Europe/Lisbon")

    def test_get_prompt_includes_content_and_date(self):
        config = get_batch_calendar_config()
        prompt = config.get_prompt("book physio Mon-Fri at 5pm", "2026-02-24", "10:00:00")
        self.assertIn("book physio Mon-Fri at 5pm", prompt)
        self.assertIn("2026-02-24", prompt)
        self.assertIn("10:00:00", prompt)
        self.assertIn("Europe/Lisbon", prompt)
