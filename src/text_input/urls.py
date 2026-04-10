from django.urls import path

from . import views

app_name = 'text_input'

urlpatterns = [
    path('', views.text_input_page, name='page'),
    path('ingest/', views.ingest_text, name='ingest'),
]
