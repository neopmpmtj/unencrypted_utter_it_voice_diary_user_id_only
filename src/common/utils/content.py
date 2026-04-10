"""
Plaintext accessors for IngestItem title and content.

Used by parsers (batch_calendar, list_parser, financial_parser).
"""

from src.ingestion.models import IngestItem


def get_item_title_and_content(item: IngestItem) -> tuple[str, str]:
    """Return (title, content_text) as stored on the item (plaintext)."""
    return (item.title or "", item.content_text or "")
