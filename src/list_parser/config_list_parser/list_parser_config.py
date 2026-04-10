"""
Standalone configuration for the list_parser module.

Self-contained Pydantic config that reads from environment variables.
Uses central LLM config (src.common.model_picker) for model defaults when available.

Env-var priority (highest wins):
  1. LIST_PARSER_MODEL, LIST_PARSER_TEMPERATURE, ...  (module-specific)
  2. OPENAI_API_KEY                                    (OpenAI API key)
  3. Central LLM config (llm_models.json / CENTRAL_LLM_*)
  4. Defaults defined below
"""

import functools
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("list_parser")
    _DEFAULT_MODEL = _central.get("model", "gpt-4.1-mini")
    _DEFAULT_TEMPERATURE = _central.get("temperature", 0.1)
    _DEFAULT_MAX_OUTPUT_TOKENS = _central.get("max_output_tokens", 4096)
except Exception:
    # [Google Gemini API — default model]
    # _DEFAULT_MODEL = "gemini-2.5-flash"
    _DEFAULT_MODEL = "gpt-4.1-mini"
    _DEFAULT_TEMPERATURE = 0.1
    _DEFAULT_MAX_OUTPUT_TOKENS = 4096


def _default_env_path() -> str:
    """Path to .env in project root (parent of src/)."""
    return str(Path(__file__).resolve().parent.parent.parent.parent / ".env")

_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
)

LIST_PARSER_PROMPT_TEMPLATE = (
    _LANGUAGE_INSTRUCTION
    + "You are an assistant specialized in extracting structured item lists from natural language input.\n"
    "Your task is to identify exactly ONE list from the given text and return it as a structured JSON object.\n\n"

    "System date (reference for 'today'): {system_date}\n"
    "System time (reference for 'now'): {system_time}\n\n"

    "Text to analyze:\n"
    "Title: {title}\n"
    "Content: {content_text}\n\n"

    "EXTRACTION RULES:\n"
    "1) Extract exactly ONE named list from the text.\n"
    "2) Infer the list name from context (e.g. 'compras' from 'lista de compras: leite, pão').\n"
    "   If no name can be inferred, use 'itens'.\n"
    "2a) 'list_context': ALWAYS extract when the user mentions an occasion, event, or purpose for the list. Examples: \"guests list for John's birthday party\" -> list_context=\"John's birthday party\"; \"lista da festa da Joana\" -> list_context=\"festa da Joana\"; \"shopping for the weekend\" -> list_context=\"the weekend\". Use empty string only when no such context is mentioned. This is NOT the same as item 'description'.\n"
    "3) Each item must be a separate dictionary with 'text', 'description', 'due_date', 'quantity', and 'unit' fields.\n"
    "4) 'text': the primary content of the item (required, concise).\n"
    "5) 'description': optional qualitative detail about the item (e.g. 'integral' for bread, 'fresco' for cheese). NEVER put measurement units here; use 'unit' instead. Empty string if none.\n"
    "6) 'due_date': optional date in YYYY-MM-DD format (null if not mentioned).\n"
    "   - Relative dates ('amanhã', 'próxima segunda') must be converted using the system date.\n"
    "   - Never set a due_date in the past relative to system date.\n"
    "7) 'quantity': optional numeric quantity when mentioned (null if not applicable).\n"
    "   - Extract when the text explicitly states amounts (e.g. '2 leite', '3 kg farinha', '1.5 litros de água').\n"
    "   - Use a number only (e.g. 2, 3, 1.5).\n"
    "   - Omit (null) when no quantity is stated.\n"
    "8) 'unit': measurement unit ONLY when the text states a quantity with a unit (e.g. '2 kg farinha' -> unit 'kg'). Use ONLY the canonical names from the UNITS section below. NEVER put units in 'description'. Use empty string or null when no unit applies.\n"
    "{units_section}\n"
    "9) Preserve the original language of the items (do NOT translate).\n"
    "10) Handle various list formats: comma-separated, bullet points, numbered lists, colon-separated.\n"
    "11) If the text contains narrative mixed with a list, extract only the list portion.\n"
    "12) Items should be in the same order as they appear in the text.\n"
    "13) SUBLISTS: When the text describes a list with sublists (e.g. 'Christmas shopping: Paul - book, shirt; John - mug, scarf'), extract a hierarchical structure:\n"
    "    - Create parent items for each sublist subject (Paul, John).\n"
    "    - Put their items in a 'children' array under that parent.\n"
    "    - Each parent and child must have the same fields: text, description, due_date, quantity, unit.\n"
    "    - Preserve order: parents in text order; children in text order within each parent.\n"
    "14) FLAT LISTS: When no sublists are present, use flat items (omit 'children' key).\n"
    "15) TRACEABILITY: The main list (list_name, list_context) is the root; all items (including nested) belong to this list. Parent items link sublists to the main subject.\n\n"

    "THREE DISTINCT FIELDS - do not confuse:\n"
    "- list_context: occasion/context for the WHOLE list (e.g. \"John's birthday party\").\n"
    "- description (per item): qualitative detail about THAT item only (e.g. \"integral\", \"fresco\"). NEVER units or quantities.\n"
    "- unit (per item): measurement only (kg, litre, etc.). If user says \"2 kg bananas\", put \"kg\" in unit, not in description.\n\n"

    "Respond ONLY with valid JSON (no markdown, no extra text).\n"
    "Exact format. quantity and unit may be null when not mentioned. 'children' is optional (omit for flat lists):\n"
    "{{\n"
    "  \"list_name\": \"inferred name\",\n"
    "  \"list_context\": \"occasion/event when mentioned, else empty string\",\n"
    "  \"items\": [\n"
    "    {{\"text\": \"item text\", \"description\": \"\", \"due_date\": null, \"quantity\": null, \"unit\": null}},\n"
    "    {{\"text\": \"parent\", \"description\": \"\", \"due_date\": null, \"quantity\": null, \"unit\": null, \"children\": [{{\"text\": \"child\", \"description\": \"\", \"due_date\": null, \"quantity\": null, \"unit\": null}}]}}\n"
    "  ]\n"
    "}}\n\n"

    "Example (flat): \"Shopping for the weekend: 2 kg bananas, pão integral, 3 leite\" -> list_name=\"shopping\", list_context=\"the weekend\", items: bananas (quantity=2, unit=kg), pão (description=integral), leite (quantity=3, unit=null).\n"
    "Example (hierarchical): \"Christmas shopping: Paul - book, shirt; John - mug\" -> list_name=\"Christmas shopping\", list_context=\"Christmas\", items: [{{\"text\":\"Paul\", \"children\":[{{\"text\":\"book\",...}},{{\"text\":\"shirt\",...}}]}}, {{\"text\":\"John\", \"children\":[{{\"text\":\"mug\",...}}]}}].\n\n"

    "If there is insufficient information to extract a list, return exactly:\n"
    "{{\"error\": \"Insufficient information to extract a list\"}}"
)


class ListParserConfig(BaseSettings):
    """Self-contained configuration for the list_parser module."""

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
        description="OpenAI model for list extraction",
    )
    temperature: float = Field(
        default=_DEFAULT_TEMPERATURE,
        description="Temperature for extraction (low for deterministic output)",
    )
    max_tokens: int = Field(
        default=_DEFAULT_MAX_OUTPUT_TOKENS,
        description="Maximum tokens in OpenAI response",
    )
    default_timezone: str = Field(
        default="Europe/Lisbon",
        description="Default timezone for due-date inference",
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
        env_prefix = "LIST_PARSER_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_prompt(self, title: str, content_text: str, system_date: str = "", system_time: str = "") -> str:
        """Generate the list extraction prompt with dynamic units from DB."""
        if not system_date:
            now = datetime.now()
            system_date = now.strftime("%Y-%m-%d")
            system_time = now.strftime("%H:%M:%S")
        example_date = system_date

        from src.list_parser.unit_utils import get_units_for_prompt
        units_section = get_units_for_prompt()

        return LIST_PARSER_PROMPT_TEMPLATE.format(
            system_date=system_date,
            system_time=system_time,
            example_date=example_date,
            title=title or "",
            content_text=content_text or "",
            units_section=units_section,
        )


@functools.lru_cache(maxsize=1)
def get_list_parser_config() -> ListParserConfig:
    """Return a cached ListParserConfig instance."""
    return ListParserConfig()
