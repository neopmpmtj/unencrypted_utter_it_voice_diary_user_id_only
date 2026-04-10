from django.urls import path

from src.billing.views import (
    checkout_success,
    checkout_view,
    customer_portal,
    pricing_page,
    subscription_view,
)
from src.billing.webhooks import webhook_receiver

app_name = 'billing'

urlpatterns = [
    path('pricing/', pricing_page, name='pricing'),
    path('checkout/<str:tier>/', checkout_view, name='checkout'),
    path('success/', checkout_success, name='success'),
    path('portal/', customer_portal, name='portal'),
    path('subscription/', subscription_view, name='subscription'),
    path('webhook/', webhook_receiver, name='webhook'),
]
