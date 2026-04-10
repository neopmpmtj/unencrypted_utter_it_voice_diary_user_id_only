/**
 * Batch Calendar Confirmation Page
 * Collects resolutions per event and submits to confirm API.
 * Day-by-day navigation for alternative slots.
 */

document.addEventListener('DOMContentLoaded', function() {
    const btnConfirm = document.getElementById('btnConfirm');
    const btnCancel = document.getElementById('btnCancel');
    const loadingOverlay = document.getElementById('loadingOverlay');
    const errorMessage = document.getElementById('errorMessage');

    const resolutions = {};
    const dayIndexByEvent = {};

    function getCsrfToken() {
        const tokenElement = document.querySelector('[name=csrfmiddlewaretoken]');
        if (tokenElement) return tokenElement.value;
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.content;
        const name = 'csrftoken';
        const cookies = document.cookie.split(';');
        for (const cookie of cookies) {
            const c = cookie.trim();
            if (c.startsWith(name + '=')) {
                return c.substring(name.length + 1);
            }
        }
        return '';
    }

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.classList.remove('hidden');
        setTimeout(() => errorMessage.classList.add('hidden'), 5000);
    }

    function showLoading(show) {
        if (show) {
            loadingOverlay.classList.remove('hidden');
            btnConfirm.disabled = true;
            btnCancel.disabled = true;
        } else {
            loadingOverlay.classList.remove('hidden');
            btnConfirm.disabled = false;
            btnCancel.disabled = false;
        }
    }

    const i18n = window.batchI18n || {};
    const prevDayLabel = i18n.previous_day || 'Previous day';
    const nextDayLabel = i18n.next_day || 'Next day';
    const noSlotsLabel = i18n.no_slots_available || 'No alternative slots available';

    function buildDaysFromSlots(alternativeSlots) {
        if (!alternativeSlots || alternativeSlots.length === 0) return [];
        const byDate = {};
        alternativeSlots.forEach(function(slot, idx) {
            const startStr = slot.start || '';
            const dateKey = startStr.split('T')[0] || 'unknown';
            if (!byDate[dateKey]) {
                byDate[dateKey] = { date: dateKey, date_formatted: slot.start_formatted ? slot.start_formatted.split(' at ')[0] : dateKey, slots: [] };
            }
            byDate[dateKey].slots.push({
                start: slot.start,
                end: slot.end,
                start_formatted: slot.start_formatted,
                end_formatted: slot.end_formatted,
                flat_index: idx
            });
        });
        return Object.keys(byDate).sort().map(function(k) { return byDate[k]; });
    }

    function renderDayNavSlotContainer(ev, container) {
        const eventIndex = String(ev.event_index);
        const days = ev.alternative_slots_by_day && ev.alternative_slots_by_day.length > 0
            ? ev.alternative_slots_by_day
            : buildDaysFromSlots(ev.alternative_slots || []);

        let currentIdx = dayIndexByEvent[eventIndex] !== undefined ? dayIndexByEvent[eventIndex] : 0;
        if (currentIdx >= days.length) currentIdx = Math.max(0, days.length - 1);
        dayIndexByEvent[eventIndex] = currentIdx;

        if (days.length === 0) {
            container.innerHTML = '<p class="text-sm text-muted-foreground mb-2">' + noSlotsLabel + '</p>';
            return;
        }

        const day = days[currentIdx];
        const canGoPrev = currentIdx > 0;
        const canGoNext = currentIdx < days.length - 1;

        let html = '<div class="flex items-center gap-2 mb-2">';
        html += '<button type="button" class="day-nav-prev vd-btn vd-btn-ghost p-1.5 rounded border border-input" data-event-index="' + eventIndex + '" aria-label="' + prevDayLabel + '"' + (canGoPrev ? '' : ' disabled') + '>&#9664;</button>';
        html += '<span class="day-label text-sm font-medium text-foreground min-w-[140px] text-center">' + (day.date_formatted || day.date) + '</span>';
        html += '<button type="button" class="day-nav-next vd-btn vd-btn-ghost p-1.5 rounded border border-input" data-event-index="' + eventIndex + '" aria-label="' + nextDayLabel + '"' + (canGoNext ? '' : ' disabled') + '>&#9654;</button>';
        html += '</div>';

        html += '<div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2 slot-grid" data-event-index="' + eventIndex + '">';
        (day.slots || []).forEach(function(slot) {
            const flatIdx = slot.flat_index !== undefined ? slot.flat_index : 0;
            html += '<div class="slot-card p-3 rounded-lg border-2 border-border cursor-pointer hover:border-accent/50 hover:bg-accent/5 transition-colors" data-slot-flat-index="' + flatIdx + '" data-event-index="' + eventIndex + '">';
            html += '<p class="text-sm font-medium text-foreground">' + (slot.start_formatted || slot.start) + '</p>';
            html += '<p class="text-xs text-muted-foreground">' + (i18n.until || 'Until') + ' ' + (slot.end_formatted || slot.end) + '</p>';
            html += '</div>';
        });
        html += '</div>';

        container.innerHTML = html;

        container.querySelectorAll('.slot-card').forEach(function(card) {
            card.addEventListener('click', function() {
                const evIdx = this.dataset.eventIndex;
                const flatIdx = parseInt(this.dataset.slotFlatIndex, 10);
                document.querySelectorAll('.slot-card[data-event-index="' + evIdx + '"]').forEach(function(c) { c.classList.remove('selected'); });
                this.classList.add('selected');
                document.querySelectorAll('.btn-override[data-event-index="' + evIdx + '"]').forEach(function(b) { b.classList.remove('selected'); });
                resolutions[evIdx] = { slot_index: flatIdx };
            });
        });

        container.querySelectorAll('.day-nav-prev').forEach(function(btn) {
            if (btn.disabled) return;
            btn.addEventListener('click', function() {
                const evIdx = this.dataset.eventIndex;
                dayIndexByEvent[evIdx] = (dayIndexByEvent[evIdx] || 0) - 1;
                const evData = (window.eventsWithConflicts || []).find(function(e) { return String(e.event_index) === evIdx; });
                if (evData) renderDayNavSlotContainer(evData, container);
            });
        });

        container.querySelectorAll('.day-nav-next').forEach(function(btn) {
            if (btn.disabled) return;
            btn.addEventListener('click', function() {
                const evIdx = this.dataset.eventIndex;
                dayIndexByEvent[evIdx] = (dayIndexByEvent[evIdx] || 0) + 1;
                const evData = (window.eventsWithConflicts || []).find(function(e) { return String(e.event_index) === evIdx; });
                if (evData) renderDayNavSlotContainer(evData, container);
            });
        });
    }

    document.querySelectorAll('.day-nav-slot-container').forEach(function(container) {
        const eventIndex = container.dataset.eventIndex;
        const ev = (window.eventsWithConflicts || []).find(function(e) { return String(e.event_index) === eventIndex; });
        if (ev) renderDayNavSlotContainer(ev, container);
    });

    document.querySelectorAll('.btn-override').forEach(btn => {
        btn.addEventListener('click', function() {
            const eventIndex = this.dataset.eventIndex;
            document.querySelectorAll(`.slot-card[data-event-index="${eventIndex}"]`).forEach(c => c.classList.remove('selected'));
            document.querySelectorAll(`.btn-override[data-event-index="${eventIndex}"]`).forEach(b => b.classList.remove('selected'));
            this.classList.add('selected');
            resolutions[eventIndex] = { override: true };
        });
    });

    const eventsWithConflicts = window.eventsWithConflicts || [];
    const conflictEventIndices = new Set(eventsWithConflicts.map(e => String(e.event_index)));

    btnConfirm.addEventListener('click', async function() {
        for (const idx of conflictEventIndices) {
            if (!resolutions[idx]) {
                showError(i18n.please_resolve_conflicts || 'Please resolve all conflicts (select alternative time or override).');
                return;
            }
        }

        showLoading(true);
        try {
            const response = await fetch(`/batch-calendar/api/confirm/${window.batchId}/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({ resolutions })
            });
            const data = await response.json();

            if (data.success) {
                window.location.href = data.redirect_url || '/entries/';
            } else {
                showLoading(false);
                showError(data.error || (i18n.failed_to_create_events || 'Failed to create events'));
            }
        } catch (err) {
            showLoading(false);
            showError(i18n.network_error || 'Network error. Please try again.');
        }
    });

    btnCancel.addEventListener('click', async function() {
        if (!confirm(i18n.cancel_all_events || 'Cancel all events in this batch?')) return;
        showLoading(true);
        try {
            const response = await fetch(`/batch-calendar/api/cancel/${window.batchId}/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({})
            });
            const data = await response.json();
            if (data.success) {
                window.location.href = data.redirect_url || '/entries/';
            } else {
                showLoading(false);
                showError(data.error || (i18n.failed_to_cancel || 'Failed to cancel'));
            }
        } catch (err) {
            showLoading(false);
            showError(i18n.network_error || 'Network error. Please try again.');
        }
    });
});
