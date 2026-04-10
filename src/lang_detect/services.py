"""
Language Detection Service

Provides keyword-based language detection (primary) and optional langdetect fallback.
Use detect_language_keywords() for the pipeline: your word list + min_matches, no accent issues.
"""

import logging
import re
from typing import List, Optional

from .config import KEYWORD_CONFIG, get_config

try:
    from langdetect import detect, detect_langs, LangDetectException
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase words (letters/numbers)."""
    if not text or not text.strip():
        return []
    return [m.lower() for m in _TOKEN_RE.findall(text) if m]


def _keyword_matches(text: str, lang: str) -> bool:
    """True if text matches the keyword criteria for the given language."""
    cfg = get_config(lang)
    if not cfg or not cfg.get("keywords"):
        return False
    words = _tokenize(text)
    sample_size = cfg["sample_size"]
    min_matches = cfg["min_matches"]
    keywords_set = set(cfg["keywords"])
    sample = words[:sample_size]
    if not sample:
        return False
    match_count = sum(1 for w in sample if w in keywords_set)
    return match_count >= min_matches


def detect_language_keywords(text: str, fallback_lang: str = "en") -> str:
    """
    Detect language using keyword lists (no langdetect).

    For each configured language (e.g. "en"), take the first sample_size words,
    count matches in that language's keyword list; if count >= min_matches,
    return that language code. Otherwise return fallback_lang (e.g. user's
    preferred storage language) so the pipeline does not translate.

    Args:
        text: Transcript or text to analyze.
        fallback_lang: Language code to return when no keyword list matches (e.g. "pt").

    Returns:
        ISO 639-1 language code (e.g. "en", "pt").
    """
    if not text or not text.strip():
        logger.debug("Empty text, returning fallback language")
        return fallback_lang
    for lang in KEYWORD_CONFIG:
        if _keyword_matches(text, lang):
            logger.info(f"Keyword detection: matched {lang}")
            return lang
    logger.info(f"Keyword detection: no match, using fallback {fallback_lang}")
    return fallback_lang


def detect_language(text: str, fallback_language: str = 'en') -> str:
    """
    Detect the language of the given text.
    
    Uses the langdetect library which is based on Google's language detection.
    Falls back to the provided fallback language if detection fails.
    
    Args:
        text: Text to detect language for
        fallback_language: Language code to return if detection fails (default: 'en')
        
    Returns:
        ISO 639-1 language code (e.g., 'en', 'es', 'fr')
    """
    if not text or not text.strip():
        logger.debug("Empty text provided, returning fallback language")
        return fallback_language
    
    # Clean text - remove excessive whitespace
    clean_text = ' '.join(text.split())
    
    # Need at least some text to detect language
    if len(clean_text) < 10:
        logger.debug(f"Text too short ({len(clean_text)} chars), returning fallback")
        return fallback_language
    
    if not LANGDETECT_AVAILABLE:
        logger.warning("langdetect library not available, using fallback")
        return fallback_language
    
    try:
        detected = detect(clean_text)
        logger.info(f"Detected language: {detected}")
        return detected
        
    except LangDetectException as e:
        logger.warning(f"Language detection failed: {e}")
        return fallback_language
    except Exception as e:
        logger.error(f"Unexpected error in language detection: {e}")
        return fallback_language


def detect_language_with_confidence(text: str, fallback_language: str = 'en') -> tuple:
    """
    Detect the language of the given text with confidence score.
    
    Args:
        text: Text to detect language for
        fallback_language: Language code to return if detection fails
        
    Returns:
        Tuple of (language_code, confidence) where confidence is 0.0-1.0
    """
    if not text or not text.strip():
        return fallback_language, 0.0
    
    clean_text = ' '.join(text.split())
    
    if len(clean_text) < 10:
        return fallback_language, 0.0
    
    if not LANGDETECT_AVAILABLE:
        return fallback_language, 0.0
    
    try:
        results = detect_langs(clean_text)
        if results:
            top_result = results[0]
            return top_result.lang, top_result.prob
        return fallback_language, 0.0
        
    except LangDetectException as e:
        logger.warning(f"Language detection failed: {e}")
        return fallback_language, 0.0
    except Exception as e:
        logger.error(f"Unexpected error in language detection: {e}")
        return fallback_language, 0.0


def is_same_language(lang1: str, lang2: str) -> bool:
    """
    Check if two language codes refer to the same language.
    
    Handles variations like 'zh-cn'/'zh-tw' -> 'zh'.
    
    Args:
        lang1: First language code
        lang2: Second language code
        
    Returns:
        True if they're the same language
    """
    # Normalize: take just the primary language code
    def normalize(code: str) -> str:
        if not code:
            return ''
        return code.lower().split('-')[0].split('_')[0]
    
    return normalize(lang1) == normalize(lang2)
