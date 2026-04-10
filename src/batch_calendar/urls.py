"""Batch Calendar URL configuration."""

from django.urls import path

from . import views

app_name = "batch_calendar"

urlpatterns = [
    path("api/parse/", views.parse_api, name="api_parse"),
    path("api/confirm/<uuid:batch_id>/", views.confirm_api, name="api_confirm"),
    path("api/cancel/<uuid:batch_id>/", views.cancel_api, name="api_cancel"),
    path("api/status/<uuid:batch_id>/", views.status_api, name="api_status"),
    path("confirm/<uuid:batch_id>/", views.batch_confirmation_view, name="confirm"),
    path("pending/", views.pending_list_view, name="pending_list"),
]
