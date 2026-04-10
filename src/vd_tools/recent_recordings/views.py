"""
Views for Listen to Last Recording tool.

Shows the user's last recording with playback. Available for up to N hours (N from admin config).
"""

from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from src.accounts.audio_retention_config import get_audio_retention_hours
from src.common.config import get_config
from src.ingestion.models import IngestItem, FileRole, IngestStatus


MIME_TYPES = {
    '.webm': 'audio/webm',
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
}


@login_required
@require_GET
def list_recordings(request):
    """
    Show the user's last recording. Available for up to N hours (N from admin config).
    Only items with existing audio files are shown.
    """
    retention_hours = get_audio_retention_hours()
    cutoff = timezone.now() - timezone.timedelta(hours=retention_hours)

    # Show only the single most recent recording with audio file
    items = (
        IngestItem.objects.filter(
            user=request.user,
            item_type='audio',
            status__in=(IngestStatus.NEW, IngestStatus.PROCESSED, IngestStatus.TAGGED),
            is_deleted=False,
            occurred_at__gte=cutoff,
        )
        .prefetch_related('files')
        .order_by('-occurred_at')
    )

    recordings = []
    config = get_config()
    audio_base = Path(config.storage.audio_temp_path).resolve()

    for item in items:
        original_file = item.files.filter(role=FileRole.ORIGINAL).first()
        if not original_file or not original_file.storage_url:
            continue

        file_path = Path(original_file.storage_url)
        if not file_path.is_absolute():
            file_path = audio_base / file_path
        try:
            file_path.resolve().relative_to(audio_base)
        except ValueError:
            continue
        if not file_path.exists():
            continue

        title = item.title or ""

        recordings.append({
            'id': str(item.id),
            'title': title or (f"Voice Recording {item.occurred_at.strftime('%Y-%m-%d %H:%M')}" if item.occurred_at else 'Voice Recording'),
            'occurred_at': item.occurred_at,
        })
        break  # Only the last recording

    return render(request, 'recent_recordings/list.html', {
        'recordings': recordings,
        'retention_hours': retention_hours,
    })


@login_required
@require_GET
def serve_audio(request, item_id):
    """
    Stream the original audio file for an IngestItem.
    Verifies ownership and path safety.
    """
    try:
        item = IngestItem.objects.get(
            id=item_id,
            user=request.user,
            item_type='audio',
            is_deleted=False,
        )
    except IngestItem.DoesNotExist:
        raise Http404

    retention_hours = get_audio_retention_hours()
    cutoff = timezone.now() - timezone.timedelta(hours=retention_hours)
    if item.occurred_at is None or item.occurred_at < cutoff:
        raise Http404

    original_file = item.files.filter(role=FileRole.ORIGINAL).first()
    if not original_file or not original_file.storage_url:
        raise Http404

    config = get_config()
    audio_base = Path(config.storage.audio_temp_path).resolve()
    file_path = Path(original_file.storage_url)

    if not file_path.is_absolute():
        file_path = audio_base / file_path
    file_path = file_path.resolve()

    try:
        file_path.resolve().relative_to(audio_base)
    except ValueError:
        raise Http404
    if not file_path.exists():
        raise Http404

    mime_type = MIME_TYPES.get(file_path.suffix.lower(), 'audio/webm')

    response = FileResponse(
        open(file_path, 'rb'),
        content_type=mime_type,
        as_attachment=False,
    )
    response['Content-Disposition'] = 'inline'
    return response
