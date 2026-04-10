"""
Recording URL Configuration

Routes for the recording app at /voice/
"""

from django.urls import path
from . import views

app_name = 'recordings'

urlpatterns = [
    path('', views.recording_page, name='record'),
    path('upload/', views.upload_audio, name='upload'),
    path('upload-to-drive/', views.upload_file_to_drive, name='upload_to_drive'),
    path('status/<uuid:item_id>/', views.get_status, name='status'),
    path('status/pending/<uuid:temp_id>/', views.get_pending_status, name='status_pending'),
    path('update/<uuid:item_id>/', views.update_entry_content, name='update_entry'),
    path('manifest.json', views.manifest, name='manifest'),
    path('service-worker.js', views.service_worker, name='service-worker'),
]
