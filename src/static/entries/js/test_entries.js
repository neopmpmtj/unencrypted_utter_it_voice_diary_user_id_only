/**
 * Tests for entries list attachment rendering.
 *
 * Verifies that attachments with empty storage_url render as plain text
 * "(uploading...)" without a clickable link, and attachments with
 * storage_url render as clickable links with data-attachment-index for
 * the preview modal.
 *
 * Run with: npm test -- test_entries.js
 * or: jest src/static/entries/js/test_entries.js
 */

describe('Entries attachment rendering', () => {
    /**
     * Replicates attachment HTML logic from entries.js createEntryElement.
     * Matches the contract: empty storage_url -> span with (uploading...);
     * http(s) URL -> anchor link with data-attachment-index; other values -> (link unavailable).
     */
    function renderAttachmentItem(a, idx) {
        const index = typeof idx === 'number' ? idx : 0;
        const label = escapeHtml(a.filename || 'File');
        const url = (a.storage_url || '').trim();
        const isHttpUrl = url.indexOf('http://') === 0 || url.indexOf('https://') === 0;
        if (isHttpUrl) {
            return '<li><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="text-xs text-accent hover:underline inline-block py-1" data-attachment-link data-attachment-index="' + index + '">' + label + '</a></li>';
        }
        if (url) {
            return '<li><span class="text-xs text-muted-foreground">' + label + ' (link unavailable)</span></li>';
        }
        return '<li><span class="text-xs text-muted-foreground">' + label + ' (uploading...)</span></li>';
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    test('attachment with storage_url renders as clickable link', () => {
        const a = { filename: 'doc.pdf', storage_url: 'https://drive.google.com/file/d/abc/view' };
        const html = renderAttachmentItem(a);

        expect(html).toContain('<a ');
        expect(html).toContain('href="https://drive.google.com/file/d/abc/view"');
        expect(html).toContain('doc.pdf');
        expect(html).not.toContain('(uploading...)');
    });

    test('attachment with empty storage_url renders as plain text with (uploading...)', () => {
        const a = { filename: 'doc.pdf', storage_url: '' };
        const html = renderAttachmentItem(a);

        expect(html).toContain('<span');
        expect(html).toContain('doc.pdf');
        expect(html).toContain('(uploading...)');
        expect(html).not.toContain('<a ');
    });

    test('attachment with null storage_url renders as plain text with (uploading...)', () => {
        const a = { filename: 'Hello I would like to.txt', storage_url: null };
        const html = renderAttachmentItem(a);

        expect(html).toContain('<span');
        expect(html).toContain('Hello I would like to.txt');
        expect(html).toContain('(uploading...)');
        expect(html).not.toContain('<a ');
    });

    test('attachment with undefined storage_url renders as plain text with (uploading...)', () => {
        const a = { filename: 'file.txt' };
        const html = renderAttachmentItem(a);

        expect(html).toContain('<span');
        expect(html).toContain('(uploading...)');
        expect(html).not.toContain('<a ');
    });

    test('attachment with non-http storage_url renders as (link unavailable)', () => {
        const a = { filename: 'doc.txt', storage_url: '/tmp/local/path/file.txt' };
        const html = renderAttachmentItem(a);

        expect(html).toContain('<span');
        expect(html).toContain('doc.txt');
        expect(html).toContain('(link unavailable)');
        expect(html).not.toContain('<a ');
    });

    describe('attachment link click behavior', () => {
        test('valid Drive URL link has attributes for reliable navigation', () => {
            const driveUrl = 'https://drive.google.com/file/d/1abc-xyz123/view?usp=drivesdk';
            const a = { filename: 'Hello I would like to.txt', storage_url: driveUrl };
            const html = renderAttachmentItem(a);

            expect(html).toContain('href="' + driveUrl + '"');
            expect(html).toContain('target="_blank"');
            expect(html).toContain('rel="noopener noreferrer"');
            expect(html).toContain('data-attachment-link');
        });

        test('attachment link includes data-attachment-index for modal', () => {
            const a = { filename: 'IMG_0542.png', storage_url: 'https://drive.google.com/file/d/abc123/view' };
            const html = renderAttachmentItem(a, 0);

            expect(html).toContain('data-attachment-index="0"');
            expect(html).toContain('data-attachment-link');
        });

        test('attachment link index varies by position', () => {
            const a = { filename: 'second.png', storage_url: 'https://example.com/second.png' };
            const html = renderAttachmentItem(a, 1);

            expect(html).toContain('data-attachment-index="1"');
        });

        test('Drive URL with query params is preserved in href', () => {
            const urlWithParams = 'https://drive.google.com/file/d/fileId/view?usp=drivesdk';
            const a = { filename: 'doc.pdf', storage_url: urlWithParams };
            const html = renderAttachmentItem(a);

            expect(html).toContain('https://drive.google.com/file/d/fileId/view');
            expect(html).toContain('usp=drivesdk');
            expect(html).toContain('<a ');
        });

        test('http URL also produces clickable link', () => {
            const a = { filename: 'file.txt', storage_url: 'http://example.com/file.pdf' };
            const html = renderAttachmentItem(a);

            expect(html).toContain('href="http://example.com/file.pdf"');
            expect(html).toContain('rel="noopener noreferrer"');
        });
    });

    describe('modal preview attachment count', () => {
        /**
         * Replicates viewable filter from entries.js openAttachmentModal.
         * Modal displays one slide per viewable attachment.
         */
        function getViewableCount(attachments) {
            return (attachments || []).filter(function(a) {
                return (a.storage_url || '').trim().indexOf('http') === 0;
            }).length;
        }

        test('viewable count equals attachment count when all have http storage_url', () => {
            const attachments = [
                { filename: 'a.png', storage_url: 'https://drive.google.com/file/d/1/view' },
                { filename: 'b.jpg', storage_url: 'https://example.com/b.jpg' },
                { filename: 'c.pdf', storage_url: 'http://example.com/c.pdf' },
            ];
            const viewable = getViewableCount(attachments);
            expect(viewable).toBe(attachments.length);
        });

        test('viewable count less than total when some lack storage_url', () => {
            const attachments = [
                { filename: 'a.png', storage_url: 'https://drive.google.com/file/d/1/view' },
                { filename: 'b.jpg', storage_url: '' },
                { filename: 'c.pdf', storage_url: 'https://example.com/c.pdf' },
            ];
            const viewable = getViewableCount(attachments);
            expect(viewable).toBe(2);
            expect(viewable).toBeLessThan(attachments.length);
        });

        test('viewable count zero when all lack http storage_url', () => {
            const attachments = [
                { filename: 'a.png', storage_url: '' },
                { filename: 'b.jpg', storage_url: null },
            ];
            const viewable = getViewableCount(attachments);
            expect(viewable).toBe(0);
        });
    });
});
