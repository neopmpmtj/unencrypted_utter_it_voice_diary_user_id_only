from django.apps import AppConfig


class InvoiceParserConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.invoice_parser"
    verbose_name = "Invoice Parser"
