from django.contrib import admin

from src.billing.models import Subscription, StripeCustomer, StripeWebhookEvent


@admin.register(StripeCustomer)
class StripeCustomerAdmin(admin.ModelAdmin):
    list_display = ("user", "stripe_customer_id", "created_at")
    search_fields = ("user__email", "stripe_customer_id")
    readonly_fields = ("user", "stripe_customer_id", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "current_period_end", "cancel_at_period_end", "updated_at")
    list_filter = ("tier", "status", "cancel_at_period_end")
    search_fields = ("user__email", "stripe_subscription_id", "stripe_price_id")
    readonly_fields = (
        "user",
        "stripe_subscription_id",
        "stripe_price_id",
        "tier",
        "status",
        "trial_end",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "canceled_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("stripe_event_id", "event_type", "processed_at")
    list_filter = ("event_type",)
    search_fields = ("stripe_event_id", "event_type")
    readonly_fields = ("stripe_event_id", "event_type", "processed_at", "payload")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
