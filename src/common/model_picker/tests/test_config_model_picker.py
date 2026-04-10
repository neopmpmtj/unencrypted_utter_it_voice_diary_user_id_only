"""Tests for central LLM config (model_picker)."""

import os
from unittest.mock import patch

from django.test import TestCase

from src.accounts.models import GlobalSettings
from src.common.model_picker.config_model_picker import (
    ALL_GOALS,
    EMBEDDING,
    get_llm_config,
    reload_llm_config,
)


class ConfigModelPickerTests(TestCase):
    """Tests for get_llm_config and central LLM configuration."""

    def setUp(self):
        reload_llm_config()

    def tearDown(self):
        reload_llm_config()

    def test_get_llm_config_returns_defaults_when_no_file(self):
        """When JSON file is empty/missing, hardcoded defaults are used."""
        with patch(
            "src.common.model_picker.config_model_picker._load_json_overrides",
            return_value={},
        ):
            reload_llm_config()
            config = get_llm_config("batch_calendar")
            self.assertEqual(config["model"], "gpt-4.1-mini")
            self.assertEqual(config["temperature"], 0.3)
            self.assertEqual(config["max_tokens"], 4096)
            self.assertEqual(config["provider"], "openai")

    def test_get_llm_config_loads_from_json_when_present(self):
        """When llm_models.json exists with overrides, those values are used."""
        reload_llm_config()
        config = get_llm_config("batch_calendar")
        self.assertEqual(config["model"], "gpt-4.1-mini")
        self.assertEqual(config["temperature"], 0.3)
        self.assertEqual(config["max_tokens"], 4096)

    def test_get_llm_config_loads_from_globalsettings(self):
        """When GlobalSettings has llm.* keys, those values are used."""
        GlobalSettings.set_value("llm.batch_calendar.model", "gemini-2.0-flash")
        GlobalSettings.set_value("llm.batch_calendar.temperature", 0.5)
        GlobalSettings.set_value("llm.batch_calendar.max_tokens", 2048)
        self.addCleanup(lambda: GlobalSettings.objects.filter(key__startswith="llm.").delete())
        reload_llm_config()
        config = get_llm_config("batch_calendar")
        self.assertEqual(config["model"], "gemini-2.0-flash")
        self.assertEqual(config["temperature"], 0.5)
        self.assertEqual(config["max_tokens"], 2048)

    def test_get_llm_config_env_overrides_globalsettings(self):
        """Environment variables override GlobalSettings and JSON."""
        GlobalSettings.set_value("llm.batch_calendar.model", "gemini-1.5-flash")
        self.addCleanup(lambda: GlobalSettings.objects.filter(key__startswith="llm.").delete())
        with patch.dict(
            os.environ,
            {
                "CENTRAL_LLM_BATCH_CALENDAR_MODEL": "gemini-2.0-flash",
                "CENTRAL_LLM_BATCH_CALENDAR_TEMPERATURE": "0.5",
                "CENTRAL_LLM_BATCH_CALENDAR_MAX_TOKENS": "2048",
            },
            clear=False,
        ):
            reload_llm_config()
            config = get_llm_config("batch_calendar")
            self.assertEqual(config["model"], "gemini-2.0-flash")
            self.assertEqual(config["temperature"], 0.5)
            self.assertEqual(config["max_tokens"], 2048)

    def test_get_llm_config_embedding_returns_expected_structure(self):
        """Embedding goal returns model and provider; temp/max_tokens are placeholders."""
        config = get_llm_config(EMBEDDING)
        self.assertEqual(config["model"], "text-embedding-3-small")
        self.assertEqual(config["provider"], "openai")
        self.assertIn("temperature", config)
        self.assertIn("max_tokens", config)

    def test_all_goals_have_valid_config(self):
        """Every goal returns a non-empty config with required keys."""
        for goal in ALL_GOALS:
            config = get_llm_config(goal)
            self.assertIsInstance(config, dict, f"Goal {goal} returned non-dict")
            self.assertIn("model", config, f"Goal {goal} missing 'model'")
            self.assertIn("temperature", config, f"Goal {goal} missing 'temperature'")
            self.assertIn("max_tokens", config, f"Goal {goal} missing 'max_tokens'")
            self.assertIn("provider", config, f"Goal {goal} missing 'provider'")
            self.assertTrue(config["model"], f"Goal {goal} has empty model")
            self.assertIn(
                config["provider"],
                ("openai", "gemini"),
                f"Goal {goal} has invalid provider",
            )
