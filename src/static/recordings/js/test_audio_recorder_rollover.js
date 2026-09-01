/**
 * Tests for VoiceDiaryRecorder auto-save + continue at maxDuration.
 *
 * Run with: node --test src/static/recordings/js/test_audio_recorder_rollover.js
 */

'use strict';

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');

let recorderInstances = [];
let trackStopCount = 0;
let fetchCalls = [];

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
        if (this.ondataavailable) {
            this.ondataavailable({ data: new Blob(['chunk'], { type: 'audio/webm' }) });
        }
    }
    stop() {
        this.state = 'inactive';
        if (this.ondataavailable) {
            this.ondataavailable({ data: new Blob(['end'], { type: 'audio/webm' }) });
        }
        if (this.onstop) {
            this.onstop();
        }
    }
    pause() {
        this.state = 'paused';
    }
    resume() {
        this.state = 'recording';
    }
    requestData() {}
    static isTypeSupported(type) {
        return type.includes('webm');
    }
}

class MockWebSocket {
    constructor() {
        this.readyState = 1;
        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;
        MockWebSocket.instances.push(this);
    }
    close() {}
    send() {}
}
MockWebSocket.instances = [];

function installBrowserMocks() {
    recorderInstances = [];
    trackStopCount = 0;
    fetchCalls = [];
    MockWebSocket.instances = [];

    global.window = {
        location: { protocol: 'http:', host: 'localhost' },
        registration: {},
    };
    const nav = {
        onLine: true,
        mediaDevices: {
            getUserMedia: async () => ({
                getTracks: () => [{
                    stop: () => { trackStopCount += 1; },
                }],
            }),
        },
    };
    Object.defineProperty(global, 'navigator', {
        value: nav,
        configurable: true,
        writable: true,
    });
    global.document = {
        cookie: 'csrftoken=test-csrf',
        querySelector: () => null,
    };
    global.MediaRecorder = MockMediaRecorder;
    global.WebSocket = MockWebSocket;
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

function mockIndexedDB() {
    const db = {
        objectStoreNames: { contains: () => true },
        transaction: () => ({
            objectStore: () => ({
                add: () => Promise.resolve(1),
            }),
        }),
    };
    global.indexedDB = {
        open() {
            const request = {
                result: db,
                error: null,
                onsuccess: null,
                onerror: null,
                onupgradeneeded: null,
            };
            queueMicrotask(() => {
                if (typeof request.onsuccess === 'function') {
                    request.onsuccess({ target: request });
                }
            });
            return request;
        },
    };
}

function waitFor(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function startAndReachMaxDuration(recorder) {
    await recorder.startRecording();
    recorder.stopDurationTracking();
    recorder.startTime = Date.now() - (Math.max(recorder.maxDuration, 1) * 1000);
}

describe('VoiceDiaryRecorder auto-continue at max duration', () => {
    let recorder;

    beforeEach(() => {
        installBrowserMocks();
        recorder = new VoiceDiaryRecorder({
            uploadUrl: '/voice/upload/',
            maxDuration: 240,
            maxFileSize: 100 * 1024 * 1024,
            autoContinueOnMaxDuration: true,
        });
        recorder.connectWebSocket = () => {};
    });

    afterEach(() => {
        if (recorder) {
            recorder.stopDurationTracking();
            recorder.stopStream();
        }
    });

    it('defaults maxDuration to 240 seconds and auto-continues unless transcribeOnly', () => {
        const r = new VoiceDiaryRecorder();
        assert.equal(r.maxDuration, 240);
        assert.equal(r.autoContinueOnMaxDuration, true);

        const edit = new VoiceDiaryRecorder({ transcribeOnly: true });
        assert.equal(edit.autoContinueOnMaxDuration, false);
    });

    it('uploads the finished clip in the background and keeps recording on the same stream', async () => {
        await startAndReachMaxDuration(recorder);
        assert.equal(recorder.state, 'recording');
        assert.equal(recorderInstances.length, 1);
        const firstRecorder = recorderInstances[0];
        const stream = recorder.stream;

        await recorder.rolloverRecording();

        assert.equal(recorder.state, 'recording');
        assert.equal(recorderInstances.length, 2);
        assert.equal(firstRecorder.state, 'inactive');
        assert.equal(recorder.mediaRecorder, recorderInstances[1]);
        assert.equal(recorder.stream, stream);
        assert.equal(trackStopCount, 0, 'mic stream must stay open across rollover');

        await waitFor(20);
        assert.equal(fetchCalls.length, 1);
        assert.equal(fetchCalls[0].url, '/voice/upload/');
        const body = fetchCalls[0].opts.body;
        assert.ok(body instanceof FormData);
        assert.equal(body.get('transcribe_only'), null);
        assert.equal(MockWebSocket.instances.length, 0, 'background upload must not open pipeline WebSocket');
        assert.equal(recorder.currentItemId, null);
    });

    it('does not change UI state to uploading/processing during rollover', async () => {
        const states = [];
        recorder.onStateChange = (s) => states.push(s);
        await startAndReachMaxDuration(recorder);
        await recorder.rolloverRecording();
        await waitFor(20);
        assert.ok(!states.includes('uploading'));
        assert.ok(!states.includes('processing'));
        assert.equal(recorder.state, 'recording');
    });

    it('invokes onRollover after a successful segment swap', async () => {
        let rolloverCount = 0;
        recorder.onRollover = () => { rolloverCount += 1; };
        await startAndReachMaxDuration(recorder);
        await recorder.rolloverRecording();
        assert.equal(rolloverCount, 1);
    });

    it('does not include session files on auto-rollover uploads', async () => {
        await startAndReachMaxDuration(recorder);
        await recorder.rolloverRecording();
        await waitFor(20);
        const body = fetchCalls[0].opts.body;
        assert.equal(body.get('files'), null);
        assert.ok(body.get('audio'));
    });

    it('keeps recording when a background upload fails and reports onRolloverError', async () => {
        global.fetch = async () => ({
            ok: false,
            json: async () => ({ error: 'server down' }),
        });
        let rolloverError = null;
        recorder.onRolloverError = (err) => { rolloverError = err; };
        recorder.onError = () => { throw new Error('onError should not run for background failures'); };
        recorder.saveOffline = async () => {};

        await startAndReachMaxDuration(recorder);
        await recorder.rolloverRecording();
        await waitFor(20);

        assert.equal(recorder.state, 'recording');
        assert.ok(rolloverError);
        assert.match(rolloverError.message, /server down/);
    });

    it('manual stop uploads the last clip and releases the microphone', async () => {
        await recorder.startRecording();
        await recorder.stopRecording();
        assert.equal(fetchCalls.length, 1);
        assert.equal(trackStopCount, 1);
        assert.equal(recorder.state, 'processing');
        assert.equal(recorder.currentItemId, 'item-1');
    });

    it('transcribe-only recorders do not auto-continue', async () => {
        const edit = new VoiceDiaryRecorder({
            uploadUrl: '/voice/upload/',
            transcribeOnly: true,
            maxDuration: 1,
        });
        let stopCalled = false;
        edit.stopRecording = async () => { stopCalled = true; };
        edit.startTime = Date.now() - 2000;
        edit.state = 'recording';
        edit.startDurationTracking();
        await waitFor(150);
        edit.stopDurationTracking();
        assert.equal(stopCalled, true);
        assert.equal(edit.autoContinueOnMaxDuration, false);
    });

    it('duration tracker triggers rollover when maxDuration is reached', async () => {
        recorder.maxDuration = 0.05;
        await recorder.startRecording();
        await waitFor(250);
        assert.ok(recorderInstances.length >= 2, `expected a new MediaRecorder, got ${recorderInstances.length}`);
        assert.equal(recorder.state, 'recording');
        recorder.stopDurationTracking();
        await recorder.stopRecording();
    });

    it('does not rollover when maxDuration is 0 (unlimited)', async () => {
        recorder.maxDuration = 0;
        await recorder.startRecording();
        recorder.startTime = Date.now() - 10 * 60 * 1000;
        await recorder.rolloverRecording();
        assert.equal(recorderInstances.length, 1);
        assert.equal(fetchCalls.length, 0);
        recorder.stopDurationTracking();
        recorder.stopStream();
        recorder.setState('idle');
    });

    it('registers background sync when a rollover segment is saved offline', async () => {
        let registeredTag = null;
        window.registration = {
            sync: {
                register: async (tag) => { registeredTag = tag; },
            },
        };
        mockIndexedDB();
        navigator.onLine = false;

        await startAndReachMaxDuration(recorder);
        await recorder.rolloverRecording();
        await waitFor(30);

        assert.equal(registeredTag, 'sync-recordings');
        assert.equal(recorder.state, 'recording');
        assert.equal(fetchCalls.length, 0);
    });

    it('persists the finished clip if starting the next recorder fails', async () => {
        await startAndReachMaxDuration(recorder);
        recorder._beginRecorderOnStream = () => {
            throw new Error('MediaRecorder restart failed');
        };
        let seenError = null;
        recorder.onError = (err) => { seenError = err; };

        await assert.rejects(
            () => recorder.rolloverRecording(),
            /MediaRecorder restart failed/
        );
        assert.equal(fetchCalls.length, 1);
        assert.match(seenError.message, /MediaRecorder restart failed/);
        assert.equal(recorder.state, 'error');
    });
});
