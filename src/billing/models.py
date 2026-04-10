from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class StripeCustomer(models.Model):
    """Links CustomUser to their Stripe customer record."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='stripe_customer',
    )
    stripe_customer_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'billing_stripecustomer'
        verbose_name = 'Stripe Customer'
        verbose_name_plural = 'Stripe Customers'

    def __str__(self):
        return f"{self.user.email} → {self.stripe_customer_id}"


class Subscription(models.Model):
    """Mirrors the Stripe Subscription object for the user."""

    STATUS_TRIALING = 'trialing'
    STATUS_ACTIVE = 'active'
    STATUS_PAST_DUE = 'past_due'
    STATUS_CANCELED = 'canceled'
    STATUS_INCOMPLETE = 'incomplete'
    STATUS_INCOMPLETE_EXPIRED = 'incomplete_expired'

    STATUS_CHOICES = [
        (STATUS_TRIALING, _('Trialing')),
        (STATUS_ACTIVE, _('Active')),
        (STATUS_PAST_DUE, _('Past Due')),
        (STATUS_CANCELED, _('Canceled')),
        (STATUS_INCOMPLETE, _('Incomplete')),
        (STATUS_INCOMPLETE_EXPIRED, _('Incomplete Expired')),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    stripe_subscription_id = models.CharField(max_length=255, unique=True)
    stripe_price_id = models.CharField(max_length=255)
    tier = models.CharField(max_length=20)  # 'pro' or 'ultra'
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    trial_end = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_subscription'
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'

    def __str__(self):
        return f"{self.user.email} — {self.tier} ({self.status})"

    @property
    def is_active(self):
        return self.status in (self.STATUS_TRIALING, self.STATUS_ACTIVE)


class StripeWebhookEvent(models.Model):
    """Idempotency log to prevent duplicate webhook processing."""

    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    processed_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()

    class Meta:
        db_table = 'billing_stripewebokevent'
        verbose_name = 'Stripe Webhook Event'
        verbose_name_plural = 'Stripe Webhook Events'

    def __str__(self):
        return f"{self.event_type} ({self.stripe_event_id})"
