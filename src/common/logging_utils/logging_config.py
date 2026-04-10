"""
Universal Logging Configuration Module

This module provides centralized logging configuration for the entire pipeline.
It supports configurable log levels, file outputs, console outputs, and log rotation
with comprehensive logging management.

Key Features:
- Universal logging configuration management for any project
- Per-module logger configuration with global defaults
- Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Separate console and file output control
- Automatic log directory creation
- Log file rotation (size-based) to prevent oversized logging
- Consistent log formatting across all modules
- Dynamic log level adjustment
- Frozen execution support (PyInstaller)

Configuration Structure:
- LOGGING_CONFIG: Dictionary with defaults and per-logger overrides
- DEFAULT_LOG_DIR: Default directory for log files
- DEFAULT_LOG_FORMAT: Standard log message format
- DEFAULT_DATE_FORMAT: Standard timestamp format

The module provides these main functions:
- get_logger(): Create or retrieve configured logger instances
- set_console_level(): Dynamically adjust console log levels
- determine_log_dir(): Resolve log directory path
- create_rotating_file_handler(): Create size-based rotating file handler

Author: [Your Name]
Date: [Current Date]
Version: 2.0.0
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Import centralized path utilities using relative import
# Note: Entry point script must call setup_common_imports() before importing this module
from ..utils.file_sys_utils import get_script_directory, get_project_root


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
LOGGING_CONFIG = {
    "defaults": {
        "console_level": "DEBUG",
        "file_level": "DEBUG",
        "console_output": True,
        "file_output": True,
        "rotation": {
            "mode": "size",
            "max_bytes": 10 * 1024 * 1024,  # 10MB
            "backup_count": 7
        }
    },
    "loggers": {
        # sample logger
        "sample": {
            # "file_level": "DEBUG",  # Removed - will use default
            "log_filename": "sample.log",
            # "console_output": True, # Removed - will use default
            # "file_output": True   # Removed - will use default
        },
    },
    "strict_config": False  # if True, unknown logger names raise an error
}

# ============================================================================
# DEFAULT SETTINGS
# ============================================================================
DEFAULT_LOG_DIR = "logs"  # Default log directory (relative to SCRIPT_DIR)
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def determine_log_dir(base_dir: Optional[Path] = None) -> Path:
    """
    Determine the log directory path.
    
    Args:
        base_dir (Optional[Path]): Base directory for logging. If None, uses SCRIPT_DIR/logs
        
    Returns:
        Path: Resolved log directory path
    """
    if base_dir is None:
        return get_script_directory() / DEFAULT_LOG_DIR
    return Path(base_dir)


def create_rotating_file_handler(
    log_path: Path,
    level: str,
    max_bytes: int,
    backup_count: int
) -> logging.handlers.RotatingFileHandler:
    """
    Create a size-based rotating file handler.
    
    Args:
        log_path (Path): Path to the log file
        level (str): Log level for the handler
        max_bytes (int): Maximum bytes before rotation
        backup_count (int): Number of backup files to keep
        
    Returns:
        logging.handlers.RotatingFileHandler: Configured rotating file handler
    """
    # Ensure log directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    handler.setLevel(getattr(logging, level.upper()))
    return handler


def set_console_level(logger: logging.Logger, level: str) -> None:
    """
    Dynamically adjust the console log level for an existing logger.
    
    This function updates the console handler's log level without affecting
    the file handler's log level.
    
    Args:
        logger (logging.Logger): The logger instance to modify
        level (str): New log level for console output.
                    Valid values: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    
    Example:
        >>> logger = get_logger('my_module')
        >>> set_console_level(logger, 'DEBUG')  # Enable debug logging
        >>> logger.debug('This will now be visible')
    
    Note:
        - Only affects StreamHandler (console) handlers
        - File handlers remain unchanged
        - Level is converted to uppercase automatically
    """
    level_upper = level.upper()
    log_level = getattr(logging, level_upper)
    
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(log_level)


def get_logger(
    logger_name: str,
    log_dir: Optional[Path] = None,
    console_level: Optional[str] = None,
    file_level: Optional[str] = None,
) -> logging.Logger:
    """
    Get or create a configured logger instance with console and rotating file handlers.
    
    This function creates a logger with both console and rotating file handlers based on
    the configuration defined in LOGGING_CONFIG. It merges global defaults with per-logger
    overrides and supports strict configuration mode.
    
    The logger configuration process:
    1. Merges global defaults with per-logger overrides
    2. Validates logger name against strict configuration if enabled
    3. Sets up console handler with configurable log level
    4. Sets up rotating file handler with configurable log level and directory
    5. Applies consistent formatting to all handlers
    6. Prevents duplicate handler creation
    
    Args:
        logger_name (str): Name of the logger
        log_dir (Optional[Path]): Directory for log files. Overrides config setting
        console_level (Optional[str]): Override for console log level.
                                     Valid values: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
        file_level (Optional[str]): Override for file log level.
                                  Valid values: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
        
    Returns:
        logging.Logger: Configured logger instance with console and rotating file handlers
        
    Raises:
        ValueError: If strict_config is True and logger_name is not in configuration
        
    Example:
        >>> logger = get_logger('gdrive_downloader', console_level='DEBUG')
        >>> logger.info('Starting download...')
        >>> logger.debug('Detailed debug information')
        
    Note:
        - Logger configuration merges defaults with per-logger overrides
        - Existing loggers are returned without modification
        - Log directory is created automatically if it doesn't exist
        - File rotation occurs at 10MB with 7 backup files
        - Console and file levels can be set independently
    """
    # Get global defaults and per-logger overrides
    defaults = LOGGING_CONFIG.get("defaults", {})
    logger_config = LOGGING_CONFIG.get("loggers", {}).get(logger_name, {})
    strict_config = LOGGING_CONFIG.get("strict_config", False)
    
    # Check strict configuration
    if strict_config and logger_name not in LOGGING_CONFIG.get("loggers", {}):
        raise ValueError(f"Logger '{logger_name}' not found in configuration and strict_config is enabled")
    
    # Warn once about unknown loggers in non-strict mode
    if not strict_config and logger_name not in LOGGING_CONFIG.get("loggers", {}):
        # Use a simple cache to warn only once per logger
        if not hasattr(get_logger, '_warned_loggers'):
            get_logger._warned_loggers = set()
        
        if logger_name not in get_logger._warned_loggers:
            print(f"WARNING: Logger '{logger_name}' not in configuration, using defaults")
            get_logger._warned_loggers.add(logger_name)
    
    # Merge defaults with per-logger overrides
    config = {**defaults, **logger_config}
    
    # Apply parameter overrides
    if console_level is not None:
        config["console_level"] = console_level
    if file_level is not None:
        config["file_level"] = file_level
    if log_dir is not None:
        config["log_dir"] = log_dir
    
    # Create logger
    logger = logging.getLogger(logger_name)
    
    # Only configure if not already configured (avoid duplicate handlers)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)  # Set to DEBUG to allow handlers to filter
        logger.propagate = False  # Don't propagate to root logger
        
        # Create formatters
        formatter = logging.Formatter(
            fmt=DEFAULT_LOG_FORMAT,
            datefmt=DEFAULT_DATE_FORMAT
        )
        
        # Console handler (if enabled in config)
        if config.get("console_output", True):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(getattr(logging, config["console_level"].upper()))
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        
        # Rotating file handler (if enabled in config)
        if config.get("file_output", True):
            # Determine log directory
            log_dir = determine_log_dir(config.get("log_dir"))
            
            # Get log filename from config or use logger name
            log_filename = config.get("log_filename", f"{logger_name}.log")
            log_file = log_dir / log_filename
            
            # Get rotation settings
            rotation = config.get("rotation", {})
            max_bytes = rotation.get("max_bytes", 10 * 1024 * 1024)  # 10MB default
            backup_count = rotation.get("backup_count", 7)
            
            # Create rotating file handler
            file_handler = create_rotating_file_handler(
                log_file, config["file_level"], max_bytes, backup_count
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    
    return logger


