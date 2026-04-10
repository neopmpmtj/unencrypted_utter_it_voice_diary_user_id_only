"""
Central LLM model picker configuration.

Provides a single source of truth for LLM model settings across batch calendar,
voice (transcription/translation), text rewrite, list parser, list formatter,
classification, and calendar parser modules.
"""

from .config_model_picker import get_llm_config

__all__ = ["get_llm_config"]
