# Interruption Handling — Voice Diary Recorder

**Date:** 2026-09-15 · **Status:** **v2 (current)** = auto-pause + manual resume · **v1 (archived below)** = fully automatic "close clip + auto-continue".

## Goal (v2 — Pedro's requirement, 2026-09-15)
> "When an incoming call takes over the phone and the mic is interrupted, simply pause. …
> when the call is finished, the user has to manually carry on the recording."

1. Mic interruption (phone call) → recording **auto-pauses** — the same pause mechanism as the manual pause button (same UI state, same resume button).
2. **No automatic continuation.** The user presses ▶ when they are back.
3. On manual resume, recording continues on a **fresh microphone** (iOS never gives the old mic back to the old recorder — see Learnings) and only after real audio bytes are verified flowing. If the mic is still busy, it stays paused and shows a gentle notice.
4. All segments of one session share `recording_group_id` → the diary still reads as one entry (same machinery as the 4-minute split).

## Learnings from the diagnostic pages (v1–v4) — why it is built this way
(Source: `~/backups/call-interruption-test/` — iPhone iOS 18.7, Safari)
- `track.mute` fires **instantly** when a call grabs the mic — the reliable detector.
- Resuming the **same** MediaRecorder after a call = **silent zombie**: it reports "recording" and delivers **0 bytes** forever (even after the mic's natural unmute ~10s later).
- Swapping mic tracks **mid-recording** → `InvalidModificationError` (Safari kills the recorder).
- A **fresh** `getUserMedia` + **new** recorder works — verified by watching actual audio bytes (probe).
- A fresh mic request right at unmute time can still return a muted track → retry a few times.

## v1 — the "auto-continue" version (ARCHIVED 2026-09-15, replaced by v2)
**What it did:** on mic-grab → closed the current clip instantly and uploaded it in the background; ran an auto-recovery loop (fresh mic only, verified by audio flow, retries with slow mode, visibility/unmute triggers) and **continued recording automatically** as a new clip in the same group; stop-time salvage kept in-flight audio; UI had an "interrupted" state + toasts. **It worked in production** — archived because Pedro prefers manual control.

**Full verbatim copies of the v1 files:** `~/backups/voice-diary-interrupt-simplify-2026-09-15/archived-auto-continue/` · git commit `741b856` (the SW cache fix `a53ca6a` is independent and stays).

### v1 — constructor block (verbatim)
```js
        // Interruption handling (e.g. a phone call grabs the microphone mid-recording):
        // close the current clip cleanly and save it, then continue recording as a NEW clip
        // on a fresh mic stream once the microphone is available again (verified by audio flow).
        this.handleInterruptions = options.handleInterruptions ?? !this.transcribeOnly;
        this.interruptionRetryMs = options.interruptionRetryMs ?? 1800;
        this.interruptionSlowRetryMs = options.interruptionSlowRetryMs ?? 10000;
        this.interruptionProbeMs = options.interruptionProbeMs ?? 6000;
        this.interruptionWatchdogMs = options.interruptionWatchdogMs ?? 15000;
        this.interruptionMaxFastAttempts = options.interruptionMaxFastAttempts ?? 20;
        this._interrupted = false;
        this._interruptStartTs = null;
        this._recoveryAttempts = 0;
        this._recoveryInFlight = false;
        this._recoveryTimer = null;
        this._recoveryDueAt = 0;
        this._stuckNotified = false;
        this._probe = null;
        this._recoveryRecorderActive = false;
        this._lastDataTs = 0;
        this._sawData = false;
        this._trackBound = null;
        this._trackHandlers = null;
        this._onVisibilityChangeHandler = null;
        this.onInterruption = null;           // Called when an interruption was detected and the clip saved
        this.onInterruptionResolved = null;   // Called when recording continued on a fresh mic
        this.onInterruptionStuck = null;      // Called once when auto-recovery switches to slow retries
```

### v1 — core methods (verbatim) — `_handleData` through `retryInterruptionNow`
```js
    /**
     * Handle a data chunk from any active MediaRecorder (segment, rollover, or recovery probe).
     */
    _handleData(e) {
        const size = e.data ? e.data.size : 0;
        if (size > 0) {
            this.audioChunks.push(e.data);
            this._sawData = true;
        }
        this._lastDataTs = Date.now();
        if (this._probe && this._probe.active) {
            this._probe.events += 1;
            this._probe.bytes += size;
        }
    }

    /**
     * Bind mute/unmute/ended listeners on the current mic track.
     */
    _bindTrack(track) {
        this._unbindTrack();
        if (!track || typeof track.addEventListener !== 'function') {
            return;
        }
        const onMute = () => this._onTrackMute();
        const onUnmute = () => this._onTrackUnmute();
        const onEnded = () => {
            if (this.state === 'recording' && !this._stopRequested) {
                this._onInterruption('ended');
            }
        };
        track.addEventListener('mute', onMute);
        track.addEventListener('unmute', onUnmute);
        track.addEventListener('ended', onEnded);
        this._trackBound = track;
        this._trackHandlers = { onMute, onUnmute, onEnded };
    }

    _unbindTrack() {
        if (this._trackBound && this._trackHandlers) {
            try {
                this._trackBound.removeEventListener('mute', this._trackHandlers.onMute);
                this._trackBound.removeEventListener('unmute', this._trackHandlers.onUnmute);
                this._trackBound.removeEventListener('ended', this._trackHandlers.onEnded);
            } catch (e) { /* ignore */ }
        }
        this._trackBound = null;
        this._trackHandlers = null;
    }

    _onTrackMute() {
        if (this._stopRequested) return;
        if (this.state === 'recording') {
            this._onInterruption('mute');
        }
    }

    _onTrackUnmute() {
        if (this._interrupted && !this._stopRequested) {
            this._scheduleRecovery(600);   // mic may be back — check soon
        }
    }

    /**
     * The mic was grabbed (call or similar). Close the current clip cleanly and start
     * looking for the mic again; recording continues as a NEW clip once it is verified back.
     */
    _onInterruption(source) {
        if (!this.handleInterruptions || this.transcribeOnly) return;
        if (this.state !== 'recording' || this._interrupted || this._stopRequested) return;

        this._interrupted = true;
        this._interruptStartTs = Date.now();
        this._recoveryAttempts = 0;
        this._stuckNotified = false;
        // Freeze the clip clock while we wait for the mic.
        if (!this.pauseStartTime) this.pauseStartTime = Date.now();

        console.warn('[VoiceDiaryRecorder] Interruption detected (' + source + ') — closing clip and waiting for mic');
        this.setState('interrupted');
        if (this.onInterruption) this.onInterruption();

        this._attachVisibilityHandler();

        this._enqueueSegmentOp(async () => {
            const durationSeconds = this._captureSegmentDurationSeconds();
            let blob = null;
            try {
                blob = await this._stopRecorderKeepStream();
            } catch (error) {
                console.error('[VoiceDiaryRecorder] Could not close interrupted clip:', error);
                return;
            }
            if (!blob || !blob.size) return;
            // Persist the finished clip in the background so nothing is lost.
            const persist = this.upload([], { background: true, blob, durationSeconds });
            persist.catch((error) => {
                console.error('[VoiceDiaryRecorder] Interrupted clip upload failed:', error);
            });
        }).catch(() => {});

        this._scheduleRecovery(12000);   // fallback in case no event fires
    }

    _attachVisibilityHandler() {
        if (this._onVisibilityChangeHandler) return;
        if (typeof document === 'undefined' || typeof document.addEventListener !== 'function') return;
        this._onVisibilityChangeHandler = () => {
            if (this._interrupted && !this._stopRequested && document.visibilityState === 'visible') {
                this._scheduleRecovery(800);
            }
        };
        document.addEventListener('visibilitychange', this._onVisibilityChangeHandler);
    }

    _detachVisibilityHandler() {
        if (!this._onVisibilityChangeHandler) return;
        if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
            document.removeEventListener('visibilitychange', this._onVisibilityChangeHandler);
        }
        this._onVisibilityChangeHandler = null;
    }

    _scheduleRecovery(delay) {
        if (!this._interrupted || this._stopRequested) return;
        const due = Date.now() + delay;
        if (this._recoveryTimer && this._recoveryDueAt <= due) return;   // sooner check already pending
        if (this._recoveryTimer) clearTimeout(this._recoveryTimer);
        this._recoveryDueAt = due;
        this._recoveryTimer = setTimeout(() => {
            this._recoveryTimer = null;
            this._recoveryDueAt = 0;
            this._attemptRecovery();
        }, delay);
    }

    _clearRecoveryTimers() {
        if (this._recoveryTimer) {
            clearTimeout(this._recoveryTimer);
            this._recoveryTimer = null;
        }
        this._recoveryDueAt = 0;
        if (this._probe) {
            this._probe.active = false;
            if (this._probe.checkTimer) clearTimeout(this._probe.checkTimer);
            if (typeof this._probe.resolve === 'function') {
                try {
                    this._probe.resolve({ ok: false, events: this._probe.events, bytes: this._probe.bytes, ms: 0, aborted: true });
                } catch (e) { /* ignore */ }
            }
            this._probe = null;
        }
    }

    _sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    _swapInStream(stream) {
        this.stopStream();       // stop + unbind the previous (dead) stream
        this.stream = stream;
    }

    /**
     * Try to get the mic back. Retries with backoff; every attempt that returns a live
     * track is verified by watching for real audio data before recording continues.
     */
    async _attemptRecovery() {
        if (!this._interrupted || this._stopRequested || this.state !== 'interrupted') return;
        if (this._recoveryInFlight) return;
        this._recoveryInFlight = true;
        const fastMax = Math.max(1, this.interruptionMaxFastAttempts | 0);
        try {
            while (this._interrupted && !this._stopRequested && this.state === 'interrupted') {
                if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
                    await this._sleep(2500);
                    continue;
                }
                this._recoveryAttempts += 1;
                const slow = this._recoveryAttempts > fastMax;
                if (slow && !this._stuckNotified) {
                    this._stuckNotified = true;
                    console.warn('[VoiceDiaryRecorder] Interruption recovery slow mode');
                    if (this.onInterruptionStuck) this.onInterruptionStuck();
                }
                let fresh = null;
                try {
                    fresh = await navigator.mediaDevices.getUserMedia(this._micConstraints());
                } catch (error) {
                    console.warn('[VoiceDiaryRecorder] Recovery getUserMedia failed:', error);
                }
                if (fresh) {
                    const track = fresh.getAudioTracks ? fresh.getAudioTracks()[0] : null;
                    if (track && !track.muted) {
                        this._swapInStream(fresh);
                        const probePromise = this._probeRecorder(this.interruptionProbeMs);
                        this._beginRecorderOnStream();
                        this._recoveryRecorderActive = true;
                        const result = await probePromise;
                        if (!this._interrupted || this._stopRequested) {
                            return;   // session already moved on (stop/salvage handled)
                        }
                        if (result && result.ok) {
                            console.warn('[VoiceDiaryRecorder] Mic recovered — continuing (' + result.bytes + 'B in ' + result.ms + 'ms)');
                            this._commitInterruption();
                            return;
                        }
                        // The fresh mic delivered no audio — drop this attempt and try again.
                        try { await this._stopRecorderKeepStream(); } catch (e) { /* ignore */ }
                        this._recoveryRecorderActive = false;
                        this.stopStream();
                        this.audioChunks = [];
                        this.audioBlob = null;
                    } else {
                        fresh.getTracks().forEach((t) => { try { t.stop(); } catch (e) { /* ignore */ } });
                    }
                }
                await this._sleep(slow ? this.interruptionSlowRetryMs : this.interruptionRetryMs);
            }
        } finally {
            this._recoveryInFlight = false;
        }
    }

    /**
     * Watch a just-started recorder for proof of real audio before trusting it.
     */
    _probeRecorder(timeoutMs) {
        return new Promise((resolve) => {
            const startedAt = Date.now();
            const earlyFailMs = Math.min(4500, Math.max(400, Math.round(timeoutMs * 0.75)));
            this._probe = { events: 0, bytes: 0, active: true, startedAt, checkTimer: null, resolve };
            const check = () => {
                const probe = this._probe;
                if (!probe || !probe.active) return;
                const elapsed = Date.now() - startedAt;
                let done = false;
                let ok = false;
                if ((probe.events >= 2 && probe.bytes >= 8000) || (probe.events >= 1 && probe.bytes >= 30000)) {
                    done = true;
                    ok = true;
                } else if (elapsed >= earlyFailMs && probe.events === 0) {
                    done = true;
                    ok = false;
                } else if (elapsed >= timeoutMs) {
                    done = true;
                    ok = (probe.events >= 1 && probe.bytes >= 1000);
                }
                if (done) {
                    probe.active = false;
                    const result = { ok, events: probe.events, bytes: probe.bytes, ms: elapsed };
                    if (this._probe === probe) this._probe = null;
                    resolve(result);
                } else {
                    probe.checkTimer = setTimeout(check, 120);
                }
            };
            check();
        });
    }

    _commitInterruption(opts) {
        opts = opts || {};
        this._interrupted = false;
        this._interruptStartTs = null;
        this._recoveryAttempts = 0;
        this._stuckNotified = false;
        this._clearRecoveryTimers();
        this._detachVisibilityHandler();
        this._recoveryRecorderActive = false;
        if (this.state !== 'recording') {
            this.setState('recording');
        }
        if (!opts.silent && this.onInterruptionResolved) {
            this.onInterruptionResolved();
        }
    }

    async _abortInterruption() {
        this._interrupted = false;
        this._interruptStartTs = null;
        this._recoveryAttempts = 0;
        this._stuckNotified = false;
        this._clearRecoveryTimers();
        this._detachVisibilityHandler();
        this._recoveryRecorderActive = false;
        try {
            if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                await this._stopRecorderKeepStream();
            }
        } catch (error) { /* ignore */ }
        this.stopStream();
        this.audioChunks = [];
        this.audioBlob = null;
        if (this.pauseStartTime) this.pauseStartTime = null;
    }

    /**
     * The user pressed Stop while an interruption is being recovered.
     * Keep a just-recovered clip if it already captured audio; otherwise finish cleanly.
     */
    async _finishInterruptedStop(files = []) {
        if (this._hasSalvageableRecovery()) {
            this._commitInterruption({ silent: true });
            return this.stopRecording(files);
        }
        return this._enqueueSegmentOp(async () => {
            await this._abortInterruption();
            this.setState('idle');
        });
    }

    _hasSalvageableRecovery() {
        if (!this._interrupted || !this._recoveryRecorderActive) return false;
        if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') return false;
        return this._recoveryChunkBytes() >= 8000;
    }

    _recoveryChunkBytes() {
        return (this.audioChunks || []).reduce((total, chunk) => total + ((chunk && chunk.size) ? chunk.size : 0), 0);
    }

    /**
     * Manual nudge: reset the retry budget and attempt recovery immediately.
     */
    retryInterruptionNow() {
        if (!this._interrupted || this._stopRequested) return false;
        this._recoveryAttempts = 0;
        this._stuckNotified = false;
        if (this._recoveryTimer) {
            clearTimeout(this._recoveryTimer);
            this._recoveryTimer = null;
        }
        this._recoveryDueAt = 0;
        this._attemptRecovery();
        return true;
    }
```

### v1 — UI hooks (index.html): "interrupted" state + toasts (verbatim)
```js
      case 'interrupted':
        // A phone call (or similar) grabbed the mic; the clip so far was saved.
        // Recording continues automatically once the mic is available again.
        recordBtn.disabled = true;
        recordBtn.classList.add('paused');
        stopBtn.classList.remove('hidden');
        stopBtn.disabled = false;
        showIcon('resume');
        break;

  recorder.onInterruption = function() {
    if (window.VDTheme) {
      window.VDTheme.showToast(
        '{% trans "Interruption detected — recording saved so far. It will continue automatically when the microphone returns." %}',
        'info'
      );
    }
  };

  recorder.onInterruptionResolved = function() {
    if (window.VDTheme) {
      window.VDTheme.showToast(
        '{% trans "Microphone is back — recording continued." %}',
        'info'
      );
    }
  };

  recorder.onInterruptionStuck = function() {
    if (window.VDTheme) {
      window.VDTheme.showToast(
        '{% trans "Still waiting for the microphone. You can stop and start a new recording, or wait — it will continue automatically." %}',
        'info'
      );
    }
  };
```

### v1 — tests (test_audio_recorder_interrupt.js, verbatim)
```js
/**
 * Tests for VoiceDiaryRecorder interruption handling (a phone call grabs the mic mid-recording).
 *
 * Run with: node --test src/static/recordings/js/test_audio_recorder_interrupt.js
 */

'use strict';

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');

let recorderInstances = [];
let fetchCalls = [];
let gumCalls = [];
let gumQueue = [];
let gumDefault = { muted: false };
let visibilityListeners = [];

function bigBlob(bytes) {
    return new Blob([new Uint8Array(bytes)], { type: 'audio/webm' });
}

class MockTrack {
    constructor() {
        this.readyState = 'live';
        this.muted = false;
        this.stopped = false;
        this._listeners = {};
    }
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); }
    removeEventListener(ev, fn) {
        const arr = this._listeners[ev] || [];
        const i = arr.indexOf(fn);
        if (i >= 0) arr.splice(i, 1);
    }
    dispatch(ev) { (this._listeners[ev] || []).slice().forEach((fn) => fn({})); }
    stop() { this.stopped = true; this.readyState = 'ended'; this.dispatch('ended'); }
    getSettings() { return {}; }
}

class MockStream {
    constructor(track) { this.track = track; this.active = true; }
    getTracks() { return [this.track]; }
    getAudioTracks() { return [this.track]; }
}

class MockMediaRecorder {
    constructor(stream, opts) {
        this.stream = stream;
        this.opts = opts;
        this.state = 'inactive';
        this.ondataavailable = null;
        this.onstop = null;
        this.mimeType = (opts && opts.mimeType) || 'audio/webm';
        recorderInstances.push(this);
    }
    start() {
        this.state = 'recording';
        if (MockMediaRecorder.autoEmit && this.ondataavailable) {
            this.ondataavailable({ data: bigBlob(40000) });
        }
    }
    emit(bytes) {
        if (this.ondataavailable) this.ondataavailable({ data: bigBlob(bytes || 40000) });
    }
    stop() {
        this.state = 'inactive';
        if (this.ondataavailable) this.ondataavailable({ data: new Blob(['end'], { type: 'audio/webm' }) });
        if (this.onstop) this.onstop();
    }
    pause() { this.state = 'paused'; }
    resume() { this.state = 'recording'; }
    requestData() {}
    static isTypeSupported(type) { return type.includes('webm'); }
}
MockMediaRecorder.autoEmit = true;

function installBrowserMocks() {
    recorderInstances = [];
    fetchCalls = [];
    gumCalls = [];
    gumQueue = [];
    gumDefault = { muted: false };
    visibilityListeners = [];
    MockMediaRecorder.autoEmit = true;

    global.window = { location: { protocol: 'http:', host: 'localhost' }, registration: {} };

    const nav = {
        onLine: true,
        mediaDevices: {
            getUserMedia: async () => {
                gumCalls.push(Date.now());
                const spec = gumQueue.length ? gumQueue.shift() : gumDefault;
                if (spec && spec.fail) throw new Error('mic unavailable');
                const track = new MockTrack();
                track.muted = !!(spec && spec.muted);
                return new MockStream(track);
            },
        },
    };
    Object.defineProperty(global, 'navigator', { value: nav, configurable: true, writable: true });

    global.document = {
        cookie: 'csrftoken=test-csrf',
        querySelector: () => null,
        visibilityState: 'visible',
        addEventListener: (ev, fn) => { if (ev === 'visibilitychange') visibilityListeners.push(fn); },
        removeEventListener: (ev, fn) => {
            const i = visibilityListeners.indexOf(fn);
            if (i >= 0) visibilityListeners.splice(i, 1);
        },
    };
    global.MediaRecorder = MockMediaRecorder;
    global.fetch = async (url, opts) => {
        fetchCalls.push({ url, opts });
        return {
            ok: true,
            json: async () => ({ item_id: `item-${fetchCalls.length}`, status: 'processing' }),
        };
    };
}

installBrowserMocks();
const VoiceDiaryRecorder = require('./audio_recorder.js');

function waitFor(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function newRecorder(overrides) {
    const recorder = new VoiceDiaryRecorder(Object.assign({
        uploadUrl: '/voice/upload/',
        maxDuration: 240,
        maxFileSize: 100 * 1024 * 1024,
        autoContinueOnMaxDuration: true,
        interruptionRetryMs: 5,
        interruptionSlowRetryMs: 25,
        interruptionProbeMs: 600,
        interruptionWatchdogMs: 0,
        interruptionMaxFastAttempts: 2,
    }, overrides || {}));
    recorder.connectWebSocket = () => {};
    return recorder;
}

function currentTrack(recorder) {
    return recorder.stream.getAudioTracks()[0];
}

describe('VoiceDiaryRecorder interruption handling', () => {
    let recorder;

    beforeEach(() => {
        installBrowserMocks();
    });

    afterEach(() => {
        if (recorder) {
            recorder._clearRecoveryTimers();
            recorder.stopDurationTracking();
            recorder.stopStream();
            recorder = null;
        }
    });

    it('mute during recording closes the current clip and saves it in the background', async () => {
        recorder = newRecorder();
        let interruptions = 0;
        recorder.onInterruption = () => { interruptions += 1; };

        await recorder.startRecording();
        recorder.startTime = Date.now() - 5000;   // pretend 5s already recorded
        currentTrack(recorder).dispatch('mute');
        await waitFor(60);

        assert.equal(recorder.state, 'interrupted');
        assert.equal(interruptions, 1);
        assert.equal(recorder.mediaRecorder.state, 'inactive', 'clip recorder must be closed');
        assert.equal(fetchCalls.length, 1, 'clip must be uploaded in background');
        const body = fetchCalls[0].opts.body;
        assert.ok(body instanceof FormData);
        assert.equal(body.get('recording_group_id'), recorder.recordingGroupId);
        assert.ok(Number(body.get('recording_duration_seconds')) >= 5);
        assert.equal(recorder.currentItemId, null, 'background upload must not attach UI state');
    });

    it('recovers on a fresh mic, continues recording, and keeps one recording group', async () => {
        recorder = newRecorder();
        let resolved = 0;
        recorder.onInterruptionResolved = () => { resolved += 1; };

        await recorder.startRecording();
        const firstGroupId = recorder.recordingGroupId;
        // First mic grab is still muted; second one is usable.
        gumQueue = [{ muted: true }, { muted: false }];

        currentTrack(recorder).dispatch('mute');
        await waitFor(30);
        recorder.retryInterruptionNow();
        await waitFor(300);

        assert.equal(resolved, 1, 'interruption must resolve once mic verified');
        assert.equal(recorder.state, 'recording');
        assert.ok(recorderInstances.length >= 2, 'a new MediaRecorder must be running');
        assert.equal(recorder.mediaRecorder.state, 'recording');

        await recorder.stopRecording();
        assert.equal(fetchCalls.length, 2);
        assert.equal(fetchCalls[0].opts.body.get('recording_group_id'), firstGroupId);
        assert.equal(fetchCalls[1].opts.body.get('recording_group_id'), firstGroupId);
        assert.equal(recorder.currentItemId, 'item-2');
    });

    it('gives up gracefully (stuck notice once) and can still be stopped', async () => {
        recorder = newRecorder();
        let stuck = 0;
        recorder.onInterruptionStuck = () => { stuck += 1; };
        gumQueue = [];
        gumDefault = { muted: true };   // mic never comes back

        await recorder.startRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);
        recorder.retryInterruptionNow();
        await waitFor(150);

        assert.equal(stuck, 1, 'stuck notice must fire once');
        assert.equal(recorder.state, 'interrupted');

        await recorder.stopRecording();
        assert.equal(recorder.state, 'idle');
        assert.equal(fetchCalls.length, 1, 'only the saved clip was uploaded');
    });

    it('watchdog treats stalled audio data as an interruption', async () => {
        recorder = newRecorder({ interruptionWatchdogMs: 150 });
        await recorder.startRecording();   // autoEmit provides the first chunk

        await waitFor(600);
        assert.equal(recorder.state, 'interrupted', 'watchdog must have fired');
    });

    it('manual pause/resume is unaffected, and mute while paused does nothing', async () => {
        recorder = newRecorder();
        await recorder.startRecording();

        recorder.pauseRecording();
        assert.equal(recorder.state, 'paused');
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);
        assert.equal(recorder.state, 'paused');
        assert.equal(fetchCalls.length, 0);

        recorder.resumeRecording();
        assert.equal(recorder.state, 'recording');

        await recorder.stopRecording();
        assert.equal(fetchCalls.length, 1);
    });

    it('transcribe-only recorders ignore interruptions', async () => {
        recorder = newRecorder({ transcribeOnly: true });
        await recorder.startRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);
        assert.equal(recorder.state, 'recording');
        assert.equal(fetchCalls.length, 0);
        await recorder.stopRecording();
    });

    it('stop during an in-flight recovery keeps a just-started clip (salvage)', async () => {
        recorder = newRecorder();
        gumQueue = [{ muted: false }];
        await recorder.startRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        // Kick recovery; the fresh attempt starts capturing, then we stop
        // while the verification probe is still running.
        recorder.retryInterruptionNow();
        await waitFor(60);
        await recorder.stopRecording();
        await waitFor(40);

        assert.ok(fetchCalls.length >= 2, 'salvaged clip must be uploaded');
        assert.equal(recorder.currentItemId, 'item-2');
    });
});
```

### v1 — integration points (summary; exact diff in commit 741b856)
- `startRecording()`: resets interruption state; forbids starting while 'interrupted'.
- `stopRecording()`: branch for state 'interrupted' (salvage / finish cleanly).
- `startDurationTracking()`: 15s no-data watchdog → `_onInterruption('watchdog')`.
- `stopStream()`: unbinds track listeners.
- `_beginRecorderOnStream()`: routes chunks via `_handleData()`; binds track events.

### v1 — restore instructions
- `git cherry-pick 741b856` (or `git revert <the v2-revert commit>`) → `collectstatic` → restart.
- No DB/server changes involved. Served JS is fetched fresh by the service worker.

## v2 — current implementation (auto-pause + manual resume)
*(to be completed after implementation — see git log: "pause + manual resume" commit.)*
