"""
Retrieval Views

Chat page and API endpoints for the diary chatbot.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from src.ingestion.tasks import log_api_usage
from src.common.model_picker import get_llm_config
from .models import AssistantChatMessage, ChatSession
from .services import get_session_messages_ordered, query_diary

logger = logging.getLogger(__name__)

_LANG_CODE_TO_NAME = {
    "en": "English",
    "pt": "Portuguese",
    "pt-pt": "Portuguese",
    "pt-br": "Portuguese",
    "es": "Spanish",
    "es-es": "Spanish",
    "fr": "French",
    "de": "German",
}


def _get_user_chat_language(user) -> str | None:
    """Return user's preferred response language (full name) for chat, or None."""
    try:
        prefs = getattr(user, "preferences", None)
        if not prefs:
            from src.accounts.models import UserPreferences
            prefs = UserPreferences.objects.filter(user=user).first()
        if prefs:
            lang = (prefs.interface_language or prefs.preferred_language or "").strip()
            if lang:
                return _LANG_CODE_TO_NAME.get(lang.lower(), lang)
    except Exception:
        pass
    return None


def _chat_i18n():
    return {
        "request_failed": _("Request failed (status %(status)s)"),
        "something_went_wrong": _("Something went wrong (status %(status)s)"),
        "sources": _("Sources:"),
    }


@login_required
@require_GET
def chat_page(request):
    """Render the chat page."""
    session = (
        ChatSession.objects.filter(user=request.user)
        .order_by("-updated_at")
        .first()
    )
    if not session:
        return render(request, "retrieval/chat.html", {
            "latest_session": None,
            "chat_i18n": _chat_i18n(),
        })

    AssistantChatMessage.objects.filter(
        session=session, status=AssistantChatMessage.Status.UNREAD
    ).update(status=AssistantChatMessage.Status.READ)

    ordered = get_session_messages_ordered(session, _user_id=request.user.id)[-10:]
    messages = []
    for role, m in ordered:
        msg = {
            "id": str(m["id"]),
            "role": role,
            "content": m["content"],
            "source_entries": m.get("source_entries", []),
            "metadata": m.get("metadata", {}),
            "created_at": m["created_at"].isoformat() if m.get("created_at") else "",
        }
        messages.append(msg)

    return render(request, "retrieval/chat.html", {
        "latest_session": {"id": str(session.id), "messages": messages},
        "chat_i18n": _chat_i18n(),
    })


@login_required
@require_POST
def chat_message_api(request):
    """
    Send a message and receive an AI answer.

    POST body: {"message": str, "session_id": str|null}
    Returns: {"answer": str, "sources": [...], "session_id": str}
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid JSON")}, status=400)

    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": _("Message cannot be empty")}, status=400)

    from src.quotas.services import check_token_quota

    allowed, remaining, info = check_token_quota(request.user)
    if not allowed:
        return JsonResponse(
            {
                "error": "quota_exceeded",
                "message": _("Daily token quota exceeded. Please try again tomorrow."),
                "quota": {
                    "used_tokens": info.get("used_tokens", 0),
                    "limit_tokens": info.get("limit_tokens", 0),
                    "remaining_tokens": info.get("remaining_tokens", 0),
                },
            },
            status=429,
        )

    session_id = body.get("session_id") or None
    user_language = _get_user_chat_language(request.user)

    try:
        result = query_diary(
            user_id=str(request.user.id),
            user=request.user,
            session_id=session_id,
            user_message=message,
            user_language=user_language,
        )
    except Exception:
        logger.exception("query_diary failed for user %s", request.user.pk)
        return JsonResponse(
            {"error": _("Something went wrong. Please try again.")}, status=500,
        )

    usage = result.get("usage", {})
    if usage and request.user:
        embed_usage = usage.get("embedding", {})
        if embed_usage.get("total"):
            embed_model = get_llm_config("embedding").get("model", "text-embedding-3-small")
            log_api_usage(
                request.user,
                embed_model,
                "input_tokens",
                embed_usage.get("total", 0),
                origin="chat_message_api",
            )
        chat_usage = usage.get("chat", {})
        if chat_usage and chat_usage.get("input", 0) + chat_usage.get("output", 0) > 0:
            chat_model = get_llm_config("diary_chat").get("model", "")
            if chat_model:
                log_api_usage(
                    request.user,
                    chat_model,
                    "input_tokens",
                    chat_usage.get("input", 0),
                    origin="chat_message_api",
                )
                log_api_usage(
                    request.user,
                    chat_model,
                    "output_tokens",
                    chat_usage.get("output", 0),
                    origin="chat_message_api",
                )

    return JsonResponse(result)


@login_required
@require_http_methods(["GET", "POST"])
def sessions_api(request):
    """
    GET  — list chat sessions
    POST — create a new empty session
    """
    if request.method == "GET":
        sessions = list(
            ChatSession.objects.filter(user=request.user)
            .order_by("-updated_at")
            .values("id", "title", "updated_at")[:50]
        )
        for s in sessions:
            s["id"] = str(s["id"])
            s["updated_at"] = s["updated_at"].isoformat() if s["updated_at"] else ""
            s["title"] = s.get("title") or ""
        return JsonResponse({"sessions": sessions})

    # POST — create new session
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}

    title_plain = (body.get("title") or "").strip() or _("New conversation")
    try:
        session = ChatSession.objects.create(
            user=request.user,
            title=title_plain,
        )
    except Exception:
        logger.exception("Failed to create chat session for user %s", request.user.pk)
        return JsonResponse(
            {"error": _("Could not create session.")}, status=500,
        )
    return JsonResponse({
        "id": str(session.id),
        "title": title_plain,
        "updated_at": session.updated_at.isoformat(),
    }, status=201)


@login_required
@require_http_methods(["DELETE"])
def session_detail_api(request, session_id):
    """Delete a chat session."""
    try:
        deleted, _counts = ChatSession.objects.filter(
            id=session_id, user=request.user
        ).delete()
    except Exception:
        logger.exception("Failed to delete session %s", session_id)
        return JsonResponse(
            {"error": _("Could not delete session.")}, status=500,
        )

    if not deleted:
        return JsonResponse({"error": _("Not found")}, status=404)

    return JsonResponse({"deleted": True})


@login_required
@require_GET
def session_messages_api(request, session_id):
    """Return all messages in a session."""
    try:
        session = ChatSession.objects.filter(
            id=session_id, user=request.user
        ).first()
    except Exception:
        logger.exception("Failed to fetch session %s", session_id)
        return JsonResponse(
            {"error": _("Could not load session.")}, status=500,
        )
    if not session:
        return JsonResponse({"error": _("Not found")}, status=404)

    AssistantChatMessage.objects.filter(
        session=session, status=AssistantChatMessage.Status.UNREAD
    ).update(status=AssistantChatMessage.Status.READ)

    try:
        ordered = get_session_messages_ordered(session, _user_id=request.user.id)
        messages = []
        for role, m in ordered:
            msg = {
                "id": str(m["id"]),
                "role": role,
                "content": m["content"],
                "source_entries": m.get("source_entries", []),
                "metadata": m.get("metadata", {}),
                "created_at": m["created_at"].isoformat() if m.get("created_at") else "",
            }
            messages.append(msg)
    except Exception:
        logger.exception("Failed to fetch messages for session %s", session_id)
        return JsonResponse(
            {"error": _("Could not load messages.")}, status=500,
        )

    title_plain = session.title or ""
    return JsonResponse({
        "session_id": str(session.id),
        "title": title_plain,
        "messages": messages,
    })
