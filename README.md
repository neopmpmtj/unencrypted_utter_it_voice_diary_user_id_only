# Utter It -- Voice Diary

A Django-based voice diary application that records audio, transcribes speech, classifies content, and organises it into structured entries. Supports Google integrations (OAuth, Gmail, Calendar), AI-powered parsing (OpenAI, Gemini), user-scoped architecture, and tiered billing via Stripe.

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
| `conversation_summarizer` | Groups cap-split clips into conversations and summarizes them (editable prompt, few-shot examples, per-user toggle) |
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

> **Fresh box:** the retrieval app stores embeddings in a `vector(1536)` column, so
> `pgvector` must be installed **before** `migrate` — and once in `template1`, so
> Django's test runner can create its test database. Full runbook:
> [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

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

## Conversation summaries for long recordings

The recorder caps a single recording at **240 seconds**. A longer talk is therefore
saved as several consecutive clips that share one `recording_group_id`. The
`conversation_summarizer` app turns those clips back into ONE conversation and
produces a single summary for it — automatically, with no timer and no manual step.

### How it triggers

Every finalized entry enqueues `summarizer_on_entry_task`:

- `process_audio_ingest` (audio entries) — next to the existing classification enqueue
- `ingest_text_entry` (typed notes)

Each run does three things, in order:

1. **Respects the user's switch first** — if summaries are off it returns
   immediately: no LLM call, no writes, raw clips kept as recorded.
2. **Summarizes the conversation the entry belongs to**, if it is now closed.
3. **Sweeps the user's other closed-but-unsummarized conversations** (bounded to
   the last 30 days).

A conversation is *closed* when its last clip is shorter than the cap (the "tail"),
or when its last clip hit the cap and nothing new arrived within the 10-minute quiet
window. The quiet rule is what covers a talk that ended exactly on the cap — the one
case where no further entry would ever arrive, and why the sweep exists.

There is **no Celery beat job and no polling** for this feature.

### Journal vs instruction (long recordings are not classified)

Recordings come in two kinds and are treated differently **by design**:

| Kind | Cue | What happens |
|------|-----|--------------|
| **Instruction** | a recording that never reaches the cap | classified → acted upon (calendar, lists, financial, to-dos) |
| **Journal** | a session that contains a cap-length tranche (the recorder auto-continues, keeping one session id) | summarized for later recall; **never classified** — no triage, no parsers, no derived records. Badged 📓 in the entries UI. |

The cap rule lives in ONE place — `src/ingestion/session_mode.py` — shared by the
summarizer and the classification gate, so they can never disagree about what
counts as a long recording. The gate is applied in two places: the pipeline
enqueues classification only for instruction mode, and `classify_item_task`
re-checks defensively (so nothing — e.g. an edit — can classify a journal entry).
Journal entries still get their completion broadcast and retrieval indexing, so
they remain searchable and chat-able.

A missed trigger (a closed conversation the sweep did not see) self-heals: the
entry hook schedules **one** deferred re-check of that conversation ~60s later.

### Per-user on/off

`UserPreferences.enable_conversation_summary` (default **True**) — toggled on the
profile page and visible per user in Django admin. When off, the pipeline skips
summarization entirely and the raw clips are kept exactly as recorded. Turning it
off never deletes existing summaries, and turning it back on does not backfill
history (that would be an unbounded, surprise LLM bill).

### Editing the agent (no deploy required)

Everything about the prompt lives in the database, editable in Django admin:

| Admin item | What it controls |
|------------|------------------|
| **Summary prompt templates** | System + user prompt with `{{transcript}}`, `{{clip_count}}`, `{{duration_minutes}}`, `{{started_at}}`, `{{ended_at}}`, `{{language}}`, `{{user_name}}`. `version` increments automatically when the prompt text changes, and every summary records the version that produced it. Unknown placeholders are rejected on save. |
| **Summary examples** | Few-shot input → expected-output pairs, prepended in order |
| **Summary agent configurations** | One global default row plus optional per-user overrides (model, temperature, cap tolerance, quiet window, minimum length, chunk size) |

**Preview on my last session** (an admin action on any template) runs the selected
prompt against your most recent conversation and shows the exact messages the model
receives, the raw reply, the parsed JSON, tokens and latency. It writes **no**
summary — only a `SummaryRun` audit row — so prompts can be iterated safely.

Output is strict JSON (`type`, `title`, `summary`, `key_points`, `decisions`,
`action_items`, `people`, `language`) in the language of the transcript. A non-JSON
reply degrades to a plain-text summary flagged `partial` rather than being lost.
Transcripts above `chunk_chars` (60 000 by default) are summarized with
**map-reduce**: each chunk of clips is mapped to notes, then the notes are reduced
into the final summary.

### Data model

| Model | Purpose |
|-------|---------|
| `SummaryAgentConfig` | Global defaults + per-user tuning |
| `SummaryPromptTemplate` | The editable, versioned prompt |
| `SummaryExample` | Few-shot examples |
| `ConversationSummary` | One canonical summary per `(user, recording_group_id)`; `revision` increments when the conversation grows |
| `SummaryRun` | Audit of every LLM call (model, tokens, latency, error) |

**Nothing is ever removed.** The summary is an addition: all clips stay live, each
keeping its own transcript, timestamp and attachments. The entries page renders the
clips of one conversation as a **single card** — summary on top, clips nested below.

### Manual operations

```bash
python manage.py summarize_recording_groups                  # sweep now (all users)
python manage.py summarize_recording_groups --dry-run        # preview: no AI, no writes
python manage.py summarize_recording_groups --user-id 1 --json
python manage.py summarize_recording_groups --force          # re-summarize even if up to date
python manage.py summarize_recording_groups --backfill       # rows from existing text, no AI
```

Tests for the app:

```bash
python manage.py test src.conversation_summarizer
```

The service tests inject a fake LLM, so they run offline and cost nothing.

## Running tests

```bash
MASTER_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  DJANGO_SETTINGS_MODULE=src.utter_it.settings.dev \
  python manage.py test
```

Add `--keepdb` to reuse the test database between runs for faster iteration.

> A test database is copied from `template1`, so `pgvector` must be installed there
> or test-database creation fails with `type "vector" does not exist`
> (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) §1.1).

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

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for fresh-box prerequisites, the
routine deploy loop, systemd units and a verification checklist. Static-file and
Nginx troubleshooting lives in
[docs/deployment-static-and-nginx.md](docs/deployment-static-and-nginx.md).
