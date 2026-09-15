/**
 * Voice Diary Audio Recorder
 * 
 * Browser-based audio recorder with WebSocket real-time status updates,
 * offline support via IndexedDB, and automatic format detection.
 * 
 * @class VoiceDiaryRecorder
 */
class VoiceDiaryRecorder {
    /**
     * Create a new VoiceDiaryRecorder instance.
     * 
     * @param {Object} options - Configuration options
     * @param {string} options.uploadUrl - Server endpoint for audio upload (default: '/voice/upload/')
     * @param {number} options.maxDuration - Max seconds per segment (default: 240). 0 = unlimited.
     * @param {number} options.maxFileSize - Maximum file size in bytes (default: 100MB, matches RECORDER_MAX_FILE_SIZE_MB)
     * @param {boolean} options.autoContinueOnMaxDuration - When true (default unless transcribeOnly),
     *        hitting maxDuration uploads the current clip and starts a new recording on the same mic stream.
     */
    constructor(options = {}) {
        this.uploadUrl = options.uploadUrl || '/voice/upload/';
        this.wsBaseUrl = options.wsBaseUrl || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
        this.maxDuration = options.maxDuration ?? 240;
        this.maxFileSize = options.maxFileSize ?? 100 * 1024 * 1024;
        
        // State management
        this.state = 'idle';  // idle, recording, paused, uploading, processing, done, error
        this.audioChunks = [];
        this.audioBlob = null;
        this.mediaRecorder = null;
        this.stream = null;
        this.ws = null;
        this.currentItemId = null;
        this.currentTempId = null;  // For transcribe-only mode (edit recorder)
        this.pollIntervalId = null;
        this.templateType = 'plain'; // Template type: 'plain' or 'list'
        
        // Duration tracking
        this.startTime = null;
        this.pauseStartTime = null;
        this.pauseDuration = 0;
        this.durationInterval = null;
        
        // Detect supported MIME type
        this.mimeType = this.getSupportedMimeType();
        
        // Event callbacks
        this.onStateChange = null;
        this.onDurationUpdate = null;
        this.onStatusUpdate = null;
        this.onComplete = null;
        this.onError = null;
        this.onCalendarConflict = null;  // Called when calendar conflict requires user confirmation
        this.onTranscriptionReady = null;  // Called when transcribe-only transcription is ready (edit mode)
        this.onContentReady = null;  // Called when transcription is ready (normal mode) - user can edit while classification runs
        this.onGuardDiscard = null;  // Called when speech guard rejects (normal mode)
        this.onTranscriptionDiscarded = null;  // Called when speech guard rejects (transcribe-only)
        this.onRollover = null;  // Called when a max-duration segment is saved and recording continues
        this.onRolloverError = null;  // Called if a background segment upload fails (recording continues)
        this.onSegmentHeld = null;  // Called when a segment is held for the final merge (multi-part take)
        this.onHeldPartsRecovered = null;  // Called after an unfinished take was recovered from the local backup

        // Transcribe-only mode: transcribe only, no IngestItem created (used by edit recorder)
        this.transcribeOnly = options.transcribeOnly || false;
        this.autoContinueOnMaxDuration = options.autoContinueOnMaxDuration ?? !this.transcribeOnly;

        // Serialize stop vs auto-rollover so a manual Stop during a segment swap is not lost
        this._segmentMutex = Promise.resolve();
        this._stopRequested = false;
        this._rolloverInFlight = false;

        // Shared by consecutive clips until the user starts a new Record session
        this.recordingGroupId = null;

        // Interruption handling: when the mic is grabbed (phone call or similar) the recording
        // AUTO-PAUSES — the same pause as the manual pause button; the user resumes manually.
        // On iOS the old mic is never handed back to the old recorder, so the resume continues
        // on a FRESH mic + new recorder; all parts are merged at STOP into ONE single recording
        // (see _mergePartsToWav) so the diary still gets one entry.
        this.pauseOnInterruption = options.pauseOnInterruption ?? !this.transcribeOnly;
        this.interruptionWatchdogMs = options.interruptionWatchdogMs ?? 15000;
        this.interruptionResumeRetryMs = options.interruptionResumeRetryMs ?? 1200;
        this.interruptionMaxResumeAttempts = options.interruptionMaxResumeAttempts ?? 3;
        this.resumeProbeMs = options.resumeProbeMs ?? 4000;
        this._resumeNeedsFreshMic = false;   // mic was lost; next resume needs a fresh handshake
        this._resumeInFlight = false;
        this._heldParts = [];                // finished sub-recordings of an interrupted take (merged at stop)
        this._heldDurationSeconds = 0;       // cumulative talk-time of held parts
        this.heldPartBackup = options.heldPartBackup !== false;  // persist held parts locally (crash safety)
        this.heldPartRecoveryMinAgeMs = options.heldPartRecoveryMinAgeMs ?? 30000;  // skip parts fresher than this
        this._recoveryInFlight = false;
        this._probe = null;
        this._lastDataTs = 0;
        this._sawData = false;
        this._trackBound = null;
        this._trackHandlers = null;
        this.onInterruptionPause = null;             // Called when an interruption auto-pauses the recording
        this.onInterruptionContinueBlocked = null;   // Called when resuming needs a mic that is not back yet

        // Quota state (populated by applyQuota or fetchAndApplyQuota)
        this.quotaData = null;
    }

    _newRecordingGroupId() {
        if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
            return crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    _captureSegmentDurationSeconds() {
        return Math.max(0, Math.round(this.getDuration()));
    }

    _appendRecordingMeta(formData, durationSeconds) {
        if (durationSeconds != null && durationSeconds > 0) {
            formData.append('recording_duration_seconds', String(durationSeconds));
        }
        if (this.recordingGroupId) {
            formData.append('recording_group_id', this.recordingGroupId);
        }
    }
    
    /**
     * Detect supported audio MIME type.
     * Prefers WebM, falls back to WAV for iOS Safari.
     */
    getSupportedMimeType() {
        const types = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/wav',
            'audio/mp4',
        ];
        for (const type of types) {
            if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type)) {
                console.log('[VoiceDiaryRecorder] Using MIME type:', type);
                return type;
            }
        }
        return '';
    }
    
    /**
     * Run fn exclusively against the current MediaRecorder (stop vs auto-rollover).
     */
    _enqueueSegmentOp(fn) {
        const run = this._segmentMutex.then(fn, fn);
        this._segmentMutex = run.then(() => undefined, () => undefined);
        return run;
    }

    _micConstraints() {
        return {
            audio: {
                sampleRate: 44100,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
            },
        };
    }

    /**
     * Create a MediaRecorder on the existing mic stream and start it.
     * Does not request a new getUserMedia permission prompt.
     */
    _beginRecorderOnStream() {
        if (!this.stream) {
            throw new Error('No media stream');
        }
        const recorderOpts = this.mimeType ? { mimeType: this.mimeType } : {};
        this.mediaRecorder = new MediaRecorder(this.stream, recorderOpts);
        if (!this.mimeType) {
            this.mimeType = this.mediaRecorder.mimeType || 'audio/webm';
        }
        this.audioChunks = [];
        this.audioBlob = null;
        this.pauseDuration = 0;
        this.pauseStartTime = null;
        this.startTime = Date.now();
        this._lastDataTs = Date.now();
        this._sawData = false;
        this.mediaRecorder.ondataavailable = (e) => this._handleData(e);
        const track = (typeof this.stream.getAudioTracks === 'function')
            ? this.stream.getAudioTracks()[0]
            : null;
        this._bindTrack(track);
        this.mediaRecorder.start(1000);
    }

    /**
     * After a max-duration stop, start the next clip.
     * Reuses the live stream when possible; otherwise requests the mic again
     * (required on many mobile browsers after MediaRecorder.stop()).
     */
    async _restartCaptureForRollover() {
        await new Promise((resolve) => setTimeout(resolve, 100));
        if (this._stopRequested) {
            return;
        }

        const streamUsable = !!(
            this.stream
            && this.stream.active !== false
            && typeof this.stream.getTracks === 'function'
            && this.stream.getTracks().some((track) => track.readyState === 'live')
        );

        if (streamUsable) {
            try {
                this._beginRecorderOnStream();
                return;
            } catch (error) {
                console.warn('[VoiceDiaryRecorder] Could not reuse mic stream, requesting a new one:', error);
            }
        }

        this.stopStream();
        if (this._stopRequested) {
            return;
        }
        this.stream = await navigator.mediaDevices.getUserMedia(this._micConstraints());
        this._beginRecorderOnStream();
    }

    /**
     * Handle a data chunk from any active MediaRecorder (segment, rollover, or resume probe).
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
     * Bind mute/ended listeners on the current mic track (interruption detection).
     */
    _bindTrack(track) {
        this._unbindTrack();
        if (!track || typeof track.addEventListener !== 'function') {
            return;
        }
        const onMute = () => this._onMicInterrupted('mute');
        const onEnded = () => {
            if (!this._stopRequested) this._onMicInterrupted('ended');
        };
        track.addEventListener('mute', onMute);
        track.addEventListener('ended', onEnded);
        this._trackBound = track;
        this._trackHandlers = { onMute, onEnded };
    }

    _unbindTrack() {
        if (this._trackBound && this._trackHandlers) {
            try {
                this._trackBound.removeEventListener('mute', this._trackHandlers.onMute);
                this._trackBound.removeEventListener('ended', this._trackHandlers.onEnded);
            } catch (e) { /* ignore */ }
        }
        this._trackBound = null;
        this._trackHandlers = null;
    }

    /**
     * The mic was taken (phone call or similar) — or audio stopped flowing (watchdog).
     * Auto-pause, exactly like the user pressing the pause button; resume stays manual.
     */
    _onMicInterrupted(source) {
        if (!this.pauseOnInterruption || this.transcribeOnly) return;
        if (this._stopRequested) return;
        if (this.state === 'recording') {
            console.warn('[VoiceDiaryRecorder] Mic interrupted (' + source + ') — auto-pausing');
            this.pauseRecording();
            this._resumeNeedsFreshMic = true;   // the old mic will not come back to this recorder
            if (this.onInterruptionPause) this.onInterruptionPause();
        } else if (this.state === 'paused') {
            this._resumeNeedsFreshMic = true;   // mic lost while the user had paused manually
        }
    }

    /**
     * Resume after a mic interruption. The old recorder cannot be trusted on iOS after a call
     * (silent zombie — see docs/INTERRUPTION-HANDLING.md), so: hold the paused segment for the
     * final merge, continue on a FRESH mic + new recorder, and only switch over after real
     * audio bytes are seen flowing.
     */
    async _resumeWithFreshMic() {
        if (this._resumeInFlight) return;
        this._resumeInFlight = true;
        try {
            // 1) Wait for a healthy mic — the call may still be holding it.
            let fresh = null;
            for (let attempt = 0; attempt < this.interruptionMaxResumeAttempts && !fresh; attempt++) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia(this._micConstraints());
                    const track = (typeof stream.getAudioTracks === 'function') ? stream.getAudioTracks()[0] : null;
                    if (track && !track.muted) {
                        fresh = stream;
                    } else {
                        stream.getTracks().forEach((t) => { try { t.stop(); } catch (e) { /* ignore */ } });
                    }
                } catch (error) {
                    console.warn('[VoiceDiaryRecorder] Resume mic request failed:', error);
                }
                if (!fresh) await this._sleep(this.interruptionResumeRetryMs);
            }
            if (this._stopRequested) return;
            if (!fresh) {
                if (this.onInterruptionContinueBlocked) this.onInterruptionContinueBlocked();
                return;   // stay paused — nothing was torn down; try again later
            }

            // 2) Close the paused segment and HOLD it — it stays part of ONE single recording
            //    that gets merged when the take ends (never uploaded on its own).
            const durationSeconds = this._captureSegmentDurationSeconds();
            await this._enqueueSegmentOp(async () => {
                let blob = null;
                try {
                    blob = await this._stopRecorderKeepStream();
                } catch (error) { /* ignore */ }
                if (blob && blob.size) {
                    this._heldParts.push({ blob, durationSeconds });
                    this._heldDurationSeconds += durationSeconds;
                    this._persistHeldPart(blob, durationSeconds);
                }
            });
            if (this._stopRequested) return;

            // 3) Continue on the fresh mic; verify real audio is flowing before switching.
            this.stopStream();
            this.stream = fresh;
            const probePromise = this._probeRecorder(this.resumeProbeMs);
            this._beginRecorderOnStream();
            const result = await probePromise;
            if (this._stopRequested) return;   // user stopped while we were verifying
            if (result && result.ok) {
                this._resumeNeedsFreshMic = false;
                this.setState('recording');
                console.warn('[VoiceDiaryRecorder] Continued on fresh mic (' + result.bytes + 'B in ' + result.ms + 'ms)');
                return;
            }

            // Probe failed — drop this attempt; the user can press Play again.
            try { await this._stopRecorderKeepStream(); } catch (error) { /* ignore */ }
            this.stopStream();
            this.audioChunks = [];
            this.audioBlob = null;
            if (this.onInterruptionContinueBlocked) this.onInterruptionContinueBlocked();
        } finally {
            this._resumeInFlight = false;
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

    _sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    /**
     * Merge the parts of an interrupted take into ONE single WAV recording.
     * The parts are the recorded pieces before/after mic handovers; merging them is what
     * makes the take land as a single recording (one entry) on the server side.
     */
    async _mergePartsToWav(parts) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) throw new Error('AudioContext unavailable');
        const totalSeconds = (parts || []).reduce((n, p) => n + (p.durationSeconds || 0), 0);
        if (totalSeconds > 25 * 60) throw new Error('Take too long to merge in-browser');
        const ac = new AC();
        try {
            const chunks = [];
            let sampleRate = 0;
            for (const part of parts) {
                const arrayBuffer = await part.blob.arrayBuffer();
                const audioBuffer = await new Promise((resolve, reject) => {
                    ac.decodeAudioData(arrayBuffer, resolve, reject);
                });
                if (!sampleRate) sampleRate = audioBuffer.sampleRate;
                const float = audioBuffer.getChannelData(0);
                const int16 = new Int16Array(float.length);
                for (let i = 0; i < float.length; i++) {
                    const s = Math.max(-1, Math.min(1, float[i]));
                    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
                }
                chunks.push(int16);
            }
            if (!chunks.length || !sampleRate) throw new Error('Nothing to merge');
            let totalSamples = 0;
            for (const c of chunks) totalSamples += c.length;
            const header = new ArrayBuffer(44);
            const view = new DataView(header);
            const writeStr = (offset, str) => {
                for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
            };
            writeStr(0, 'RIFF');
            view.setUint32(4, 36 + totalSamples * 2, true);
            writeStr(8, 'WAVE');
            writeStr(12, 'fmt ');
            view.setUint32(16, 16, true);
            view.setUint16(20, 1, true);
            view.setUint16(22, 1, true);
            view.setUint32(24, sampleRate, true);
            view.setUint32(28, sampleRate * 2, true);
            view.setUint16(32, 2, true);
            view.setUint16(34, 16, true);
            writeStr(36, 'data');
            view.setUint32(40, totalSamples * 2, true);
            const blobParts = [header];
            for (const c of chunks) blobParts.push(c.buffer);
            return new Blob(blobParts, { type: 'audio/wav' });
        } finally {
            try { ac.close(); } catch (e) { /* ignore */ }
        }
    }

    // ------------------------------------------------------------------
    // Crash safety: local backup of held take parts
    // ------------------------------------------------------------------

    /**
     * Persist a held part to a small local IndexedDB database so an unfinished
     * multi-part take survives a crash or reload. Fire-and-forget: never rejects,
     * never blocks the recording flow; silently skipped when IndexedDB is not
     * available (the recording itself is unaffected).
     */
    async _persistHeldPart(blob, durationSeconds) {
        if (!this.heldPartBackup || this.transcribeOnly) return;
        if (typeof indexedDB === 'undefined' || !indexedDB) return;
        try {
            if (!blob || !blob.size || typeof blob.arrayBuffer !== 'function') return;
            // Capture index/group synchronously (before any await): persist calls are
            // fire-and-forget and must not race each other's part numbering.
            const index = Math.max(0, this._heldParts.length - 1);
            const group = this.recordingGroupId || 'nogroup';
            const buffer = await blob.arrayBuffer();
            const db = await this._openHeldBackupDB();
            await new Promise((resolve, reject) => {
                const tx = db.transaction('held-parts', 'readwrite');
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
                tx.onabort = () => reject(tx.error || new Error('transaction aborted'));
                tx.objectStore('held-parts').put({
                    id: group + ':' + index,
                    group: group,
                    index: index,
                    buffer: buffer,
                    mimeType: blob.type || this.mimeType || 'audio/webm',
                    durationSeconds: durationSeconds || 0,
                    updatedAt: Date.now(),
                });
            });
        } catch (error) {
            console.warn('[VoiceDiaryRecorder] Could not back up held part:', error);
        }
    }

    _openHeldBackupDB() {
        return new Promise((resolve, reject) => {
            let request;
            try {
                request = indexedDB.open('VoiceDiaryHeldPartsDB', 1);
            } catch (error) {
                reject(error);
                return;
            }
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains('held-parts')) {
                    db.createObjectStore('held-parts', { keyPath: 'id' });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Remove the local backup records of one take. Called after the take's audio
     * has been uploaded (success only) and after a recovered take was uploaded.
     * Never rejects.
     */
    async _deleteHeldParts(group) {
        if (!this.heldPartBackup) return;
        if (typeof indexedDB === 'undefined' || !indexedDB) return;
        if (!group) return;
        try {
            const db = await this._openHeldBackupDB();
            await new Promise((resolve, reject) => {
                const tx = db.transaction('held-parts', 'readwrite');
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
                tx.onabort = () => reject(tx.error || new Error('transaction aborted'));
                const store = tx.objectStore('held-parts');
                const req = store.getAll();
                req.onsuccess = () => {
                    (req.result || []).forEach((record) => {
                        if (record && record.group === group) store.delete(record.id);
                    });
                };
            });
        } catch (error) {
            console.warn('[VoiceDiaryRecorder] Could not delete held-part backup:', error);
        }
    }

    async _clearHeldPartsBackup() {
        if (!this.heldPartBackup) return;
        await this._deleteHeldParts(this.recordingGroupId);
    }

    _loadHeldPartGroups() {
        return new Promise((resolve) => {
            const fail = (error) => {
                console.warn('[VoiceDiaryRecorder] Could not read held-part backups:', error);
                resolve({});
            };
            let opening;
            try {
                opening = this._openHeldBackupDB();
            } catch (error) {
                fail(error);
                return;
            }
            opening.then((db) => {
                let tx;
                try {
                    tx = db.transaction('held-parts', 'readonly');
                } catch (error) {
                    fail(error);
                    return;
                }
                const req = tx.objectStore('held-parts').getAll();
                req.onsuccess = () => {
                    const groups = {};
                    (req.result || []).forEach((record) => {
                        if (!record || !record.group) return;
                        (groups[record.group] = groups[record.group] || []).push(record);
                    });
                    resolve(groups);
                };
                req.onerror = () => fail(req.error);
            }).catch(fail);
        });
    }

    /**
     * Crash recovery: upload any held parts left behind by an unfinished take
     * (browser crashed or was reloaded mid-recording). Runs silently on the
     * recording page; only when the recorder is idle. Parts fresher than
     * heldPartRecoveryMinAgeMs are left alone (another tab may still be
     * recording them) and are recovered on a later visit.
     * Returns the number of recovered parts.
     */
    async recoverHeldParts() {
        if (!this.heldPartBackup || this.transcribeOnly) return 0;
        if (this.state !== 'idle' || this._recoveryInFlight) return 0;
        this._recoveryInFlight = true;
        let recovered = 0;
        try {
            const groups = await this._loadHeldPartGroups();
            const keys = Object.keys(groups);
            for (const key of keys) {
                if (this.state !== 'idle') break;   // user is recording — retry on a later visit
                const records = groups[key].slice().sort((a, b) => (a.index || 0) - (b.index || 0));
                const newest = records.reduce((n, r) => Math.max(n, r.updatedAt || 0), 0);
                if (Date.now() - newest < this.heldPartRecoveryMinAgeMs) continue;
                const ok = await this._recoverOneTake(key, records);
                if (ok) recovered += records.length;
            }
        } catch (error) {
            console.warn('[VoiceDiaryRecorder] Held-part recovery failed (will retry):', error);
        } finally {
            this._recoveryInFlight = false;
        }
        return recovered;
    }

    async _recoverOneTake(groupKey, records) {
        if (this.state !== 'idle') return false;
        const parts = records.map((r) => ({
            blob: new Blob([r.buffer], { type: r.mimeType || 'audio/webm' }),
            durationSeconds: r.durationSeconds || 0,
        }));
        const totalDuration = parts.reduce((n, p) => n + (p.durationSeconds || 0), 0);
        const groupId = (groupKey && groupKey !== 'nogroup') ? groupKey : null;

        let merged = null;
        if (parts.length > 1) {
            try {
                merged = await this._mergePartsToWav(parts);
            } catch (error) {
                console.warn('[VoiceDiaryRecorder] Recovery merge failed — uploading parts separately:', error);
            }
        }
        if (this.state !== 'idle') return false;

        try {
            if (merged) {
                await this._uploadRecoveredBlob(merged, totalDuration, groupId);
            } else {
                for (let i = 0; i < parts.length; i++) {
                    await this._uploadRecoveredBlob(parts[i].blob, parts[i].durationSeconds, groupId);
                }
            }
        } catch (error) {
            if (error && error.status >= 400 && error.status < 500) {
                // Unprocessable (e.g. corrupt audio) — drop it so it does not retry forever.
                console.warn('[VoiceDiaryRecorder] Dropping unrecoverable held part(s):', error);
                await this._deleteHeldParts(groupKey);
            } else {
                console.warn('[VoiceDiaryRecorder] Could not upload recovered take (will retry next visit):', error);
            }
            return false;
        }
        await this._deleteHeldParts(groupKey);
        console.warn('[VoiceDiaryRecorder] Recovered unfinished take (' + parts.length + ' part(s), ' + Math.round(totalDuration) + 's)');
        if (this.onHeldPartsRecovered) {
            try { this.onHeldPartsRecovered(parts.length, Math.round(totalDuration)); } catch (e) { /* ignore */ }
        }
        return true;
    }

    async _uploadRecoveredBlob(blob, durationSeconds, groupId) {
        const formData = new FormData();
        const mime = blob.type || this.mimeType || '';
        const extension = mime.includes('webm') ? 'webm'
            : (mime.includes('wav') ? 'wav'
            : (mime.includes('mp4') ? 'mp4' : 'wav'));
        formData.append('audio', blob, 'recovered.' + extension);
        formData.append('template_type', this.templateType);
        if (durationSeconds) formData.append('recording_duration_seconds', String(Math.round(durationSeconds)));
        if (groupId) formData.append('recording_group_id', groupId);
        const response = await fetch(this.uploadUrl, {
            method: 'POST',
            body: formData,
            headers: { 'X-CSRFToken': this.getCsrfToken() },
        });
        if (!response.ok) {
            const error = new Error('Recovery upload failed (HTTP ' + response.status + ')');
            error.status = response.status;
            throw error;
        }
        return response;
    }

    /**
     * Stop the current MediaRecorder and resolve with its audio blob.
     * Leaves the microphone stream running so a new segment can start immediately.
     */
    _stopRecorderKeepStream() {
        return new Promise((resolve, reject) => {
            if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') {
                const blob = this.audioBlob || new Blob(this.audioChunks, { type: this.mimeType });
                this.audioChunks = [];
                resolve(blob);
                return;
            }
            this.mediaRecorder.onstop = () => {
                const blob = new Blob(this.audioChunks, { type: this.mimeType });
                this.audioChunks = [];
                this.audioBlob = blob;
                resolve(blob);
            };
            try {
                // Stopping from the "paused" state can drop the buffered tail in some browsers.
                if (this.mediaRecorder.state === 'paused') {
                    this.mediaRecorder.resume();
                }
                this.mediaRecorder.stop();
            } catch (error) {
                reject(error);
            }
        });
    }

    /**
     * Start recording audio.
     */
    async startRecording() {
        if (this.state === 'recording' || this.state === 'paused' || this.state === 'uploading') {
            throw new Error(`Cannot start recording in state: ${this.state}`);
        }

        try {
            this._stopRequested = false;
            this.currentItemId = null;
            this.currentTempId = null;
            this._resumeNeedsFreshMic = false;
            this._resumeInFlight = false;
            this._probe = null;
            this.recordingGroupId = this.transcribeOnly ? null : this._newRecordingGroupId();

            this.stream = await navigator.mediaDevices.getUserMedia(this._micConstraints());

            this._beginRecorderOnStream();
            this.setState('recording');
            this.startDurationTracking();
            
        } catch (error) {
            this.setState('error');
            this.stopStream();
            throw error;
        }
    }
    
    /**
     * Pause recording.
     */
    pauseRecording() {
        if (this.state !== 'recording') {
            throw new Error(`Cannot pause in state: ${this.state}`);
        }
        if (!this.mediaRecorder || this.mediaRecorder.state !== 'recording') {
            return;
        }
        
        if (typeof this.mediaRecorder.requestData === 'function') {
            this.mediaRecorder.requestData();
        }
        this.mediaRecorder.pause();
        this.pauseStartTime = Date.now();
        this.setState('paused');
    }
    
    /**
     * Resume recording.
     */
    resumeRecording() {
        if (this.state !== 'paused') {
            throw new Error(`Cannot resume in state: ${this.state}`);
        }

        // After a mic interruption the old recorder cannot be trusted (iOS never hands the
        // same mic back) — continue on a fresh mic instead of a silent zombie resume.
        if (this._resumeNeedsFreshMic) {
            return this._resumeWithFreshMic();
        }
        
        if (this.pauseStartTime) {
            this.pauseDuration += Date.now() - this.pauseStartTime;
            this.pauseStartTime = null;
        }
        
        this.mediaRecorder.resume();
        this.setState('recording');
    }
    
    /**
     * Stop recording and upload.
     * @param {File[]} files - Optional array of files to include with the upload (managed by caller/session)
     */
    async stopRecording(files = []) {
        this._stopRequested = true;
        this.stopDurationTracking();
        const durationSeconds = this._captureSegmentDurationSeconds();

        return this._enqueueSegmentOp(async () => {
            if (this.state !== 'recording' && this.state !== 'paused') {
                if (this.audioBlob && (!this.mediaRecorder || this.mediaRecorder.state === 'inactive')) {
                    this.stopStream();
                    await this.upload(files, { durationSeconds });
                    return;
                }
                throw new Error(`Cannot stop in state: ${this.state}`);
            }

            if (this.pauseStartTime) {
                this.pauseDuration += Date.now() - this.pauseStartTime;
                this.pauseStartTime = null;
            }

            // Collect every part of this take: held parts (after interruptions) + the current segment.
            let currentBlob = null;
            if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                currentBlob = await this._stopRecorderKeepStream();
            } else if (this.audioBlob) {
                currentBlob = this.audioBlob;
            }
            const parts = this._heldParts.slice();
            this._heldParts = [];
            this._heldDurationSeconds = 0;
            if (currentBlob && currentBlob.size) {
                parts.push({ blob: currentBlob, durationSeconds });
            }

            if (parts.length === 0) {
                this.stopStream();
                this.setState('idle');
                return;
            }

            if (parts.length === 1) {
                // Single recording — upload as-is (no re-encode, original quality).
                this.audioBlob = parts[0].blob;
                this.stopStream();
                await this.upload(files, { durationSeconds: parts[0].durationSeconds });
                return;
            }

            // Multi-part take (interrupted + resumed): merge into ONE single recording.
            const totalDuration = parts.reduce((n, p) => n + (p.durationSeconds || 0), 0);
            this.setState('uploading');
            let merged = null;
            try {
                merged = await this._mergePartsToWav(parts);
            } catch (error) {
                console.warn('[VoiceDiaryRecorder] Could not merge take parts — uploading them separately:', error);
            }

            if (merged) {
                this.audioBlob = merged;
                this.stopStream();
                await this.upload(files, { durationSeconds: totalDuration });
                await this._clearHeldPartsBackup();
                return;
            }

            // Fallback: upload each part so nothing is lost.
            this.stopStream();
            for (let i = 0; i < parts.length - 1; i++) {
                const persist = this.upload([], { background: true, blob: parts[i].blob, durationSeconds: parts[i].durationSeconds });
                persist.catch((error) => {
                    console.error('[VoiceDiaryRecorder] Part upload failed:', error);
                });
            }
            this.audioBlob = parts[parts.length - 1].blob;
            await this.upload(files, { durationSeconds: parts[parts.length - 1].durationSeconds });
            await this._clearHeldPartsBackup();
        }).finally(() => {
            this.stopDurationTracking();
        });
    }

    /**
     * Auto-save the current clip at maxDuration and immediately start the next segment.
     * Upload of the finished clip runs in the background so the conversation is not interrupted.
     */
    async rolloverRecording() {
        if (!(this.maxDuration > 0) || !this.autoContinueOnMaxDuration || this.transcribeOnly || this._stopRequested) {
            return;
        }
        if (this.state !== 'recording') {
            return;
        }
        if (this.getDuration() < this.maxDuration) {
            return;
        }

        return this._enqueueSegmentOp(async () => {
            if (!(this.maxDuration > 0) || !this.autoContinueOnMaxDuration || this.transcribeOnly || this._stopRequested) {
                return;
            }
            if (this.state !== 'recording') {
                return;
            }
            if (this.getDuration() < this.maxDuration) {
                return;
            }

            const durationSeconds = this._captureSegmentDurationSeconds();
            const blob = await this._stopRecorderKeepStream();

            if (this._stopRequested) {
                this.audioBlob = blob;
                return;
            }

            const multiPart = this._heldParts.length > 0;
            let persist = Promise.resolve();
            if (multiPart) {
                // Multi-part take (post-interruption): hold the finished segment for the
                // final merge — no mid-take uploads, the take stays ONE recording.
                if (blob && blob.size) {
                    this._heldParts.push({ blob, durationSeconds });
                    this._heldDurationSeconds += durationSeconds;
                    this._persistHeldPart(blob, durationSeconds);
                }
                if (this.onSegmentHeld) this.onSegmentHeld(durationSeconds);
            } else {
                // Persist the finished clip before swapping recorders so a restart
                // failure cannot drop audio that is already in memory.
                persist = this.upload([], { background: true, blob, durationSeconds });
                persist.catch((error) => {
                    console.error('[VoiceDiaryRecorder] Rollover upload failed:', error);
                });
            }

            this.stopDurationTracking();

            try {
                await this._restartCaptureForRollover();
                if (this._stopRequested) {
                    this.stopDurationTracking();
                    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                        try {
                            await this._stopRecorderKeepStream();
                        } catch (_) { /* ignore */ }
                    }
                    this.stopStream();
                    return;
                }
                this.setState('recording');
                this.startDurationTracking();
            } catch (error) {
                this.audioBlob = blob;
                this.stopStream();
                if (this._stopRequested) {
                    return;
                }
                console.warn('[VoiceDiaryRecorder] Rollover restart failed, starting a new session:', error);
                try {
                    this.setState('idle');
                    await this.startRecording();
                } catch (startError) {
                    this.setState('error');
                    try {
                        await persist;
                    } catch (uploadError) {
                        try {
                            await this.saveOffline({ blob, background: true, durationSeconds });
                        } catch (offlineError) {
                            console.error('[VoiceDiaryRecorder] Could not save segment after restart failure:', offlineError);
                        }
                        if (this.onRolloverError) {
                            this.onRolloverError(uploadError);
                        }
                    }
                    if (this.onError) {
                        this.onError(startError);
                    }
                    throw startError;
                }
            }

            if (this._stopRequested) {
                return;
            }

            if (!multiPart && this.onRollover) {
                this.onRollover();
            }
        });
    }
    
    /**
     * Upload audio to server.
     * @param {File[]} files - Optional array of files to include with the upload (managed by caller/session)
     * @param {Object} options
     * @param {Blob} options.blob - Audio to upload (defaults to this.audioBlob)
     * @param {boolean} options.background - If true, do not change recorder UI state or attach WebSocket
     */
    async upload(files = [], options = {}) {
        const blob = options.blob || this.audioBlob;
        const background = !!options.background;
        const durationSeconds = options.durationSeconds != null
            ? options.durationSeconds
            : this._captureSegmentDurationSeconds();

        if (!blob) {
            throw new Error('No audio to upload');
        }
        
        if (blob.size > this.maxFileSize) {
            const err = new Error(`File too large. Maximum size is ${this.maxFileSize / 1024 / 1024}MB`);
            if (background) {
                if (this.onRolloverError) this.onRolloverError(err);
                throw err;
            }
            throw err;
        }
        
        if (!background) {
            this.setState('uploading');
        }
        
        if (!navigator.onLine) {
            await this.saveOffline({ blob, background, durationSeconds });
            return;
        }
        
        try {
            const formData = new FormData();
            const mime = blob.type || this.mimeType || '';
            const extension = mime.includes('webm') ? 'webm'
                : (mime.includes('wav') ? 'wav'
                : (mime.includes('mp4') ? 'mp4' : (this.mimeType.includes('webm') ? 'webm' : 'wav')));
            formData.append('audio', blob, `recording.${extension}`);
            formData.append('template_type', this.templateType);
            if (this.transcribeOnly) {
                formData.append('transcribe_only', '1');
            }
            this._appendRecordingMeta(formData, durationSeconds);

            files.forEach((file) => {
                formData.append('files', file);
            });
            
            const response = await fetch(this.uploadUrl, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                },
            });
            
            if (!response.ok) {
                const errorBody = await response.json().catch(() => ({ error: 'Upload failed' }));
                const err = new Error(errorBody.message || errorBody.error || 'Upload failed');
                err.code = errorBody.error;
                err.status = response.status;
                err.quota = errorBody.quota || null;
                throw err;
            }
            
            const data = await response.json();
            console.log('[VoiceDiaryRecorder] Upload response:', data);

            if (background) {
                return data;
            }
            
            const tempId = data.temp_id;
            const itemId = data.item_id;
            
            if (tempId) {
                this.currentTempId = tempId;
                this.currentItemId = null;
                console.log('[VoiceDiaryRecorder] Transcribe-only mode, temp_id:', tempId);
                this.connectWebSocket(tempId);
            } else if (itemId) {
                this.currentItemId = itemId;
                this.currentTempId = null;
                console.log('[VoiceDiaryRecorder] Normal mode, item_id:', itemId);
                this.connectWebSocket(itemId);
            } else {
                throw new Error('Upload response missing temp_id/item_id');
            }
            
            this.setState('processing');
            return data;
            
        } catch (error) {
            if (background) {
                try {
                    await this.saveOffline({ blob, background: true, durationSeconds });
                } catch (offlineError) {
                    console.error('[VoiceDiaryRecorder] Could not save failed rollover offline:', offlineError);
                }
                if (this.onRolloverError) {
                    this.onRolloverError(error);
                }
                return;
            }
            this.setState('error');
            if (this.onError) {
                this.onError(error);
            }
            throw error;
        }
    }
    
    /**
     * Connect WebSocket for real-time status updates.
     * Falls back to polling /voice/status/<id>/ if WebSocket is unavailable (e.g. runserver instead of daphne).
     */
    connectWebSocket(itemId) {
        if (this.ws) {
            this.ws.close();
        }
        this.clearPolling();
        
        const wsUrl = `${this.wsBaseUrl}/ws/pipeline/${itemId}/`;
        console.log('[VoiceDiaryRecorder] Connecting WebSocket:', wsUrl);
        
        this.ws = new WebSocket(wsUrl);
        let fallbackStarted = false;
        
        const startPollingFallback = () => {
            if (fallbackStarted) return;
            fallbackStarted = true;
            console.log('[VoiceDiaryRecorder] WebSocket unavailable, falling back to polling');
            this.startPollingStatus(itemId);
        };
        
        this.ws.onopen = () => {
            console.log('[VoiceDiaryRecorder] WebSocket connected');
        };
        
        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('[VoiceDiaryRecorder] Status update:', data);

            // A prior clip's pipeline must not stop or replace an in-progress recording.
            if (this.state === 'recording' || this.state === 'paused') {
                const terminal = (
                    data.type === 'complete'
                    || data.type === 'error'
                    || data.type === 'content.ready'
                    || data.type === 'transcription.ready'
                    || data.type === 'transcription.discarded'
                    || data.checkpoint === 'guard_discard'
                    || data.status === 'calendar_conflict'
                    || data.conflict
                );
                if (terminal) {
                    this.clearPolling();
                    try { this.ws.close(); } catch (_) { /* ignore */ }
                }
                return;
            }
            
            if (this.onStatusUpdate) {
                this.onStatusUpdate(data);
            }
            
            // Handle guard discard (normal mode) - checkpoint + status from pipeline.status
            if (data.type === 'status' && data.checkpoint === 'guard_discard') {
                this.clearPolling();
                this.setState('done');
                this.ws.close();
                if (this.onGuardDiscard) this.onGuardDiscard(data.message || 'No speech detected');
                return;
            }
            
            // Handle transcription discarded (transcribe-only / edit mode)
            if (data.type === 'transcription.discarded') {
                this.clearPolling();
                this.setState('done');
                this.ws.close();
                if (this.onTranscriptionDiscarded) {
                    this.onTranscriptionDiscarded(data.reason || 'No speech detected');
                } else if (this.onError) {
                    this.onError(new Error(data.reason || 'No speech detected'));
                }
                return;
            }
            
            // Handle transcription ready (transcribe-only / edit mode)
            if (data.type === 'transcription.ready') {
                console.log('[VoiceDiaryRecorder] Transcription ready');
                this.setState('done');
                if (this.onTranscriptionReady) {
                    this.onTranscriptionReady({
                        temp_id: data.temp_id || this.currentTempId,
                        transcribed_text: data.transcribed_text,
                        detected_language: data.detected_language,
                    });
                }
                this.ws.close();
                return;
            }

            // Handle content ready (normal mode) - show text immediately, keep WebSocket open for complete/calendar_conflict
            if (data.type === 'content.ready') {
                this.setState('content_ready');
                if (this.onContentReady) {
                    this.onContentReady({
                        content_text: data.content_text,
                        detected_language: data.detected_language,
                    });
                }
                return;
            }
            
            if (data.type === 'complete') {
                this.setState('done');
                if (this.onComplete) {
                    this.onComplete(data);
                }
                this.ws.close();
            }
            
            // Handle calendar conflict - redirect to confirmation page
            if (data.status === 'calendar_conflict' || data.conflict) {
                console.log('[VoiceDiaryRecorder] Calendar conflict detected');
                this.setState('done');
                if (this.onCalendarConflict) {
                    this.onCalendarConflict(data);
                } else if (data.confirmation_url) {
                    // Default: redirect to confirmation page
                    window.location.href = data.confirmation_url;
                }
                this.ws.close();
            }
            
            if (data.type === 'error') {
                this.setState('error');
                if (this.onError) {
                    this.onError(new Error(data.error));
                }
            }
        };
        
        this.ws.onclose = () => {
            console.log('[VoiceDiaryRecorder] WebSocket closed');
            if ((this.state === 'processing' || this.state === 'content_ready') && !fallbackStarted) {
                startPollingFallback();
            }
        };
        
        this.ws.onerror = () => {
            if ((this.state === 'processing' || this.state === 'content_ready') && !fallbackStarted) {
                startPollingFallback();
            }
        };
    }
    
    clearPolling() {
        if (this.pollIntervalId) {
            clearInterval(this.pollIntervalId);
            this.pollIntervalId = null;
        }
    }
    
    /**
     * Poll GET /voice/status/<itemId>/ or /voice/status/pending/<tempId>/ until processed or error (fallback when WebSocket not available).
     */
    startPollingStatus(itemId) {
        const statusUrl = this.transcribeOnly
            ? `/voice/status/pending/${itemId}/`
            : `/voice/status/${itemId}/`;
        const poll = async () => {
            try {
                const response = await fetch(statusUrl, { headers: { 'Accept': 'application/json' } });
                if (response.status === 404) {
                    this.clearPolling();
                    this.setState('error');
                    if (this.onError) this.onError(new Error('Recording could not be processed. It may have been discarded.'));
                    return;
                }
                if (!response.ok) return;
                const data = await response.json();

                if (this.transcribeOnly) {
                    if (data.status === 'ready') {
                        this.clearPolling();
                        this.setState('done');
                        if (this.onTranscriptionReady) {
                            this.onTranscriptionReady({
                                temp_id: this.currentTempId,
                                transcribed_text: data.transcribed_text || data.content_text || '',
                                detected_language: data.detected_language || '',
                            });
                        }
                        return;
                    }
                    if (data.status === 'discarded') {
                        this.clearPolling();
                        this.setState('done');
                        if (this.onTranscriptionDiscarded) {
                            this.onTranscriptionDiscarded(data.reason || 'No speech detected');
                        } else if (this.onError) {
                            this.onError(new Error(data.reason || 'No speech detected'));
                        }
                        return;
                    }
                    if (data.status === 'error') {
                        this.clearPolling();
                        this.setState('error');
                        if (this.onError) this.onError(new Error(data.error || 'Transcription failed'));
                        return;
                    }
                    if (data.status === 'in_progress' && this.onStatusUpdate) {
                        this.onStatusUpdate({ type: 'status', message: data.message || 'Processing...' });
                    }
                    return;
                }

                const payload = {
                    type: (data.item_status === 'processed' || data.item_status === 'tagged') ? 'complete' : 'status',
                    status: data.item_status,
                    message: data.progress_message || data.item_status,
                    content_text: data.content_text,
                    detected_language: data.detected_language,
                };
                if (this.onStatusUpdate) this.onStatusUpdate(payload);

                if (data.calendar_conflict && data.confirmation_url) {
                    console.log('[VoiceDiaryRecorder] Calendar conflict detected (polling)');
                    this.clearPolling();
                    this.setState('done');
                    const conflictData = {
                        conflict: true,
                        confirmation_url: data.confirmation_url,
                        calendar_event_id: data.calendar_event_id
                    };
                    if (this.onCalendarConflict) {
                        this.onCalendarConflict(conflictData);
                    } else {
                        window.location.href = data.confirmation_url;
                    }
                    return;
                }

                if (data.item_status === 'processed' || data.item_status === 'tagged') {
                    this.clearPolling();
                    this.setState('done');
                    if (this.onComplete) this.onComplete(payload);
                    return;
                }
                if (data.job_status === 'error' && data.last_error) {
                    this.clearPolling();
                    this.setState('error');
                    if (this.onError) this.onError(new Error(data.last_error));
                    return;
                }
            } catch (_) {}
        };
        poll();
        this.pollIntervalId = setInterval(poll, 2000);
    }
    
    /**
     * Save recording offline for later sync.
     * @param {Object} options
     * @param {Blob} options.blob - Audio to store (defaults to this.audioBlob)
     * @param {boolean} options.background - If true, do not change recorder UI state
     */
    async saveOffline(options = {}) {
        const blob = options.blob || this.audioBlob;
        const background = !!options.background;
        const durationSeconds = options.durationSeconds != null
            ? options.durationSeconds
            : this._captureSegmentDurationSeconds();
        const db = await this.openDB();
        const tx = db.transaction('offline-recordings', 'readwrite');
        const store = tx.objectStore('offline-recordings');
        
        await store.add({
            blob: blob,
            timestamp: Date.now(),
            mimeType: this.mimeType,
            csrfToken: this.getCsrfToken(),
            transcribeOnly: this.transcribeOnly,
            templateType: this.templateType,
            recordingGroupId: this.recordingGroupId || null,
            recordingDurationSeconds: durationSeconds || null,
        });

        await this._registerBackgroundSync();
        
        if (background) {
            return;
        }

        this.setState('done');
        
        if (this.onStatusUpdate) {
            this.onStatusUpdate({
                type: 'offline',
                message: 'Recording saved offline. Will upload when online.',
            });
        }
    }

    /**
     * Ask the service worker to upload IndexedDB recordings when the network returns.
     * Used for both manual-stop and background (rollover) offline saves.
     */
    async _registerBackgroundSync() {
        try {
            let registration = null;
            if (typeof window !== 'undefined' && window.registration && window.registration.sync) {
                registration = window.registration;
            } else if (typeof navigator !== 'undefined' && navigator.serviceWorker) {
                registration = await navigator.serviceWorker.ready;
            }
            if (registration && registration.sync && typeof registration.sync.register === 'function') {
                await registration.sync.register('sync-recordings');
            }
        } catch (e) {
            console.warn('[VoiceDiaryRecorder] Background sync registration failed:', e);
        }
    }
    
    /**
     * Open IndexedDB for offline storage.
     */
    openDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open('VoiceDiaryDB', 1);
            
            request.onerror = () => reject(request.error);
            request.onsuccess = () => resolve(request.result);
            
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains('offline-recordings')) {
                    db.createObjectStore('offline-recordings', { keyPath: 'id', autoIncrement: true });
                }
            };
        });
    }
    
    /**
     * Get current recording duration in seconds.
     */
    getDuration() {
        if (!this.startTime) return 0;
        
        let elapsed = Date.now() - this.startTime - this.pauseDuration;
        
        if (this.pauseStartTime) {
            elapsed -= Date.now() - this.pauseStartTime;
        }
        
        return Math.max(0, elapsed / 1000);
    }

    /**
     * Elapsed talk-time of the whole take (held parts + current segment), so the timer
     * stays continuous across an interruption resume.
     */
    getTakeDuration() {
        return this._heldDurationSeconds + this.getDuration();
    }
    
    /**
     * Start duration tracking interval.
     */
    startDurationTracking() {
        this.stopDurationTracking();
        
        this.durationInterval = setInterval(() => {
            const duration = this.getDuration();
            
            if (this.onDurationUpdate) {
                this.onDurationUpdate(this.getTakeDuration());
            }

            // Mic-interruption watchdog: if audio data stops flowing while recording, treat it
            // as a mic interruption (auto-pause). Covers browsers without mute events.
            if (this.pauseOnInterruption && this.interruptionWatchdogMs > 0 && !this._stopRequested
                && this.state === 'recording' && this._sawData && this._lastDataTs
                && (Date.now() - this._lastDataTs) > this.interruptionWatchdogMs) {
                this._onMicInterrupted('watchdog');
            }
            
            if (this.maxDuration > 0 && duration >= this.maxDuration) {
                if (this.autoContinueOnMaxDuration && !this.transcribeOnly && this.state === 'recording') {
                    if (!this._rolloverInFlight) {
                        this._rolloverInFlight = true;
                        this.rolloverRecording().finally(() => {
                            this._rolloverInFlight = false;
                        });
                    }
                } else if (this.state === 'recording' || this.state === 'paused') {
                    this.stopRecording();
                }
            }
        }, 100);
    }
    
    /**
     * Stop duration tracking interval.
     */
    stopDurationTracking() {
        if (this.durationInterval) {
            clearInterval(this.durationInterval);
            this.durationInterval = null;
        }
    }
    
    /**
     * Stop media stream.
     */
    stopStream() {
        this._unbindTrack();
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
    }
    
    /**
     * Set state and trigger callback.
     */
    setState(newState) {
        this.state = newState;
        if (this.onStateChange) {
            this.onStateChange(newState);
        }
    }
    
    /**
     * Set the template type for the recording.
     * 
     * @param {string} templateType - 'plain' or 'list'
     */
    setTemplateType(templateType) {
        this.templateType = templateType || 'plain';
    }
    
    /**
     * Get CSRF token from cookies.
     */
    getCsrfToken() {
        const name = 'csrftoken';
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                return cookie.substring(name.length + 1);
            }
        }
        // Try from meta tag
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) {
            return meta.content;
        }
        // Try from hidden input
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input) {
            return input.value;
        }
        return '';
    }
    
    /**
     * Fetch the user's current quota from the server.
     * Returns the quota JSON or null on failure.
     */
    static async fetchQuota() {
        try {
            const response = await fetch('/voice/quota/', {
                headers: { 'Accept': 'application/json' },
            });
            if (!response.ok) return null;
            return await response.json();
        } catch (e) {
            console.warn('[VoiceDiaryRecorder] Could not fetch quota:', e);
            return null;
        }
    }

    /**
     * Apply quota data to this recorder instance.
     *
     * Token-based quotas: no maxDuration cap. Recorder uses only config max_duration
     * (per-segment limit; main recorder auto-continues until the user stops).
     * Stores quotaData for potential UI display (e.g. usage card).
     *
     * @param {Object} quota - Quota JSON from fetchQuota()
     */
    applyQuota(quota) {
        if (!quota) return;
        this.quotaData = quota;
    }

    /**
     * Convenience: fetch quota from server and apply it in one call.
     * Returns the quota data (or null).
     */
    async fetchAndApplyQuota() {
        const quota = await VoiceDiaryRecorder.fetchQuota();
        this.applyQuota(quota);
        return quota;
    }

    /**
     * Format duration as MM:SS.
     */
    static formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
}

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = VoiceDiaryRecorder;
}
