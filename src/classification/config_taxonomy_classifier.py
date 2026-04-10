"""
Configuration for the v14 Taxonomy Classifier.

Contains system/user prompt templates for:
- LLM #1: Primary classifier (PRD 9.1)
- LLM #2: Verifier / reviewer (PRD 9.3)

Also: selection limits, feature flags, and model config accessors.
"""

from typing import Dict

from decouple import config
from src.common.model_picker import get_llm_config


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

_raw = config("CLASSIFICATION_ENABLE_VERIFIER", default="true").lower()
ENABLE_VERIFIER = _raw in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Selection limits (PRD section 5.6)
#
# This flat-dict format is sent to the LLM payload via
# taxonomy_loader.build_classification_payload. Enforcement-side limits
# live in validator.py:SELECTION_LIMITS using a nested-dict format.
# Both encode the same numerical limits for different consumers.
# ---------------------------------------------------------------------------

SELECTION_LIMITS: Dict[str, int] = {
    "subject_secondary_max": 3,
    "intent_secondary_max": 2,
    "context_secondary_max": 2,
    "time_max": 3,
}


# ---------------------------------------------------------------------------
# Version strings (embedded in classification run records)
# ---------------------------------------------------------------------------

CLASSIFIER_VERSION = "v14.1"
CLASSIFIER_PROMPT_VERSION = "v14.1"
VERIFIER_VERSION = "v14.1"
VERIFIER_PROMPT_VERSION = "v14.1"


# ---------------------------------------------------------------------------
# Model config accessors
# ---------------------------------------------------------------------------

def get_classifier_model_config() -> dict:
    try:
        cfg = get_llm_config("taxonomy_classifier")
        if cfg:
            return cfg
    except Exception:
        pass
    return {"model": "gpt-4o-mini", "temperature": 0.0, "max_tokens": 2000}


def get_verifier_model_config() -> dict:
    try:
        cfg = get_llm_config("taxonomy_verifier")
        if cfg:
            return cfg
    except Exception:
        pass
    return {"model": "gpt-4o-mini", "temperature": 0.0, "max_tokens": 2000}


# ---------------------------------------------------------------------------
# LLM #1 — Primary classifier prompts (PRD 9.1)
# ---------------------------------------------------------------------------

CLASSIFIER_SYSTEM_PROMPT = """\
You are a strict classification engine.

Your task is to classify a single ingest item into a controlled hierarchical taxonomy.

You must follow these rules exactly:

1. You may only select taxonomy keys that are explicitly present in the supplied allowed_taxonomy lists.
2. Do not invent, paraphrase, or alter taxonomy keys.
3. Classify across these dimensions separately:
   - subject
   - intent
   - context
   - time
   - governance
4. Choose exactly one primary key for subject, intent, context, and governance.
5. For time, choose zero to three keys only when time is semantically relevant.
6. For subject, intent, and context, you may choose secondary keys up to the provided limits.
7. Extract entities separately. Do not misuse taxonomy keys to represent people, organizations, projects, contacts, or locations if they should be entities.
8. Use the content itself as the source of truth. Do not assume facts not present in the input.
9. When uncertain, choose the closest valid supplied taxonomy key and lower confidence. Do not invent a new key.
10. Keep reasoning brief and operational, not verbose.
11. Output valid JSON only.
12. If the item is ambiguous, set has_ambiguity=true and explain briefly.
13. Governance must reflect sensitivity, access scope, and retention/compliance implications as best as possible from the supplied vocabulary.
14. Time should be used semantically, not as a substitute for exact date metadata.
15. Respect the selection limits exactly.
16. For taxonomy key accuracy, distinguish between time-bound and open-ended action items:
    - Use intent.reminder.future.followup when the input references a specific date, time, or scheduling context.
    - Use intent.task.create.todo for action items without a specific date/time.
    - Do NOT use intent.capture.note.freeform for either case.
"""

CLASSIFIER_USER_TEMPLATE = """\
Classify the following ingest item.

INGEST ITEM:
{ingest_item_json}

Return JSON in exactly this structure:
{{
  "ingest_item_id": "string",
  "taxonomy_pack": "string",
  "primary": {{
    "subject_key": "string|null",
    "intent_key": "string|null",
    "context_key": "string|null",
    "governance_key": "string|null"
  }},
  "secondary": {{
    "subject_keys": [],
    "intent_keys": [],
    "context_keys": [],
    "time_keys": []
  }},
  "entities": [
    {{
      "entity_type": "string (person|organization|project|location|device|account|document|product|contact|vendor|client|unknown)",
      "canonical_name": "string|null",
      "raw_mention": "string",
      "role": "string (use \"\" when unknown, never null)",
      "confidence": 0.0
    }}
  ],
  "actionability": {{
    "is_actionable": true,
    "recommended_action_type": "string|null",
    "urgency_level": "low|normal|high|critical|null"
  }},
  "confidence": {{
    "subject": 0.0,
    "intent": 0.0,
    "context": 0.0,
    "time": 0.0,
    "governance": 0.0,
    "overall": 0.0
  }},
  "ambiguity": {{
    "has_ambiguity": false,
    "notes": []
  }},
  "reasoning": {{
    "subject_reason": "string",
    "intent_reason": "string",
    "context_reason": "string",
    "time_reason": "string",
    "governance_reason": "string"
  }}
}}

If a dimension is truly not classifiable, return null for the primary key and explain why in reasoning.
JSON only.\
"""


# ---------------------------------------------------------------------------
# LLM #2 — Verifier / reviewer prompts (PRD 9.3)
# ---------------------------------------------------------------------------

VERIFIER_SYSTEM_PROMPT = """\
You are a strict classification reviewer.

You will receive:
1. the original ingest item,
2. the allowed taxonomy lists,
3. the primary classifier's proposed output,
4. any validator findings.

Your task is to review the proposal and produce a corrected final proposal if necessary.

Rules:
1. You may only use taxonomy keys present in the supplied allowed_taxonomy.
2. Do not invent or rewrite keys.
3. Preserve correct selections unless there is a strong reason to change them.
4. Correct any dimension mistakes, overly broad choices, invalid governance choices, or missed entities.
5. Respect the same selection limits.
6. If validator findings indicate invalid keys or combinations, fix them.
7. Keep reasoning concise and specific.
8. Output valid JSON only.
9. Your goal is consistency, precision, and policy compliance.
10. If the original classifier is already correct, return the same structure with review_decision='confirmed'.
11. If changed, set review_decision='corrected' and explain the main reason.\
"""

VERIFIER_USER_TEMPLATE = """\
Review the classification proposal below.

ORIGINAL INGEST ITEM:
{ingest_item_json}

PRIMARY CLASSIFIER OUTPUT:
{primary_output_json}

VALIDATOR FINDINGS:
{validator_json}

Return JSON in exactly this structure:
{{
  "ingest_item_id": "string",
  "review_decision": "confirmed|corrected|rejected",
  "primary": {{
    "subject_key": "string|null",
    "intent_key": "string|null",
    "context_key": "string|null",
    "governance_key": "string|null"
  }},
  "secondary": {{
    "subject_keys": [],
    "intent_keys": [],
    "context_keys": [],
    "time_keys": []
  }},
  "entities": [
    {{
      "entity_type": "string (person|organization|project|location|device|account|document|product|contact|vendor|client|unknown)",
      "canonical_name": "string|null",
      "raw_mention": "string",
      "role": "string (use \"\" when unknown, never null)",
      "confidence": 0.0
    }}
  ],
  "actionability": {{
    "is_actionable": true,
    "recommended_action_type": "string|null",
    "urgency_level": "low|normal|high|critical|null"
  }},
  "confidence": {{
    "subject": 0.0,
    "intent": 0.0,
    "context": 0.0,
    "time": 0.0,
    "governance": 0.0,
    "overall": 0.0
  }},
  "ambiguity": {{
    "has_ambiguity": false,
    "notes": []
  }},
  "reasoning": {{
    "subject_reason": "string",
    "intent_reason": "string",
    "context_reason": "string",
    "time_reason": "string",
    "governance_reason": "string",
    "review_reason": "string"
  }}
}}

JSON only.\
"""
