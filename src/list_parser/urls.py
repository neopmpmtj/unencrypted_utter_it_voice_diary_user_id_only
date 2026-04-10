from django.urls import path
from src.list_parser import views

app_name = "list_parser"

urlpatterns = [
    path("lists/",                              views.lists_page,          name="lists"),
    path("api/lists/",                          views.lists_list_api,      name="api_lists_list"),
    path("api/lists/create/",                   views.list_create_api,     name="api_list_create"),
    path("api/lists/<uuid:record_id>/record/",  views.list_record_api,     name="api_list_record"),
    path("api/lists/<uuid:item_id>/item/",      views.list_item_api,       name="api_list_item"),
    path("api/lists/<uuid:record_id>/items/",   views.list_items_add_api,  name="api_list_items_add"),
]
