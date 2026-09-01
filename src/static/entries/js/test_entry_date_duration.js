/**
 * Tests for entry card date line: recording time stays on the date,
 * duration is appended as whole seconds.
 *
 * Run with: node --test src/static/entries/js/test_entry_date_duration.js
 */

'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

function formatDateWithDuration(dateStr, durationSeconds) {
    const seconds = parseInt(durationSeconds, 10);
    if (!seconds || seconds < 1) {
        return dateStr;
    }
    return dateStr + ' · ' + seconds + 's';
}

describe('entry card date line', () => {
    it('shows duration in line with the date', () => {
        assert.equal(
            formatDateWithDuration('Sep 1, 2026 12:31 PM', 240),
            'Sep 1, 2026 12:31 PM · 240s'
        );
    });

    it('keeps the date unchanged when duration is absent', () => {
        assert.equal(
            formatDateWithDuration('Sep 1, 2026 12:31 PM', null),
            'Sep 1, 2026 12:31 PM'
        );
    });
});
