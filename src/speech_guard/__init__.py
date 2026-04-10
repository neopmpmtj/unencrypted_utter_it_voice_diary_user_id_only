"""
Speech Detection Guard Gate

Lightweight voice activity detection that determines whether an audio
recording contains human speech before sending to transcription.
"""

from .services import should_proceed_to_transcription

__all__ = ["should_proceed_to_transcription"]
