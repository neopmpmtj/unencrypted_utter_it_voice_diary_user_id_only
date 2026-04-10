from django.urls import path

from . import views

app_name = "managed_lists"

urlpatterns = [
    path("todos/", views.todos_page, name="todos"),
    path("api/todos/", views.todos_list_api, name="api_todos_list"),
    path("api/todos/bulk/", views.todos_bulk_api, name="api_todos_bulk"),
    path("api/todos/create/", views.todo_create_api, name="api_todo_create"),
    path("api/todos/<uuid:item_id>/", views.todo_item_api, name="api_todo_item"),
    path("api/todos/<uuid:record_id>/record/", views.todo_record_api, name="api_todo_record"),
]
