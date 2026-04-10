"""
Text Rewrite API views.

Single endpoint that accepts text + template name, returns rewritten text.
Quota-limited by tier (rewrites per day).
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from src.text_rewrite.config_text_rewrite.text_rewrite_config import (
    PROMPT_TEMPLATES,
    DEFAULT_TEMPLATE,
)
from src.text_rewrite.services import rewrite_text
from src.text_rewrite.config_text_rewrite.text_rewrite_config import get_rewrite_config
from src.ingestion.tasks import log_api_usage
from src.quotas.services import check_token_quota

logger = logging.getLogger(__name__)


@login_required
@require_POST
def rewrite_entry_api(request):
    """
    Rewrite text using a prompt template.

    POST body: {"text": str, "template": str (optional)}
    Returns:   {"rewritten_text": str, "template_used": str, "tokens": {...}}
    """
    user = request.user

    allowed, remaining, info = check_token_quota(user)
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

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid JSON")}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": _("Text is required")}, status=400)

    template_name = body.get("template", DEFAULT_TEMPLATE)
    if template_name not in PROMPT_TEMPLATES:
        return JsonResponse(
            {
                "error": _(
                    "Unknown template '%(name)s'. Available: %(available)s"
                )
                % {
                    "name": template_name,
                    "available": ", ".join(PROMPT_TEMPLATES),
                }
            },
            status=400,
        )

    try:
        rewritten, tokens = rewrite_text(text, template_name, user=user)
    except ValueError as exc:
        logger.error("Rewrite failed for user %s: %s", user.pk, exc)
        return JsonResponse(
            {"error": _("Rewrite failed. Please try again.")}, status=502
        )
    except Exception as exc:
        logger.exception("Unexpected error during rewrite for user %s", user.pk)
        return JsonResponse(
            {"error": _("An unexpected error occurred.")}, status=500
        )

    config = get_rewrite_config()
    log_api_usage(user, config.model, "input_tokens", tokens.get("input", 0), origin="rewrite_entry_api")
    log_api_usage(user, config.model, "output_tokens", tokens.get("output", 0), origin="rewrite_entry_api")

    return JsonResponse(
        {
            "rewritten_text": rewritten,
            "template_used": template_name,
            "tokens": tokens,
        }
    )
