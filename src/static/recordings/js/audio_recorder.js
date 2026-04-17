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
     * @param {number} options.maxDuration - Maximum recording duration in seconds (default: 600)
     * @param {number} options.maxFileSize - Maximum file size in bytes (default: 100MB, matches RECORDER_MAX_FILE_SIZE_MB)
     */
    constructor(options = {}) {
        this.uploadUrl = options.uploadUrl || '/voice/upload/';
        this.wsBaseUrl = options.wsBaseUrl || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
        this.maxDuration = options.maxDuration ?? 600;
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

        // Transcribe-only mode: transcribe only, no IngestItem created (used by edit recorder)
        this.transcribeOnly = options.transcribeOnly || false;

        // Quota state (populated by applyQuota or fetchAndApplyQuota)
        this.quotaData = null;
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
     * Start recording audio.
     */
    async startRecording() {
        if (this.state !== 'idle' && this.state !== 'done' && this.state !== 'error') {
            throw new Error(`Cannot start recording in state: ${this.state}`);
        }
        
        try {
            // Request microphone access
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: 44100,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            });
            
            // Create MediaRecorder (use browser default if no type verified)
            const recorderOpts = this.mimeType ? { mimeType: this.mimeType } : {};
            this.mediaRecorder = new MediaRecorder(this.stream, recorderOpts);
            if (!this.mimeType) {
                this.mimeType = this.mediaRecorder.mimeType || 'audio/webm';
            }
            
            // Reset state
            this.audioChunks = [];
            this.audioBlob = null;
            this.pauseDuration = 0;
            this.pauseStartTime = null;
            this.startTime = Date.now();
            this.currentItemId = null;
            
            // Handle data
            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) {
                    this.audioChunks.push(e.data);
                }
            };
            
            // Handle stop
            this.mediaRecorder.onstop = () => {
                this.audioBlob = new Blob(this.audioChunks, { type: this.mimeType });
                this.audioChunks = [];
                this.stopStream();
            };
            
            // Start recording
            this.mediaRecorder.start(1000);
            this.setState('recording');
            
            // Start duration tracking
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
        
        this.mediaRecorder.requestData();
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
        if (this.state !== 'recording' && this.state !== 'paused') {
            throw new Error(`Cannot stop in state: ${this.state}`);
        }
        
        // Finalize pause duration
        if (this.pauseStartTime) {
            this.pauseDuration += Date.now() - this.pauseStartTime;
            this.pauseStartTime = null;
        }
        
        // Stop duration tracking
        this.stopDurationTracking();
        
        // Stop recording
        return new Promise((resolve, reject) => {
            this.mediaRecorder.onstop = async () => {
                this.audioBlob = new Blob(this.audioChunks, { type: this.mimeType });
                this.audioChunks = [];
                this.stopStream();
                
                try {
                    await this.upload(files);
                    resolve();
                } catch (error) {
                    reject(error);
                }
            };
            
            this.mediaRecorder.stop();
        });
    }
    
    /**
     * Upload audio to server.
     * @param {File[]} files - Optional array of files to include with the upload (managed by caller/session)
     */
    async upload(files = []) {
        if (!this.audioBlob) {
            throw new Error('No audio to upload');
        }
        
        if (this.audioBlob.size > this.maxFileSize) {
            throw new Error(`File too large. Maximum size is ${this.maxFileSize / 1024 / 1024}MB`);
        }
        
        this.setState('uploading');
        
        // Check if online
        if (!navigator.onLine) {
            await this.saveOffline();
            return;
        }
        
        try {
            const formData = new FormData();
            const extension = this.mimeType.includes('webm') ? 'webm' : 'wav';
            formData.append('audio', this.audioBlob, `recording.${extension}`);
            formData.append('template_type', this.templateType);
            if (this.transcribeOnly) {
                formData.append('transcribe_only', '1');
            }

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
            
        } catch (error) {
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
     */
    async saveOffline() {
        const db = await this.openDB();
        const tx = db.transaction('offline-recordings', 'readwrite');
        const store = tx.objectStore('offline-recordings');
        
        await store.add({
            blob: this.audioBlob,
            timestamp: Date.now(),
            mimeType: this.mimeType,
            csrfToken: this.getCsrfToken(),
            transcribeOnly: this.transcribeOnly,
            templateType: this.templateType,
        });
        
        this.setState('done');
        
        if (this.onStatusUpdate) {
            this.onStatusUpdate({
                type: 'offline',
                message: 'Recording saved offline. Will upload when online.',
            });
        }
        
        // Register for background sync
        if ('serviceWorker' in navigator && 'sync' in window.registration) {
            try {
                await window.registration.sync.register('sync-recordings');
            } catch (e) {
                console.warn('[VoiceDiaryRecorder] Background sync registration failed:', e);
            }
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
            
            // Auto-stop if max duration reached
            if (this.maxDuration > 0 && duration >= this.maxDuration) {
                this.stopRecording();
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
     * Token-based quotas: no maxDuration cap. Recorder uses only config max_duration.
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
