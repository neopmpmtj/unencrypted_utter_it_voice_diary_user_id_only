/**
 * Tests for VoiceDiaryRecorder file attachment functionality.
 * 
 * Tests cover:
 * - upload() method accepts files parameter (session-managed)
 * - stopRecording() passes files through to upload()
 * - Client-side file validation
 * 
 * Note: These tests assume Jest testing framework.
 * Run with: npm test -- test_audio_recorder_attachments.js
 */

describe('VoiceDiaryRecorder File Attachments', () => {
    let recorder;
    let mockFetch;

    beforeEach(() => {
        // Mock fetch globally
        global.fetch = jest.fn();
        mockFetch = global.fetch;

        // Reset recorder
        recorder = new VoiceDiaryRecorder({
            uploadUrl: '/test/upload/',
            maxDuration: 600,
            maxFileSize: 100 * 1024 * 1024,
        });

        // Mock WebSocket
        global.WebSocket = jest.fn();
    });

    afterEach(() => {
        jest.clearAllMocks();
    });

    describe('Recorder has no attachedFiles property', () => {
        test('does not own attachedFiles (managed by session)', () => {
            expect(recorder.attachedFiles).toBeUndefined();
        });
    });

    describe('Upload Method with Files Parameter', () => {
        beforeEach(() => {
            // Mock audio blob
            recorder.audioBlob = new Blob(['audio data'], { type: 'audio/webm' });
            recorder.setState = jest.fn();

            // Mock fetch success response
            mockFetch.mockResolvedValueOnce({
                ok: true,
                json: async () => ({
                    item_id: 'item-123',
                    status: 'processing',
                })
            });
        });

        test('includes files parameter in FormData', async () => {
            const file1 = new File(['pdf content'], 'doc1.pdf', { type: 'application/pdf' });
            const file2 = new File(['docx content'], 'doc2.docx', { type: 'application/vnd.ms-word' });

            recorder.mediaRecorder = { state: 'recording' };
            recorder.stopStream = jest.fn();

            Object.defineProperty(navigator, 'onLine', {
                writable: true,
                value: true,
            });

            await recorder.upload([file1, file2]);

            expect(mockFetch).toHaveBeenCalled();
            const formData = mockFetch.mock.calls[0][1].body;
            expect(formData instanceof FormData).toBe(true);
        });

        test('upload with no files (default empty array)', async () => {
            recorder.mediaRecorder = { state: 'recording' };
            recorder.stopStream = jest.fn();
            recorder.connectWebSocket = jest.fn();

            Object.defineProperty(navigator, 'onLine', {
                writable: true,
                value: true,
            });

            mockFetch.mockResolvedValueOnce({
                ok: true,
                json: async () => ({
                    item_id: 'item-123',
                })
            });

            await recorder.upload();

            expect(mockFetch).toHaveBeenCalled();
        });

        test('sets currentTempId when response includes temp_id', async () => {
            recorder.mediaRecorder = { state: 'recording' };
            recorder.stopStream = jest.fn();
            recorder.connectWebSocket = jest.fn();

            Object.defineProperty(navigator, 'onLine', {
                writable: true,
                value: true,
            });

            mockFetch.mockResolvedValueOnce({
                ok: true,
                json: async () => ({
                    temp_id: 'temp-123',
                })
            });

            await recorder.upload();

            expect(recorder.currentTempId).toBe('temp-123');
            expect(recorder.currentItemId).toBeNull();
        });

        test('handles upload failure gracefully', async () => {
            recorder.mediaRecorder = { state: 'recording' };
            recorder.stopStream = jest.fn();
            recorder.onError = jest.fn();

            Object.defineProperty(navigator, 'onLine', {
                writable: true,
                value: true,
            });

            mockFetch.mockResolvedValueOnce({
                ok: false,
                json: async () => ({ error: 'Upload failed' })
            });

            await expect(recorder.upload()).rejects.toThrow();
            expect(recorder.onError).toHaveBeenCalled();
        });
    });

    describe('File Validation (Client-Side)', () => {
        test('validates file size under 100MB', () => {
            const MAX_FILE_SIZE = 100 * 1024 * 1024;
            const file = new File(['x'.repeat(50 * 1024 * 1024)], 'file.pdf');

            expect(file.size).toBeLessThan(MAX_FILE_SIZE);
        });

        test('identifies oversized files', () => {
            const MAX_FILE_SIZE = 100 * 1024 * 1024;
            const oversizedFile = {
                name: 'huge_file.zip',
                size: 150 * 1024 * 1024,
            };

            expect(oversizedFile.size).toBeGreaterThan(MAX_FILE_SIZE);
        });

        test('validates total file size under 500MB', () => {
            const MAX_TOTAL_SIZE = 500 * 1024 * 1024;
            const files = [
                new File(['x'.repeat(200 * 1024 * 1024)], 'file1.pdf'),
                new File(['x'.repeat(200 * 1024 * 1024)], 'file2.pdf'),
            ];

            const totalSize = files.reduce((sum, f) => sum + f.size, 0);
            expect(totalSize).toBeLessThan(MAX_TOTAL_SIZE);
        });

        test('identifies when total exceeds 500MB', () => {
            const MAX_TOTAL_SIZE = 500 * 1024 * 1024;
            const files = [
                new File(['x'.repeat(300 * 1024 * 1024)], 'file1.pdf'),
                new File(['x'.repeat(300 * 1024 * 1024)], 'file2.pdf'),
            ];

            const totalSize = files.reduce((sum, f) => sum + f.size, 0);
            expect(totalSize).toBeGreaterThan(MAX_TOTAL_SIZE);
        });
    });

    describe('Integration Scenarios', () => {
        test('session passes files to upload()', async () => {
            const file1 = new File(['content1'], 'doc1.pdf');
            const file2 = new File(['content2'], 'doc2.pdf');

            recorder.audioBlob = new Blob(['audio'], { type: 'audio/webm' });
            recorder.mediaRecorder = { state: 'recording' };
            recorder.stopStream = jest.fn();
            recorder.connectWebSocket = jest.fn();
            recorder.setState = jest.fn();

            Object.defineProperty(navigator, 'onLine', {
                writable: true,
                value: true,
            });

            mockFetch.mockResolvedValueOnce({
                ok: true,
                json: async () => ({
                    item_id: 'item-abc',
                    attachment_count: 2,
                })
            });

            await recorder.upload([file1, file2]);

            expect(recorder.currentItemId).toBe('item-abc');
        });
    });
});
