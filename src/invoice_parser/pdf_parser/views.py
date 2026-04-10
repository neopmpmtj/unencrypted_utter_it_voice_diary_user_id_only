"""
Invoice parser API views.
"""

from django.http import JsonResponse

from src.accounts.decorators import app_admin_required
from src.common.google_account.auth import verify_gmail_permissions

from .services import process_invoice_messages


@app_admin_required
def parse_pdf_api(request):
    """
    POST /invoice-parser/api/parse-pdf/

    Searches Gmail inbox for invoice emails, downloads PDF attachments,
    and extracts structured data via OpenAI.

    Returns:
        {"results": [...], "errors": [...], "summary": {...}}
        403 if Gmail not connected.
        503 on unexpected errors.
    """
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)

    if not verify_gmail_permissions(request.user):
        return JsonResponse(
            {
                "error": "gmail_not_connected",
                "message": "Connect Google account with Gmail access to parse invoices.",
            },
            status=403,
        )

    try:
        result = process_invoice_messages(request.user)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse(
            {"error": "parse_failed", "message": str(e)},
            status=503,
        )
