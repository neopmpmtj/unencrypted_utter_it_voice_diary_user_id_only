# Utter It -- Voice Diary

A Django-based voice diary application that records audio, transcribes speech, classifies content, and organises it into structured entries. Supports Google integrations (OAuth, Gmail, Calendar), AI-powered parsing (OpenAI, Gemini), user-scoped architecture, and tiered billing via Stripe.

## Recording & interruption handling

Audio is captured in the browser (MediaRecorder) and uploaded to the checkpointed
ingest pipeline. Recorder behaviours worth knowing:

- **Segment cap** — a segment rolls over at the configured cap (default 240 s): the
  finished clip is saved and recording continues seamlessly.
- **Call / mic interruption** — when a phone call grabs the microphone the recording
  **auto-pauses** (same as the manual pause button). The user resumes manually (▶); on
  iOS the old microphone cannot be revived, so the take continues on a fresh mic +
  new recorder.
- **One recording per take** — all parts of an interrupted take are merged into a
  single WAV **at stop** (client-side merge; fallback = separate uploads — nothing is
  lost), so the diary receives one entry, exactly like a manual pause. The on-screen
  timer stays continuous across the resume.
- **Visible feedback** — toasts for *"Saved, continuing recording…"* (segment rollover)
  and *"Continuing — everything will be saved as one single recording."* (held parts).
- **Crash safety** — each completed part is also copied to a small local IndexedDB
  store as soon as it is held. If the browser crashes or the page reloads mid-take,
  the next visit recovers the parts automatically and uploads them (merged as one
  recording). The in-progress segment (≤ one cap) is the only thing a crash can still
  lose.

Full implementation notes, version history (v1 → v3.2) and diagnostic learnings:
[`docs/INTERRUPTION-HANDLING.md`](docs/INTERRUPTION-HANDLING.md).

## Audio processing pipeline

Uploads are queued to Celery (`src.ingestion.tasks.process_audio_ingest`) as a
**checkpointed pipeline**, so a retry resumes from the last completed step:

| Step | What it does |
|------|----------------|
| `chunking` | files over ~20 MB are split into overlapping chunks (the transcription API has a 25 MB limit) |
| `silence_removal` | strips quiet stretches (per chunk) |
| `loudness_normalization` | EBU R128 |
| `transcription` | OpenAI `gpt-4o-transcribe`, chunk transcripts merged |
| `lang_detect` → `translation` | detect language; translate when it differs from the user's |
| `cleanup` | saves the entry, records the GIGO quality row, queues classification, deletes temporary chunk files |

Failures are loud: if chunking produces nothing the job errors (rather than silently
"completing" with an empty transcript), and the split step is guarded against runaway
loops. See [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

## Requirements

- Python 3.10+
- PostgreSQL (with pgvector extension for retrieval)
- Redis (Celery broker, Channels layer, caching)
- ffmpeg (audio processing)

## Project structure

All first-party Django apps live under `src/`:

| App | Purpose |
|-----|---------|
| `accounts` | Email auth, Google OAuth, user profiles, account deletion |
| `UserFeatureConfig` (`accounts`) | Per-user flags: auto-classification, calendar integration, trigger tags, default calendar |
| `core` | Core project views and admin utilities |
| `ingestion` | Ingest pipeline, checkpoints, content-ready broadcasting |
| `recordings` | Voice recording UI, WebSocket upload flow |
| `transcription` | Audio transcription via OpenAI |
| `translation` | Text translation |
| `lang_detect` | Language detection |
| `entries` | Diary entry models and views |
| `text_input` | Non-voice text input path ([docs](src/text_input/TEXT_INPUT_README.md)) |
| `classification` | LLM-based content classification and routing taxonomy |
| `intent_router` | Intent triage / utterance routing |
| `list_parser` | Structured list extraction from text |
| `managed_lists` | Managed list projections and todo items |
| `financial_parser` | Financial record parsing |
| `batch_calendar` | Multi-event calendar extraction |
| `retrieval` | Vector search and diary chat |
| `invoice_parser` | PDF invoice parsing from Gmail |
| `gmail_parsers` | Gmail-specific parsers |
| `text_rewrite` | Text rewrite flows |
| `quotas` | Token / usage quotas by subscription tier |
| `billing` | Stripe subscriptions and tier management |
| `gigo` | Quality monitoring and alerts |
| `vd_tools` | Developer utilities (mic test, recent recordings) |

Shared code lives in `src/common/` (encryption, Google account helpers, model picker, logging, tasks).

## Setup

### 1. Clone and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

Key variables:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Django secret key |
| `MASTER_ENCRYPTION_KEY` | Fernet key for encrypting Google OAuth tokens |
| `CELERY_BROKER_URL` | Redis URL for Celery (default: `redis://127.0.0.1:6379/0`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth credentials |
| `AI_OPENAI_API_KEY` | OpenAI API key for transcription, classification, parsing |
| `STRIPE_SECRET_KEY` | Stripe key for billing (optional) |

Generate a `MASTER_ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Database setup

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Run services

**Development server** (HTTP only):

```bash
python manage.py runserver
```

**ASGI server** (HTTP + WebSockets):

```bash
uvicorn src.utter_it.asgi:application --host 0.0.0.0 --port 8000
```

**Celery worker** (async tasks):

```bash
celery -A src.utter_it worker -l info
```

**Celery beat** (scheduled tasks):

```bash
celery -A src.utter_it beat -l info
```

### 5. Text input (web or CLI)

Text entries use the same ingest and classification pipeline as voice. Full documentation: [src/text_input/TEXT_INPUT_README.md](src/text_input/TEXT_INPUT_README.md).

**Web:** log in and open `/text-input/`.

**CLI** (no browser; requires Celery worker running):

```bash
python manage.py ingest_text --email your@email.com --text "Your diary entry"

# Or pipe body from stdin
echo "Your diary entry" | python manage.py ingest_text --email your@email.com
```

Optional flags: `--user-id`, `--template-type plain|list`, `--title`, `--occurred-at` (ISO 8601).

## Running tests

```bash
MASTER_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  DJANGO_SETTINGS_MODULE=src.utter_it.settings.dev \
  python manage.py test
```

Add `--keepdb` to reuse the test database between runs for faster iteration.

## Documentation

| Document | Contents |
|----------|----------|
| [`docs/INTERRUPTION-HANDLING.md`](docs/INTERRUPTION-HANDLING.md) | Recorder interruption handling — full version history (v1–v3.2), verbatim code, diagnostic learnings |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Notable changes, with commits |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Fresh-box prerequisites + deploy runbook |
| [`docs/deployment-static-and-nginx.md`](docs/deployment-static-and-nginx.md) | Static files / nginx troubleshooting |
| [`src/text_input/TEXT_INPUT_README.md`](src/text_input/TEXT_INPUT_README.md) | Text input path (web + CLI) |

## Encryption

Only Google OAuth tokens (`access_token`, `refresh_token`, `token_expiry`) are encrypted at rest using `Fernet(MASTER_ENCRYPTION_KEY)`. All other data (diary entries, lists, financial records, calendar events) is stored as plaintext.

The encryption module lives at `src/common/utils/encryption.py` and exposes:

- `encrypt_value(value)` / `decrypt_value(encrypted_value)` -- used by `src/common/google_account/auth.py`
- `encrypt_value_with_master(value, key)` / `decrypt_value_with_master(value, key)` -- used for master key rotation

## Settings modules

| Module | Use |
|--------|-----|
| `src.utter_it.settings.dev` | Local development (DEBUG=True, local DB) |
| `src.utter_it.settings.prod` | Production (DEBUG=False, Supabase, HTTPS) |
| `src.utter_it.settings.test` | Test runner (eager Celery, test overrides) |

Set via `DJANGO_SETTINGS_MODULE` environment variable.
