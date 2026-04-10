"""
Logging Utilities Package

This package provides centralized logging configuration and utilities for the entire pipeline.
It supports configurable log levels, file outputs, and console outputs with comprehensive logging management.

Available Functions:
- get_logger(): Create or retrieve configured logger instances
- set_console_level(): Dynamically adjust console log levels

Available Constants:
- LOGGING_CONFIG: Dictionary defining logger configurations
- DEFAULT_LOG_DIR: Default directory for log files
- DEFAULT_LOG_FORMAT: Standard log message format
- DEFAULT_DATE_FORMAT: Standard timestamp format

Key Features:
- Centralized logging configuration management
- Per-module logger configuration
- Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Separate console and file output control
- Automatic log directory creation
- Consistent log formatting across all modules
- Dynamic log level adjustment

Usage:
    from common.logging_utils import get_logger, set_console_level
    
    # Create a logger
    logger = get_logger('my_module', console_level='DEBUG')
    logger.info('Starting process...')
    
    # Adjust log level dynamically
    set_console_level(logger, 'INFO')

Author: [Your Name]
Date: [Current Date]
Version: 1.0.0
"""

from .logging_config import (
    get_logger,
    set_console_level,
    LOGGING_CONFIG,
    DEFAULT_LOG_DIR,
    DEFAULT_LOG_FORMAT,
    DEFAULT_DATE_FORMAT,
)

__all__ = [
    'get_logger',
    'set_console_level',
    'LOGGING_CONFIG',
    'DEFAULT_LOG_DIR',
    'DEFAULT_LOG_FORMAT',
    'DEFAULT_DATE_FORMAT',
]
