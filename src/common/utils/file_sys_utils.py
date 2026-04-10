"""
Path Utilities Module

This module provides cross-platform path handling utilities for the entire pipeline.
It supports both regular Python execution and PyInstaller frozen applications, ensuring 
consistent path resolution across different platforms.

Key Features:
- Cross-platform path resolution (Windows, macOS, Linux)
- PyInstaller frozen application support
- Relative and absolute path handling
- Directory creation and validation
- Filename sanitization for filesystem safety
- Script directory detection for both regular and frozen execution

The utilities handle:
- Path resolution with proper base directory handling
- Directory creation with parent directory support
- Script directory detection for frozen applications
- Filename sanitization to prevent filesystem issues

Author: [Your Name]
Date: [Current Date]
Version: 1.0.0
"""

import os
import sys
from pathlib import Path
from typing import Union, Optional

# Module-level flag to track if path setup has been done
_path_setup_done = False


def setup_common_imports() -> None:
    """
    Setup sys.path for common imports. Idempotent - safe to call multiple times.
    
    This function adds the project root (src/ directory) to sys.path, enabling
    imports like 'from common.xxx' to work. It uses a module-level flag to ensure
    the path setup happens only once per Python process, providing zero overhead
    on subsequent calls.
    
    The function calculates the project root by going up two levels from
    common/utils/ to src/, which is the same logic used by get_project_root().
    
    This is essential for:
    - High-frequency pipeline execution (many times per minute)
    - Module execution mode (python -m src.xxx)
    - Direct script execution
    - Interactive Python sessions
    
    Returns:
        None
        
    Example:
        >>> setup_common_imports()  # First call - sets up path
        >>> setup_common_imports()  # Subsequent calls - no-op (fast)
        >>> from common.logging_utils import get_logger  # Now works
    """
    global _path_setup_done
    if _path_setup_done:
        return
    
    # Calculate src/ directory (project root)
    # Goes from common/utils/ to src/
    utils_dir = Path(__file__).resolve().parent
    project_root = utils_dir.parent.parent
    
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    _path_setup_done = True


def resolve_path(path_input: Union[str, Path], base_dir: Optional[Path] = None) -> Path:
    """
    Resolve a path, handling both relative and absolute paths.
    
    This function provides cross-platform path resolution that works consistently
    in both regular Python execution and PyInstaller frozen applications. It
    properly handles relative paths by resolving them against a base directory.
    
    The resolution process:
    1. Uses script directory as base if no base_dir provided
    2. Converts string inputs to Path objects
    3. Returns absolute paths as-is (resolved)
    4. Resolves relative paths against the base directory
    
    Args:
        path_input (Union[str, Path]): Input path as string or Path object
        base_dir (Optional[Path]): Base directory for relative paths.
                                 Defaults to script directory if None
        
    Returns:
        Path: Resolved Path object (always absolute)
        
    Raises:
        ValueError: If the path cannot be resolved due to invalid input
        OSError: If the path resolution fails due to filesystem issues
        
    Example:
        >>> resolve_path("config/settings.json")
        PosixPath('/path/to/script/config/settings.json')
        >>> resolve_path("/absolute/path/file.txt")
        PosixPath('/absolute/path/file.txt')
    """
    try:
        if base_dir is None:
            base_dir = get_script_directory()
        
        # Convert to Path object if needed
        if isinstance(path_input, str):
            path_input = Path(path_input)
        
        # Validate input
        if not path_input:
            raise ValueError("Path input cannot be empty")
        
        # Handle absolute paths
        if path_input.is_absolute():
            return path_input.resolve()
        
        # Handle relative paths
        resolved_path = (base_dir / path_input).resolve()
        
        return resolved_path
    except Exception as e:
        raise OSError(f"Failed to resolve path '{path_input}': {e}")


def ensure_directory(directory_path: Union[str, Path]) -> Path:
    """
    Ensure a directory exists, creating it if necessary.
    
    This function creates a directory and all necessary parent directories
    if they don't exist. It's safe to call multiple times and won't raise
    an error if the directory already exists.
    
    The creation process:
    1. Converts input to Path object
    2. Checks if directory already exists
    3. Creates directory with all parent directories if needed
    4. Returns the Path object pointing to the directory
    
    Args:
        directory_path (Union[str, Path]): Path to the directory to ensure
        
    Returns:
        Path: Path object pointing to the ensured directory
        
    Raises:
        ValueError: If the directory path is invalid
        OSError: If the directory cannot be created due to permissions
                or filesystem issues
        
    Example:
        >>> ensure_directory("logging/2024/01")
        PosixPath('/path/to/logging/2024/01')
        >>> ensure_directory("/existing/directory")
        PosixPath('/existing/directory')
    """
    try:
        directory_path = Path(directory_path)
        
        # Validate input
        if not str(directory_path).strip():
            raise ValueError("Directory path cannot be empty")
        
        if not directory_path.exists():
            directory_path.mkdir(parents=True, exist_ok=True)
        
        return directory_path
    except Exception as e:
        raise OSError(f"Failed to ensure directory '{directory_path}': {e}")


def get_project_root() -> Path:
    """
    Get the project root directory (piped-dl-transcribe-ingest-audio-txt level).
    
    This function provides a consistent way to access the project root directory
    across all modules. It's designed to work with the new common/ structure
    where common/ is at the project root level.
    
    Returns:
        Path: Path object pointing to the project root directory
        
    Raises:
        OSError: If the project root cannot be determined
        
    Example:
        >>> get_project_root()
        PosixPath('/path/to/piped-dl-transcribe-ingest-audio-txt')
        
    Note:
        This function assumes the common/utils module is located at project_root/common/utils/
        and calculates the project root accordingly.
    """
    try:
        # Get the directory containing this utils module
        utils_dir = Path(__file__).resolve().parent
        # Project root is two levels up from common/utils/
        project_root = utils_dir.parent.parent
        return project_root
    except Exception as e:
        raise OSError(f"Failed to determine project root directory: {e}")


def get_script_directory() -> Path:
    """
    Get the project root directory for consistent base path resolution.
    
    This function returns the project root directory to ensure all modules
    use the same base directory for resolving relative paths. This provides
    consistent behavior across different deployment scenarios.
    
    Returns:
        Path: Path object pointing to the project root directory
        
    Raises:
        OSError: If the project root directory cannot be determined
        
    Example:
        >>> get_script_directory()
        PosixPath('/path/to/project/root')
        
    Note:
        This function is essential for resolving relative paths consistently
        across all modules in the application.
    """
    return get_project_root()


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename by removing or replacing unsafe characters.
    
    This function ensures that a filename is safe for use on all major
    filesystems (Windows, macOS, Linux) by replacing or removing characters
    that could cause issues or security problems.
    
    The sanitization process:
    1. Replaces unsafe characters (< > : " / \\ | ? *) with underscores
    2. Removes leading/trailing whitespace and dots
    3. Ensures filename is not empty (uses 'unnamed_file' if empty)
    4. Limits filename length to 255 characters (preserving extension)
    
    Args:
        filename (str): Original filename to sanitize
        
    Returns:
        str: Sanitized filename safe for filesystem use
        
    Raises:
        ValueError: If the filename is None or invalid type
        
    Example:
        >>> sanitize_filename("My:File<Name>.mp3")
        "My_File_Name_.mp3"
        >>> sanitize_filename("   .hidden_file   ")
        "hidden_file"
        >>> sanitize_filename("")
        "unnamed_file"
        
    Note:
        This function is essential for preventing filesystem errors when
        downloading files with potentially problematic names from Google Drive.
    """
    try:
        # Validate input
        if filename is None:
            raise ValueError("Filename cannot be None")
        
        if not isinstance(filename, str):
            raise ValueError(f"Filename must be a string, got {type(filename).__name__}")
        
        # Define unsafe characters
        unsafe_chars = r'<>:"/\\|?*'
        
        # Replace unsafe characters with underscores
        sanitized = filename
        for char in unsafe_chars:
            sanitized = sanitized.replace(char, '_')
        
        # Remove leading/trailing whitespace and dots
        sanitized = sanitized.strip(' .')
        
        # Ensure filename is not empty
        if not sanitized:
            sanitized = 'unnamed_file'
        
        # Limit filename length (keep extension)
        if len(sanitized) > 255:
            name, ext = os.path.splitext(sanitized)
            if ext:
                max_name_length = 255 - len(ext)
                sanitized = name[:max_name_length] + ext
            else:
                sanitized = sanitized[:255]
        
        return sanitized
    except Exception as e:
        raise ValueError(f"Failed to sanitize filename '{filename}': {e}")
