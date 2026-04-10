"""
Onboarding Middleware

Ensures new users complete the language selection onboarding
before accessing the main application.
"""

from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation


class UserInterfaceLanguageMiddleware:
    """
    For authenticated users, activate the interface language from UserPreferences.
    This ensures the UI language persists across browser clears (cookies/localStorage).
    Runs after AuthenticationMiddleware; overrides LocaleMiddleware's cookie-based choice.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                from .models import UserPreferences
                prefs = UserPreferences.objects.filter(user=request.user).first()
                if prefs and prefs.interface_language:
                    translation.activate(prefs.interface_language)
            except Exception:
                pass
        return self.get_response(request)


class OnboardingMiddleware:
    """
    Middleware that redirects users who haven't completed onboarding
    to the onboarding page.
    
    Skips redirect for:
    - Anonymous users (not logged in)
    - Staff/admin users
    - Requests to onboarding, logout, and static URLs
    - Users who have already completed onboarding
    """
    
    # URLs that should be accessible without completing onboarding
    EXEMPT_URL_NAMES = [
        'core:test',
        'accounts:onboarding',
        'accounts:logout',
        'accounts:login',
        'accounts:register',
        'accounts:password_reset',
        'accounts:password_reset_done',
        'accounts:password_reset_confirm',
        'accounts:password_reset_complete',
        'accounts:verify_email',
        'accounts:resend_verification',
        'accounts:google_login',
        'accounts:google_callback',
        'accounts:google_link_confirm',
    ]
    
    # URL prefixes that should be exempt (for static files, admin, etc.)
    EXEMPT_URL_PREFIXES = [
        '/static/',
        '/media/',
        '/admin/',
        '/__debug__/',
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Skip for anonymous users
        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # Skip for staff/admin users
        if request.user.is_staff or request.user.is_superuser:
            return self.get_response(request)
        
        # Skip for exempt URL prefixes
        for prefix in self.EXEMPT_URL_PREFIXES:
            if request.path.startswith(prefix):
                return self.get_response(request)
        
        # Skip for exempt URL names
        try:
            from django.urls import resolve
            resolved = resolve(request.path)
            url_name = f"{resolved.namespace}:{resolved.url_name}" if resolved.namespace else resolved.url_name
            if url_name in self.EXEMPT_URL_NAMES:
                return self.get_response(request)
        except Exception:
            pass
        
        # Check if user has completed onboarding
        # Users without preferences or with onboarding_completed=False need to complete onboarding
        if hasattr(request.user, 'preferences') and request.user.preferences.onboarding_completed:
            return self.get_response(request)
        
        # User hasn't completed onboarding - redirect to onboarding page
        return redirect('accounts:onboarding')
