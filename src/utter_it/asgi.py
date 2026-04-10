"""
ASGI config for Voice Diary project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/

This configuration supports both HTTP and WebSocket protocols via Django Channels.
Works with both Daphne and Uvicorn ASGI servers.
"""

import os

from decouple import config
from django.core.asgi import get_asgi_application

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE',
    config('DJANGO_SETTINGS_MODULE', default='src.utter_it.settings.dev'),
)

# Thread pool size for synchronous Django views dispatched by asgiref.
# On 1 vCPU: 3 threads allows I/O concurrency without excessive context switching.
# Scale with CPU cores (e.g., 6 for 2-core, 10 for 4-core).
os.environ.setdefault('ASGI_THREADS', '3')

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

# Import after Django setup
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

from src.recordings.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
