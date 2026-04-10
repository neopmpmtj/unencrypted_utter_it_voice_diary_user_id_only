"""
Standalone configuration for the to-do parser module.

Self-contained Pydantic config that reads from environment variables.
Uses central LLM config (src.common.model_picker) for model defaults when available.

Env-var priority (highest wins):
  1. TODO_PARSER_MODEL, TODO_PARSER_TEMPERATURE, ...  (module-specific)
  2. OPENAI_API_KEY                                    (OpenAI API key)
  3. Central LLM config (llm_models.json / CENTRAL_LLM_*)
  4. Defaults defined below
"""

import functools
from datetime import datetime
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

try:
    from src.common.model_picker import get_llm_config
    _central = get_llm_config("todo_parser")
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

TODO_PARSER_PROMPT_TEMPLATE = (
    _LANGUAGE_INSTRUCTION
    + "You are an assistant specialized in extracting structured to-do/task items from natural language input.\n"
    "Your task is to identify all actionable tasks from the given text and return them as a structured JSON object.\n\n"

    "System date (reference for 'today'): {system_date}\n"
    "System time (reference for 'now'): {system_time}\n\n"

    "Text to analyze:\n"
    "Title: {title}\n"
    "Content: {content_text}\n\n"

    "EXTRACTION RULES:\n"
    "1) Extract all actionable tasks/to-dos from the text.\n"
    "2) Infer a record name that summarizes the set of tasks (e.g. 'work tasks', 'errands').\n"
    "   If no name can be inferred, use 'tasks'.\n"
    "3) 'record_context': Extract when the user mentions a context/occasion for the tasks. "
    "Use empty string when none is mentioned.\n"
    "4) Each item must be a separate dictionary with these fields:\n"
    "   - 'text': the primary task description (required, concise action statement)\n"
    "   - 'description': optional extra detail about the task. Empty string if none.\n"
    "   - 'priority': integer 1-5 (1=lowest, 2=low, 3=medium, 4=high, 5=urgent).\n"
    "     Infer from urgency words: 'urgent/asap/immediately' -> 5, 'important/must' -> 4, default -> 3, "
    "'low priority/when possible' -> 2, 'someday/maybe' -> 1.\n"
    "   - 'due_date': optional date in YYYY-MM-DD format (null if not mentioned).\n"
    "     Relative dates ('tomorrow', 'next Monday') must be converted using the system date.\n"
    "     Never set a due_date in the past relative to system date.\n"
    "   - 'due_time': optional time in HH:MM format (null if not mentioned).\n"
    "   - 'topic': broad category for the task (e.g. 'health', 'work', 'shopping', 'home', 'personal').\n"
    "     Infer from context. Use empty string if unclear.\n"
    "   - 'subtopic': finer category within topic (e.g. under 'health' -> 'dentist', 'gym').\n"
    "     Use empty string if not applicable.\n"
    "   - 'recurrence_rule': 'daily', 'weekly', 'monthly', or '' if not recurring.\n"
    "   - 'entity_name': the primary person, vendor, or organization associated with the task.\n"
    "     Examples: 'call dentist' -> 'dentist'; 'buy at Zara' -> 'Zara'; "
    "'email João about the project' -> 'João'. Use empty string if no entity.\n"
    "   - 'entity_type': type of entity. Valid values: 'person', 'organization', 'vendor', "
    "'location', 'project', 'contact', 'client', 'product', 'unknown'. "
    "Use empty string if no entity.\n"
    "5) SUBTASKS: When a task has sub-steps, create a parent item with a 'children' array.\n"
    "   Each child has the same fields. Omit 'children' key for leaf tasks.\n"
    "6) Preserve the original language of the items (do NOT translate).\n"
    "7) Items should be in the same order as they appear in the text.\n\n"

    "Respond ONLY with valid JSON (no markdown, no extra text).\n"
    "Exact format:\n"
    "{{\n"
    "  \"record_name\": \"inferred name for the task set\",\n"
    "  \"record_context\": \"context/occasion when mentioned, else empty string\",\n"
    "  \"items\": [\n"
    "    {{\n"
    "      \"text\": \"task description\",\n"
    "      \"description\": \"\",\n"
    "      \"priority\": 3,\n"
    "      \"due_date\": null,\n"
    "      \"due_time\": null,\n"
    "      \"topic\": \"category\",\n"
    "      \"subtopic\": \"\",\n"
    "      \"recurrence_rule\": \"\",\n"
    "      \"entity_name\": \"\",\n"
    "      \"entity_type\": \"\"\n"
    "    }}\n"
    "  ]\n"
    "}}\n\n"

    "Example: \"I need to call the dentist tomorrow, buy groceries at Continente, and finish the report by Friday\" ->\n"
    "{{\n"
    "  \"record_name\": \"errands\",\n"
    "  \"record_context\": \"\",\n"
    "  \"items\": [\n"
    "    {{\"text\": \"call the dentist\", \"description\": \"\", \"priority\": 3, "
    "\"due_date\": \"{example_date_tomorrow}\", \"due_time\": null, "
    "\"topic\": \"health\", \"subtopic\": \"dentist\", \"recurrence_rule\": \"\", "
    "\"entity_name\": \"dentist\", \"entity_type\": \"contact\"}},\n"
    "    {{\"text\": \"buy groceries\", \"description\": \"\", \"priority\": 3, "
    "\"due_date\": null, \"due_time\": null, "
    "\"topic\": \"shopping\", \"subtopic\": \"groceries\", \"recurrence_rule\": \"\", "
    "\"entity_name\": \"Continente\", \"entity_type\": \"vendor\"}},\n"
    "    {{\"text\": \"finish the report\", \"description\": \"\", \"priority\": 3, "
    "\"due_date\": \"{example_date_friday}\", \"due_time\": null, "
    "\"topic\": \"work\", \"subtopic\": \"\", \"recurrence_rule\": \"\", "
    "\"entity_name\": \"\", \"entity_type\": \"\"}}\n"
    "  ]\n"
    "}}\n\n"

    "If there is insufficient information to extract tasks, return exactly:\n"
    "{{\"error\": \"Insufficient information to extract tasks\"}}"
)


class TodoParserConfig(BaseSettings):
    """Self-contained configuration for the to-do parser module."""

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
        description="OpenAI model for to-do extraction",
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
        env_prefix = "TODO_PARSER_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_prompt(self, title: str, content_text: str, system_date: str = "", system_time: str = "") -> str:
        """Generate the to-do extraction prompt."""
        if not system_date:
            now = datetime.now()
            system_date = now.strftime("%Y-%m-%d")
            system_time = now.strftime("%H:%M:%S")

        from datetime import date as date_type, timedelta
        try:
            base = date_type.fromisoformat(system_date)
        except (ValueError, TypeError):
            base = date_type.today()
        tomorrow = base + timedelta(days=1)
        days_until_friday = (4 - base.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        friday = base + timedelta(days=days_until_friday)

        return TODO_PARSER_PROMPT_TEMPLATE.format(
            system_date=system_date,
            system_time=system_time,
            title=title or "",
            content_text=content_text or "",
            example_date_tomorrow=tomorrow.isoformat(),
            example_date_friday=friday.isoformat(),
        )


@functools.lru_cache(maxsize=1)
def get_todo_parser_config() -> TodoParserConfig:
    """Return a cached TodoParserConfig instance."""
    return TodoParserConfig()
