"""
Invoice checker API views.
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from src.common.google_account.auth import verify_gmail_permissions

from .services import check_invoice_emails_in_inbox


@login_required
def invoices_check_api(request):
    """
    GET /gmail-parsers/api/invoices/check/

    Returns {"messages": [...]}.
    Returns 403 if user has not granted Gmail scope.
    """
    if not verify_gmail_permissions(request.user):
        return JsonResponse(
            {
                "error": "gmail_not_connected",
                "message": "Connect Google account with Gmail access to check for invoices.",
            },
            status=403,
        )

    try:
        result = check_invoice_emails_in_inbox(request.user)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse(
            {"error": "gmail_unavailable", "message": str(e)},
            status=503,
        )
