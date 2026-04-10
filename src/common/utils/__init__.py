"""
Utilities Package

This package provides cross-platform utilities used across the entire pipeline.
It includes path handling, file system operations, and other common utilities.

Available Functions:
- setup_common_imports(): Setup sys.path for common imports (idempotent)
- resolve_path(): Resolve relative and absolute paths with base directory support
- ensure_directory(): Create directories and parent directories as needed
- get_script_directory(): Get script directory for both regular and frozen execution
- get_project_root(): Get the project root directory
- sanitize_filename(): Sanitize filenames for filesystem safety

Key Features:
- Cross-platform compatibility (Windows, macOS, Linux)
- PyInstaller frozen application support
- Relative and absolute path handling
- Directory creation and validation
- Filename sanitization for filesystem safety

Usage:
    from common.utils import resolve_path, ensure_directory, sanitize_filename
    
    # Resolve a relative path
    config_path = resolve_path("config/settings.json")
    
    # Ensure a directory exists
    log_dir = ensure_directory("logging/2024")
    
    # Sanitize a filename
    safe_name = sanitize_filename("My:File<Name>.mp3")

Note:
    For FFmpeg utilities, use explicit imports:
    from common.utils.ffmpeg_utils import find_ffmpeg_executables

Author: [Your Name]
Date: [Current Date]
Version: 1.0.0
"""

from .file_sys_utils import (
    setup_common_imports,
    resolve_path,
    ensure_directory,
    get_script_directory,
    get_project_root,
    sanitize_filename,
)

__all__ = [
    "setup_common_imports",
    "resolve_path",
    "ensure_directory",
    "get_script_directory",
    "get_project_root",
    "sanitize_filename",
]
