from django.urls import path

from . import views

app_name = 'test_microphone'

urlpatterns = [
    path('test-microphone/', views.microphone_test, name='microphone_test'),
]
