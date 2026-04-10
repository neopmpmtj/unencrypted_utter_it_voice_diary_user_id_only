"""
Stripe webhook handler.

Mounted at POST /billing/webhook/ (CSRF-exempt via @csrf_exempt).

Processing contract:
  - Always returns HTTP 200 to Stripe, even on internal errors, to prevent
    unnecessary retries for non-transient failures.
  - StripeWebhookEvent is written before processing to guarantee idempotency.
  - Each event type is dispatched to a dedicated _handle_* function.
"""
import logging

import stripe
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from src.common.config import get_config

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def webhook_receiver(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = get_config().stripe.webhook_secret

    try:
        stripe.api_key = get_config().stripe.secret_key
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        return HttpResponse(status=400)
    except Exception as exc:
        logger.error("Failed to parse Stripe webhook: %s", exc)
        return HttpResponse(status=400)

    event_id = event["id"]
    event_type = event["type"]

    # Idempotency check
    from src.billing.models import StripeWebhookEvent

    if StripeWebhookEvent.objects.filter(stripe_event_id=event_id).exists():
        logger.debug("Duplicate webhook event %s — skipping", event_id)
        return HttpResponse(status=200)

    StripeWebhookEvent.objects.create(
        stripe_event_id=event_id,
        event_type=event_type,
        payload=event,
    )

    try:
        _dispatch(event_type, event)
    except Exception as exc:
        # Log but still return 200 so Stripe does not retry indefinitely.
        logger.exception("Error processing webhook %s (%s): %s", event_id, event_type, exc)

    return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _dispatch(event_type: str, event: dict) -> None:
    handlers = {
        "checkout.session.completed": _handle_checkout_completed,
        "customer.subscription.updated": _handle_subscription_updated,
        "customer.subscription.deleted": _handle_subscription_deleted,
        "invoice.payment_succeeded": _handle_invoice_payment_succeeded,
        "invoice.payment_failed": _handle_invoice_payment_failed,
    }
    handler = handlers.get(event_type)
    if handler:
        handler(event["data"]["object"])
    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _handle_checkout_completed(session: dict) -> None:
    """checkout.session.completed → create/update Subscription + set tier."""
    from django.contrib.auth import get_user_model
    from src.billing.models import StripeCustomer, Subscription
    from src.billing.services import get_tier_for_price_id, set_user_tier

    User = get_user_model()

    user_id = session.get("metadata", {}).get("user_id")
    tier = session.get("metadata", {}).get("tier")
    stripe_customer_id = session.get("customer")
    stripe_subscription_id = session.get("subscription")

    if not all([user_id, tier, stripe_customer_id, stripe_subscription_id]):
        logger.warning("checkout.session.completed missing required fields: %s", session.get("id"))
        return

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("checkout.session.completed: user %s not found", user_id)
        return

    # Ensure StripeCustomer row exists
    StripeCustomer.objects.get_or_create(
        user=user,
        defaults={"stripe_customer_id": stripe_customer_id},
    )

    # Fetch the full subscription object from Stripe to get period/trial dates
    stripe.api_key = get_config().stripe.secret_key
    sub = stripe.Subscription.retrieve(stripe_subscription_id)

    price_id = sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else ""
    resolved_tier = get_tier_for_price_id(price_id) or tier

    import datetime

    def _ts(val):
        if val is None:
            return None
        return datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc)

    Subscription.objects.update_or_create(
        user=user,
        defaults={
            "stripe_subscription_id": stripe_subscription_id,
            "stripe_price_id": price_id,
            "tier": resolved_tier,
            "status": sub["status"],
            "trial_end": _ts(sub.get("trial_end")),
            "current_period_start": _ts(sub["current_period_start"]),
            "current_period_end": _ts(sub["current_period_end"]),
            "cancel_at_period_end": sub.get("cancel_at_period_end", False),
            "canceled_at": _ts(sub.get("canceled_at")),
        },
    )

    set_user_tier(user, resolved_tier)
    logger.info("checkout.session.completed: user %s upgraded to %s", user.email, resolved_tier)


def _handle_subscription_updated(sub: dict) -> None:
    """customer.subscription.updated → sync Subscription model."""
    from src.billing.models import Subscription
    from src.billing.services import set_user_tier, get_tier_for_price_id

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=sub["id"])
    except Subscription.DoesNotExist:
        logger.warning("subscription.updated: no local Subscription for %s", sub["id"])
        return

    import datetime

    def _ts(val):
        if val is None:
            return None
        return datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc)

    price_id = sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else subscription.stripe_price_id
    tier = get_tier_for_price_id(price_id) or subscription.tier

    subscription.stripe_price_id = price_id
    subscription.tier = tier
    subscription.status = sub["status"]
    subscription.trial_end = _ts(sub.get("trial_end"))
    subscription.current_period_start = _ts(sub["current_period_start"])
    subscription.current_period_end = _ts(sub["current_period_end"])
    subscription.cancel_at_period_end = sub.get("cancel_at_period_end", False)
    subscription.canceled_at = _ts(sub.get("canceled_at"))
    subscription.save()

    if sub["status"] in ("active", "trialing"):
        set_user_tier(subscription.user, tier)
    elif sub["status"] in ("paused", "past_due", "incomplete", "incomplete_expired"):
        set_user_tier(subscription.user, "free")

    logger.info("subscription.updated: %s → status=%s tier=%s", sub["id"], sub["status"], tier)


def _handle_subscription_deleted(sub: dict) -> None:
    """customer.subscription.deleted → mark canceled + downgrade to free."""
    from src.billing.models import Subscription
    from src.billing.services import set_user_tier

    import datetime

    def _ts(val):
        if val is None:
            return None
        return datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc)

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=sub["id"])
    except Subscription.DoesNotExist:
        logger.warning("subscription.deleted: no local Subscription for %s", sub["id"])
        return

    subscription.status = Subscription.STATUS_CANCELED
    subscription.canceled_at = _ts(sub.get("canceled_at"))
    subscription.save(update_fields=["status", "canceled_at", "updated_at"])

    set_user_tier(subscription.user, "free")
    logger.info("subscription.deleted: user %s downgraded to free", subscription.user.email)


def _handle_invoice_payment_succeeded(invoice: dict) -> None:
    """invoice.payment_succeeded → update current_period_end."""
    from src.billing.models import Subscription

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        return

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
    except Subscription.DoesNotExist:
        return

    import datetime

    period_end = invoice.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
    if period_end:
        subscription.current_period_end = datetime.datetime.fromtimestamp(
            period_end, tz=datetime.timezone.utc
        )
        subscription.save(update_fields=["current_period_end", "updated_at"])
        logger.info("invoice.payment_succeeded: updated period_end for sub %s", stripe_subscription_id)


def _handle_invoice_payment_failed(invoice: dict) -> None:
    """invoice.payment_failed → downgrade user to free immediately."""
    from src.billing.models import Subscription
    from src.billing.services import set_user_tier

    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        return

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
    except Subscription.DoesNotExist:
        logger.warning("invoice.payment_failed: no local Subscription for %s", stripe_subscription_id)
        return

    set_user_tier(subscription.user, "free")
    logger.info(
        "invoice.payment_failed: user %s downgraded to free (sub %s)",
        subscription.user.email, stripe_subscription_id,
    )
