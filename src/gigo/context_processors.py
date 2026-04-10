"""
Context processors for the GIGO app.

Provides gigo_alert_pending to templates for showing the nudge banner.
"""

from .services import get_alert_pending


def gigo_alert(request):
    """
    Inject gigo_alert_pending into template context.

    Returns:
        dict with 'gigo_alert_pending' (bool).
    """
    pending = False
    if hasattr(request, "user") and request.user.is_authenticated:
        pending = get_alert_pending(request.user)
    return {"gigo_alert_pending": pending}
