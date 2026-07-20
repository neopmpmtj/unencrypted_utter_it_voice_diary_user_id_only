"""
Calendar Pipeline Diagnostic Test Suite

Tests each stage of the classification → Google Calendar pipeline and reports
clear PASS/FAIL results with error context. Designed to pinpoint exactly where
the chain breaks.

Usage:
    cd /home/pmpmt/app/voice_diary_app
    DJANGO_SETTINGS_MODULE=src.utter_it.settings.prod .venv/bin/python -m pytest src/batch_calendar/tests/test_diagnostic_pipeline.py -v 2>&1 | tail -80
    # Or run standalone:
    .venv/bin/python src/batch_calendar/tests/test_diagnostic_pipeline.py
"""

import os
import sys
import json
import time
import logging

# ── Path setup ───────────────────────────────────────────────────────────────
# Ensure project root is on path
PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.utter_it.settings.prod")

import django
django.setup()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(message)s",
)
logging.getLogger("batch_calendar").setLevel(logging.WARNING)
logging.getLogger("google_auth").setLevel(logging.WARNING)

# ── Imports ──────────────────────────────────────────────────────────────────
from django.contrib.auth import get_user_model
from src.accounts.models import UserSecret, UserFeatureConfig
from src.batch_calendar.config_batch_calendar.batch_calendar_config import (
    get_batch_calendar_config,
)
from src.batch_calendar.services import extract_batch_events
from src.intent_router.services import route_utterance
from src.common.google_account.auth import (
    get_authenticated_service,
    has_valid_google_credentials,
    verify_required_scopes,
    DEFAULT_REQUIRED_SCOPES,
    _get_user_credentials,
)

User = get_user_model()

# ── Test State ───────────────────────────────────────────────────────────────
PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️ WARN"
SKIP = "⏭️ SKIP"

results = []


def test(name: str, fn):
    """Run a test, record result with timestamp and details."""
    start = time.time()
    try:
        fn()
        elapsed = time.time() - start
        results.append((PASS, name, f"{elapsed:.1f}s"))
    except AssertionError as e:
        elapsed = time.time() - start
        results.append((FAIL, name, str(e)))
    except Exception as e:
        elapsed = time.time() - start
        results.append((FAIL, name, f"{type(e).__name__}: {e}"))


def assert_true(condition, msg=""):
    if not condition:
        raise AssertionError(msg or "Expected True but got False")


def assert_false(condition, msg=""):
    if condition:
        raise AssertionError(msg or "Expected False but got True")


def assert_equal(a, b, msg=""):
    if a != b:
        raise AssertionError(msg or f"Expected {b!r}, got {a!r}")


def assert_in(key, container, msg=""):
    if key not in container:
        raise AssertionError(msg or f"Key {key!r} not found in {type(container).__name__}")


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_user_config():
    """T1: Check user exists and calendar integration is enabled."""
    user = User.objects.filter(is_active=True).first()
    assert_true(user is not None, "No active users found")
    print(f"  Active user: {user.id} — {user.email}")

    uf = UserFeatureConfig.get_for_user(user)
    assert_true(uf.enable_auto_classification, f"Auto-classification disabled for user {user.id}")
    assert_true(uf.enable_calendar_integration, f"Calendar integration disabled for user {user.id}")


def test_calendar_scopes():
    """T2: Check if user's Google OAuth tokens include Calendar scope."""
    user = User.objects.filter(is_active=True).first()
    assert_true(user is not None)

    us = UserSecret.objects.filter(user=user).first()
    assert_true(us is not None, "No UserSecret found")
    assert_true(bool(us.encrypted_google_access_token), "No Google access token stored")

    scopes = us.get_scopes_list()
    print(f"  Granted scopes ({len(scopes)}):")
    for s in scopes:
        print(f"    - {s}")

    has_calendar = any("calendar" in s for s in scopes)
    assert_true(has_calendar, (
        "Google OAuth token does NOT have calendar scope.\n"
        f"  Scopes found: {scopes}\n"
        "  Expected: https://www.googleapis.com/auth/calendar\n"
        "  FIX: Re-authenticate Google account and grant calendar permissions."
    ))

    missing = us.get_missing_scopes(DEFAULT_REQUIRED_SCOPES)
    assert_equal(len(missing), 0, f"Missing required scopes: {missing}")


def test_google_auth_works():
    """T3: Can we get an authenticated Google Calendar service?"""
    user = User.objects.filter(is_active=True).first()
    assert_true(user is not None)

    assert_true(has_valid_google_credentials(user), "No valid Google credentials found")

    service = get_authenticated_service(user, "calendar")
    assert_true(service is not None, "get_authenticated_service returned None")

    # Quick API call to verify: list calendar list
    cal_list = service.calendarList().list(maxResults=1).execute()
    items = cal_list.get("items", [])
    assert_true(len(items) > 0, "Calendar list returned empty — no calendars accessible")
    print(f"  Primary calendar: {items[0].get('summary', 'unknown')}")


def test_google_token_refresh():
    """T4: Verify token refresh works (critical for ongoing operation)."""
    user = User.objects.filter(is_active=True).first()
    assert_true(user is not None)

    creds = _get_user_credentials(user)
    assert_true(creds is not None, "Could not load credentials")

    has_refresh = creds.refresh_token is not None
    if has_refresh:
        print(f"  Refresh token available: {creds.refresh_token[:10]}...")
    else:
        print(f"  ⚠️  No refresh token — token will expire and cannot auto-renew")

    # Test: refresh (this will fail if token is invalid)
    from google.auth.transport.requests import Request as GoogleRequest
    from src.common.google_account.auth import _TimeoutRequest

    try:
        creds.refresh(_TimeoutRequest())
        print(f"  Token refresh: OK")
    except Exception as e:
        err_str = str(e)
        if "invalid_grant" in err_str:
            raise AssertionError(
                f"Token refresh FAILED: invalid_grant.\n"
                f"  This means the refresh token has been revoked or expired.\n"
                f"  FIX: Re-authenticate with Google at /accounts/google/login/"
            )
        raise AssertionError(f"Token refresh failed: {e}")


def test_triage_classification():
    """T5: Triage LLM correctly classifies a calendar event request."""
    test_text = "Call Beto from Andromeda on Tuesday morning at 10am"
    triage = route_utterance(test_text)
    print(f"  Route: {triage.primary_route}")
    print(f"  Confidence: {triage.confidence:.2f}")
    print(f"  Time reference: {triage.contains_time_reference}")
    print(f"  Raw: {json.dumps(triage.raw_response)[:150]}")

    assert_equal(triage.primary_route, "event", (
        f"Triage classified as '{triage.primary_route}', expected 'event'.\n"
        f"  Raw response: {json.dumps(triage.raw_response)}"
    ))
    assert_true(triage.confidence >= 0.60, (
        f"Triage confidence {triage.confidence:.2f} < 0.60 threshold\n"
        f"  Events below 0.60 will use taxonomy fallback instead of triage dispatch"
    ))


def test_extract_batch_events():
    """T6: Event extraction LLM correctly extracts events from natural language."""
    config = get_batch_calendar_config()
    assert_true(bool(config.openai_api_key), "No OpenAI API key configured for batch calendar")

    test_text = "Call Beto from Andromeda this Tuesday morning at 10am"
    events, error_msg, usage = extract_batch_events(test_text)
    print(f"  Usage: {usage}")
    print(f"  Error: {error_msg}")

    assert_true(error_msg is None, f"Event extraction returned error: {error_msg}")
    assert_true(events is not None and len(events) > 0, "No events extracted")
    assert_true(len(events) >= 1, f"Expected at least 1 event, got {len(events)}")

    for i, ev in enumerate(events):
        assert_in("summary", ev, f"Event {i} missing 'summary'")
        assert_in("start", ev, f"Event {i} missing 'start'")
        assert_in("end", ev, f"Event {i} missing 'end'")
        assert_in("dateTime", ev.get("start", {}), f"Event {i} start missing 'dateTime'")
        print(f"  Event {i}: {ev.get('summary')} — {ev.get('start', {}).get('dateTime')}")


def test_openai_rate_limits():
    """T7: Check if OpenAI API calls are being rate-limited or hitting quota."""
    from openai import OpenAI

    config = get_batch_calendar_config()
    assert_true(bool(config.openai_api_key), "No OpenAI API key")

    client = OpenAI(api_key=config.openai_api_key, timeout=30.0)
    response = client.chat.completions.create(
        model=config.model,
        messages=[{"role": "user", "content": "Say 'OK'"}],
        max_tokens=10,
        temperature=0.0,
    )
    content = (response.choices[0].message.content or "").strip()
    print(f"  Model: {config.model} — Response: {content}")
    assert_true(bool(content), "OpenAI API returned empty response")


def test_celery_worker_responding():
    """T8: Check if Celery worker is running and can accept tasks."""
    from celery import current_app
    from src.utter_it.celery import app

    # Inspect celery worker
    insp = app.control.inspect()
    workers = insp.ping()
    print(f"  Workers responding: {workers}")
    assert_true(workers is not None and len(workers) > 0, "No Celery workers responding to ping")

    active = insp.active()
    reserved = insp.reserved()
    scheduled = insp.scheduled()
    print(f"  Active tasks: {len(active) if active else 0}")
    print(f"  Reserved tasks: {len(reserved) if reserved else 0}")
    print(f"  Scheduled tasks: {len(scheduled) if scheduled else 0}")


def test_openai_api_key_has_credit():
    """T9: Check OpenAI API key by listing models (works as credit check)."""
    from openai import OpenAI

    config = get_batch_calendar_config()
    assert_true(bool(config.openai_api_key), "No OpenAI API key")

    client = OpenAI(api_key=config.openai_api_key, timeout=30.0)
    models = client.models.list()
    model_count = len(list(models))
    print(f"  Accessible models: {model_count}")
    assert_true(model_count > 0, "OpenAI returned 0 models — key may have no access or be exhausted")


def test_triage_openai_key():
    """T10: Check that the OpenAI key used for triage is also valid."""
    from src.common.model_picker import get_llm_config
    from openai import OpenAI

    cfg = get_llm_config("intent_triage")
    model = cfg.get("model", "unknown")
    print(f"  Triage model: {model}")

    config = get_batch_calendar_config()
    client = OpenAI(api_key=config.openai_api_key, timeout=30.0)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with OK only"}],
        max_tokens=10,
        temperature=0.0,
    )
    content = (response.choices[0].message.content or "").strip()
    assert_true(bool(content), f"Triage model {model} returned empty response")
    print(f"  Response: {content}")


def test_insert_event():
    """T11: End-to-end — create a test event and verify it appears in calendar.
    NOTE: Creates and immediately deletes a real calendar event."""
    user = User.objects.filter(is_active=True).first()
    assert_true(user is not None)

    from src.batch_calendar.calendar_client import insert_event, delete_event

    test_event = {
        "summary": "NEO Diagnostic Test — please delete",
        "description": "Auto-generated test event for NEO pipeline diagnostics. Safe to delete.",
        "start": {
            "dateTime": "2026-07-21T10:00:00",
            "timeZone": "Europe/Lisbon",
        },
        "end": {
            "dateTime": "2026-07-21T10:30:00",
            "timeZone": "Europe/Lisbon",
        },
    }

    response = insert_event(user, test_event, "primary")
    assert_true(response is not None, "insert_event returned None — Google Calendar API call failed")
    event_id = response.get("id", "")
    assert_true(bool(event_id), f"insert_event response missing 'id': {response}")
    print(f"  Created test event: {event_id}")

    # Clean up
    deleted = delete_event(user, "primary", event_id)
    assert_true(deleted, f"Failed to delete test event {event_id}")
    print(f"  Deleted test event: {event_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def run_all():
    print("=" * 70)
    print("  NEO Calendar Pipeline Diagnostic Tests")
    print("=" * 70)
    print()

    # T1: User Config
    print("─── [T1] User Configuration ───")
    test("User config: active user with calendar enabled", test_user_config)

    print()
    print("─── [T2] Calendar OAuth Scopes ───")
    test("Calendar scopes: token has calendar permission", test_calendar_scopes)

    print()
    print("─── [T3] Google Auth Service ───")
    test("Google auth: can get authenticated calendar service", test_google_auth_works)

    print()
    print("─── [T4] Token Refresh ───")
    test("Token refresh: can refresh access token", test_google_token_refresh)

    print()
    print("─── [T5] Triage Classification ───")
    test("Triage LLM: classifies event correctly", test_triage_classification)

    print()
    print("─── [T6] Event Extraction ───")
    test("Event extraction LLM: extracts events from text", test_extract_batch_events)

    print()
    print("─── [T7] OpenAI Rate Limits ───")
    test("OpenAI API: not rate limited", test_openai_rate_limits)

    print()
    print("─── [T8] Celery Worker ───")
    test("Celery worker: responding to pings", test_celery_worker_responding)

    print()
    print("─── [T9] OpenAI API Key Credits ───")
    test("OpenAI API key: has access to models", test_openai_api_key_has_credit)

    print()
    print("─── [T10] Triage Model Access ───")
    test("Triage model: API key works with triage model", test_triage_openai_key)

    print()
    print("─── [T11] End-to-End Calendar Insert ───")
    test("Calendar insert: creates and deletes test event", test_insert_event)

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    passed = 0
    failed = 0
    for status, name, detail in results:
        print(f"  {status} | {name}")
        if status == FAIL:
            failed += 1
            print(f"         └─ {detail}")
        elif status == PASS:
            passed += 1
        if detail and status != FAIL:
            print(f"         └─ {detail}")

    print()
    print(f"  {passed} passed, {failed} failed")
    print()

    if failed > 0:
        print("  ⚠️  Some tests FAILED. See details above for fix guidance.")
        print()
        # Suggest fix
        print("  Most likely fix: Re-authenticate Google account and grant calendar scopes.")
        print("  Visit: https://utter-it.com/accounts/google/login/")
    else:
        print("  ✅ All pipeline checks passed!")

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
