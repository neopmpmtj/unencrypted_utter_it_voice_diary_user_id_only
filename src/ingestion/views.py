from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
import json

from .services import (
    create_audio_entry,
    create_gmail_entry,
)


@login_required
@require_POST
def ingest_audio(request):
    payload = json.loads(request.body)
    item = create_audio_entry(
        user=request.user,
        storage_url=payload["storage_url"],
        title=payload.get("title", ""),
        occurred_at=payload.get("occurred_at"),
    )
    return JsonResponse({"id": str(item.id)})


@login_required
@require_POST
def ingest_gmail(request):
    payload = json.loads(request.body)
    item = create_gmail_entry(
        user=request.user,
        gmail_message_id=payload["gmail_message_id"],
    )
    return JsonResponse({"id": str(item.id)})

