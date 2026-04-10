"""
Text Rewrite Service

Self-contained service for rewriting text using OpenAI chat completions.
No dependency on the main app's centralized config.
"""

import logging
import time
from typing import Dict, Optional, Tuple

from openai import OpenAI

from src.text_rewrite.config_text_rewrite.text_rewrite_config import (
    DEFAULT_TEMPLATE,
    PROMPT_TEMPLATES,
    get_rewrite_config,
)

logger = logging.getLogger(__name__)


def get_openai_client() -> OpenAI:
    """
    Get an OpenAI client using the module's own config.

    Raises:
        ValueError: If no API key is configured.
    """
    config = get_rewrite_config()
    api_key = config.openai_api_key

    if not api_key:
        raise ValueError(
            "OpenAI API key not configured for text_rewrite. "
            "Set AI_OPENAI_API_KEY or OPENAI_API_KEY in the environment or .env file."
        )

    return OpenAI(api_key=api_key, timeout=60.0)


def rewrite_text(
    text: str,
    template_name: Optional[str] = None,
    user=None,
) -> Tuple[str, Dict[str, int]]:
    """
    Rewrite text using the specified prompt template.

    Args:
        text: The text to rewrite.
        template_name: Key in PROMPT_TEMPLATES (defaults to DEFAULT_TEMPLATE).
        user: Django User instance (reserved for future per-user key support).

    Returns:
        Tuple of (rewritten_text, token_usage_dict).
        token_usage_dict keys: 'input', 'output', 'total'.

    Raises:
        ValueError: On invalid template, missing API key, or API failure.
    """
    config = get_rewrite_config()
    template_name = template_name or DEFAULT_TEMPLATE

    if template_name not in PROMPT_TEMPLATES:
        raise ValueError(
            f"Unknown template '{template_name}'. "
            f"Available: {', '.join(PROMPT_TEMPLATES)}"
        )

    prompt = PROMPT_TEMPLATES[template_name]["prompt"].format(text=text)

    logger.info(
        "Starting rewrite: template=%s, model=%s, text_len=%d",
        template_name, config.model, len(text),
    )

    client = get_openai_client()

    last_error: Optional[Exception] = None
    for attempt in range(config.max_retries + 1):
        try:
            logger.debug(
                "Rewrite API call attempt %d/%d",
                attempt + 1, config.max_retries + 1,
            )

            response = client.chat.completions.create(
                model=config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )

            rewritten = response.choices[0].message.content.strip()

            usage = response.usage
            token_usage = {
                "input": usage.prompt_tokens if usage else 0,
                "output": usage.completion_tokens if usage else 0,
                "total": usage.total_tokens if usage else 0,
            }

            logger.info(
                "Rewrite complete: result_len=%d, tokens=%d",
                len(rewritten), token_usage["total"],
            )

            return rewritten, token_usage

        except Exception as exc:
            last_error = exc
            if attempt < config.max_retries:
                delay = config.retry_delay * (config.retry_backoff_factor ** attempt)
                logger.warning(
                    "Rewrite API call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt + 1, config.max_retries + 1, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Rewrite failed after %d attempts: %s",
                    config.max_retries + 1, exc,
                )

    raise ValueError(f"Text rewrite failed: {last_error}")
