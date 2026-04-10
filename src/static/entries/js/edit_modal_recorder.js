/**
 * Edit Modal Recorder
 *
 * Dedicated copy of the audio recorder for the entries list Edit Entry modal.
 * Transcribe-only mode: records voice, uploads, receives transcription, inserts at cursor.
 * Isolated from the recordings page to avoid breaking that edit mode.
 *
 * @class EditModalRecorder
 */
class EditModalRecorder {
    /**
     * Create a new EditModalRecorder instance.
     *
     * @param {Object} options - Configuration options
     * @param {string} options.uploadUrl - Server endpoint for audio upload (default: '/voice/upload/')
     * @param {number} options.maxDuration - Maximum recording duration in seconds (default: 300)
     * @param {number} options.maxFileSize - Maximum file size in bytes (default: 25MB)
     */
    constructor(options = {}) {
        this.uploadUrl = options.uploadUrl || '/voice/upload/';
        this.wsBaseUrl = options.wsBaseUrl || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
        this.maxDuration = options.maxDuration || 300;
        this.maxFileSize = options.maxFileSize || 25 * 1024 * 1024;

        // State management
        this.state = 'idle';  // idle, recording, paused, uploading, processing, done, error
        this.audioChunks = [];
        this.audioBlob = null;
        this.mediaRecorder = null;
        this.stream = null;
        this.currentItemId = null;
        this.currentTempId = null;  // For transcribe-only mode
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
        this.onCalendarConflict = null;
        this.onTranscriptionReady = null;
        this.onTranscriptionDiscarded = null;

        // Transcribe-only mode: transcribe only, no IngestItem created
        this.transcribeOnly = options.transcribeOnly !== false;

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
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: 44100,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            });

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
            this.currentItemId = null;

            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) {
                    this.audioChunks.push(e.data);
                }
            };

            this.mediaRecorder.onstop = () => {
                this.audioBlob = new Blob(this.audioChunks, { type: this.mimeType });
                this.audioChunks = [];
                this.stopStream();
            };

            this.mediaRecorder.start(1000);
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
     * @param {File[]} files - Optional array of files to include with the upload
     */
    async stopRecording(files = []) {
        if (this.state !== 'recording' && this.state !== 'paused') {
            throw new Error(`Cannot stop in state: ${this.state}`);
        }

        if (this.pauseStartTime) {
            this.pauseDuration += Date.now() - this.pauseStartTime;
            this.pauseStartTime = null;
        }

        this.stopDurationTracking();

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
     * @param {File[]} files - Optional array of files to include with the upload
     */
    async upload(files = []) {
        if (!this.audioBlob) {
            throw new Error('No audio to upload');
        }

        if (this.audioBlob.size > this.maxFileSize) {
            throw new Error(`File too large. Maximum size is ${this.maxFileSize / 1024 / 1024}MB`);
        }

        this.setState('uploading');

        if (!navigator.onLine) {
            await this.saveOffline();
            return;
        }

        try {
            const formData = new FormData();
            const extension = this.mimeType.includes('webm') ? 'webm' : 'wav';
            formData.append('audio', this.audioBlob, `recording.${extension}`);
            formData.append('template_type', this.templateType);
            formData.append('transcribe_only', '1');

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
            const tempId = data.temp_id;
            const itemId = data.item_id;

            if (tempId) {
                this.currentTempId = tempId;
                this.currentItemId = null;
                this.connectWebSocket(tempId);
            } else if (itemId) {
                this.currentItemId = itemId;
                this.currentTempId = null;
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
     * Falls back to polling if WebSocket is unavailable.
     */
    connectWebSocket(itemId) {
        if (this.ws) {
            this.ws.close();
        }
        this.clearPolling();

        const wsUrl = `${this.wsBaseUrl}/ws/pipeline/${itemId}/`;
        this.ws = new WebSocket(wsUrl);
        let fallbackStarted = false;

        const startPollingFallback = () => {
            if (fallbackStarted) return;
            fallbackStarted = true;
            this.startPollingStatus(itemId);
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (this.onStatusUpdate) {
                this.onStatusUpdate(data);
            }

            if (data.type === 'transcription.discarded') {
                this.setState('done');
                this.ws.close();
                if (this.onTranscriptionDiscarded) {
                    this.onTranscriptionDiscarded(data.reason || 'No speech detected');
                } else if (this.onError) {
                    this.onError(new Error(data.reason || 'No speech detected'));
                }
                return;
            }

            if (data.type === 'transcription.ready') {
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

            if (data.type === 'complete') {
                this.setState('done');
                if (this.onComplete) {
                    this.onComplete(data);
                }
                this.ws.close();
            }

            if (data.status === 'calendar_conflict' || data.conflict) {
                this.setState('done');
                if (this.onCalendarConflict) {
                    this.onCalendarConflict(data);
                } else if (data.confirmation_url) {
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
            if (this.state === 'processing' && !fallbackStarted) {
                startPollingFallback();
            }
        };

        this.ws.onerror = () => {
            if (this.state === 'processing' && !fallbackStarted) {
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
     * Poll GET /voice/status/pending/<tempId>/ until processed or error (transcribe-only always).
     */
    startPollingStatus(itemId) {
        const statusUrl = `/voice/status/pending/${itemId}/`;
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
            transcribeOnly: true,
            templateType: this.templateType,
        });

        this.setState('done');

        if (this.onStatusUpdate) {
            this.onStatusUpdate({
                type: 'offline',
                message: 'Recording saved offline. Will upload when online.',
            });
        }

        if ('serviceWorker' in navigator) {
            try {
                const reg = await navigator.serviceWorker.ready;
                if (reg && reg.sync) {
                    await reg.sync.register('sync-recordings');
                }
            } catch (e) {}
        }
    }

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

    getDuration() {
        if (!this.startTime) return 0;
        let elapsed = Date.now() - this.startTime - this.pauseDuration;
        if (this.pauseStartTime) {
            elapsed -= Date.now() - this.pauseStartTime;
        }
        return Math.max(0, elapsed / 1000);
    }

    startDurationTracking() {
        this.stopDurationTracking();
        this.durationInterval = setInterval(() => {
            const duration = this.getDuration();
            if (this.onDurationUpdate) {
                this.onDurationUpdate(duration);
            }
            if (this.maxDuration > 0 && duration >= this.maxDuration) {
                this.stopRecording();
            }
        }, 100);
    }

    stopDurationTracking() {
        if (this.durationInterval) {
            clearInterval(this.durationInterval);
            this.durationInterval = null;
        }
    }

    stopStream() {
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
    }

    setState(newState) {
        this.state = newState;
        if (this.onStateChange) {
            this.onStateChange(newState);
        }
    }

    setTemplateType(templateType) {
        this.templateType = templateType || 'plain';
    }

    getCsrfToken() {
        const name = 'csrftoken';
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                return cookie.substring(name.length + 1);
            }
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.content;
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input) return input.value;
        return '';
    }

    static formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
}

window.EditModalRecorder = EditModalRecorder;
