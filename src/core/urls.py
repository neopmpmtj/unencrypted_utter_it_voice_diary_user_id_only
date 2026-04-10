from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('terms/', views.TermsView.as_view(), name='terms'),
    path('test/', views.test_page, name='test'),
]
