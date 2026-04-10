"""
Quota API Views

Provides the ``GET /voice/quota/`` endpoint consumed by the frontend
to display remaining allowances and adapt recorder behaviour.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from src.ingestion.models import IngestItem, ItemType
from src.quotas.services import get_dashboard_tier_summary, get_user_quota_summary

logger = logging.getLogger(__name__)

VALID_SCOPES = frozenset({"today", "week", "month", "all"})


def _get_entry_counts(user, scope: str):
    """Return total_entries, audio_entries, text_entries for the given user and scope."""
    base = IngestItem.objects.filter(
        user=user,
        is_deleted=False,
        item_type__in=[ItemType.AUDIO, ItemType.TEXT],
    )
    now = timezone.now()
    if scope == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        base = base.filter(
            Q(occurred_at__gte=start) | Q(occurred_at__isnull=True, ingested_at__gte=start)
        )
    elif scope == "week":
        start = now - timedelta(days=7)
        base = base.filter(
            Q(occurred_at__gte=start) | Q(occurred_at__isnull=True, ingested_at__gte=start)
        )
    elif scope == "month":
        start = now - timedelta(days=30)
        base = base.filter(
            Q(occurred_at__gte=start) | Q(occurred_at__isnull=True, ingested_at__gte=start)
        )
    # scope == "all": no date filter

    agg = base.aggregate(
        total=Count("id"),
        audio=Count("id", filter=Q(item_type=ItemType.AUDIO)),
        text=Count("id", filter=Q(item_type=ItemType.TEXT)),
    )
    return {
        "total_entries": agg["total"] or 0,
        "audio_entries": agg["audio"] or 0,
        "text_entries": agg["text"] or 0,
    }


@login_required
@require_GET
def usage_stats_page(request):
    """Render the Usage & Statistics HTML page."""
    return render(request, "quotas/usage_stats.html", {})


@login_required
@require_GET
def usage_stats_api(request):
    """
    Return stats and quotas as JSON for the Update stats button.
    Query param: scope=today|week|month|all (default: all)
    """
    scope = request.GET.get("scope", "all").lower()
    if scope not in VALID_SCOPES:
        scope = "all"

    stats = _get_entry_counts(request.user, scope)
    quota = get_user_quota_summary(request.user)
    return JsonResponse({"stats": stats, "quota": quota})


@login_required
@require_GET
def quota_summary(request):
    """
    Return the authenticated user's current quota state.

    Response (200):
    {
        "tier": "free",
        "is_test_user": false,
        "recording": {
            "used_seconds": 120,
            "limit_seconds": 1800,
            "remaining_seconds": 1680
        },
        "text": {
            "used_bytes": 1024,
            "limit_bytes": 15728640,
            "remaining_bytes": 15727616
        },
        "features": {
            "edit": true
        }
    }

    ``limit_seconds: 0`` / ``limit_bytes: 0`` means unlimited.
    """
    summary = get_user_quota_summary(request.user)
    return JsonResponse(summary)


@login_required
@require_GET
def quota_dashboard(request):
    """
    Return tier and show_usage_card for dashboard display.
    Lightweight. Cached per user.
    """
    cache_key = f"quota_dashboard:{request.user.id}"
    ttl = getattr(settings, "QUOTA_DASHBOARD_CACHE_SECONDS", 120)
    summary = cache.get(cache_key)
    if summary is None:
        summary = get_dashboard_tier_summary(request.user)
        cache.set(cache_key, summary, timeout=ttl)
    return JsonResponse(summary)
