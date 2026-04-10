"""
Standalone configuration for the financial_parser module.

Self-contained Pydantic config that reads from environment variables.
Uses central LLM config (src.common.model_picker) for model defaults when available.
"""

import functools
from datetime import datetime
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("financial_parser")
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

FINANCIAL_PARSER_PROMPT_TEMPLATE = (
    _LANGUAGE_INSTRUCTION
    + "You are an assistant specialized in extracting financial entries (expenses and income) from natural language input.\n"
    "Your task is to identify one or more financial entries from the given text and return them as a structured JSON object.\n\n"

    "System date (reference for 'today', 'yesterday'): {system_date}\n"
    "System time (reference for 'now'): {system_time}\n\n"

    "Text to analyze:\n"
    "Title: {title}\n"
    "Content: {content_text}\n\n"

    "EXTRACTION RULES:\n"
    "1) Extract all expenses and/or income mentioned in the text.\n"
    "2) Infer record_name from context (e.g. 'Despesas de hoje' from 'gastei 20 no café e 12 no almoço'). Use 'Despesas' or 'Receitas' if unclear.\n"
    "3) record_context: extract when user mentions an occasion (e.g. 'viagem a Paris', 'fim de semana'). Empty string if none.\n"
    "4) Each item: type ('expense' or 'income'), amount (positive number), currency (default EUR if not stated), category (free-form, e.g. 'Food', 'Transport'), merchant (optional), transaction_date (YYYY-MM-DD or null), description (optional), payment_method (optional: card, cash, transfer).\n"
    "5) Use system_date for relative dates ('today', 'hoje', 'yesterday', 'ontem'). Never set transaction_date in the future.\n"
    "6) Infer currency from locale or symbols (euros, EUR, €, dollars, USD, $). Default EUR.\n"
    "7) Handle single entry ('gastei 20 no café'), multiple ('café 3, almoço 12, uber 8'), or mixed ('recebi 500, paguei 50 ao dentista').\n"
    "8) Preserve the original language for category, merchant, description.\n"
    "9) Items in same order as they appear in the text.\n\n"

    "Respond ONLY with valid JSON (no markdown, no extra text).\n"
    "Format:\n"
    "{{\n"
    "  \"record_name\": \"inferred name\",\n"
    "  \"record_context\": \"occasion when mentioned, else empty string\",\n"
    "  \"items\": [\n"
    "    {{\"type\": \"expense\", \"amount\": 20.00, \"currency\": \"EUR\", \"category\": \"Food\", \"merchant\": \"\", \"transaction_date\": \"2025-02-27\", \"description\": \"café\", \"payment_method\": \"\"}},\n"
    "    {{\"type\": \"income\", \"amount\": 500.00, \"currency\": \"EUR\", \"category\": \"Freelance\", \"merchant\": \"\", \"transaction_date\": null, \"description\": \"projeto\", \"payment_method\": \"\"}}\n"
    "  ]\n"
    "}}\n\n"

    "Examples:\n"
    "- 'Gastei 20 no supermercado e 8 no uber.' -> 2 expense items.\n"
    "- 'Recebi 500 do projeto freelance.' -> 1 income item.\n"
    "- 'Despesas de hoje: café 3, almoço 12, gasolina 45.' -> 3 expense items.\n\n"

    "If there is no financial information to extract, return exactly:\n"
    "{{\"error\": \"No financial information to extract\"}}"
)


class FinancialParserConfig(BaseSettings):
    """Self-contained configuration for the financial_parser module."""

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
        description="OpenAI model for financial extraction",
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
        description="Default timezone for date inference",
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
        env_prefix = "FINANCIAL_PARSER_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_prompt(self, title: str, content_text: str, system_date: str = "", system_time: str = "") -> str:
        """Generate the financial extraction prompt."""
        if not system_date:
            now = datetime.now()
            system_date = now.strftime("%Y-%m-%d")
            system_time = now.strftime("%H:%M:%S")
        return FINANCIAL_PARSER_PROMPT_TEMPLATE.format(
            system_date=system_date,
            system_time=system_time,
            title=title or "",
            content_text=content_text or "",
        )


@functools.lru_cache(maxsize=1)
def get_financial_parser_config() -> FinancialParserConfig:
    """Return a cached FinancialParserConfig instance."""
    return FinancialParserConfig()
