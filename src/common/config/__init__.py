"""
Common configuration module with Pydantic settings classes.

This module provides type-safe configuration with environment variable support
and database overrides via the GlobalSettings model.
"""

from .settings import (
    RecorderConfig,
    SilenceRemovalConfig,
    LoudnessConfig,
    ChunkingConfig,
    AIConfig,
    StorageConfig,
    AppConfig,
    get_config,
    reload_config,
)

__all__ = [
    'RecorderConfig',
    'SilenceRemovalConfig',
    'LoudnessConfig',
    'ChunkingConfig',
    'AIConfig',
    'StorageConfig',
    'AppConfig',
    'get_config',
    'reload_config',
]
