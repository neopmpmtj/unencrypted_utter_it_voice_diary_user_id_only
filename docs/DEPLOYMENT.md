# DEPLOYMENT.md — Voice Diary (utter-it.com)

Prerequisites and runbook for standing up this app on a **fresh box**, and for
routine deploys. Static-file/Nginx troubleshooting has its own guide:
[`deployment-static-and-nginx.md`](deployment-static-and-nginx.md).

---

## 1. Prerequisites (fresh box)

Target: Ubuntu 24.04. Verified working set on the current box —
Python 3.12.3 · PostgreSQL 16.15 · ffmpeg 6.1.1 · Node 24 (Tailwind CLI 4.2.2) · nginx.

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv postgresql-16 postgresql-16-pgvector \
                    nginx ffmpeg
# Node (for the django-tailwind CLI used by `manage.py tailwind build`)
```

| Component | Why |
|---|---|
| Python 3.12 | app runtime |
| PostgreSQL 16 | primary datastore |
| **pgvector (`postgresql-16-pgvector`)** | the retrieval app stores embeddings in a `vector(1536)` column |
| ffmpeg | transcription/audio pipeline (checked at startup) |
| Node | Tailwind build step for production CSS |
| nginx | TLS + serving `/static/` |

### 1.1 Database — create the role, the DB, **and the extension**

```bash
sudo -u postgres psql -c "CREATE ROLE appuser LOGIN PASSWORD '<strong-password>';"
sudo -u postgres psql -c "CREATE DATABASE voicediary_db OWNER appuser;"

# pgvector in the application database (embeddings)
sudo -u postgres psql -d voicediary_db -c "CREATE EXTENSION IF NOT EXISTS vector;"

# ►► REQUIRED, ONCE PER CLUSTER, BEFORE `migrate` AND BEFORE ANY TEST RUN ◄◄
sudo -u postgres psql -d template1     -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Why the `template1` line matters.** The app runs as `appuser`, which is
deliberately **not** a superuser (least privilege — see §4). Django's test runner
creates a *brand-new* database, which is copied from `template1`; without the
extension there, test-database creation dies with:

```
django.db.utils.ProgrammingError: type "vector" does not exist
LINE 1: ... "todo_items_flat" text NOT NULL, "embedding" vector(153...
```

Installing it in `template1` makes every future database inherit it, so
`manage.py test` works forever with **no** privilege change. It is additive and
reversible (`DROP EXTENSION vector;` from `template1`).

> ⚠️ **Do not "fix" this by giving the app the `postgres` superuser.** A
> superuser connection can read every database in the cluster and can execute
> OS commands via `COPY ... FROM PROGRAM`; a single app-level flaw would then
> escalate to full host takeover. Keep the app user least-privilege and run the
> one-off superuser step above instead.

Verify:

```bash
psql "$DATABASE_URL" -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
```

### 1.2 Environment file

```bash
cd /home/pmpmt/app/voice_diary_app
cp .env.example .env      # then edit: DATABASE_URL, API keys, DJANGO_SETTINGS_MODULE, ALLOWED_HOSTS
chmod 600 .env            # secrets never world-readable
```

Production settings module: `src.utter_it.settings.prod`.

### 1.3 Python environment

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## 2. First deploy

```bash
cd /home/pmpmt/app/voice_diary_app
set -a && . ./.env && set +a

.venv/bin/python manage.py check
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py tailwind build          # production CSS
.venv/bin/python manage.py collectstatic --noinput # → STATIC_ROOT = src/staticfiles
sudo -n systemctl restart utter-it-uvicorn.service utter-it-celery.service utter-it-celery-beat.service
```

---

## 3. Routine deploy (after `git pull`)

```bash
cd /home/pmpmt/app/voice_diary_app
git pull origin main
set -a && . ./.env && set +a
.venv/bin/pip install -r requirements.txt                      # if requirements changed
.venv/bin/python manage.py migrate --noinput                   # if migrations were added
.venv/bin/python manage.py collectstatic --noinput
sudo -n systemctl restart utter-it-uvicorn.service utter-it-celery.service utter-it-celery-beat.service
```

---

## 4. Services (systemd, run as `pmpmt`)

| Unit | Role |
|---|---|
| `utter-it-uvicorn.service` | app server, `127.0.0.1:8000` |
| `utter-it-celery.service` | background tasks (transcription, classification, retrieval) |
| `utter-it-celery-beat.service` | scheduled tasks |
| `utter-it-watchdog.service` + `.timer` | health check → restarts uvicorn if unhealthy |

DB user: **`appuser`**, owner of `voicediary_db` only (`rolsuper=f`,
`rolcreatedb=t`, `rolcreaterole=f`). Postgres listens on loopback only.

---

## 5. Verification checklist

- [ ] `curl -sI https://utter-it.com/` → **200**
- [ ] `curl -sI https://utter-it.com/static/css/tailwind.css` → **200**
- [ ] `.venv/bin/python manage.py check` → no errors
- [ ] `.venv/bin/python manage.py test src.entries.tests.test_views --noinput` → **OK**
      *(requires §1.1's `template1` step; if it fails with `type "vector" does not exist`, that step was skipped)*
- [ ] Login works, entries page renders, a recording round-trips

---

## 6. Related

- Static files / Nginx `403`s / `STATIC_ROOT`: `docs/deployment-static-and-nginx.md`
- App operations ownership + restart rights: workspace `TOOLS.md`

---

## 7. Troubleshooting — audio pipeline / disk

**Symptom:** an entry never gets its text ("processing" seems to hang), or disk alerts fire.

Check the audio chunk scratch directory first (it should be empty between jobs):

```bash
ls /home/pmpmt/.voicediary/storage/recordings/1/chunks/ | wc -l
df -h /
```

Temporary chunk files are derived data and are deleted automatically after a successful
pipeline run — they are safe to remove when no pipeline job is running. If the directory
grows into the thousands, look for a runaway split: the 2026-09-15 incident (566 k files /
~52 GB) was caused by the splitter looping after the final chunk. The fix lives in
`src/ingestion/audio_services/audio_chunking.py` (stop after the final chunk + chunk-count
guard) and `src/ingestion/tasks.py` (raise when chunking yields nothing; delete chunk
files after success). See `docs/CHANGELOG.md`.
