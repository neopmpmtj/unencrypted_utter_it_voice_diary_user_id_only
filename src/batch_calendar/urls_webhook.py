"""URL configuration for calendar webhook (Google push notifications)."""

from django.urls import path

from . import views

urlpatterns = [
    path("webhook/", views.calendar_webhook_receiver, name="webhook"),
]
