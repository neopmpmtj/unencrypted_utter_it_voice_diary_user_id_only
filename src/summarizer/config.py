"""
Standalone configuration for the summarizer module — v14.

Self-contained Pydantic config that reads from environment variables.
Uses central LLM config (src.common.model_picker) for model defaults when available.

Env-var priority (highest wins):
  1. SUMMARIZER_MODEL, SUMMARIZER_TEMPERATURE, ...  (module-specific)
  2. AI_OPENAI_API_KEY / OPENAI_API_KEY      (shared API key)
  3. Central LLM config (llm_models.json / CENTRAL_LLM_*)
  4. Defaults defined below

v14 change: topics and facets removed; taxonomy dimensions replace them.
"""


import functools
from pathlib import Path
from typing import Dict

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

from src.common.model_picker import get_llm_config

_central = get_llm_config("semantic_search_summarizer")
_DEFAULT_MODEL = _central.get("model", "gpt-4o")
_DEFAULT_TEMPERATURE = _central.get("temperature", 0.0)
_DEFAULT_MAX_TOKENS = _central.get("max_tokens", 800)

_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
)

PROMPT_TEMPLATES: Dict[str, Dict] = {
    "semantic_search_summarizer": {
        "label": "Summarizer",
        "prompt": _LANGUAGE_INSTRUCTION + (
            "You are an assistant that generates machine-readable summaries for semantic search.\n"
            "Inputs:\n"
            '- `classification`: a string tag for the entry type.\n'
            "- `utterance`: the user's raw text (may be brief or empty).\n"
            "- `list_items`: an array of strings representing list items (may be empty).\n"
            "- `financial_items`: an array of dicts with keys (merchant, category, description, amount, currency) (may be empty).\n"
            "Task:\n"
            "1. Review `classification`, `utterance`, `list_items`, and `financial_items`. "
            "Use **only** the information provided; do not add external knowledge.\n"
            "2. Create a concise, structured summary that captures all relevant details. "
            "Condense information while preserving the main topics and key details.\n"
            "3. Incorporate important keywords from the original text; do not speculate.\n"
            "4. Produce a valid JSON object (double-quoted keys and string values, no trailing commas):\n"
            "{\n"
            '  "summary": "<concise summary capturing key details and keywords>",\n'
            '  "keywords": [/* important keywords extracted from the text */]\n'
            "}\n"
        ),
    }
}
