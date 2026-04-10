"""
GIGO Monitor Views

API endpoint for dismissing the GIGO nudge alert.
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from .services import dismiss_alert


@login_required
@require_POST
@csrf_protect
def dismiss(request):
    """Clear the GIGO alert_pending flag for the current user."""
    dismiss_alert(request.user)
    return JsonResponse({"ok": True})
