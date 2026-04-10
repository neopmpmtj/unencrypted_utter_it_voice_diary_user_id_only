"""
Quota URL Configuration

Mounted under /voice/ alongside recording URLs.
"""

from django.urls import path
from . import views

app_name = 'quotas'

urlpatterns = [
    path('quota/', views.quota_summary, name='quota_summary'),
    path('quota/dashboard/', views.quota_dashboard, name='quota_dashboard'),
    path('usage/', views.usage_stats_page, name='usage_stats'),
    path('usage/api/', views.usage_stats_api, name='usage_stats_api'),
]
