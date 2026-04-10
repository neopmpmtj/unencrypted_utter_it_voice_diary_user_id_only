from django.urls import path

from . import views

app_name = 'recent_recordings'

urlpatterns = [
    path('listen-recordings/', views.list_recordings, name='list'),
    path('listen-recordings/<uuid:item_id>/audio/', views.serve_audio, name='serve_audio'),
]
