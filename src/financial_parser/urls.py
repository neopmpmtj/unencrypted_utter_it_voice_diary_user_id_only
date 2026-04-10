from django.urls import path
from src.financial_parser import views

app_name = "financial_parser"

urlpatterns = [
    path("financials/",                               views.financials_page,         name="financials"),
    path("api/financials/",                           views.financials_list_api,     name="api_financials_list"),
    path("api/financials/create/",                    views.financial_create_api,    name="api_financial_create"),
    path("api/financials/<uuid:record_id>/record/",   views.financial_record_api,    name="api_financial_record"),
    path("api/financials/<uuid:item_id>/item/",       views.financial_item_api,      name="api_financial_item"),
    path("api/financials/<uuid:record_id>/items/",    views.financial_items_add_api, name="api_financial_items_add"),
]
