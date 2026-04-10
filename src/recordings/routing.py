"""
WebSocket URL Routing for Recordings App
"""

from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r'ws/pipeline/(?P<item_id>[0-9a-f-]+)/$',
        consumers.PipelineStatusConsumer.as_asgi()
    ),
]
