"""
Context processors for the batch_calendar app.

Provides pending calendar count to all templates (BatchCalendarRequest PENDING only).
"""

from .models import BatchCalendarRequest, BatchRequestStatus


def get_pending_calendar_count(user):
    """
    Return the number of pending batch calendar items for a user.
    """
    if not user or not user.is_authenticated:
        return 0
    return BatchCalendarRequest.objects.filter(
        user=user,
        status=BatchRequestStatus.PENDING,
    ).count()


def pending_calendar_events(request):
    """
    Add pending_calendar_count to every template context for navbar badge.
    """
    return {"pending_calendar_count": get_pending_calendar_count(request.user)}
