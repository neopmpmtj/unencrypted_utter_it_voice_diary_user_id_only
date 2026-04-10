"""
Configuration for the retrieval (diary chat) module — v14.

Holds LLM system prompt, search parameters, and retry settings.
Uses central LLM config (src.common.model_picker) for model defaults when available.
"""

from decouple import config

_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the user's question. "
    "If the question is in English, respond in English. If in Portuguese, respond in Portuguese. "
    "Do not translate or switch to another language. Preserve the language of the question.\n\n"
)

# Search parameters
TOP_K = 8
# Max merged candidates (vector + token) before composite ranking; keep aligned with vector/token fetch size.
CANDIDATE_POOL_SIZE = TOP_K
# How many top-ranked entries feed context and API sources (1 = single-best).
CONTEXT_TOP_N = 1

# Composite rank weights (tune without code changes)
RANK_WEIGHT_VECTOR = 1.0
RANK_WEIGHT_ENTITY = 0.55
RANK_WEIGHT_TOKEN = 0.45
RANK_WEIGHT_KEYWORD = 0.35
RANK_WEIGHT_TEXT = 0.4

MAX_DISTANCE = 0.5
HISTORY_WINDOW = 10

# token_index retrieval + re-ranking
TOKEN_INDEX_ENABLED = config("RETRIEVAL_TOKEN_INDEX_ENABLED", default="true").lower() in ("1", "true", "yes")

# Retry settings
MAX_RETRIES = 2
RETRY_DELAY = 2.0

SYSTEM_PROMPT = (
    _LANGUAGE_INSTRUCTION
    + "You are a helpful diary assistant. The user has a personal voice/text diary. "
    "You answer questions about the user's diary entries based on the retrieved context below.\n\n"
)

SINGLE_BEST_CONTEXT_INSTRUCTION = (
    "The context below is the single best-matching diary entry for this question. "
    "Do not assume other entries exist unless the user asks follow-ups.\n\n"
)

SYSTEM_PROMPT_BODY = (
    "Each entry in the context includes:\n"
    "- A date and subject classification (what the entry is about)\n"
    "- An intent classification (what the user was trying to do)\n"
    "- A summary and keywords\n"
    "- Entities (people, organizations, projects mentioned)\n"
    "- List items or financial details when applicable\n\n"
    "Rules:\n"
    "- Only use information from the provided context. If the context does not contain "
    "enough information, say so honestly.\n"
    "- When the user asks about a specific person or entity by name, base your answer only "
    "on entries that explicitly mention that person or entity. Ignore entries that mention "
    "different people or entities.\n"
    "- Reference specific dates, subjects, and entities from the entries when relevant.\n"
    "- Be concise and direct. Use the same language as the user's question.\n"
    "- When listing items, use the data from the entries (list items, financial items, etc.).\n"
    "- Do not invent or hallucinate information not present in the context.\n"
)

SYSTEM_PROMPT = SYSTEM_PROMPT + SYSTEM_PROMPT_BODY
