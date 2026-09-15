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

        // Transcribe-only mode: transcribe only, no IngestItem created (used by edit recorder)
        this.transcribeOnly = options.transcribeOnly || false;
        this.autoContinueOnMaxDuration = options.autoContinueOnMaxDuration ?? !this.transcribeOnly;

        // Serialize stop vs auto-rollover so a manual Stop during a segment swap is not lost
        this._segmentMutex = Promise.resolve();
        this._stopRequested = false;
        this._rolloverInFlight = false;

        // Shared by consecutive clips until the user starts a new Record session
        this.recordingGroupId = null;

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
        if (this.state === 'recording' || this.state === 'paused' || this.state === 'interrupted' || this.state === 'uploading') {
            throw new Error(`Cannot start recording in state: ${this.state}`);
        }

        try {
            this._stopRequested = false;
            this.currentItemId = null;
            this.currentTempId = null;
            this._interrupted = false;
            this._interruptStartTs = null;
            this._recoveryAttempts = 0;
            this._stuckNotified = false;
            this._clearRecoveryTimers();
            this._detachVisibilityHandler();
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

        if (this.state === 'interrupted') {
            return this._finishInterruptedStop(files);
        }

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

            const blob = await this._stopRecorderKeepStream();
            this.audioBlob = blob;
            this.stopStream();
            await this.upload(files, { durationSeconds });
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

            // Persist the finished clip before swapping recorders so a restart
            // failure cannot drop audio that is already in memory.
            const persist = this.upload([], { background: true, blob, durationSeconds });
            persist.catch((error) => {
                console.error('[VoiceDiaryRecorder] Rollover upload failed:', error);
            });

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

            if (this.onRollover) {
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
            const extension = mime.includes('webm') || this.mimeType.includes('webm') ? 'webm' : 'wav';
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
     * Start duration tracking interval.
     */
    startDurationTracking() {
        this.stopDurationTracking();
        
        this.durationInterval = setInterval(() => {
            const duration = this.getDuration();
            
            if (this.onDurationUpdate) {
                this.onDurationUpdate(duration);
            }

            // Interruption watchdog: treat "audio data stopped flowing while recording"
            // as an interruption, for browsers that do not fire track mute events.
            if (this.handleInterruptions && this.interruptionWatchdogMs > 0 && !this._stopRequested
                && this.state === 'recording' && this._sawData && this._lastDataTs
                && (Date.now() - this._lastDataTs) > this.interruptionWatchdogMs) {
                this._onInterruption('watchdog');
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
