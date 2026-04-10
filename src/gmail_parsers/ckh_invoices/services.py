"""
Invoice checker service.

Returns whether user's INBOX contains emails with invoice-related keywords
in subject, and whether any such email has attachments.
"""

from src.common.google_account.auth import verify_gmail_permissions
from src.common.google_account.gmail_services import (
    search_inbox_messages,
    message_has_attachments,
)

from .config import TRIGGER_WORDS


def _build_invoice_query() -> str:
    """Build Gmail search query from trigger words (subject:word OR subject:word ...)."""
    parts = [f"subject:{w}" for w in TRIGGER_WORDS]
    return " OR ".join(parts)


def check_invoice_emails_in_inbox(user) -> dict:
    """
    Check if user's INBOX contains invoice emails and whether any have attachments.

    Args:
        user: Django User instance

    Returns:
        {"messages": [...]} — messages: list of {"id": str, "has_attachment": bool}
        per matching email. Empty list if user has no Gmail permission or no matching emails.
    """
    result = {"messages": []}
    if not verify_gmail_permissions(user):
        return result

    query = _build_invoice_query()
    msg_ids = search_inbox_messages(user, query, max_results=20)
    if not msg_ids:
        return result

    result["messages"] = [
        {"id": mid, "has_attachment": message_has_attachments(user, mid)}
        for mid in msg_ids
    ]
    return result
