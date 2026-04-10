import logging

import stripe

from src.common.config import get_config

logger = logging.getLogger(__name__)


def _stripe_client():
    """Return a configured Stripe client using the secret key from config."""
    cfg = get_config().stripe
    stripe.api_key = cfg.secret_key
    return stripe


def get_or_create_stripe_customer(user):
    """
    Return the StripeCustomer for *user*, creating one in Stripe + DB if needed.

    Returns:
        StripeCustomer instance
    """
    from src.billing.models import StripeCustomer

    try:
        return user.stripe_customer
    except StripeCustomer.DoesNotExist:
        pass

    _stripe_client()
    customer = stripe.Customer.create(
        email=user.email,
        name=user.get_full_name() or user.email,
        metadata={"user_id": str(user.pk)},
    )
    stripe_customer = StripeCustomer.objects.create(
        user=user,
        stripe_customer_id=customer["id"],
    )
    logger.info("Created Stripe customer %s for user %s", customer["id"], user.email)
    return stripe_customer


def create_checkout_session(user, tier: str, success_url: str, cancel_url: str):
    """
    Create a Stripe Checkout Session for the given tier.

    - mode=subscription
    - Card always collected upfront (payment_method_collection='always')
    - Trial only on first-ever subscription; repeat subscribers are charged immediately
    - Trial length from StripeConfig.trial_days

    Returns:
        str — the Checkout Session URL to redirect the user to
    """
    from src.billing.models import Subscription

    cfg = get_config().stripe
    _stripe_client()

    price_id = _price_id_for_tier(tier, cfg)
    if not price_id:
        raise ValueError(f"No Stripe price configured for tier '{tier}'")

    stripe_customer = get_or_create_stripe_customer(user)

    # Only offer a trial to users who have never had a subscription before.
    had_prior_subscription = Subscription.objects.filter(user=user).exists()
    subscription_data: dict = {"metadata": {"user_id": str(user.pk), "tier": tier}}
    if not had_prior_subscription:
        subscription_data["trial_period_days"] = cfg.trial_days

    session = stripe.checkout.Session.create(
        customer=stripe_customer.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        subscription_data=subscription_data,
        payment_method_collection="always",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": str(user.pk), "tier": tier},
    )
    logger.info(
        "Created Checkout Session %s for user %s (tier=%s, trial=%s)",
        session["id"], user.email, tier, not had_prior_subscription,
    )
    return session["url"]


def create_portal_session(user, return_url: str) -> str:
    """
    Create a Stripe Customer Portal session URL.

    Returns:
        str — the portal URL to redirect the user to
    """
    _stripe_client()
    stripe_customer = get_or_create_stripe_customer(user)
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer.stripe_customer_id,
        return_url=return_url,
    )
    return session["url"]


def get_tier_for_price_id(price_id: str) -> str | None:
    """
    Map a Stripe price ID to a tier name ('pro' or 'ultra').

    Returns None if the price ID is not recognised.
    """
    cfg = get_config().stripe
    if price_id == cfg.price_pro_monthly:
        return "pro"
    if price_id == cfg.price_ultra_monthly:
        return "ultra"
    return None


def set_user_tier(user, tier: str) -> None:
    """Update CustomUser.tier and save."""
    if user.tier != tier:
        user.tier = tier
        user.save(update_fields=["tier"])
        logger.info("Set tier=%s for user %s", tier, user.email)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _price_id_for_tier(tier: str, cfg) -> str:
    if tier == "pro":
        return cfg.price_pro_monthly
    if tier == "ultra":
        return cfg.price_ultra_monthly
    return ""
