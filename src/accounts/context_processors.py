"""
Context processors for the accounts app.

Provides theme preferences (dark_mode, accent_theme) and PWA standalone mode
flag to all templates.
"""

from .models import GlobalSettings, UserPreferences


def theme_preferences(request):
    """
    Inject the authenticated user's theme preferences into every template context.

    Returns:
        dict with 'dark_mode' (bool as string), 'accent_theme' (str),
        and 'standalone_app_ui' (bool).
    """
    dark_mode = False
    accent_theme = 'green'
    global_allowed = GlobalSettings.get_value('pwa.standalone_ui_allowed', True)
    standalone_app_ui = bool(global_allowed)

    transcription_text_size = 'small'

    if hasattr(request, 'user') and request.user.is_authenticated:
        try:
            prefs = UserPreferences.objects.filter(user=request.user).first()
            if prefs:
                dark_mode = prefs.dark_mode
                accent_theme = prefs.accent_theme or 'green'
                standalone_app_ui = bool(global_allowed) and bool(prefs.standalone_app_ui)
                if prefs.transcription_text_size:
                    transcription_text_size = prefs.transcription_text_size
        except Exception:
            pass

    return {
        'dark_mode': 'true' if dark_mode else 'false',
        'accent_theme': accent_theme,
        'standalone_app_ui': standalone_app_ui,
        'transcription_text_size': transcription_text_size,
    }
