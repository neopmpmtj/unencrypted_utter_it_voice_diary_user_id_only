"""
Shared AI/LLM client utilities.

Single source of truth for OpenAI API key retrieval and JSON-returning LLM calls.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def get_openai_api_key() -> str:
    """Retrieve the OpenAI API key from config or environment."""
    try:
        from src.common.config import get_config
        return get_config().ai.openai_api_key
    except Exception:
        from decouple import config
        return config("AI_OPENAI_API_KEY", default="") or config("OPENAI_API_KEY", default="")


def _strip_json_fences(text: str) -> str:
    """Remove markdown JSON code fences from LLM output."""
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    model_config: dict,
    api_key: str | None = None,
    timeout: float = 60.0,
    max_retries: int = 2,
    retry_delay: float = 2.0,
) -> tuple[dict, dict]:
    """
    Call the OpenAI API and parse a JSON response.

    Returns (parsed_json, usage_dict).
    Raises on persistent failure.
    """
    if api_key is None:
        api_key = get_openai_api_key()

    client = OpenAI(api_key=api_key, timeout=timeout)
    delay = retry_delay
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)

            response = client.chat.completions.create(
                model=model_config.get("model", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=model_config.get("temperature", 0.0),
                max_tokens=model_config.get("max_tokens", 2000),
            )

            raw = response.choices[0].message.content.strip()
            parsed = json.loads(_strip_json_fences(raw))
            usage = response.usage
            usage_dict = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total": getattr(usage, "total_tokens", 0) if usage else 0,
            }
            return parsed, usage_dict

        except json.JSONDecodeError as e:
            logger.warning("LLM returned invalid JSON (attempt %d): %s", attempt + 1, e)
            last_exc = e
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                logger.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
            else:
                logger.error("LLM call failed after %d attempts: %s", max_retries + 1, e)

    raise last_exc  # type: ignore[misc]
