"""
GIGO URL Configuration
"""

from django.urls import path
from . import views

app_name = "gigo"

urlpatterns = [
    path("dismiss/", views.dismiss, name="dismiss"),
]
