/**
 * Tests for VoiceDiaryRecorder interruption handling v3
 * (auto-pause + manual resume + single-recording merge).
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
        cookie: 'csrftoken=test',
        querySelector: () => null,
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
        interruptionWatchdogMs: 0,
        interruptionResumeRetryMs: 5,
        interruptionMaxResumeAttempts: 2,
        resumeProbeMs: 600,
    }, overrides || {}));
    recorder.connectWebSocket = () => {};
    return recorder;
}

function currentTrack(recorder) {
    return recorder.stream.getAudioTracks()[0];
}

describe('VoiceDiaryRecorder interruption handling (auto-pause + resume + single-recording merge)', () => {
    let recorder;

    beforeEach(() => {
        installBrowserMocks();
    });

    afterEach(() => {
        if (recorder) {
            recorder.stopDurationTracking();
            recorder.stopStream();
            recorder = null;
        }
    });

    it('auto-pauses (like the manual pause) when the mic is interrupted — nothing uploads yet', async () => {
        recorder = newRecorder();
        let pauses = 0;
        recorder.onInterruptionPause = () => { pauses += 1; };

        await recorder.startRecording();
        recorder.startTime = Date.now() - 5000;
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        assert.equal(pauses, 1);
        assert.equal(recorder.state, 'paused');
        assert.equal(recorder.mediaRecorder.state, 'paused');
        assert.equal(fetchCalls.length, 0, 'nothing uploads until the user resumes or stops');
        assert.equal(gumCalls.length, 1, 'no extra mic request at pause time');
        assert.equal(recorder._heldParts.length, 0);
    });

    it('manual resume after an interruption holds part 1 and continues on a fresh mic', async () => {
        recorder = newRecorder();
        await recorder.startRecording();
        recorder.startTime = Date.now() - 8000;
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        await recorder.resumeRecording();
        await waitFor(200);

        assert.equal(recorder.state, 'recording');
        assert.equal(recorder._resumeNeedsFreshMic, false);
        assert.ok(gumCalls.length >= 2, 'resume must request a fresh mic');
        assert.equal(recorderInstances.length, 2, 'a new recorder runs');
        assert.equal(recorder.mediaRecorder.state, 'recording');
        assert.equal(recorder._heldParts.length, 1, 'part 1 is held for the final merge');
        assert.equal(fetchCalls.length, 0, 'nothing is uploaded while the take continues');
    });

    it('stop merges the held parts into ONE single upload', async () => {
        recorder = newRecorder();
        await recorder.startRecording();
        recorder.startTime = Date.now() - 8000;
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        await recorder.resumeRecording();
        await waitFor(200);

        let mergedParts = null;
        recorder._mergePartsToWav = async (parts) => {
            mergedParts = parts;
            return new Blob([new Uint8Array(2048)], { type: 'audio/wav' });
        };

        await recorder.stopRecording();
        await waitFor(40);

        assert.ok(mergedParts, 'merge must be used for a multi-part take');
        assert.equal(mergedParts.length, 2, 'both parts go into the merge');
        assert.equal(fetchCalls.length, 1, 'exactly ONE upload — the merged recording');
        const body = fetchCalls[0].opts.body;
        assert.equal(body.get('recording_group_id'), recorder.recordingGroupId);
        assert.ok(Number(body.get('recording_duration_seconds')) >= 5, 'cumulative duration sent');
        assert.equal(recorder.state, 'processing');
    });

    it('fallback: if the merge fails, the parts are uploaded separately (nothing lost)', async () => {
        recorder = newRecorder();
        await recorder.startRecording();
        recorder.startTime = Date.now() - 8000;
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        await recorder.resumeRecording();
        await waitFor(200);

        // No _mergePartsToWav stub: Node has no AudioContext → the merge throws → fallback path.
        await recorder.stopRecording();
        await waitFor(60);

        assert.equal(fetchCalls.length, 2, 'both parts uploaded separately');
        assert.ok(fetchCalls[0].opts.body.get('audio'), 'part 1 uploaded');
        assert.ok(fetchCalls[1].opts.body.get('audio'), 'part 2 uploaded');
        assert.equal(fetchCalls[1].opts.body.get('recording_group_id'), recorder.recordingGroupId);
        assert.equal(recorder.state, 'processing');
    });

    it('stays paused while the call still holds the mic; stop then saves the captured part', async () => {
        recorder = newRecorder();
        let blocked = 0;
        recorder.onInterruptionContinueBlocked = () => { blocked += 1; };
        gumDefault = { muted: true };

        await recorder.startRecording();
        recorder.startTime = Date.now() - 5000;
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        await recorder.resumeRecording();
        await waitFor(120);

        assert.equal(blocked, 1);
        assert.equal(recorder.state, 'paused');
        assert.equal(recorder.mediaRecorder.state, 'paused', 'recorder untouched while mic is busy');
        assert.equal(fetchCalls.length, 0);

        await recorder.stopRecording();
        await waitFor(40);
        assert.equal(fetchCalls.length, 1);
        assert.ok(fetchCalls[0].opts.body.get('audio'));
    });

    it('retries within one press (muted first, then healthy)', async () => {
        recorder = newRecorder();

        await recorder.startRecording();
        // Queue AFTER the start mic call, so the first resume attempt gets the muted mic.
        gumQueue = [{ muted: true }, { muted: false }];
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);
        await recorder.resumeRecording();
        await waitFor(200);

        assert.equal(recorder.state, 'recording');
        assert.ok(gumCalls.length >= 3);
    });

    it('plain manual pause/resume (no interruption) is unchanged — no fresh mic request', async () => {
        recorder = newRecorder();
        await recorder.startRecording();
        const gumBefore = gumCalls.length;

        recorder.pauseRecording();
        assert.equal(recorder.state, 'paused');
        recorder.resumeRecording();
        assert.equal(recorder.state, 'recording');
        assert.equal(gumCalls.length, gumBefore, 'plain resume must not touch getUserMedia');

        await recorder.stopRecording();
        assert.equal(fetchCalls.length, 1);
    });

    it('mic lost while manually paused flags the next resume for a fresh mic', async () => {
        recorder = newRecorder();
        await recorder.startRecording();
        recorder.pauseRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(20);

        await recorder.resumeRecording();
        await waitFor(200);
        assert.equal(recorder.state, 'recording');
        assert.ok(gumCalls.length >= 2, 'fresh mic used after the mic was lost');
        assert.equal(recorder._heldParts.length, 1, 'paused segment held for the final merge');
    });

    it('probe failure drops the attempt; the held part is still uploaded on stop', async () => {
        recorder = newRecorder();
        let blocked = 0;
        recorder.onInterruptionContinueBlocked = () => { blocked += 1; };

        await recorder.startRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        MockMediaRecorder.autoEmit = false;
        await recorder.resumeRecording();
        await waitFor(900);

        assert.equal(blocked, 1);
        assert.equal(recorder.state, 'paused');
        assert.equal(fetchCalls.length, 0, 'nothing uploaded yet');
        assert.equal(recorder._heldParts.length, 1, 'part 1 held; failed part 2 dropped');

        await recorder.stopRecording();
        await waitFor(40);
        assert.equal(recorder.state, 'processing');
        assert.equal(fetchCalls.length, 1);
        assert.ok(fetchCalls[0].opts.body.get('audio'));
    });

    it('rollover during a multi-part take holds the segment instead of uploading', async () => {
        recorder = newRecorder();
        await recorder.startRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);

        await recorder.resumeRecording();
        await waitFor(200);
        assert.equal(recorder._heldParts.length, 1);
        assert.equal(fetchCalls.length, 0);

        recorder.startTime = Date.now() - (recorder.maxDuration * 1000);
        await recorder.rolloverRecording();
        await waitFor(40);

        assert.equal(recorder._heldParts.length, 2, 'rollover segment held, not uploaded');
        assert.equal(fetchCalls.length, 0, 'no mid-take uploads');
        assert.equal(recorder.state, 'recording');
    });

    it('watchdog auto-pauses when audio data stops flowing', async () => {
        recorder = newRecorder({ interruptionWatchdogMs: 150 });
        await recorder.startRecording();
        await waitFor(600);
        assert.equal(recorder.state, 'paused');
    });

    it('transcribe-only recorders ignore interruptions', async () => {
        recorder = newRecorder({ transcribeOnly: true });
        await recorder.startRecording();
        currentTrack(recorder).dispatch('mute');
        await waitFor(30);
        assert.equal(recorder.state, 'recording');
        await recorder.stopRecording();
    });
});
