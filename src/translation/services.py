"""
Translation Service

Provides text translation using OpenAI GPT models.
Supports per-user API key overrides and configuration.
"""

import logging
import time
from typing import Dict, Optional, Tuple

from openai import OpenAI

from src.common.config import get_config

logger = logging.getLogger(__name__)


# Default translation prompt template
DEFAULT_PROMPT_TEMPLATE = """Translate the following text to {target_language}.
Preserve the original meaning, tone, and formatting.
Only output the translated text, nothing else.

Text to translate:
{text}"""

# Map language codes to explicit prompt strings so the model outputs the right variant.
# OpenAI does not distinguish pt-PT vs pt-BR via API; the prompt must specify (e.g. "European Portuguese (Portugal)").
TARGET_LANGUAGE_PROMPT = {
    "en": "English",
    "pt": "European Portuguese (Portugal)",
    "pt-PT": "European Portuguese (Portugal)",
    "pt-BR": "Brazilian Portuguese",
}


def get_openai_client(user=None) -> OpenAI:
    """
    Get OpenAI client with appropriate API key.
    
    Args:
        user: Django User instance (optional)
        
    Returns:
        OpenAI client instance
        
    Raises:
        ValueError: If no API key is configured
    """
    config = get_config()
    api_key = config.ai.openai_api_key
    
    if not api_key:
        raise ValueError(
            "OpenAI API key not configured. "
            "Set OPENAI_API_KEY or AI_OPENAI_API_KEY in .env (or environment), or configure in GlobalSettings."
        )
    
    return OpenAI(api_key=api_key, timeout=60.0)


def translate_text(
    text: str,
    source_language: str,
    target_language: str,
    user=None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[str, Dict[str, int]]:
    """
    Translate text from source language to target language.
    
    Args:
        text: Text to translate
        source_language: Source language ISO code
        target_language: Target language ISO code or name
        user: Django User instance (for per-user API key)
        model: GPT model to use (optional, defaults to config)
        temperature: Generation temperature (optional, defaults to config)
        max_tokens: Maximum tokens in response (optional, defaults to config)
        
    Returns:
        Tuple of (translated_text, token_usage_dict)
        token_usage_dict has keys: 'input', 'output', 'total'
        
    Raises:
        ValueError: If API key not configured or translation fails
    """
    config = get_config()
    
    # Get defaults from config
    model = model or config.ai.translation_model
    temperature = temperature if temperature is not None else config.ai.translation_temperature
    max_tokens = max_tokens or config.ai.translation_max_tokens
    
    logger.info(f"Starting translation: {source_language} -> {target_language}, model={model}")
    logger.debug(f"Text length: {len(text)} chars")
    
    # Skip translation if source and target are the same
    if source_language == target_language:
        logger.info("Source and target language are the same, skipping translation")
        return text, {'input': 0, 'output': 0, 'total': 0}
    
    # Get OpenAI client
    client = get_openai_client(user)

    # Use explicit prompt string so the model outputs the right variant (e.g. European vs Brazilian Portuguese)
    target_for_prompt = TARGET_LANGUAGE_PROMPT.get(
        target_language.strip(),
        target_language if len(target_language) > 2 else "English",
    )

    # Build prompt
    prompt = DEFAULT_PROMPT_TEMPLATE.format(
        target_language=target_for_prompt,
        text=text
    )
    
    # Retry logic with exponential backoff
    max_retries = config.ai.max_retries
    retry_delay = config.ai.retry_delay
    backoff_factor = config.ai.retry_backoff_factor
    
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            logger.debug(f"Translation API call attempt {attempt + 1}/{max_retries + 1}")
            
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            # Extract translated text
            translated_text = response.choices[0].message.content.strip()
            
            # Extract token usage
            usage = response.usage
            token_usage = {
                'input': usage.prompt_tokens if usage else 0,
                'output': usage.completion_tokens if usage else 0,
                'total': usage.total_tokens if usage else 0,
            }
            
            logger.info(
                f"Translation complete: {len(translated_text)} chars, "
                f"tokens: {token_usage['total']}"
            )
            
            return translated_text, token_usage
            
        except Exception as e:
            last_error = e
            
            if attempt < max_retries:
                delay = retry_delay * (backoff_factor ** attempt)
                logger.warning(
                    f"Translation API call failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"Translation failed after {max_retries + 1} attempts: {e}")
    
    raise ValueError(f"Translation failed: {last_error}")


def get_language_name(iso_code: str) -> str:
    """
    Convert ISO 639-1 code to language name.
    
    Args:
        iso_code: Two-letter ISO 639-1 code
        
    Returns:
        Language name in English
    """
    language_names = {
        'en': 'English',
        'es': 'Spanish',
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'ru': 'Russian',
        'zh': 'Chinese',
        'ja': 'Japanese',
        'ko': 'Korean',
        'ar': 'Arabic',
        'hi': 'Hindi',
        'nl': 'Dutch',
        'pl': 'Polish',
        'tr': 'Turkish',
        'vi': 'Vietnamese',
        'th': 'Thai',
        'id': 'Indonesian',
        'sv': 'Swedish',
        'da': 'Danish',
        'no': 'Norwegian',
        'fi': 'Finnish',
        'el': 'Greek',
        'he': 'Hebrew',
        'cs': 'Czech',
        'ro': 'Romanian',
        'hu': 'Hungarian',
        'uk': 'Ukrainian',
        'bg': 'Bulgarian',
        'hr': 'Croatian',
        'sk': 'Slovak',
        'sl': 'Slovenian',
        'lt': 'Lithuanian',
        'lv': 'Latvian',
        'et': 'Estonian',
    }
    
    return language_names.get(iso_code.lower(), iso_code)
