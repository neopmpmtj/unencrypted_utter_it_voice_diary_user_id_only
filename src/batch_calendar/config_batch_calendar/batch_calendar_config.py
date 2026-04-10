"""
Standalone configuration for the batch_calendar module.

Self-contained Pydantic config that reads from environment variables.
Uses central LLM config (src.common.model_picker) for model defaults when available.

Env-var priority (highest wins):
  1. BATCH_CAL_MODEL, BATCH_CAL_TEMPERATURE, ...  (module-specific)
  2. OPENAI_API_KEY                                (OpenAI API key)
  3. Central LLM config (llm_models.json / CENTRAL_LLM_*)
  4. Defaults defined below
"""

import functools
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("batch_calendar")
    _DEFAULT_MODEL = _central.get("model", "gpt-4.1-mini")
    _DEFAULT_TEMPERATURE = _central.get("temperature", 0.3)
    _DEFAULT_MAX_OUTPUT_TOKENS = _central.get("max_output_tokens", 4096)
except Exception:
    # [Google Gemini API — default model]
    # _DEFAULT_MODEL = "gemini-2.5-flash"
    _DEFAULT_MODEL = "gpt-4.1-mini"
    _DEFAULT_TEMPERATURE = 0.3
    _DEFAULT_MAX_OUTPUT_TOKENS = 4096


def _default_env_path() -> str:
    """Path to .env in project root (parent of src/)."""
    return str(Path(__file__).resolve().parent.parent.parent.parent / ".env")


_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
)

BATCH_PROMPT_TEMPLATE = _LANGUAGE_INSTRUCTION + (
    "You are an assistant specialized in extracting MULTIPLE calendar events from natural language requests.\n"
    "Your task is to transform the given text into a list of calendar events in Google Calendar API format.\n\n"

    "System date (reference for 'today'): {system_date}\n"
    "System time (reference for 'now'): {system_time}\n"
    "Default timezone: {default_timezone}\n"
    "Default duration (minutes): {default_duration_minutes}\n\n"

    "Text to analyze:\n"
    "{content_text}\n\n"

    "Extract and return ALL events implied by the text. Examples of multi-event patterns:\n"
    "- 'Monday through Friday at 5pm' -> 5 events (one per weekday)\n"
    "- 'every Tuesday and Thursday at 10am for the next 3 weeks' -> 6 events\n"
    "- 'physiotherapy on Feb 24, 25, 26 at 17:00' -> 3 events\n"
    "- 'daily at 9am from tomorrow for 5 days' -> 5 events\n\n"

    "Each event must have:\n"
    "- summary: Brief title (in the same language as the input)\n"
    "- description: Optional detailed description\n"
    "- location: If mentioned\n"
    "- start: {{dateTime: ISO 8601, timeZone: \"{default_timezone}\"}}\n"
    "- end: {{dateTime: ISO 8601, timeZone: \"{default_timezone}\"}}\n"
    "- reminders: {{useDefault: false, overrides: {reminders_json_example}}}\n\n"

    "DATE/TIME RULES (critical):\n"
    "1) Relative dates ('tomorrow', 'next Monday') must be converted to absolute dates using system date/time.\n"
    "2) Never schedule in the past relative to system date/time.\n"
    "3) If date is not mentioned, choose the next valid occurrence.\n"
    "4) If time is not mentioned, use 09:00.\n"
    "5) For ambiguous times without AM/PM, infer the NEXT future occurrence from system time.\n"
    "6) If inferred time for today has passed, advance to the next logical day.\n"
    "7) If duration is not mentioned, use {default_duration_minutes} minutes.\n\n"

    "REMINDERS:\n"
    "A) Identify explicitly mentioned reminders in the text.\n"
    "B) Convert to minutes: 1 hour=60, 1 day=1440, etc.\n"
    "C) If none mentioned, use: {reminders_description}\n"
    "D) For medical appointments without reminders: add 1 day before (1440).\n\n"

    "Respond ONLY with valid JSON (no markdown, no extra text).\n"
    "Exact format:\n"
    "{{\n"
    "  \"events\": [\n"
    "    {{\n"
    "      \"summary\": \"event title\",\n"
    "      \"description\": \"optional\",\n"
    "      \"location\": \"optional\",\n"
    "      \"start\": {{\"dateTime\": \"{example_date}T17:00:00\", \"timeZone\": \"{default_timezone}\"}},\n"
    "      \"end\": {{\"dateTime\": \"{example_date}T18:00:00\", \"timeZone\": \"{default_timezone}\"}},\n"
    "      \"reminders\": {{\"useDefault\": false, \"overrides\": {reminders_json_example}}}\n"
    "    }}\n"
    "  ]\n"
    "}}\n\n"

    "If there is insufficient information to create at least one event, return exactly:\n"
    "{{\"error\": \"Insufficient information to create calendar events\"}}"
)


class BatchCalendarConfig(BaseSettings):
    """Self-contained configuration for the batch_calendar module."""

    # [Google Gemini API — API key configuration for Gemini generative AI]
    # gemini_api_key: str = Field(
    #     default="",
    #     description="Google Gemini API key",
    #     validation_alias=AliasChoices("GOOGLE_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    # )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key",
        validation_alias=AliasChoices("AI_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    model: str = Field(
        default=_DEFAULT_MODEL,
        description="OpenAI model for batch event extraction",
    )
    temperature: float = Field(
        default=_DEFAULT_TEMPERATURE,
        description="Temperature for extraction (0.3 = slightly creative)",
    )
    max_tokens: int = Field(
        default=_DEFAULT_MAX_OUTPUT_TOKENS,
        description="Maximum tokens in OpenAI response",
    )
    default_timezone: str = Field(
        default="Europe/Lisbon",
        description="Default timezone for events",
    )
    default_duration_minutes: int = Field(
        default=30,
        description="Default event duration in minutes",
    )
    default_reminders: List[Dict[str, Any]] = Field(
        default_factory=lambda: [
            {"method": "popup", "minutes": 10},
            {"method": "popup", "minutes": 1440},
        ],
        description="Default reminder overrides",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum API call retries",
    )
    retry_delay: float = Field(
        default=1.0,
        description="Initial retry delay in seconds",
    )
    retry_backoff_factor: float = Field(
        default=2.0,
        description="Backoff multiplier between retries",
    )
    calendar_id: str = Field(
        default="primary",
        description="Google Calendar ID for insertion",
    )

    class Config:
        env_prefix = "BATCH_CAL_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_prompt(self, content_text: str, system_date: str, system_time: str, timezone: Optional[str] = None) -> str:
        """Generate the batch extraction prompt."""
        example_date = system_date
        effective_timezone = timezone if timezone else self.default_timezone
        reminder_descriptions = []
        for r in self.default_reminders:
            m = r.get("minutes", 0)
            if m < 60:
                reminder_descriptions.append(f"{m} minutes before")
            elif m == 60:
                reminder_descriptions.append("1 hour before")
            elif m < 1440:
                reminder_descriptions.append(f"{m // 60} hours before")
            elif m == 1440:
                reminder_descriptions.append("1 day before")
            else:
                reminder_descriptions.append(f"{m // 1440} days before")
        reminders_description = ", ".join(reminder_descriptions)
        reminders_json_example = json.dumps(self.default_reminders, ensure_ascii=False)

        return BATCH_PROMPT_TEMPLATE.format(
            system_date=system_date,
            system_time=system_time,
            example_date=example_date,
            default_timezone=effective_timezone,
            default_duration_minutes=self.default_duration_minutes,
            reminders_description=reminders_description,
            reminders_json_example=reminders_json_example,
            content_text=content_text or "",
        )


@functools.lru_cache(maxsize=1)
def get_batch_calendar_config() -> BatchCalendarConfig:
    """Return a cached BatchCalendarConfig instance."""
    return BatchCalendarConfig()
