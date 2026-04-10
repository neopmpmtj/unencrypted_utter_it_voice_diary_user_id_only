"""
Keyword-based language detection configuration (in code).

Edit this file to tune sample_size, min_matches, and keyword lists per language.
Structure supports adding more languages later (e.g. "pt" with Portuguese keywords).
"""

from typing import Any, Dict, List

# Default English keyword list (edit to tune detection)
DEFAULT_ENGLISH_KEYWORDS = [
    "the", "and", "is", "in", "it", "you", "that", "he", "was", "for",
    "on", "are", "with", "as", "I", "this", "have", "or", "be", "at",
    "want", "need", "do", "does", "did", "can", "could", "would", "should",
    "what", "when", "where", "why", "how", "who", "which",
    "will", "shall", "going", "am", "been", "being", "has", "had",
    "may", "might", "must", "ought", "to", "of", "from", "by",
    "but", "not", "if", "so", "my", "your", "his", "her",
    "yes", "no", "maybe", "here", "I'm", "I've", "I'll", "I'd",
    "we", "brother", "sister", "mom", "dad", "a", "an"

]

# Per-language config: sample_size, min_matches, keywords (lowercase)
# Add more entries later for other languages if needed.
KEYWORD_CONFIG: Dict[str, Dict[str, Any]] = {
    "en": {
        "sample_size": 15,
        "min_matches": 3,
        "keywords": DEFAULT_ENGLISH_KEYWORDS,
    },
}


def get_config(lang: str = "en") -> Dict[str, Any]:
    """
    Get keyword detection config for a language.

    Returns:
        Dict with keys: sample_size, min_matches, keywords (list of lowercase strings).
        If lang is unknown, returns empty dict (caller should use fallback).
    """
    cfg = KEYWORD_CONFIG.get(lang)
    if not cfg:
        return {}
    keywords = cfg.get("keywords") or []
    if isinstance(keywords, list) and keywords:
        keywords = [k.lower() for k in keywords]
    return {
        "sample_size": int(cfg.get("sample_size", 11)),
        "min_matches": int(cfg.get("min_matches", 3)),
        "keywords": keywords,
    }
