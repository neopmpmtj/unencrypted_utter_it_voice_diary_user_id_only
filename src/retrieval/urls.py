from django.urls import path

from . import views

app_name = "retrieval"

urlpatterns = [
    # Chat page
    path("", views.chat_page, name="chat"),
    # Chat API
    path("api/chat/", views.chat_message_api, name="api_chat"),
    path("api/sessions/", views.sessions_api, name="api_sessions"),
    path("api/sessions/<uuid:session_id>/", views.session_detail_api, name="api_session_detail"),
    path("api/sessions/<uuid:session_id>/messages/", views.session_messages_api, name="api_session_messages"),
]
