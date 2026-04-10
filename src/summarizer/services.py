"""
Summarizer Service — v14

Calls the LLM to produce a machine-readable summary and keywords for an entry,
used by the retrieval indexing pipeline.

Topics and facets are no longer produced here — taxonomy dimensions replace them.
"""

import json
import logging
import time

from decouple import config
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import PROMPT_TEMPLATES, _DEFAULT_MAX_TOKENS, _DEFAULT_MODEL, _DEFAULT_TEMPERATURE

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_RETRY_DELAY = 2.0
_RETRY_BACKOFF = 2.0


def _get_api_key() -> str:
    from src.common.ai_client import get_openai_api_key
    return get_openai_api_key()


def summarize_for_search(
    content_text: str,
    title: str,
    classification: str,
    list_items: Optional[List[str]] = None,
    financial_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Generate a search summary via LLM.

    Returns dict with keys: summary, keywords, usage.
    Raises on persistent failure.
    """
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("OpenAI API key is required for summarization")

    system_prompt = PROMPT_TEMPLATES["semantic_search_summarizer"]["prompt"]

    utterance = f"{title}\n{content_text}".strip() if title else content_text

    user_payload = json.dumps(
        {
            "classification": classification,
            "utterance": utterance,
            "list_items": list_items or [],
            "financial_items": financial_items or [],
        },
        ensure_ascii=False,
    )

    model = config("SUMMARIZER_MODEL", default=_DEFAULT_MODEL)
    temperature = float(config("SUMMARIZER_TEMPERATURE", default=str(_DEFAULT_TEMPERATURE)))
    max_tokens = int(config("SUMMARIZER_MAX_TOKENS", default=str(_DEFAULT_MAX_TOKENS)))

    client = OpenAI(api_key=api_key, timeout=60.0)
    delay = _RETRY_DELAY
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            if attempt > 0:
                logger.debug("Summarizer retry %d/%d after %.1fs", attempt, _MAX_RETRIES, delay)
                time.sleep(delay)
                delay = min(delay * _RETRY_BACKOFF, 30.0)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.strip("`").removeprefix("json").strip()

            result = json.loads(raw)
            usage = response.usage
            usage_dict = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total": getattr(usage, "total_tokens", 0) if usage else 0,
            }
            return {
                "summary": result.get("summary", ""),
                "keywords": result.get("keywords", []),
                "usage": usage_dict,
            }

        except json.JSONDecodeError as e:
            logger.warning("Summarizer returned invalid JSON (attempt %d): %s", attempt + 1, e)
            last_exc = e
        except Exception as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                logger.warning("Summarizer API call failed (attempt %d): %s", attempt + 1, e)
            else:
                logger.error("Summarizer failed after %d attempts: %s", _MAX_RETRIES + 1, e)

    raise last_exc  # type: ignore[misc]
