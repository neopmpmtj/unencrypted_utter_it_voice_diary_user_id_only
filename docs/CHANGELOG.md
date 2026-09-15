# Changelog — Utter It (Voice Diary)

Notable changes, newest first. Commit hashes refer to this repository; deploy numbers
refer to the deployment tracker used for the production box (`deploylog` #N).

---

## 2026-09-15 — Interruption handling v3.x + audio-pipeline hardening

### v3.2 — crash-safe held parts (`b3b20ca`, deploy #47)

- Held parts of a multi-part (interrupted) take are now backed up to a local
  IndexedDB store (`VoiceDiaryHeldPartsDB`) the moment they are held.
- If the browser crashes or the page reloads mid-take, the next visit recovers the
  parts and uploads them — merged into ONE recording when possible, otherwise as
  separate grouped clips (nothing is lost).
- Fail-safe by design: every step is optional and silently skipped if unavailable,
  so the normal recording flow cannot be affected; records are deleted only after a
  successful upload, and unprocessable (4xx) data is dropped so it cannot retry
  forever. The in-progress segment (≤ one cap) remains the only crash exposure —
  the same exposure a normal recording already had.
- UI: *"Recovered an unfinished recording — it has been saved to your diary."*
- Tests: 5 new (17 interruption + 15 rollover, all green).

### Incident & fix — runaway audio chunking filled the disk (`ae7f2f1`, deploy #46)

- A live test uploaded the first >20 MB merged recording (292 s / 28 MB). The chunk
  splitter (`src/ingestion/audio_services/audio_chunking.py`) looped forever once the
  final chunk was reached — `start = end - overlap` points back inside the file — and
  wrote one-second chunks until the disk hit 100 % (566 k files / ~52 GB). The pipeline
  then "completed" with an empty transcript, so the recording appeared lost.
- Fixed:
  - the split now **stops after the final chunk** and has a chunk-count guard that
    aborts loudly instead of writing endlessly;
  - the pipeline **raises** when chunking produces nothing (no more silent empty
    "successes");
  - temporary chunk files are **deleted after successful processing**.
- The affected recording was reprocessed and fully recovered (re-ran the pipeline:
  2 chunks, both transcribed, classified); the disk was cleaned. A regression test
  (`src/ingestion/tests/test_audio_chunking_split.py`) locks in the termination.

### v3.1 — visible segment-hold feedback (`be078cd`, deploy #46)

- Inside a multi-part take, cap-length rollover *holds* the finished segment for the
  final single-recording merge instead of uploading it mid-take. That was silent, so
  the take looked like it was "not doing anything". A toast now fires when a segment
  is held: *"Continuing — everything will be saved as one single recording."*

### v3 — one recording per interrupted take (`242e504`, deploy #45)

- After a call interruption, all parts of the take are merged into ONE WAV at stop
  (client-side merge) and uploaded as a single recording — matching the manual-pause
  behaviour. Fallback: separate uploads (nothing lost). The on-screen timer stays
  continuous across the resume.

### Earlier the same day

- `5552865` (deploy #44) — interruption handling v2: auto-pause on mic loss + manual
  resume on a fresh mic.
- `a53ca6a` — service worker: navigations network-first (fresh page after deploys).
- `741b856` / `1cd15e2` — v1 auto-continue implementation (shipped, then reverted;
  archived verbatim in `docs/INTERRUPTION-HANDLING.md`).
