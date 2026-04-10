"""
Financial formatter services: LLM-based enhancement of formatted financial text.
"""

import logging
from typing import Optional

# [Google Gemini API — google-genai library imports]
# from google import genai
# from google.genai import types
from openai import OpenAI

from src.common.logging_utils.logging_config import get_logger

from .config_financial_formatter.financial_formatter_config import (
    FinancialFormatterConfig,
    get_financial_formatter_config,
)

logger = get_logger("financial_formatter")


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from response."""
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _format_record_for_fallback(record):
    """Format record as bullet list for fallback when LLM is disabled or fails."""
    from src.financial_parser.services import format_financial_for_display
    return format_financial_for_display(record)


def enhance_financial_display(
    record,
    config: Optional[FinancialFormatterConfig] = None,
) -> tuple[str, dict]:
    """
    Run financial record through Gemini to improve display appearance.

    Accepts a FinancialRecord (with items). Builds structured input and uses
    enhanced prompt for grouping by merchant/category, hierarchy, etc.

    Returns (enhanced_text, usage_dict) on success, or (fallback_text, {}) on failure.
    """
    if config is None:
        config = get_financial_formatter_config()

    fallback = _format_record_for_fallback(record)

    if not config.enabled:
        return fallback, {}

    if not config.openai_api_key:
        logger.debug("Financial formatter: no API key, returning raw format")
        return fallback, {}

    if not record.items.exists():
        return fallback, {}

    try:
        prompt = config.get_prompt_from_record(record)
        # [Google Gemini API — Gemini client and generate_content call]
        # client = genai.Client(api_key=config.gemini_api_key)
        client = OpenAI(api_key=config.openai_api_key, timeout=60.0)
        response = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        usage = response.usage
        usage_dict = {
            "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "output": getattr(usage, "completion_tokens", 0) if usage else 0,
            "total": getattr(usage, "total_tokens", 0) if usage else 0,
        }
        enhanced = (response.choices[0].message.content or "").strip()
        if enhanced:
            enhanced = _strip_markdown_fences(enhanced)
            return (enhanced if enhanced else fallback), usage_dict
        return fallback, usage_dict
    except Exception as e:
        logger.warning("Financial formatter enhancement failed, using raw format: %s", e)

    return fallback, {}
