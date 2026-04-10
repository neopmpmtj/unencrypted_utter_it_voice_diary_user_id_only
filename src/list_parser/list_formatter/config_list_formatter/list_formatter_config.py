"""
Standalone configuration for the list_formatter module.

Uses OpenAI to enhance formatted list text for display.
Reuses OPENAI_API_KEY from list_parser.
Uses central LLM config (src.common.model_picker) for model defaults when available.
"""

import functools
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("list_formatter")
    _DEFAULT_MODEL = _central.get("model", "gpt-4.1-mini")
    _DEFAULT_TEMPERATURE = _central.get("temperature", 0.2)
    _DEFAULT_MAX_OUTPUT_TOKENS = _central.get("max_output_tokens", 2048)
except Exception:
    # [Google Gemini API — default model]
    # _DEFAULT_MODEL = "gemini-2.5-flash"
    _DEFAULT_MODEL = "gpt-4.1-mini"
    _DEFAULT_TEMPERATURE = 0.2
    _DEFAULT_MAX_OUTPUT_TOKENS = 2048


def _default_env_path() -> str:
    """Path to .env in project root (parent of src/)."""
    return str(Path(__file__).resolve().parent.parent.parent.parent.parent / ".env")


_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
)

_LIST_FORMATTER_PROMPT_TEMPLATE = _LANGUAGE_INSTRUCTION + (
    "You are an assistant that improves the visual presentation of bullet-point lists.\n"
    "Your task is to take the raw list text below and enhance its structure and readability for display.\n\n"
    "RULES:\n"
    "1) Preserve ALL content; do not add, remove, or change any items.\n"
    "2) Preserve the original language (no translation).\n"
    "3) Improve visual structure: when semantic groups exist (e.g. participants vs picnic items), "
    "organize them with clearer sections or indentation.\n"
    "4) Use consistent bullet formatting and spacing for readability.\n"
    "5) Return plain text only. No JSON, no markdown code fences, no extra commentary.\n\n"
    "Input list:\n"
    "{raw_text}\n\n"
    "Enhanced list (plain text only):"
)


class ListFormatterConfig(BaseSettings):
    """Self-contained configuration for the list_formatter module."""

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
        description="OpenAI model for list display enhancement",
    )
    temperature: float = Field(
        default=_DEFAULT_TEMPERATURE,
        description="Temperature for enhancement (slightly higher for variety)",
    )
    max_tokens: int = Field(
        default=_DEFAULT_MAX_OUTPUT_TOKENS,
        description="Maximum tokens in OpenAI response",
    )
    enabled: bool = Field(
        default=True,
        description="Whether to run LLM enhancement (disable to use raw format)",
    )

    class Config:
        env_prefix = "LIST_FORMATTER_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_prompt(self, raw_text: str) -> str:
        """Generate the enhancement prompt."""
        return _LIST_FORMATTER_PROMPT_TEMPLATE.format(raw_text=raw_text or "")


@functools.lru_cache(maxsize=1)
def get_list_formatter_config() -> ListFormatterConfig:
    """Return a cached ListFormatterConfig instance."""
    return ListFormatterConfig()
