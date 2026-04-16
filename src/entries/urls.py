from django.urls import path
from . import views

app_name = 'entries'

urlpatterns = [
    path('entries/', views.entries_page, name='list'),
    path(
        'api/attachments/<uuid:file_id>/download/',
        views.serve_attachment,
        name='serve_attachment',
    ),
    path('api/entries/', views.entries_list_api, name='api_list'),
    path('api/entries/<uuid:entry_id>/delete/', views.entry_delete_api, name='api_delete'),
    path('api/entries/<uuid:entry_id>/edit/', views.entry_edit_api, name='api_edit'),
]
