"""
Text Input Service

Handles text-based entry ingestion with language detection and translation.
Flow: validate -> normalize (if list) -> detect language -> translate (if needed) -> store
"""

import logging
from typing import Tuple

from django.utils.translation import gettext as _

from src.accounts.models import UserPreferences
from src.common.config import get_config
from src.ingestion.models import IngestItem, IngestStatus, TemplateType
from src.ingestion.tasks import log_api_usage
from src.lang_detect.services import detect_language_keywords, is_same_language
from src.translation.services import translate_text

from .utils import is_whitespace_only, normalize_list_text, validate_template_type

logger = logging.getLogger(__name__)


class TextInputError(Exception):
    """Base exception for text input errors."""
    pass


class EmptyTextError(TextInputError):
    """Raised when text is empty or whitespace-only."""
    pass


class InvalidTemplateTypeError(TextInputError):
    """Raised when template_type is not valid."""
    pass


def ingest_text_entry(
    *,
    user,
    text: str,
    template_type: str = "plain",
    title: str = "",
    occurred_at=None,
) -> Tuple[IngestItem, dict]:
    """
    Ingest a text entry with language detection and optional translation.
    
    Flow:
    1. Validate non-empty text
    2. Validate template_type
    3. Apply template normalization (if list)
    4. Detect language
    5. Translate if detected != user's preferred language
    6. Create IngestItem
    
    Args:
        user: CustomUser instance
        text: The text content to ingest
        template_type: 'plain' or 'list'
        title: Optional title for the entry
        occurred_at: Optional datetime for when the entry occurred
        
    Returns:
        Tuple of (IngestItem, metadata_dict)
        metadata_dict contains: detected_language, translated, final_text
        
    Raises:
        EmptyTextError: If text is empty or whitespace-only
        InvalidTemplateTypeError: If template_type is not valid
    """
    # Step 1: Validate non-empty
    if is_whitespace_only(text):
        raise EmptyTextError(_("Text cannot be empty or whitespace-only"))
    
    # Step 2: Validate template_type
    if not validate_template_type(template_type):
        raise InvalidTemplateTypeError(
            _("Invalid template_type '%(type)s'. Must be 'plain' or 'list'") % {"type": template_type}
        )
    
    # Step 3: Apply template normalization
    if template_type == TemplateType.LIST:
        processed_text = normalize_list_text(text)
        logger.info("Applied list normalization to text input")
    else:
        # Plain: minimal cleanup (trim edges)
        processed_text = text.strip()
    
    # Step 4: Get user's preferred language
    preferred_language = _get_user_preferred_language(user)
    
    # Step 5: Detect language
    detected_language = detect_language_keywords(
        processed_text, 
        fallback_lang=preferred_language
    )
    logger.info(f"Detected language: {detected_language}, preferred: {preferred_language}")
    
    # Step 6: Translate if needed (synchronous)
    translated = False
    final_text = processed_text
    enable_translation = _get_enable_translation(user)
    
    if enable_translation and not is_same_language(detected_language, preferred_language):
        logger.info(f"Translating from {detected_language} to {preferred_language}")
        try:
            final_text, token_usage = translate_text(
                text=processed_text,
                source_language=detected_language,
                target_language=preferred_language,
                user=user,
            )
            translated = True
            if user:
                config = get_config()
                log_api_usage(user, config.ai.translation_model, 'input_tokens', token_usage.get('input', 0), ingest_item=None, origin='ingest_text_entry')
                log_api_usage(user, config.ai.translation_model, 'output_tokens', token_usage.get('output', 0), ingest_item=None, origin='ingest_text_entry')
            logger.info(f"Translation complete. Tokens used: {token_usage.get('total', 0)}")
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            # On translation failure, store original text
            # This is a design choice - could also raise an error
            final_text = processed_text
    
    # Step 7: Encrypt and create IngestItem
    item = _create_text_ingest_item(
        user=user,
        content_text=final_text,
        template_type=template_type,
        detected_language=detected_language,
        title=title,
        occurred_at=occurred_at,
    )
    
    metadata = {
        "detected_language": detected_language,
        "translated": translated,
        "final_text_length": len(final_text),
        "final_text": final_text,
    }

    try:
        from src.gigo.services import record_entry
        record_entry(user=user, item=item, content_text=final_text, item_type="text")
    except Exception as e:
        logger.warning(f"Could not record GIGO entry: {e}")
    
    logger.info(f"Text entry created: {item.id}, template={template_type}, translated={translated}")
    
    # Step 8: Queue classification task (same as audio entries)
    try:
        from src.classification.tasks import classify_item_task
        classify_item_task.delay(str(item.id), final_text, detected_language or '')
        logger.info(f"Queued classification task for text entry {item.id}")
    except Exception as e:
        logger.warning(f"Could not queue classification task for text entry {item.id}: {e}")
    
    return item, metadata


def _get_user_preferred_language(user) -> str:
    """
    Get user's preferred language from UserPreferences.
    
    Args:
        user: CustomUser instance
        
    Returns:
        Language code (defaults to 'en' if not set)
    """
    try:
        prefs = UserPreferences.objects.get(user=user)
        return prefs.preferred_language or 'en'
    except UserPreferences.DoesNotExist:
        return 'en'


def _get_enable_translation(user) -> bool:
    """
    Get whether the user has translation enabled from UserPreferences.
    
    Args:
        user: CustomUser instance (can be None)
        
    Returns:
        True if translation is enabled (default when no user or no prefs)
    """
    if not user:
        return True
    try:
        prefs = UserPreferences.objects.get(user=user)
        return getattr(prefs, 'enable_translation', True)
    except UserPreferences.DoesNotExist:
        return True


def _create_text_ingest_item(
    *,
    user,
    content_text: str,
    template_type: str,
    detected_language: str,
    title: str = "",
    occurred_at=None,
) -> IngestItem:
    """
    Create and save an IngestItem for text input.
    
    Args:
        user: CustomUser instance
        content_text: Final text content (translated if applicable)
        template_type: 'plain' or 'list'
        detected_language: Detected language code
        title: Optional title
        occurred_at: Optional occurrence datetime
        
    Returns:
        Created IngestItem instance
    """
    return IngestItem.objects.create(
        user=user,
        item_type="text",
        template_type=template_type,
        content_text=content_text or "",
        summary_text="",
        title=title or "",
        detected_language=detected_language,
        occurred_at=occurred_at,
        status=IngestStatus.PROCESSED,
    )
