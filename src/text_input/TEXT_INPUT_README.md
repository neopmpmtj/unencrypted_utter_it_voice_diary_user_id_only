# Text Input

Non-voice path for creating diary entries. Text is validated, optionally translated, stored as an `IngestItem`, and queued through the same classification and parser pipeline as voice recordings.

Use this document when onboarding to the `text_input` app or when you need to submit entries without the browser.

## Overview

| Entry point | When to use |
|-------------|-------------|
| **Web UI** (`/text-input/`) | Normal use in a browser; supports attachments and live pipeline WebSocket updates |
| **HTTP API** (`POST /text-input/ingest/`) | Programmatic ingest from another service (requires session auth) |
| **CLI** (`python manage.py ingest_text`) | Headless / server / SSH workflows; no browser required |

All three paths call **`ingest_text_entry()`** in `services.py`, which:

1. Validates non-empty text and `template_type` (`plain` or `list`)
2. Normalizes list-style input when `template_type=list`
3. Detects language and optionally translates to the user's preferred language
4. Creates an `IngestItem` with `item_type="text"`
5. Records a GIGO quality entry (best effort)
6. Queues **`classify_item_task`** on Celery (classification → triage → parsers → indexing)

The CLI does **not** support file attachments. Use the web UI or HTTP multipart API for attachments.

## Prerequisites

Before ingesting text (web or CLI), ensure these services are running:

```bash
# Database migrations applied
python manage.py migrate

# Web server (pick one)
python manage.py runserver
# or, for HTTP + WebSockets:
uvicorn src.utter_it.asgi:application --host 0.0.0.0 --port 8000

# Required for the async pipeline after ingest
celery -A src.utter_it worker -l info
```

You also need:

- **PostgreSQL** (configured via `DATABASE_URL` in `.env`)
- **Redis** (Celery broker; default `redis://127.0.0.1:6379/0`)
- **`AI_OPENAI_API_KEY`** (classification and downstream parsers)
- A **user account** (created via signup or `python manage.py createsuperuser`)

Users are identified by **email** (not username). The CLI accepts `--email` or `--user-id` (UUID).

## CLI: `ingest_text`

Management command: `src/text_input/management/commands/ingest_text.py`

### Basic usage

```bash
# Body on the command line
python manage.py ingest_text --email your@email.com --text "Your diary entry"

# Body from stdin (scripts, long text, heredocs)
echo "Your diary entry" | python manage.py ingest_text --email your@email.com

cat notes.txt | python manage.py ingest_text --email your@email.com

python manage.py ingest_text --email your@email.com <<'EOF'
Multi-line entry
with several paragraphs.
EOF
```

### Identify user by UUID

```bash
python manage.py ingest_text --user-id <uuid> --text "Entry body"
```

Use `--email` **or** `--user-id`, not both.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--email` | — | Login email for the account that owns the entry |
| `--user-id` | — | User UUID (alternative to `--email`) |
| `--text` | stdin | Entry body. If omitted, reads from stdin (must not be an interactive TTY) |
| `--template-type` | `plain` | `plain` or `list` |
| `--title` | `""` | Optional title |
| `--occurred-at` | now | ISO 8601 datetime, e.g. `2026-04-19T14:30:00` |

### Examples

```bash
# Plain note
python manage.py ingest_text --email dev@example.com --text "Meeting with Alex at 3pm"

# List template (comma-separated items normalized to a list)
python manage.py ingest_text --email dev@example.com \
  --template-type list \
  --title "Groceries" \
  --text "milk, eggs, bread"

# Backdated entry
python manage.py ingest_text --email dev@example.com \
  --occurred-at "2026-04-19T14:30:00" \
  --text "Retroactive journal note"
```

### Success output

On success the command prints the new item id, detected language, and whether translation ran:

```
Created ingest item <uuid> (language=en, translated=False). Classification queued; ensure a Celery worker is running.
```

Watch the Celery worker logs to confirm classification and parser tasks complete.

### Errors

| Situation | Result |
|-----------|--------|
| Missing `--email` and `--user-id` | CommandError |
| Both `--email` and `--user-id` | CommandError |
| Unknown user | CommandError |
| Empty or whitespace-only text | CommandError |
| Daily token quota exceeded | CommandError (same check as the web API) |
| Interactive terminal with no `--text` and no stdin pipe | CommandError (use `--text` or pipe input) |

Test users and app admins bypass token quotas (see `src/quotas/services.py`).

## Web UI

1. Start the dev server and Celery worker (see [Prerequisites](#prerequisites)).
2. Log in at the app root.
3. Open **`/text-input/`**.
4. Enter text, optionally set title and template type, attach files if needed, and submit.

The page POSTs to `/text-input/ingest/` and opens a WebSocket on `/ws/pipeline/<item_id>/` for pipeline progress.

## HTTP API

**Endpoint:** `POST /text-input/ingest/`  
**Auth:** Session login (`@login_required`)

### JSON body (no attachments)

```json
{
  "text": "Entry body",
  "template_type": "plain",
  "title": "Optional title",
  "occurred_at": "2026-04-19T14:30:00"
}
```

### Multipart form (text + optional files)

Fields: `text`, `template_type`, `title`, `occurred_at`, `files` (repeatable).

When files are attached and local filesystem storage is disabled, the user must have Google Drive connected.

### Success response (201)

```json
{
  "id": "<uuid>",
  "detected_language": "en",
  "translated": false,
  "content_text": "...",
  "attachment_count": 0
}
```

## Pipeline flow

```text
ingest_text_entry()
  ├── validate + normalize (plain | list)
  ├── detect language
  ├── translate (if enabled and language differs)
  ├── create IngestItem (status=PROCESSED)
  └── classify_item_task.delay()
        ├── triage (intent_router)
        ├── taxonomy classification
        ├── parser dispatch (lists, calendar, financial, …)
        └── index_entry_prep_task (retrieval) when pipeline completes
```

Voice and text entries converge at **`classify_item_task`**. Downstream behavior depends on triage/classification results and per-user feature flags (`UserFeatureConfig`).

## Module layout

| File | Role |
|------|------|
| `services.py` | `ingest_text_entry()` — core ingest logic |
| `views.py` | Web page + HTTP ingest endpoint |
| `urls.py` | Routes under `/text-input/` |
| `utils.py` | Whitespace checks, list normalization, template validation |
| `management/commands/ingest_text.py` | CLI command |
| `tests/` | Service, view, and CLI tests |

## Running tests

From the project root (with venv activated):

```bash
MASTER_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  DJANGO_SETTINGS_MODULE=src.utter_it.settings.test \
  python manage.py test src.text_input
```

CLI command tests only:

```bash
python manage.py test src.text_input.tests.test_ingest_text_command
```

## Related docs

- Project setup and services: [README.md](../../README.md)
- Ingest models and jobs: `src/ingestion/`
- Classification pipeline: `src/classification/tasks.py`
