from django.urls import path

from .pdf_parser import views as pdf_views

app_name = "invoice_parser"

urlpatterns = [
    path("api/parse-pdf/", pdf_views.parse_pdf_api, name="parse_pdf"),
]
