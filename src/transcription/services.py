"""
Transcription Service

Provides audio transcription using OpenAI Whisper API.
Supports per-user API key overrides and configuration.
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Any

from openai import OpenAI

from src.common.config import get_config

logger = logging.getLogger(__name__)


class TranscriptionResult:
    """Result container for transcription."""
    
    def __init__(self, text: str, language: Optional[str] = None, 
                 duration: Optional[float] = None, metadata: Optional[Dict] = None):
        self.text = text
        self.language = language
        self.duration = duration
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'text': self.text,
            'language': self.language,
            'duration': self.duration,
            'metadata': self.metadata,
        }


def get_openai_client(user=None) -> OpenAI:
    """
    Get OpenAI client with appropriate API key.
    
    Checks for user-specific API key first, then falls back to global config.
    
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
    
    return OpenAI(api_key=api_key, timeout=120.0)


def transcribe_audio(
    audio_path: Path,
    user=None,
    language: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> TranscriptionResult:
    """
    Transcribe an audio file using OpenAI Whisper.
    
    Args:
        audio_path: Path to the audio file
        user: Django User instance (for per-user API key)
        language: ISO-639-1 language code to force (optional)
        model: Whisper model to use (optional, defaults to config)
        temperature: Decoding temperature (optional, defaults to 0)
        
    Returns:
        TranscriptionResult with text and metadata
        
    Raises:
        FileNotFoundError: If audio file doesn't exist
        ValueError: If API key not configured
    """
    config = get_config()
    
    # Validate file exists
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    
    # Get defaults from config
    model = model or config.ai.transcription_model
    temperature = temperature if temperature is not None else 0
    
    logger.info(f"Starting transcription: file={audio_path.name}, model={model}, language={language}")
    
    # Get OpenAI client
    client = get_openai_client(user)
    
    # Prepare API call parameters
    # Note: gpt-4o-transcribe only supports 'json' or 'text', not 'verbose_json'
    # whisper-1 supports 'verbose_json' but gpt-4o-transcribe does not
    api_params = {
        'model': model,
        'response_format': 'json',
        'temperature': temperature,
    }
    
    # Add prompt if configured (helps with accent recognition, code-switching)
    if config.ai.transcription_prompt:
        api_params['prompt'] = config.ai.transcription_prompt
    
    if language:
        api_params['language'] = language
    
    # Make API call
    with open(audio_path, 'rb') as audio_file:
        response = client.audio.transcriptions.create(
            file=audio_file,
            **api_params
        )
    
    # Extract results
    try:
        # JSON response includes text; language/duration may vary by model
        result_dict = response.model_dump() if hasattr(response, 'model_dump') else dict(response)
        
        text = result_dict.get('text', '')
        detected_language = result_dict.get('language', language)
        duration = result_dict.get('duration')
        
        logger.info(f"Transcription complete: {len(text)} chars, language={detected_language}")
        
        return TranscriptionResult(
            text=text,
            language=detected_language,
            duration=duration,
            metadata={
                'model': model,
                'source_file': str(audio_path),
                'forced_language': bool(language),
            }
        )
        
    except Exception as e:
        logger.error(f"Error parsing transcription response: {e}")
        # Fallback: just get text
        text = str(response.text) if hasattr(response, 'text') else str(response)
        return TranscriptionResult(
            text=text,
            language=language,
            metadata={'model': model, 'error': str(e)}
        )


def validate_audio_file(audio_path: Path) -> bool:
    """
    Validate that a file is a supported audio format.
    
    Args:
        audio_path: Path to the file
        
    Returns:
        True if valid, False otherwise
    """
    config = get_config()
    
    if not audio_path.exists():
        return False
    
    suffix = audio_path.suffix.lower()
    return suffix in config.silence.supported_formats
