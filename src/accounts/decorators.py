"""
View decorators for access control.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden


def app_admin_required(view_func):
    """Require user to be logged in and have is_app_admin=True. Return 403 otherwise."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not getattr(request.user, "is_app_admin", False):
            return HttpResponseForbidden()
        return view_func(request, *args, **kwargs)

    return _wrapped
