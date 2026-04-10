"""
List formatter services: LLM-based enhancement of formatted list text.
"""

import logging
from typing import Optional

# [Google Gemini API — google-genai library imports]
# from google import genai
# from google.genai import types
from openai import OpenAI

from src.common.logging_utils.logging_config import get_logger

from .config_list_formatter.list_formatter_config import (
    ListFormatterConfig,
    get_list_formatter_config,
)

logger = get_logger("list_formatter")


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


def enhance_list_display(
    raw_text: str,
    config: Optional[ListFormatterConfig] = None,
) -> tuple[str, dict]:
    """
    Run raw list text through Gemini to improve appearance.

    Returns (enhanced_text, usage_dict) on success, or (raw_text, {}) on failure (graceful fallback).
    usage_dict has keys: input, output, total.
    """
    if config is None:
        config = get_list_formatter_config()

    if not config.enabled:
        return raw_text, {}

    if not config.openai_api_key:
        logger.debug("List formatter: no API key, returning raw text")
        return raw_text, {}

    if not raw_text or not raw_text.strip():
        return raw_text, {}

    try:
        prompt = config.get_prompt(raw_text)
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
            return (enhanced if enhanced else raw_text), usage_dict
        return raw_text, usage_dict
    except Exception as e:
        logger.warning("List formatter enhancement failed, using raw text: %s", e)

    return raw_text, {}
