"""
Shared Gmail API service layer.

Provides reusable INBOX search for gmail_parsers and other Gmail modules.
"""

import base64
from typing import Dict, List, Any

from src.common.google_account.auth import (
    get_authenticated_service,
    GoogleAuthError,
)
from src.common.logging_utils.logging_config import get_logger

logger = get_logger("gmail_services")

_PDF_MIME_TYPES = {"application/pdf"}


def get_message(user, msg_id: str, format: str = "metadata") -> Dict[str, Any]:
    """
    Fetch a single Gmail message by ID.

    Args:
        user: Django User instance
        msg_id: Gmail message ID
        format: 'minimal', 'metadata', or 'full' (default metadata for attachment check)

    Returns:
        Message dict from Gmail API
    """
    service = get_authenticated_service(user, "gmail")
    return (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format=format)
        .execute()
    )


def message_has_attachments(user, msg_id: str) -> bool:
    """
    Check if a Gmail message has any attachments.

    Handles forwarded messages (message/rfc822) where attachments live in the
    inner message. Uses format='full' to get the complete MIME tree.

    Args:
        user: Django User instance
        msg_id: Gmail message ID

    Returns:
        True if message has at least one attachment
    """
    msg = get_message(user, msg_id, format="full")
    payload = msg.get("payload") or {}
    parts = payload.get("parts") or []

    def _part_has_attachment(part: dict) -> bool:
        if part.get("filename"):
            return True
        body = part.get("body") or {}
        if body.get("attachmentId"):
            return True
        return False

    def _check_parts(plist: list) -> bool:
        for p in plist:
            if _part_has_attachment(p):
                return True
            nested = (p.get("parts") or [])
            if nested and _check_parts(nested):
                return True
            inner_payload = p.get("payload") or {}
            inner_parts = inner_payload.get("parts") or []
            if inner_parts and _check_parts(inner_parts):
                return True
        return False

    return _check_parts(parts)


def get_or_create_label(user, name: str) -> str:
    """
    Get label ID by name, or create the label if it does not exist. Idempotent.

    Args:
        user: Django User instance
        name: Label name (e.g. "UtterIt/InvoiceParsed")

    Returns:
        Label ID for use with messages.modify addLabelIds
    """
    service = get_authenticated_service(user, "gmail")
    result = service.users().labels().list(userId="me").execute()
    labels = result.get("labels", [])
    for label in labels:
        if label.get("name") == name:
            return label["id"]
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "messageListVisibility": "show",
                "labelListVisibility": "labelShow",
            },
        )
        .execute()
    )
    return created["id"]


def add_label_to_message(user, msg_id: str, label_id: str) -> None:
    """
    Add a label to a Gmail message.

    Args:
        user: Django User instance
        msg_id: Gmail message ID
        label_id: Label ID from get_or_create_label or labels.list
    """
    service = get_authenticated_service(user, "gmail")
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"addLabelIds": [label_id]},
    ).execute()


def search_inbox_messages(user, query: str, max_results: int = 100) -> List[str]:
    """
    Search INBOX for messages matching the Gmail query.

    Args:
        user: Django User instance
        query: Gmail search query (e.g. "subject:invoice OR subject:fatura")
        max_results: Maximum number of message IDs to return (default 100)

    Returns:
        List of message IDs

    Raises:
        GoogleAuthError: If user has no tokens or authentication fails
    """
    service = get_authenticated_service(user, "gmail")
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], q=query, maxResults=max_results)
        .execute()
    )
    messages = result.get("messages", [])
    return [m["id"] for m in messages]


def download_attachment(user, msg_id: str, attachment_id: str) -> bytes:
    """
    Download a single attachment by its attachment ID.

    Args:
        user: Django User instance
        msg_id: Gmail message ID
        attachment_id: Attachment ID from the message part body

    Returns:
        Raw attachment bytes
    """
    service = get_authenticated_service(user, "gmail")
    att = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=msg_id, id=attachment_id)
        .execute()
    )
    return base64.urlsafe_b64decode(att["data"])


def get_pdf_attachments(user, msg_id: str) -> List[Dict[str, Any]]:
    """
    Download all PDF attachments from a Gmail message.

    Walks the MIME tree (including nested/forwarded messages) and returns
    decoded bytes for every part whose mimeType is application/pdf or
    whose filename ends with .pdf.

    Args:
        user: Django User instance
        msg_id: Gmail message ID

    Returns:
        List of dicts: [{"filename": str, "data": bytes, "mime_type": str}, ...]
    """
    msg = get_message(user, msg_id, format="full")
    payload = msg.get("payload") or {}
    parts = payload.get("parts") or []

    results: List[Dict[str, Any]] = []

    def _is_pdf(part: dict) -> bool:
        mime = (part.get("mimeType") or "").lower()
        fname = (part.get("filename") or "").lower()
        return mime in _PDF_MIME_TYPES or fname.endswith(".pdf")

    def _collect(plist: list) -> None:
        for p in plist:
            if _is_pdf(p) and p.get("filename"):
                body = p.get("body") or {}
                att_id = body.get("attachmentId")
                if att_id:
                    data = download_attachment(user, msg_id, att_id)
                    results.append({
                        "filename": p["filename"],
                        "data": data,
                        "mime_type": (p.get("mimeType") or "application/pdf").lower(),
                    })

            nested = p.get("parts") or []
            if nested:
                _collect(nested)
            inner_payload = p.get("payload") or {}
            inner_parts = inner_payload.get("parts") or []
            if inner_parts:
                _collect(inner_parts)

    _collect(parts)
    return results
