"""
Quota Enforcement Services

Token-based daily quotas derived from APIUsageLog (input_tokens + output_tokens).
Per-tier limits; test users and app admins bypass.

Every public function checks ``user.is_app_admin`` and ``user.is_test_user``
first -- when either is True, an "allowed / unlimited" response is returned.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, Tuple

from django.db.models import Sum
from django.utils import timezone

from src.accounts.models import APIUsageLog
from src.common.config import get_config

logger = logging.getLogger(__name__)

TOKEN_USAGE_TYPES = ("input_tokens", "output_tokens")


# ---------------------------------------------------------------------------
# Test-user and app-admin checks
# ---------------------------------------------------------------------------

def is_test_user(user) -> bool:
    """Return True when the user is a test user (all quotas bypassed)."""
    return getattr(user, "is_test_user", False)


def is_app_admin_user(user) -> bool:
    """Return True when the user is an app admin (no quotas, no rate limits, no usage card)."""
    return getattr(user, "is_app_admin", False)


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

def get_today_token_sum(user) -> int:
    """
    Return the sum of input_tokens + output_tokens for *user* today.
    Uses server date (timezone-aware start of day). DST-safe: uses localdate()
    and next-day midnight (not +24h) to avoid wrong window during DST transitions.
    """
    tz = timezone.get_current_timezone()
    local_date = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(local_date, time.min), tz)
    next_date = local_date + timedelta(days=1)
    today_end = timezone.make_aware(datetime.combine(next_date, time.min), tz)

    result = (
        APIUsageLog.objects.filter(
            user=user,
            usage_type__in=TOKEN_USAGE_TYPES,
            created_at__gte=today_start,
            created_at__lt=today_end,
        ).aggregate(total=Sum("amount"))
    )
    total = result.get("total")
    if total is None:
        return 0
    return int(Decimal(total))


def _is_unlimited(limit: int) -> bool:
    """0 means unlimited in TokenQuotaConfig."""
    return limit == 0


# ---------------------------------------------------------------------------
# Token quota check
# ---------------------------------------------------------------------------

def check_token_quota(user) -> Tuple[bool, int, Dict[str, Any]]:
    """
    Check whether *user* may perform an operation that consumes tokens.

    Returns (allowed, remaining_tokens, info).
    """
    tier = getattr(user, "tier", "free") or "free"
    config = get_config()
    limit = config.token_quotas.get_limit_for_tier(tier)

    if is_app_admin_user(user):
        return True, 0, {
            "tier": tier,
            "is_test_user": False,
            "is_app_admin": True,
            "used_tokens": 0,
            "limit_tokens": 0,
            "remaining_tokens": 0,
        }

    if is_test_user(user):
        return True, 0, {
            "tier": tier,
            "is_test_user": True,
            "is_app_admin": False,
            "used_tokens": 0,
            "limit_tokens": 0,
            "remaining_tokens": 0,
        }

    if _is_unlimited(limit):
        return True, 0, {
            "tier": tier,
            "is_test_user": False,
            "is_app_admin": False,
            "used_tokens": 0,
            "limit_tokens": 0,
            "remaining_tokens": 0,
        }

    used = get_today_token_sum(user)
    remaining = max(0, limit - used)
    allowed = remaining > 0

    info = {
        "tier": tier,
        "is_test_user": False,
        "is_app_admin": False,
        "used_tokens": used,
        "limit_tokens": limit,
        "remaining_tokens": remaining,
    }
    if not allowed:
        logger.info(
            "Token quota exhausted for user %s (used %d / %d)",
            user, used, limit,
        )
    return allowed, remaining, info


# ---------------------------------------------------------------------------
# Feature access (all users have access to all resources)
# ---------------------------------------------------------------------------

def can_use_feature(user, feature: str) -> bool:
    """
    Return True if *user* may use *feature*.

    All users have access to all resources (edit, rewrite, batch_calendar).
    Test users and app admins bypass; for others, access is granted by default.
    """
    return True


# ---------------------------------------------------------------------------
# Dashboard tier summary
# ---------------------------------------------------------------------------

def get_dashboard_tier_summary(user) -> Dict[str, Any]:
    """
    Return tier, show_usage_card, and token stats for dashboard display.
    """
    allowed, remaining, info = check_token_quota(user)
    return {
        "tier": info["tier"],
        "show_usage_card": not info["is_app_admin"],
        "tokens_used": info["used_tokens"],
        "tokens_limit": info["limit_tokens"],
    }


# ---------------------------------------------------------------------------
# Summary for API endpoint
# ---------------------------------------------------------------------------

def get_user_quota_summary(user) -> Dict[str, Any]:
    """
    Build the full quota summary returned by ``GET /voice/quota/``.
    """
    allowed, remaining, info = check_token_quota(user)
    used = info["used_tokens"]
    limit = info["limit_tokens"]

    return {
        "tier": info["tier"],
        "is_test_user": info["is_test_user"],
        "is_app_admin": info["is_app_admin"],
        "show_usage_card": not info["is_app_admin"],
        "tokens": {
            "used": used,
            "limit": limit,
            "remaining": 0 if _is_unlimited(limit) else max(0, limit - used),
        },
        "features": {
            "edit": True,
        },
    }
