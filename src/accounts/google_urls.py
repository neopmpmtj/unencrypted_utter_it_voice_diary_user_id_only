"""
Google OAuth URL patterns for accounts app.

These URLs handle:
- Google login initiation and callback
- Account linking confirmation
- Google services connection for existing users
- Google disconnection
"""

from django.urls import path
from . import google_views

# Note: These URLs are included under the 'accounts' namespace
# Full paths will be like /accounts/google/login/, /accounts/google/callback/, etc.

urlpatterns = [
    # Google Login (for new users and existing Google users)
    path('login/', google_views.GoogleLoginView.as_view(), name='google_login'),
    path('callback/', google_views.GoogleCallbackView.as_view(), name='google_callback'),
    
    # Account Linking (when traditional user tries Google login)
    path('link-confirm/', google_views.GoogleLinkConfirmView.as_view(), name='google_link_confirm'),
    
    # Google Services Connection (for existing users needing Google services)
    path('connect/', google_views.GoogleConnectView.as_view(), name='google_connect'),
    path('connect/callback/', google_views.GoogleConnectCallbackView.as_view(), name='google_connect_callback'),
    
    # Disconnect Google
    path('disconnect/', google_views.GoogleDisconnectView.as_view(), name='google_disconnect'),
]
