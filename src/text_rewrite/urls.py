from django.urls import path

from . import views

app_name = "text_rewrite"

urlpatterns = [
    path(
        "api/entries/rewrite/",
        views.rewrite_entry_api,
        name="api_rewrite",
    ),
]
