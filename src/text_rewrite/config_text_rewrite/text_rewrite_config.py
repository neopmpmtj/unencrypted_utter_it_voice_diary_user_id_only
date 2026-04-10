"""
Standalone configuration for the text_rewrite module.

Self-contained Pydantic config that reads from environment variables.
Uses central LLM config (src.common.model_picker) for model defaults when available.

Env-var priority (highest wins):
  1. REWRITE_MODEL, REWRITE_TEMPERATURE, …  (module-specific)
  2. AI_OPENAI_API_KEY / OPENAI_API_KEY      (shared API key)
  3. Central LLM config (llm_models.json / CENTRAL_LLM_*)
  4. Defaults defined below
"""

import functools
from pathlib import Path
from typing import Dict

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("normal_input_rewrite")
    _DEFAULT_MODEL = _central.get("model", "gpt-4o")
    _DEFAULT_TEMPERATURE = _central.get("temperature", 0.0)
    _DEFAULT_MAX_TOKENS = _central.get("max_tokens", 800)
except Exception:
    _DEFAULT_MODEL = "gpt-4o"
    _DEFAULT_TEMPERATURE = 0.0
    _DEFAULT_MAX_TOKENS = 800


def _default_env_path() -> str:
    """Path to .env in project root (parent of src/)."""
    return str(Path(__file__).resolve().parent.parent.parent.parent / ".env")


_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
)

PROMPT_TEMPLATES: Dict[str, Dict] = {
    "grammar": {
        "label": "Grammar",
        "prompt": (
            _LANGUAGE_INSTRUCTION
            + "Polish and improve the grammar, spelling, and clarity of the following text. "
            + "Keep the original meaning and tone, but make it more professional and readable.\n\n"
            + "Text: {text}\n\n"
            + "Polished Text:"
        ),
    },
    "professional": {
        "label": "Professional",
        "prompt": (
            _LANGUAGE_INSTRUCTION
            + "Convert the following text to a more professional tone while maintaining the core message. "
            + "Make it suitable for business communication.\n\n"
            + "Text: {text}\n\n"
            + "Professional Version:"
        ),
    },
    "casual": {
        "label": "Casual",
        "prompt": (
            _LANGUAGE_INSTRUCTION
            + "Convert the following text to a more casual, friendly tone while keeping the main points. "
            + "Make it sound conversational and approachable.\n\n"
            + "Text: {text}\n\n"
            + "Casual Version:"
        ),
    },
    "to-llm": {
        "label": "LLM-Friendly",
        "prompt": (
            _LANGUAGE_INSTRUCTION
            + "Convert the following text to a more LLM-friendly format. "
            + "Make it suitable for an assistant coding LLM input.\n\n"
            + "Text: {text}\n\n"
            + "Assistant Coding Version:"
        ),
    },
    "story": {
        "label": "Story",
        "prompt": (
            _LANGUAGE_INSTRUCTION
            + "Write a short fictional story with the inputted text\n\n"
            + "Text: {text}\n\n"
            + "Story:"
        ),
    },
    "fairytale": {
        "label": "Fairy Tale",
        "prompt": (
            _LANGUAGE_INSTRUCTION
            + "Write a fairy tale story with the inputted text as inspiration\n\n"
            + "Text: {text}\n\n"
            + "Fairy Tale:"
        ),
    },
}

DEFAULT_TEMPLATE = "grammar"


class RewriteConfig(BaseSettings):
    """Self-contained configuration for the text_rewrite module."""

    openai_api_key: str = Field(
        default="",
        description="OpenAI API key",
        validation_alias=AliasChoices("AI_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    model: str = Field(
        default=_DEFAULT_MODEL,
        description="OpenAI model for text rewriting",
    )
    temperature: float = Field(
        default=_DEFAULT_TEMPERATURE,
        description="Temperature for rewrite (0 = deterministic)",
    )
    max_tokens: int = Field(
        default=_DEFAULT_MAX_TOKENS,
        description="Maximum tokens in rewrite response",
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

    class Config:
        env_prefix = "REWRITE_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"


@functools.lru_cache(maxsize=1)
def get_rewrite_config() -> RewriteConfig:
    """Return a cached RewriteConfig instance."""
    return RewriteConfig()


def get_available_templates() -> list[dict[str, str]]:
    """Return list of {name, label} dicts for the frontend template picker."""
    return [
        {"name": name, "label": info["label"]}
        for name, info in PROMPT_TEMPLATES.items()
    ]
