"""
Text Input Utilities

Provides list normalization and text validation utilities.
"""

import re
from typing import List


def normalize_list_text(text: str) -> str:
    """
    Normalize user input into consistent bullet format for list template.
    
    Algorithm:
    1. Split into candidate items:
       - Prefer splitting by newline first
       - If only one line and contains commas, split by commas too
       - Trim whitespace on each candidate
    2. Drop empty candidates
    3. Ensure bullet format:
       - If line starts with '- ', keep it
       - If starts with '* ', convert to '- '
       - If starts with numbering (1. or 1)), strip and convert to '- '
       - Otherwise prefix with '- '
    4. Join items with newline
    
    Args:
        text: Raw user input text
        
    Returns:
        Normalized text with consistent '- ' bullet prefix on each line
    """
    if not text or not text.strip():
        return ""
    
    # Split by newlines first
    lines = text.strip().split('\n')
    
    # If single line with commas, split by commas
    if len(lines) == 1 and ',' in lines[0]:
        lines = lines[0].split(',')
    
    # Process each candidate line
    normalized_items: List[str] = []
    for line in lines:
        item = line.strip()
        if not item:
            continue
        
        # Normalize to bullet format
        item = _normalize_bullet(item)
        normalized_items.append(item)
    
    return '\n'.join(normalized_items)


def _normalize_bullet(item: str) -> str:
    """
    Ensure item has '- ' prefix, stripping any existing bullets/numbering.
    
    Args:
        item: Single list item (already stripped)
        
    Returns:
        Item with '- ' prefix
    """
    # Already has dash bullet
    if item.startswith('- '):
        return item
    
    # Has asterisk bullet - convert to dash
    if item.startswith('* '):
        return '- ' + item[2:]
    
    # Has numbering like "1." "1)" "1:" etc - strip and convert
    # Matches: 1. 1) 1: 10. 10) etc.
    numbering_pattern = re.compile(r'^(\d+)[.\):\-]\s*')
    match = numbering_pattern.match(item)
    if match:
        return '- ' + item[match.end():]
    
    # Has dash without space after (e.g., "-item")
    if item.startswith('-') and len(item) > 1 and item[1] != ' ':
        return '- ' + item[1:]
    
    # Has asterisk without space after
    if item.startswith('*') and len(item) > 1 and item[1] != ' ':
        return '- ' + item[1:]
    
    # No existing bullet - add one
    return '- ' + item


def is_whitespace_only(text: str) -> bool:
    """
    Check if text is empty or contains only whitespace.
    
    Args:
        text: Text to check
        
    Returns:
        True if text is None, empty, or whitespace-only
    """
    if text is None:
        return True
    return not text.strip()


def validate_template_type(template_type: str) -> bool:
    """
    Validate that template_type is an allowed value.
    
    Args:
        template_type: The template type to validate
        
    Returns:
        True if valid, False otherwise
    """
    return template_type in ('plain', 'list')
