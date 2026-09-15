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
