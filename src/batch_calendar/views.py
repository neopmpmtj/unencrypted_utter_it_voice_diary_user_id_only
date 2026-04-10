"""
Batch Calendar API Views

REST endpoints for parse, confirm, cancel, status, pending list, and webhook.
"""

import copy
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.translation import gettext as _
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as django_timezone
from django.views.decorators.http import require_GET, require_POST

from src.common.google_account.auth import GoogleAuthError
from src.common.model_picker import get_llm_config
from src.ingestion.tasks import log_api_usage
from .calendar_client import insert_event
from .conflict_resolution import check_batch_availability
from .config_batch_calendar.batch_calendar_config import get_batch_calendar_config
from .models import BatchCalendarEvent, BatchCalendarRequest, BatchEventStatus, BatchRequestStatus
from .services import extract_batch_events

logger = logging.getLogger(__name__)


def _get_calendar_id(request):
    """Get calendar_id from user config or default."""
    config = get_batch_calendar_config()
    calendar_id = config.calendar_id
    if request.user.is_authenticated:
        try:
            from src.accounts.models import UserFeatureConfig

            uf = UserFeatureConfig.get_for_user(request.user)
            if uf.default_calendar_id:
                calendar_id = uf.default_calendar_id
        except Exception:
            pass
    return calendar_id


def _ensure_aware(dt, tz_str):
    """Make datetime timezone-aware."""
    if dt is None:
        return None
    if dt.tzinfo:
        return dt
    try:
        tz = ZoneInfo(tz_str)
        return django_timezone.make_aware(dt, tz)
    except Exception:
        return dt


def _event_data_to_google_format(event_data, start_dt, end_dt, tz_str):
    """Build Google Calendar event body with updated start/end."""
    body = copy.deepcopy(event_data)
    body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_str}
    body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_str}
    return body


@login_required
@require_POST
def parse_api(request):
    """
    POST /batch-calendar/api/parse/
    Body: {"text": "book physio Mon-Fri at 5pm"}
    Returns: batch_id, events, conflicts (per-event)
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid JSON")}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": _("text is required")}, status=400)

    try:
        events, error_msg, usage = extract_batch_events(text)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=502)

    if usage and request.user and (usage.get("input", 0) + usage.get("output", 0) > 0):
        model = get_llm_config("batch_calendar").get("model", "")
        if model:
            log_api_usage(
                request.user,
                model,
                "input_tokens",
                usage.get("input", 0),
                origin="parse_api",
            )
            log_api_usage(
                request.user,
                model,
                "output_tokens",
                usage.get("output", 0),
                origin="parse_api",
            )

    if error_msg:
        return JsonResponse({"error": error_msg, "events": []}, status=200)

    config = get_batch_calendar_config()
    calendar_id = _get_calendar_id(request)

    batch = BatchCalendarRequest.objects.create(
        user=request.user,
        input_text=text,
        parsed_events_json=events,
        status=BatchRequestStatus.PENDING,
    )

    try:
        conflict_results = check_batch_availability(
            request.user,
            events,
            calendar_id=calendar_id,
            default_timezone=config.default_timezone,
        )
    except GoogleAuthError as e:
        logger.warning("Google auth error during batch conflict check: %s", e)
        return JsonResponse(
            {"error": _("Google Calendar authentication failed. Please reconnect your Google account.")},
            status=401,
        )

    events_payload = []
    for cr in conflict_results:
        ev_data = cr["event_data"]
        tz_str = ev_data.get("start", {}).get("timeZone") or config.default_timezone
        batch_ev = BatchCalendarEvent.objects.create(
            batch_request=batch,
            event_index=cr["event_index"],
            event_data=ev_data,
            summary=(ev_data.get("summary") or "")[:255],
            start_datetime=cr.get("start_datetime"),
            end_datetime=cr.get("end_datetime"),
            timezone=tz_str,
            status=(
                BatchEventStatus.PENDING_CONFIRMATION
                if not cr["is_available"]
                else BatchEventStatus.PENDING
            ),
            conflicting_events=cr.get("conflicting_events", []),
            alternative_slots=cr.get("alternative_slots", []),
            alternative_slots_by_day=cr.get("alternative_slots_by_day", []),
        )

        ev_payload = {
            "event_index": cr["event_index"],
            "summary": batch_ev.summary,
            "start": batch_ev.start_datetime.isoformat() if batch_ev.start_datetime else None,
            "end": batch_ev.end_datetime.isoformat() if batch_ev.end_datetime else None,
            "is_available": cr["is_available"],
            "conflicting_events": cr.get("conflicting_events", []),
            "alternative_slots": cr.get("alternative_slots", []),
            "alternative_slots_by_day": cr.get("alternative_slots_by_day", []),
        }
        events_payload.append(ev_payload)

    return JsonResponse({
        "batch_id": str(batch.id),
        "status": batch.status,
        "events": events_payload,
        "has_conflicts": any(not r["is_available"] for r in conflict_results),
    })


@login_required
@require_POST
def confirm_api(request, batch_id):
    """
    POST /batch-calendar/api/confirm/<batch_id>/
    Body: {"resolutions": {"0": {"slot_index": 1}, "2": {"override": true}}}
    Optional: resolutions per event index for conflicts.
    """
    batch = get_object_or_404(
        BatchCalendarRequest,
        id=batch_id,
        user=request.user,
    )

    if batch.status != BatchRequestStatus.PENDING:
        return JsonResponse(
            {"error": _("Batch is not pending (status: %(status)s)") % {"status": batch.status}},
            status=400,
        )

    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}

    resolutions = body.get("resolutions") or {}
    calendar_id = _get_calendar_id(request)
    config = get_batch_calendar_config()
    tz_str = config.default_timezone

    success_count = 0
    failed_count = 0
    inserted = []

    for batch_ev in batch.events.all().order_by("event_index"):
        ev_data = batch_ev.event_data
        ev_tz = ev_data.get("start", {}).get("timeZone") or tz_str
        start_dt = batch_ev.start_datetime
        end_dt = batch_ev.end_datetime

        if not start_dt or not end_dt:
            batch_ev.mark_failed(_("Invalid datetime"))
            failed_count += 1
            continue

        has_conflict = len(batch_ev.conflicting_events or []) > 0
        res = resolutions.get(str(batch_ev.event_index), {})
        if has_conflict and (res.get("override") or res.get("slot_index") is not None):
            if res.get("override"):
                pass
            else:
                slot_idx = res.get("slot_index", 0)
                slots = batch_ev.alternative_slots
                if 0 <= slot_idx < len(slots):
                    slot = slots[slot_idx]
                    start_dt = datetime.fromisoformat(slot["start"].replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(slot["end"].replace("Z", "+00:00"))
                    start_dt = _ensure_aware(start_dt, ev_tz)
                    end_dt = _ensure_aware(end_dt, ev_tz)
                else:
                    batch_ev.mark_failed(_("Invalid slot_index"))
                    failed_count += 1
                    continue
        elif has_conflict and not res:
            batch_ev.mark_failed(_("Conflict not resolved"))
            failed_count += 1
            continue

        body_ev = _event_data_to_google_format(ev_data, start_dt, end_dt, ev_tz)
        try:
            api_resp = insert_event(request.user, body_ev, calendar_id)
        except GoogleAuthError as e:
            logger.warning("Google auth error during batch event insert: %s", e)
            batch.status = BatchRequestStatus.FAILED
            batch.error_message = _("Google Calendar authentication failed")
            batch.save()
            return JsonResponse(
                {
                    "success": False,
                    "error": _("Google Calendar authentication failed. Please reconnect your Google account."),
                    "batch_id": str(batch.id),
                    "status": batch.status,
                },
                status=401,
            )

        if api_resp:
            batch_ev.mark_success(api_resp.get("id", ""), api_resp)
            success_count += 1
            inserted.append({
                "event_index": batch_ev.event_index,
                "summary": batch_ev.summary,
                "google_event_id": api_resp.get("id"),
                "html_link": api_resp.get("htmlLink", ""),
            })
        else:
            batch_ev.mark_failed(_("Google Calendar API error"))
            failed_count += 1

    if failed_count > 0 and success_count == 0:
        batch.status = BatchRequestStatus.FAILED
        batch.error_message = _("All %(count)s events failed") % {"count": failed_count}
    elif failed_count > 0:
        batch.status = BatchRequestStatus.PARTIAL
        batch.error_message = _("%(count)s event(s) failed") % {"count": failed_count}
    else:
        batch.status = BatchRequestStatus.CONFIRMED
    batch.save()

    from django.urls import reverse

    from .context_processors import get_pending_calendar_count

    pending_count = get_pending_calendar_count(request.user)
    redirect_url = (
        reverse("batch_calendar:pending_list")
        if pending_count > 0
        else reverse("entries:list")
    )

    return JsonResponse({
        "success": True,
        "batch_id": str(batch.id),
        "status": batch.status,
        "inserted_count": success_count,
        "failed_count": failed_count,
        "inserted": inserted,
        "redirect_url": redirect_url,
    })


@login_required
@require_POST
def cancel_api(request, batch_id):
    """POST /batch-calendar/api/cancel/<batch_id>/"""
    batch = get_object_or_404(
        BatchCalendarRequest,
        id=batch_id,
        user=request.user,
    )

    if batch.status != BatchRequestStatus.PENDING:
        return JsonResponse(
            {"error": _("Cannot cancel batch with status %(status)s") % {"status": batch.status}},
            status=400,
        )

    batch.status = BatchRequestStatus.CANCELLED
    batch.is_deleted = True
    batch.deleted_at = django_timezone.now()
    batch.save()

    if batch.ingest_item:
        batch.ingest_item.is_deleted = True
        batch.ingest_item.deleted_at = django_timezone.now()
        batch.ingest_item.save(update_fields=['is_deleted', 'deleted_at'])

    from django.urls import reverse

    return JsonResponse({
        "success": True,
        "batch_id": str(batch.id),
        "status": batch.status,
        "redirect_url": reverse("recordings:record"),
    })


@login_required
@require_GET
def status_api(request, batch_id):
    """GET /batch-calendar/api/status/<batch_id>/"""
    batch = get_object_or_404(
        BatchCalendarRequest,
        id=batch_id,
        user=request.user,
    )

    events_payload = []
    for ev in batch.events.all().order_by("event_index"):
        events_payload.append({
            "event_index": ev.event_index,
            "summary": ev.summary,
            "start": ev.start_datetime.isoformat() if ev.start_datetime else None,
            "end": ev.end_datetime.isoformat() if ev.end_datetime else None,
            "status": ev.status,
            "google_event_id": ev.google_event_id,
            "html_link": ev.html_link,
        })

    input_plain = batch.input_text or ""
    return JsonResponse({
        "batch_id": str(batch.id),
        "status": batch.status,
        "input_text": input_plain,
        "created_at": batch.created_at.isoformat(),
        "events": events_payload,
    })


@login_required
def batch_confirmation_view(request, batch_id):
    """
    Display the batch calendar confirmation page for conflict resolution.
    """
    batch = get_object_or_404(
        BatchCalendarRequest,
        id=batch_id,
        user=request.user,
    )

    if batch.status not in (BatchRequestStatus.PENDING,):
        return redirect("entries:list")

    events = []
    events_with_conflicts = []
    for ev in batch.events.all().order_by("event_index"):
        has_conflict = len(ev.conflicting_events or []) > 0
        ev_dict = {
            "event_index": ev.event_index,
            "summary": ev.summary,
            "start_datetime": ev.start_datetime,
            "end_datetime": ev.end_datetime,
            "has_conflict": has_conflict,
            "alternative_slots": ev.alternative_slots or [],
            "alternative_slots_by_day": getattr(ev, "alternative_slots_by_day", None) or [],
        }
        events.append(ev_dict)
        if has_conflict:
            events_with_conflicts.append(ev_dict)

    input_plain = batch.input_text or ""
    context = {
        "batch": batch,
        "input_text": input_plain,
        "events": events,
        "events_with_conflicts_json": json.dumps(events_with_conflicts, cls=DjangoJSONEncoder),
        "batch_i18n": {
            "please_resolve_conflicts": _("Please resolve all conflicts (select alternative time or override)."),
            "failed_to_create_events": _("Failed to create events"),
            "failed_to_cancel": _("Failed to cancel"),
            "network_error": _("Network error. Please try again."),
            "cancel_all_events": _("Cancel all events in this batch?"),
            "previous_day": _("Previous day"),
            "next_day": _("Next day"),
            "no_slots_available": _("No alternative slots available"),
            "until": _("Until"),
        },
    }
    return render(request, "batch_calendar/confirmation.html", context)


@login_required
def pending_list_view(request):
    """
    Display list of pending batch calendar confirmations for the user.
    """
    batch_qs = BatchCalendarRequest.objects.filter(
        user=request.user,
        status=BatchRequestStatus.PENDING,
    ).order_by("-created_at")

    pending_batches = []
    for batch in batch_qs:
        input_text = batch.input_text or ""
        pending_batches.append({
            "batch": batch,
            "summary": input_text[:80] + ("..." if len(input_text) > 80 else ""),
        })

    context = {"pending_batches": pending_batches}
    return render(request, "batch_calendar/pending_list.html", context)


@csrf_exempt
@require_http_methods(["POST"])
def calendar_webhook_receiver(request):
    """
    Receive Google Calendar push notifications.
    On change notification, dispatches sync task to process deletions.
    """
    channel_id = request.headers.get("X-Goog-Channel-ID", "").strip()
    resource_state = request.headers.get("X-Goog-Resource-State", "").strip().lower()

    if not channel_id:
        return HttpResponse(status=400)

    if resource_state == "sync":
        return HttpResponse(status=200)

    if resource_state == "exists":
        from .tasks import sync_calendar_changes_task

        sync_calendar_changes_task.delay(channel_id)
        return HttpResponse(status=200)

    return HttpResponse(status=200)
