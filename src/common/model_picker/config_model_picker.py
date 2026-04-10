"""
Centralized LLM configuration for all API goals.

Load order per setting (highest wins):
  1. Module-specific env vars (BATCH_CAL_MODEL, etc.) - applied in consuming modules
  2. GlobalSettings (llm.<goal>.<attr>) - admin-editable in Django admin
  3. Central env vars (CENTRAL_LLM_<GOAL>_MODEL, etc.)
  4. llm_models.json (if file exists and key present)
  5. Hardcoded defaults below

LLM goal identifiers and their purpose:
  - batch_calendar: OpenAI. Extracts multiple calendar events from natural language.
  - voice_transcription: OpenAI. Transcribes audio to text (Whisper-style).
  - voice_translation: OpenAI. Translates transcribed text to target language.
  - normal_input_rewrite: OpenAI. Rewrites/polishes text (grammar, tone, etc.).
  - list_parser: OpenAI. Extracts structured list items from natural language.
  - list_formatter: OpenAI. Enhances list display formatting.
  - taxonomy_classifier: OpenAI. v14 primary classifier: maps entries to hierarchical taxonomy.
  - taxonomy_verifier: OpenAI. v14 verifier/reviewer: validates and corrects classifier output.
  - financial_parser: OpenAI. Extracts expenses/income from natural language.
  - financial_formatter: OpenAI. Enhances financial list display formatting.
  - todo_parser: OpenAI. Extracts structured to-do/task items from natural language.
  - semantic_search_summarizer: OpenAI. Generates search summaries/keywords for the retrieval index.
  - diary_chat: OpenAI. Answers user questions about their diary entries (RAG chatbot).
  - embedding: OpenAI. Vector embeddings for semantic search (query + index).
  - intent_triage: OpenAI. Routes utterances to the correct specialist parser before taxonomy classification.
  - invoice_parser_pdf: OpenAI. Extracts structured invoice data from PDF attachments (Responses API).
  - invoice_parser_image: OpenAI. Extracts structured invoice data from image attachments (future).
"""

import json
from functools import lru_cache

from decouple import config as config_from_env
from pathlib import Path
from typing import Any, Dict

# Goal identifiers (use these when calling get_llm_config)
BATCH_CALENDAR = "batch_calendar"
VOICE_TRANSCRIPTION = "voice_transcription"
VOICE_TRANSLATION = "voice_translation"
NORMAL_INPUT_REWRITE = "normal_input_rewrite"
LIST_PARSER = "list_parser"
LIST_FORMATTER = "list_formatter"
TAXONOMY_CLASSIFIER = "taxonomy_classifier"
TAXONOMY_VERIFIER = "taxonomy_verifier"
FINANCIAL_PARSER = "financial_parser"
FINANCIAL_FORMATTER = "financial_formatter"
TODO_PARSER = "todo_parser"
SEMANTIC_SEARCH_SUMMARIZER = "semantic_search_summarizer"
DIARY_CHAT = "diary_chat"
EMBEDDING = "embedding"
INTENT_TRIAGE = "intent_triage"
INVOICE_PARSER_PDF = "invoice_parser_pdf"
INVOICE_PARSER_IMAGE = "invoice_parser_image"

ALL_GOALS = [
    BATCH_CALENDAR,
    VOICE_TRANSCRIPTION,
    VOICE_TRANSLATION,
    NORMAL_INPUT_REWRITE,
    LIST_PARSER,
    LIST_FORMATTER,
    TAXONOMY_CLASSIFIER,
    TAXONOMY_VERIFIER,
    FINANCIAL_PARSER,
    FINANCIAL_FORMATTER,
    TODO_PARSER,
    SEMANTIC_SEARCH_SUMMARIZER,
    DIARY_CHAT,
    EMBEDDING,
    INTENT_TRIAGE,
    INVOICE_PARSER_PDF,
    INVOICE_PARSER_IMAGE,
]

# Hardcoded defaults (provider, model, temperature, max_tokens)
# Gemini modules use max_output_tokens; we expose as max_tokens for consistency.
_DEFAULTS: Dict[str, Dict[str, Any]] = {
    BATCH_CALENDAR: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    VOICE_TRANSCRIPTION: {
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "temperature": 0.0,
        "max_tokens": 0,
    },
    VOICE_TRANSLATION: {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    NORMAL_INPUT_REWRITE: {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.0,
        "max_tokens": 800,
    },
    LIST_PARSER: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.1,
        "max_tokens": 4096,
    },
    LIST_FORMATTER: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.2,
        "max_tokens": 2048,
    },
    TAXONOMY_CLASSIFIER: {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_tokens": 2000,
    },
    TAXONOMY_VERIFIER: {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_tokens": 2000,
    },
    FINANCIAL_PARSER: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.1,
        "max_tokens": 4096,
    },
    FINANCIAL_FORMATTER: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.2,
        "max_tokens": 2048,
    },
    TODO_PARSER: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.1,
        "max_tokens": 4096,
    },
    SEMANTIC_SEARCH_SUMMARIZER: {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.0,
        "max_tokens": 800,
    },
    DIARY_CHAT: {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.3,
        "max_tokens": 2048,
    },
    EMBEDDING: {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "temperature": 0.0,
        "max_tokens": 0,
    },
    INTENT_TRIAGE: {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.0,
        "max_tokens": 512,
    },
    INVOICE_PARSER_PDF: {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.0,
        "max_tokens": 4096,
    },
    INVOICE_PARSER_IMAGE: {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.0,
        "max_tokens": 4096,
    },
}


def _json_path() -> Path:
    """Path to llm_models.json in the same directory as this module."""
    return Path(__file__).resolve().parent / "llm_models.json"


def _load_json_overrides() -> Dict[str, Dict[str, Any]]:
    """Load overrides from llm_models.json if present. Returns {} on failure."""
    path = _json_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load_globalsettings_overrides() -> Dict[str, Dict[str, Any]]:
    """
    Load overrides from GlobalSettings (admin-editable).
    Keys: llm.<goal>.<attr> e.g. llm.batch_calendar.model
    Returns {} on failure (e.g. during migrations).
    """
    try:
        from src.accounts.models import GlobalSettings

        rows = GlobalSettings.objects.filter(key__startswith="llm.").values_list("key", "value")
        result: Dict[str, Dict[str, Any]] = {}
        for key, value in rows:
            parts = key.split(".")
            if len(parts) != 3 or parts[0] != "llm":
                continue
            goal, attr = parts[1], parts[2]
            if goal not in result:
                result[goal] = {}
            if attr == "model" and isinstance(value, str):
                result[goal]["model"] = value
            elif attr == "temperature":
                try:
                    result[goal]["temperature"] = float(value)
                except (TypeError, ValueError):
                    pass
            elif attr in ("max_tokens", "max_output_tokens"):
                try:
                    result[goal]["max_tokens"] = int(value)
                except (TypeError, ValueError):
                    pass
        return result
    except Exception:
        return {}


def _env_key(goal: str, key: str) -> str:
    """Environment variable name for a goal/setting. e.g. CENTRAL_LLM_BATCH_CALENDAR_MODEL."""
    goal_upper = goal.upper().replace("-", "_")
    return f"CENTRAL_LLM_{goal_upper}_{key.upper()}"


def _get_merged_config(goal: str) -> Dict[str, Any]:
    """Merge defaults, JSON, GlobalSettings, and env vars. Returns full config for goal."""
    if goal not in _DEFAULTS:
        return {}
    base = dict(_DEFAULTS[goal])
    overrides = _load_json_overrides()
    if goal in overrides:
        for k, v in overrides[goal].items():
            if k in ("model", "temperature", "max_tokens", "max_output_tokens"):
                if k == "max_output_tokens":
                    base["max_tokens"] = v
                else:
                    base[k] = v
    gs_overrides = _load_globalsettings_overrides()
    if goal in gs_overrides:
        for k, v in gs_overrides[goal].items():
            if k in ("model", "temperature", "max_tokens"):
                base[k] = v
    model_env = config_from_env(_env_key(goal, "model"), default="")
    if model_env:
        base["model"] = model_env
    temp_env = config_from_env(_env_key(goal, "temperature"), default="")
    if temp_env:
        try:
            base["temperature"] = float(temp_env)
        except ValueError:
            pass
    tokens_env = config_from_env(_env_key(goal, "max_tokens"), default="")
    if tokens_env:
        try:
            base["max_tokens"] = int(tokens_env)
        except ValueError:
            pass
    max_out_env = config_from_env(_env_key(goal, "max_output_tokens"), default="")
    if max_out_env:
        try:
            base["max_tokens"] = int(max_out_env)
        except ValueError:
            pass
    return base


@lru_cache(maxsize=1)
def _get_all_configs() -> Dict[str, Dict[str, Any]]:
    """Load and cache all goal configs (JSON and env read once)."""
    return {goal: _get_merged_config(goal) for goal in ALL_GOALS}


def get_llm_config(goal: str) -> Dict[str, Any]:
    """
    Return LLM config for the given goal.

    Args:
        goal: One of BATCH_CALENDAR, VOICE_TRANSCRIPTION, VOICE_TRANSLATION,
              NORMAL_INPUT_REWRITE, LIST_PARSER, LIST_FORMATTER,
              TAXONOMY_CLASSIFIER, TAXONOMY_VERIFIER,
              FINANCIAL_PARSER, FINANCIAL_FORMATTER,
              TODO_PARSER, SEMANTIC_SEARCH_SUMMARIZER, DIARY_CHAT,
              EMBEDDING, INTENT_TRIAGE, INVOICE_PARSER_PDF,
              INVOICE_PARSER_IMAGE.

    Returns:
        Dict with keys: model, temperature, max_tokens, provider.
        For Gemini consumers, max_output_tokens equals max_tokens.
    """
    configs = _get_all_configs()
    if goal not in configs:
        return {}
    c = configs[goal]
    result = {
        "model": c.get("model", ""),
        "temperature": c.get("temperature", 0.0),
        "max_tokens": c.get("max_tokens", 0),
        "provider": c.get("provider", "openai"),
    }
    result["max_output_tokens"] = result["max_tokens"]
    return result


def reload_llm_config() -> None:
    """Clear cache so next get_llm_config() re-reads GlobalSettings, JSON, and env."""
    _get_all_configs.cache_clear()
