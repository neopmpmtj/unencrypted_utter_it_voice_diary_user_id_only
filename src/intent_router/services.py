"""
Intent Router Services

LLM-based triage routing for voice diary utterances.
Determines the primary route (task|event|collection|finance|note|other).
"""

import logging
from typing import Optional

from src.common.ai_client import call_llm_json
from src.common.model_picker import get_llm_config

from .prompts import TRIAGE_SYSTEM_PROMPT
from .schemas import TriageResult

logger = logging.getLogger(__name__)

VALID_ROUTES = {"task", "event", "collection", "finance", "note", "other"}


def route_utterance(
    text: str,
    title: str = "",
    context_hint: Optional[str] = None,
    user=None,
    ingest_item=None,
) -> TriageResult:
    """
    Call the triage LLM to decide the primary route for an utterance.

    Returns a TriageResult with primary_route, confidence, and signal flags.
    Falls back to a safe 'note' route on any failure.

    Args:
        text: Input text (or JSON string for structured input).
        title: Optional title.
        context_hint: Optional hint prepended to the prompt (e.g. for JSON invoice routing).
        user: Optional user for API usage logging.
        ingest_item: Optional IngestItem for API usage logging.
    """
    user_prompt = text.strip()
    if context_hint:
        user_prompt = f"{context_hint}\n\n{user_prompt}"
    if title:
        user_prompt = f"Title: {title}\n\n{user_prompt}"

    try:
        cfg = get_llm_config("intent_triage")
        raw, usage_dict = call_llm_json(TRIAGE_SYSTEM_PROMPT, user_prompt, cfg, timeout=30.0)

        if user:
            from src.ingestion.tasks import log_api_usage
            model = cfg.get("model", "gpt-4o-mini")
            log_api_usage(
                user, model, "input_tokens", usage_dict.get("input", 0),
                ingest_item=ingest_item, origin="invoice_parser_intent",
            )
            log_api_usage(
                user, model, "output_tokens", usage_dict.get("output", 0),
                ingest_item=ingest_item, origin="invoice_parser_intent",
            )
    except Exception as exc:
        logger.error("route_utterance failed, defaulting to 'note': %s", exc)
        return TriageResult(
            primary_route="note",
            confidence=0.0,
            contains_time_reference=False,
            contains_multiple_items=False,
            raw_response={"error": str(exc)},
        )

    primary_route = str(raw.get("primary_route", "note")).lower()
    if primary_route not in VALID_ROUTES:
        logger.warning("Unexpected primary_route %r from triage LLM, defaulting to 'note'", primary_route)
        primary_route = "note"

    return TriageResult(
        primary_route=primary_route,
        confidence=float(raw.get("confidence", 0.5)),
        contains_time_reference=bool(raw.get("contains_time_reference", False)),
        contains_multiple_items=bool(raw.get("contains_multiple_items", False)),
        raw_response=raw,
    )
