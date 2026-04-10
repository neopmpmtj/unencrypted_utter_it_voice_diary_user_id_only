import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from src.common.config import get_config

logger = logging.getLogger(__name__)


def pricing_page(request):
    """Public pricing page — no login required."""
    cfg = get_config().stripe
    context = {
        "publishable_key": cfg.publishable_key,
        "trial_days": cfg.trial_days,
    }
    return render(request, "billing/pricing.html", context)


@login_required
@require_http_methods(["POST"])
def checkout_view(request, tier: str):
    """
    Create a Stripe Checkout Session for the given tier and redirect there.

    Only 'pro' and 'ultra' are valid tiers.
    """
    if tier not in ("pro", "ultra"):
        return redirect("billing:pricing")

    from src.billing.services import create_checkout_session

    success_url = request.build_absolute_uri(reverse("billing:success"))
    cancel_url = request.build_absolute_uri(reverse("billing:pricing"))

    try:
        checkout_url = create_checkout_session(
            user=request.user,
            tier=tier,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as exc:
        logger.error("Failed to create Checkout Session for user %s: %s", request.user.email, exc)
        return redirect("billing:pricing")

    return redirect(checkout_url)


@login_required
def checkout_success(request):
    """Landing page after successful Stripe Checkout."""
    return render(request, "billing/checkout_success.html")


@login_required
def customer_portal(request):
    """Create a Stripe Customer Portal session and redirect there."""
    from src.billing.services import create_portal_session

    return_url = request.build_absolute_uri(reverse("billing:subscription"))

    try:
        portal_url = create_portal_session(user=request.user, return_url=return_url)
    except Exception as exc:
        logger.error("Failed to create portal session for user %s: %s", request.user.email, exc)
        return redirect("billing:subscription")

    return redirect(portal_url)


@login_required
def subscription_view(request):
    """Current plan dashboard — shows tier, status, next billing date."""
    from src.billing.models import Subscription

    subscription = None
    try:
        subscription = request.user.subscription
    except Subscription.DoesNotExist:
        pass

    # Grandfathered: has a tier but no Subscription row
    is_grandfathered = (
        request.user.tier != "free"
        and subscription is None
    )

    context = {
        "subscription": subscription,
        "is_grandfathered": is_grandfathered,
        "current_tier": request.user.tier,
    }
    return render(request, "billing/subscription.html", context)
