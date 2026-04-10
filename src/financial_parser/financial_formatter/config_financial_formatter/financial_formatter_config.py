"""
Standalone configuration for the financial_formatter module.

Uses OpenAI to enhance formatted financial text for display.
Reuses OPENAI_API_KEY from financial_parser.
"""

import functools
import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("financial_formatter")
    _DEFAULT_MODEL = _central.get("model", "gpt-4.1-mini")
    _DEFAULT_TEMPERATURE = _central.get("temperature", 0.2)
    _DEFAULT_MAX_OUTPUT_TOKENS = _central.get("max_output_tokens", 4096)
except Exception:
    # [Google Gemini API — default model]
    # _DEFAULT_MODEL = "gemini-2.5-flash"
    _DEFAULT_MODEL = "gpt-4.1-mini"
    _DEFAULT_TEMPERATURE = 0.2
    _DEFAULT_MAX_OUTPUT_TOKENS = 4096

def _default_env_path() -> str:
    """Path to .env in project root."""
    return str(Path(__file__).resolve().parent.parent.parent.parent.parent / ".env")


_FINANCIAL_FORMATTER_PROMPT_TEMPLATE_RAW = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
    "You are an assistant that improves the visual presentation of financial lists (expenses and income).\n"
    "Your task is to take the raw financial text below and enhance its structure and readability for display.\n\n"
    "RULES:\n"
    "1) Preserve ALL content; do not add, remove, or change any items or amounts.\n"
    "2) Preserve the original language (no translation).\n"
    "3) Improve visual structure: when semantic groups exist (e.g. expenses vs income, by category, by date), "
    "organize them with clearer sections or indentation.\n"
    "4) Use consistent bullet formatting and spacing for readability.\n"
    "5) Return plain text only. No JSON, no markdown code fences, no extra commentary.\n\n"
    "Input financial list:\n"
    "{raw_text}\n\n"
    "Enhanced list (plain text only):"
)

_FINANCIAL_FORMATTER_PROMPT_TEMPLATE_STRUCTURED = (
    "IMPORTANT: Respond in the same language as the input. Do not translate.\n\n"
    "You are an expert at formatting financial lists for clear, readable display.\n\n"
    "INPUT (structured financial data):\n"
    "{structured_input}\n\n"
    "TASK: Produce a nicely formatted plain-text display. Output ONLY the formatted text, no JSON, no markdown fences, no commentary.\n\n"
    "RULES:\n"
    "1) Preserve ALL items and amounts exactly. Do not add, remove, or change any values.\n"
    "2) Group items by merchant when multiple items share the same merchant. Use a section header for the merchant name.\n"
    "3) Group by category when items share a category and it aids readability.\n"
    "4) Separate expenses from income if both exist; use clear section labels.\n"
    "5) Use hierarchy: section headers for groups, indentation (2 spaces) for sub-items.\n"
    "6) Use consistent bullet style (- or *) and spacing between sections.\n"
    "7) Format amounts as: amount currency (e.g. 10.00 EUR). Keep currency with each amount.\n"
    "8) If record_context exists, include it as a subtitle or second line under the title.\n"
    "9) Preserve the original language for all labels and descriptions.\n\n"
    "FORMATTED OUTPUT (plain text only):"
)


def _serialize_record_for_prompt(record: Any) -> str:
    """Serialize FinancialRecord + items to JSON for the LLM prompt."""
    items: List[Dict[str, Any]] = []
    for fi in record.items.order_by("item_index"):
        items.append({
            "type": fi.type or "expense",
            "description": fi.description or "",
            "merchant": fi.merchant or "",
            "category": fi.category or "",
            "amount": float(fi.amount),
            "currency": fi.currency or "EUR",
        })
    data = {
        "record_name": record.record_name or "Despesas",
        "record_context": record.record_context or "",
        "items": items,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


class FinancialFormatterConfig(BaseSettings):
    """Self-contained configuration for the financial_formatter module."""

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
        description="OpenAI model for financial display enhancement",
    )
    temperature: float = Field(
        default=_DEFAULT_TEMPERATURE,
        description="Temperature for enhancement",
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
        env_prefix = "FINANCIAL_FORMATTER_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_prompt(self, raw_text: str) -> str:
        """Generate the enhancement prompt from raw text (legacy)."""
        return _FINANCIAL_FORMATTER_PROMPT_TEMPLATE_RAW.format(raw_text=raw_text or "")

    def get_prompt_from_record(self, record: Any) -> str:
        """Generate the enhancement prompt from a FinancialRecord."""
        structured_input = _serialize_record_for_prompt(record)
        return _FINANCIAL_FORMATTER_PROMPT_TEMPLATE_STRUCTURED.format(structured_input=structured_input)


@functools.lru_cache(maxsize=1)
def get_financial_formatter_config() -> FinancialFormatterConfig:
    """Return a cached FinancialFormatterConfig instance."""
    return FinancialFormatterConfig()
