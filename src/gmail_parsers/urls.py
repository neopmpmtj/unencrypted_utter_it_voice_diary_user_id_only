from django.urls import path

from .ckh_invoices import views as ckh_views

app_name = "gmail_parsers"

urlpatterns = [
    path("api/invoices/check/", ckh_views.invoices_check_api, name="invoices_check"),
]
