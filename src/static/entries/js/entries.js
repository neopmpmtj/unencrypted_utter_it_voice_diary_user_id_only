/**
 * Entries List JavaScript
 * 
 * Handles infinite scroll, search with debounce, date filtering,
 * and single-expansion accordion behavior.
 */

(function() {
    'use strict';

    // State
    let currentCursor = null;
    let isLoading = false;
    let hasMore = true;
    let currentSearch = '';
    let currentDatePreset = 'all';
    let expandedEntryId = null;
    let searchDebounceTimer = null;
    let currentTotalCount = 0;

    // Configuration
    const SEARCH_DEBOUNCE_MS = 500;
    const SCROLL_THRESHOLD = 200; // pixels from bottom
    const canEdit = typeof window.ENTRIES_CAN_EDIT !== 'undefined' && window.ENTRIES_CAN_EDIT;

    // DOM Elements
    let entriesListEl;
    let searchInputEl;
    let searchClearBtn;
    let datePresetBtns;
    let loadingInitialEl;
    let loadingMoreEl;
    let emptyStateEl;
    let noResultsStateEl;
    let errorStateEl;
    let entryCountEl;
    let searchedCountEl;
    let clearFiltersBtn;
    let entriesMetaEl;

    /**
     * Initialize the entries module
     */
    function init() {
        // Get DOM elements
        entriesListEl = document.getElementById('entries-list');
        searchInputEl = document.getElementById('search-input');
        searchClearBtn = document.getElementById('search-clear-btn');
        datePresetBtns = document.querySelectorAll('.date-preset-btn');
        loadingInitialEl = document.getElementById('loading-initial');
        loadingMoreEl = document.getElementById('loading-more');
        emptyStateEl = document.getElementById('empty-state');
        noResultsStateEl = document.getElementById('no-results-state');
        errorStateEl = document.getElementById('error-state');
        entryCountEl = document.getElementById('entry-count');
        searchedCountEl = document.getElementById('searched-count');
        clearFiltersBtn = document.getElementById('clear-filters-btn');
        entriesMetaEl = document.getElementById('entries-meta');

        if (!entriesListEl) {
            console.error('Entries list element not found');
            return;
        }

        setupEventListeners();
        setupAttachmentPreviewModal();
        if (canEdit) {
            setupEditModal();
        }
        loadEntries(true);
    }

    /**
     * Set up all event listeners
     */
    function setupEventListeners() {
        // Search input with debounce
        if (searchInputEl) {
            searchInputEl.addEventListener('input', function(e) {
                clearTimeout(searchDebounceTimer);
                searchDebounceTimer = setTimeout(function() {
                    currentSearch = e.target.value.trim();
                    resetAndReload();
                }, SEARCH_DEBOUNCE_MS);

                // Show/hide clear button
                if (searchClearBtn) {
                    searchClearBtn.classList.toggle('hidden', !e.target.value);
                }
            });

            // Prevent form submission on Enter
            searchInputEl.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                }
            });
        }

        // Search clear button
        if (searchClearBtn) {
            searchClearBtn.addEventListener('click', function() {
                searchInputEl.value = '';
                searchClearBtn.classList.add('hidden');
                currentSearch = '';
                resetAndReload();
            });
        }

        // Date preset buttons
        datePresetBtns.forEach(function(btn) {
            btn.addEventListener('click', function() {
                // Update active state
                datePresetBtns.forEach(function(b) {
                    b.classList.remove('vd-btn-accent');
                    b.classList.add('vd-btn-ghost');
                    if (!b.classList.contains('border')) b.classList.add('border', 'border-input');
                });
                btn.classList.add('vd-btn-accent');
                btn.classList.remove('vd-btn-ghost', 'border', 'border-input');
                
                currentDatePreset = btn.dataset.preset;
                resetAndReload();
            });
        });

        // Clear filters button
        if (clearFiltersBtn) {
            clearFiltersBtn.addEventListener('click', clearAllFilters);
        }

        // Infinite scroll
        window.addEventListener('scroll', handleScroll);

        // Retry button in error state
        const retryBtn = document.getElementById('retry-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', function() {
                resetAndReload();
            });
        }
    }

    /**
     * Handle scroll for infinite scroll
     */
    function handleScroll() {
        if (isLoading || !hasMore) return;

        const scrollPosition = window.innerHeight + window.scrollY;
        const threshold = document.documentElement.scrollHeight - SCROLL_THRESHOLD;

        if (scrollPosition >= threshold) {
            loadEntries(false);
        }
    }

    /**
     * Reset state and reload entries
     */
    function resetAndReload() {
        currentCursor = null;
        hasMore = true;
        expandedEntryId = null;
        entriesListEl.innerHTML = '';
        loadEntries(true);
    }

    /**
     * Clear all filters
     */
    function clearAllFilters() {
        // Clear search
        if (searchInputEl) {
            searchInputEl.value = '';
        }
        if (searchClearBtn) {
            searchClearBtn.classList.add('hidden');
        }
        currentSearch = '';

        // Reset date preset
        datePresetBtns.forEach(function(btn) {
            if (btn.dataset.preset === 'all') {
                btn.classList.add('vd-btn-accent');
                btn.classList.remove('vd-btn-ghost', 'border', 'border-input');
            } else {
                btn.classList.remove('vd-btn-accent');
                btn.classList.add('vd-btn-ghost');
                if (!btn.classList.contains('border')) btn.classList.add('border', 'border-input');
            }
        });
        currentDatePreset = 'all';

        resetAndReload();
    }

    /**
     * Load entries from API
     */
    function loadEntries(isInitial) {
        if (isLoading) return;
        isLoading = true;

        // Show appropriate loading state
        hideAllStates();
        if (isInitial) {
            loadingInitialEl.classList.remove('hidden');
        } else {
            loadingMoreEl.classList.remove('hidden');
        }

        // Build URL with parameters
        const params = new URLSearchParams();
        if (currentCursor) {
            params.append('cursor', currentCursor);
        }
        if (currentSearch) {
            params.append('search', currentSearch);
        }
        if (currentDatePreset && currentDatePreset !== 'all') {
            params.append('date_preset', currentDatePreset);
        }

        const url = '/api/entries/?' + params.toString();

        fetch(url, {
            method: 'GET',
            headers: {
                'Accept': 'application/json',
            },
            credentials: 'same-origin',
        })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .then(function(data) {
            isLoading = false;
            hideAllStates();

            // Update state
            hasMore = data.has_more;
            currentCursor = data.next_cursor;

            // Update meta information
            updateMetaInfo(data);

            // Handle empty states
            if (isInitial && data.entries.length === 0) {
                if (data.filters_active) {
                    noResultsStateEl.classList.remove('hidden');
                } else {
                    emptyStateEl.classList.remove('hidden');
                }
                return;
            }

            // Render entries
            renderEntries(data.entries);

            // Show loading more indicator if there are more
            if (!hasMore && entriesListEl.children.length > 0) {
                // Optionally show "end of list" indicator
            }
        })
        .catch(function(error) {
            console.error('Error loading entries:', error);
            isLoading = false;
            hideAllStates();
            errorStateEl.classList.remove('hidden');
        });
    }

    /**
     * Hide all UI state elements
     */
    function hideAllStates() {
        loadingInitialEl.classList.add('hidden');
        loadingMoreEl.classList.add('hidden');
        emptyStateEl.classList.add('hidden');
        noResultsStateEl.classList.add('hidden');
        errorStateEl.classList.add('hidden');
    }

    /**
     * Update meta information (entry count, searched count, clear filters visibility)
     */
    function updateMetaInfo(data) {
        currentTotalCount = data.total_count || 0;
        // Entry count
        if (entryCountEl) {
            const displayCount = data.entries ? (entriesListEl.children.length + data.entries.length) : entriesListEl.children.length;
            const total = data.total_count !== undefined ? data.total_count : currentTotalCount;
            if (total > 0) {
                entryCountEl.textContent = 'Showing ' + displayCount + ' of ' + total + ' entries';
            } else {
                entryCountEl.textContent = '';
            }
        }

        // Searched count (only when searching)
        if (searchedCountEl) {
            if (data.searched_count && data.searched_count > 0) {
                searchedCountEl.textContent = 'Searched ' + data.searched_count + ' entries';
                searchedCountEl.classList.remove('hidden');
            } else {
                searchedCountEl.classList.add('hidden');
            }
        }

        // Clear filters button visibility
        if (clearFiltersBtn) {
            clearFiltersBtn.classList.toggle('hidden', !data.filters_active);
        }

        // Show/hide meta section
        if (entriesMetaEl) {
            const hasContent = (data.total_count > 0) || data.filters_active;
            entriesMetaEl.classList.toggle('hidden', !hasContent);
        }
    }

    /**
     * Render entries to the DOM
     */
    function renderEntries(entries) {
        entries.forEach(function(entry) {
            const entryEl = createEntryElement(entry);
            entriesListEl.appendChild(entryEl);
        });
    }

    /**
     * Create a single entry element
     */
    function createEntryElement(entry) {
        const card = document.createElement('div');
        card.className = 'rounded-lg border border-border bg-card overflow-hidden transition-all duration-200';
        card.dataset.entryId = entry.id;

        // Format date
        const date = entry.occurred_at ? new Date(entry.occurred_at) : null;
        const dateStr = date ? formatDate(date) : 'Unknown date';

        // Get type badge class
        const typeBadgeClass = getTypeBadgeClass(entry.item_type);

        const attachments = entry.attachments || [];
        const attachmentCountHtml = attachments.length > 0
            ? ' <span class="text-xs text-muted-foreground ml-2">' + attachments.length + ' file' + (attachments.length === 1 ? '' : 's') + '</span>'
            : '';
        let attachmentLinksHtml = '';
        if (attachments.length > 0) {
            attachmentLinksHtml = '<div class="mt-3 pt-3 border-t border-border"><p class="text-xs font-medium text-muted-foreground mb-1">Attachments:</p><ul class="space-y-0.5">';
            attachments.forEach(function(a, idx) {
                const label = escapeHtml(a.filename || 'File');
                const url = (a.storage_url || '').trim();
                const isHttpUrl = url.indexOf('http://') === 0 || url.indexOf('https://') === 0;
                if (isHttpUrl) {
                    attachmentLinksHtml += '<li><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="text-xs text-accent hover:underline inline-block py-1" data-attachment-link data-attachment-index="' + idx + '">' + label + '</a></li>';
                } else if (url) {
                    attachmentLinksHtml += '<li><span class="text-xs text-muted-foreground">' + label + ' (link unavailable)</span></li>';
                } else {
                    attachmentLinksHtml += '<li><span class="text-xs text-muted-foreground">' + label + ' (uploading...)</span></li>';
                }
            });
            attachmentLinksHtml += '</ul></div>';
        }

        const tags = entry.tags || [];
        const classificationHtml = tags.length > 0
            ? '<span class="inline-block px-1.5 py-0.5 rounded text-[13px] bg-muted text-muted-foreground">' + escapeHtml(tags.join(', ')) + '</span>'
            : '';

        card.innerHTML =
            '<div class="entry-header p-4 cursor-pointer hover:bg-secondary/50 transition-colors">' +
                '<div class="flex items-start justify-between gap-2">' +
                    '<h3 class="text-sm font-medium text-foreground flex-1">' + escapeHtml(entry.title) + '</h3>' +
                    '<div class="flex items-center gap-1.5 shrink-0">' +
                        '<span class="inline-block px-1.5 py-0.5 rounded text-[13px] font-medium ' + typeBadgeClass + '">' + escapeHtml(entry.item_type) + '</span>' +
                        classificationHtml +
                        '<svg class="expand-indicator h-3 w-3 text-muted-foreground transition-transform duration-200" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>' +
                    '</div>' +
                '</div>' +
                '<p class="text-xs text-muted-foreground mt-1 line-clamp-2">' + escapeHtml(entry.content_preview) + '</p>' +
                '<span class="text-[13px] text-muted-foreground/70 mt-1 inline-block">' + dateStr + '</span>' + attachmentCountHtml +
            '</div>' +
            '<div class="entry-content hidden border-t border-border p-4 bg-secondary/20">' +
                '<div class="vd-transcription-text text-foreground whitespace-pre-wrap leading-relaxed">' + escapeHtml(entry.content_full) + '</div>' +
                attachmentLinksHtml +
                '<div class="flex items-center justify-between mt-3 pt-3 border-t border-border">' +
                    '<div class="flex items-center gap-2">' +
                        '<button type="button" class="vd-btn vd-btn-destructive px-3 py-1 text-xs entry-delete-btn" aria-label="Delete entry">Delete</button>' +
                    '</div>' +
                    '<div class="flex items-center gap-2">' +
                        (canEdit ? '<button type="button" class="vd-btn vd-btn-ghost px-3 py-1 text-xs border border-input entry-edit-btn" aria-label="Edit entry">Edit</button>' : '') +
                        '<button type="button" class="vd-btn vd-btn-ghost px-3 py-1 text-xs border border-input entry-copy-btn" aria-label="Copy text">Copy</button>' +
                    '</div>' +
                '</div>' +
            '</div>';

        // Add click handler for accordion
        const header = card.querySelector('.entry-header');
        header.addEventListener('click', function() {
            toggleEntry(card, entry.id);
        });

        // Attachment links: open preview modal instead of new tab
        card.querySelectorAll('a[data-attachment-link]').forEach(function(link) {
            link.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                const idx = parseInt(this.getAttribute('data-attachment-index'), 10);
                if (!isNaN(idx) && idx >= 0) {
                    openAttachmentModal(entry, idx);
                } else if (this.href) {
                    window.open(this.href, '_blank', 'noopener,noreferrer');
                }
            });
        });

        // Copy button: copy full text, then show "Copied!" for 2 seconds
        const copyBtn = card.querySelector('.entry-copy-btn');
        copyBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            const text = entry.content_full || '';
            if (!text) return;
            navigator.clipboard.writeText(text).then(function() {
                const label = copyBtn.textContent;
                copyBtn.textContent = 'Copied!';
                copyBtn.setAttribute('aria-label', 'Copied');
                setTimeout(function() {
                    copyBtn.textContent = label;
                    copyBtn.setAttribute('aria-label', 'Copy text');
                }, 2000);
            });
        });

        if (canEdit) {
            const editBtn = card.querySelector('.entry-edit-btn');
            if (editBtn) {
                editBtn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    openEditModal(entry);
                });
            }
        }

        // Delete button: confirm, optimistic remove, then API; revert on failure
        const deleteBtn = card.querySelector('.entry-delete-btn');
        deleteBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (!confirm('Remove this entry from your voice diary?')) return;

            const nextSibling = card.nextSibling;
            const wasExpanded = expandedEntryId === entry.id;
            if (wasExpanded) {
                expandedEntryId = null;
            }
            card.remove();
            currentTotalCount -= 1;
            updateEntryCountDisplay();

            if (entriesListEl.children.length === 0) {
                hideAllStates();
                emptyStateEl.classList.remove('hidden');
            }

            const url = '/api/entries/' + entry.id + '/delete/';
            fetch(url, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                credentials: 'same-origin',
            }).then(function(response) {
                if (!response.ok) {
                    throw new Error('Delete failed');
                }
            }).catch(function() {
                currentTotalCount += 1;
                const newCard = createEntryElement(entry);
                if (nextSibling) {
                    entriesListEl.insertBefore(newCard, nextSibling);
                } else {
                    entriesListEl.appendChild(newCard);
                }
                updateEntryCountDisplay();
                emptyStateEl.classList.add('hidden');
                alert('Could not delete entry. Please try again.');
            });
        });

        return card;
    }

    /**
     * Toggle entry expansion (single expansion only)
     */
    function toggleEntry(cardEl, entryId) {
        const contentEl = cardEl.querySelector('.entry-content');
        const indicator = cardEl.querySelector('.expand-indicator');
        const isCurrentlyExpanded = contentEl && !contentEl.classList.contains('hidden');

        // Close any previously expanded entry
        if (expandedEntryId && expandedEntryId !== entryId) {
            const previousCard = entriesListEl.querySelector('[data-entry-id="' + expandedEntryId + '"]');
            if (previousCard) {
                const prevContent = previousCard.querySelector('.entry-content');
                const prevInd = previousCard.querySelector('.expand-indicator');
                if (prevContent) prevContent.classList.add('hidden');
                if (prevInd) prevInd.classList.remove('rotate-180');
            }
        }

        // Toggle current entry
        if (isCurrentlyExpanded) {
            if (contentEl) contentEl.classList.add('hidden');
            if (indicator) indicator.classList.remove('rotate-180');
            expandedEntryId = null;
        } else {
            if (contentEl) contentEl.classList.remove('hidden');
            if (indicator) indicator.classList.add('rotate-180');
            expandedEntryId = entryId;

            // Scroll into view if needed
            setTimeout(function() {
                const rect = cardEl.getBoundingClientRect();
                if (rect.bottom > window.innerHeight) {
                    cardEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }, 350); // Wait for animation
        }
    }

    /**
     * Get CSS class for entry type badge
     */
    function getTypeBadgeClass(itemType) {
        switch (itemType.toLowerCase()) {
            case 'audio':
                return 'bg-blue-500/10 text-blue-600 dark:text-blue-400';
            case 'text':
                return 'bg-green-500/10 text-green-600 dark:text-green-400';
            case 'email':
                return 'bg-purple-500/10 text-purple-600 dark:text-purple-400';
            default:
                return 'bg-muted text-muted-foreground';
        }
    }

    /**
     * Format date for display (absolute date and time, no relative terms)
     */
    function formatDate(date) {
        const datePart = date.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
        const timePart = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        return datePart + ' ' + timePart;
    }

    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Get CSRF token from cookie or meta/input
     */
    function getCsrfToken() {
        const name = 'csrftoken';
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.indexOf(name + '=') === 0) {
                return cookie.substring(name.length + 1);
            }
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) {
            return meta.content;
        }
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input) {
            return input.value;
        }
        return '';
    }

    /**
     * Listen for calendar conflict after save: WebSocket first, fallback to polling.
     * On conflict, show toast and redirect to confirmation page.
     */
    function listenForCalendarConflict(entryId) {
        const wsBaseUrl = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host;
        const wsUrl = wsBaseUrl + '/ws/pipeline/' + entryId + '/';
        const ws = new WebSocket(wsUrl);
        const pollIntervalMs = 2000;
        const maxWaitMs = 60000;
        let pollTimer = null;
        let startTime = Date.now();

        function handleConflict(confirmationUrl) {
            if (window.VDTheme && window.VDTheme.showToast) {
                window.VDTheme.showToast(
                    (typeof window.ENTRIES_CALENDAR_CONFLICT_MSG !== 'undefined')
                        ? window.ENTRIES_CALENDAR_CONFLICT_MSG
                        : 'Calendar conflict detected. Redirecting to resolve...',
                    'warning'
                );
            }
            if (confirmationUrl) {
                window.location.href = confirmationUrl;
            }
        }

        function stopListening() {
            try { ws.close(); } catch (_) {}
            if (pollTimer) clearInterval(pollTimer);
        }

        ws.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                if (data.status === 'calendar_conflict' || data.conflict) {
                    stopListening();
                    handleConflict(data.confirmation_url);
                }
                if (data.type === 'complete') {
                    stopListening();
                }
            } catch (_) {}
        };
        function doPoll() {
            if (Date.now() - startTime > maxWaitMs) {
                stopListening();
                return;
            }
            fetch('/voice/status/' + entryId + '/', { headers: { 'Accept': 'application/json' } })
                .then(function(r) { return r.ok ? r.json() : null; })
                .then(function(data) {
                    if (data && data.calendar_conflict && data.confirmation_url) {
                        stopListening();
                        handleConflict(data.confirmation_url);
                    }
                    if (data && (data.item_status === 'processed' || data.item_status === 'tagged')) {
                        stopListening();
                    }
                })
                .catch(function() {});
        }
        ws.onclose = ws.onerror = function() {
            if (pollTimer) return;
            doPoll();
            pollTimer = setInterval(doPoll, pollIntervalMs);
        };
    }

    /**
     * Update the displayed entry count (e.g. after optimistic delete or revert)
     */
    function updateEntryCountDisplay() {
        if (!entryCountEl) return;
        const displayCount = entriesListEl.children.length;
        if (currentTotalCount > 0) {
            entryCountEl.textContent = 'Showing ' + displayCount + ' of ' + currentTotalCount + ' entries';
        } else {
            entryCountEl.textContent = '';
        }
    }

    // Attachment preview modal
    const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i;
    const VIDEO_EXT = /\.(mp4|webm|mov|avi|mkv|m4v)(\?|$)/i;
    const DRIVE_FILE_ID_RE = /\/d\/([^/]+)\//;

    function getAttachmentType(filename) {
        if (!filename) return 'other';
        if (IMAGE_EXT.test(filename)) return 'image';
        if (VIDEO_EXT.test(filename)) return 'video';
        return 'other';
    }

    function getDriveFileId(url) {
        if (!url || typeof url !== 'string') return null;
        const m = url.match(DRIVE_FILE_ID_RE);
        return m ? m[1] : null;
    }

    function buildAttachmentSlideHtml(att) {
        const url = (att.storage_url || '').trim();
        const filename = att.filename || 'File';
        const type = getAttachmentType(filename);
        const driveId = getDriveFileId(url);
        const isDrive = !!driveId;

        if (!url || url.indexOf('http') !== 0) {
            return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center min-h-[200px] p-4"><span class="text-sm text-muted-foreground">' + escapeHtml(filename) + ' (uploading...)</span></div>';
        }

        if (type === 'image') {
            if (isDrive) {
                const iframeSrc = 'https://drive.google.com/file/d/' + encodeURIComponent(driveId) + '/preview';
                return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center p-2"><iframe src="' + escapeHtml(iframeSrc) + '" class="w-full min-h-[400px] max-h-[60vh] rounded border-0" title="' + escapeHtml(filename) + '"></iframe></div>';
            }
            return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center p-2"><img src="' + escapeHtml(url) + '" alt="' + escapeHtml(filename) + '" class="max-w-full max-h-[60vh] object-contain rounded"></div>';
        }

        if (type === 'video') {
            if (isDrive) {
                const iframeSrc = 'https://drive.google.com/file/d/' + encodeURIComponent(driveId) + '/preview';
                return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center p-2"><iframe src="' + escapeHtml(iframeSrc) + '" class="w-full aspect-video max-h-[60vh] rounded border-0" allow="autoplay" allowfullscreen></iframe></div>';
            }
            return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center p-2"><video src="' + escapeHtml(url) + '" controls playsinline class="max-w-full max-h-[60vh] rounded" data-attachment-video></video></div>';
        }

        return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex flex-col items-center justify-center min-h-[200px] p-4 gap-2"><span class="text-sm text-muted-foreground">' + escapeHtml(filename) + '</span><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="vd-btn vd-btn-accent px-4 py-2 text-sm">Open in new tab</a></div>';
    }

    function setupAttachmentPreviewModal() {
        const modal = document.getElementById('attachment-preview-modal');
        const carouselEl = document.getElementById('attachment-preview-carousel');
        const prevBtn = document.getElementById('attachment-preview-prev');
        const nextBtn = document.getElementById('attachment-preview-next');
        const closeBtn = document.getElementById('attachment-preview-close');
        const slidesEl = document.getElementById('attachment-preview-slides');
        if (!modal || !carouselEl || !prevBtn || !nextBtn || !slidesEl) return;

        function closeAttachmentModal() {
            slidesEl.querySelectorAll('video[data-attachment-video]').forEach(function(v) { v.pause(); });
            modal.close();
        }

        modal.addEventListener('close', function() {
            slidesEl.querySelectorAll('video[data-attachment-video]').forEach(function(v) { v.pause(); });
        });

        if (closeBtn) closeBtn.addEventListener('click', closeAttachmentModal);
        modal.addEventListener('click', function(e) {
            if (e.target === modal) closeAttachmentModal();
        });

        prevBtn.addEventListener('click', function() {
            carouselEl.scrollBy({ left: -carouselEl.offsetWidth, behavior: 'smooth' });
        });
        nextBtn.addEventListener('click', function() {
            carouselEl.scrollBy({ left: carouselEl.offsetWidth, behavior: 'smooth' });
        });

        let scrollTimeout;
        carouselEl.addEventListener('scroll', function() {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(function() {
                const idx = Math.round(carouselEl.scrollLeft / (carouselEl.offsetWidth || 1));
                const videos = slidesEl.querySelectorAll('video[data-attachment-video]');
                videos.forEach(function(v, i) {
                    if (i !== idx) v.pause();
                });
            }, 100);
        });
    }

    function openAttachmentModal(entry, startIndex) {
        const modal = document.getElementById('attachment-preview-modal');
        const textEl = document.getElementById('attachment-preview-text');
        const slidesEl = document.getElementById('attachment-preview-slides');
        const carouselEl = document.getElementById('attachment-preview-carousel');
        const prevBtn = document.getElementById('attachment-preview-prev');
        const nextBtn = document.getElementById('attachment-preview-next');

        if (!modal || !textEl || !slidesEl) return;

        const attachments = entry.attachments || [];
        const viewable = attachments.filter(function(a) { return (a.storage_url || '').trim().indexOf('http') === 0; });
        const count = viewable.length;

        textEl.textContent = entry.content_full || '';

        slidesEl.innerHTML = '';
        viewable.forEach(function(a) {
            const div = document.createElement('div');
            div.className = 'attachment-slide-wrapper flex-shrink-0 snap-center';
            div.innerHTML = buildAttachmentSlideHtml(a);
            slidesEl.appendChild(div);
        });

        prevBtn.classList.toggle('hidden', count <= 1);
        nextBtn.classList.toggle('hidden', count <= 1);

        modal.showModal();

        requestAnimationFrame(function() {
            const slideWidth = carouselEl ? carouselEl.offsetWidth : 0;
            slidesEl.querySelectorAll('.attachment-slide-wrapper').forEach(function(w) {
                w.style.width = slideWidth + 'px';
                w.style.minWidth = slideWidth + 'px';
            });
            if (count > 0) {
                const targetScroll = Math.min(startIndex, count - 1) * slideWidth;
                carouselEl.scrollLeft = targetScroll;
            }
        });
    }

    let currentEditingEntry = null;

    var editModalRecorder = null;
    var editModalAttachmentFiles = [];

    function setupEditModal() {
        const modal = document.getElementById('edit-entry-modal');
        const form = document.getElementById('edit-entry-form');
        const cancelBtn = document.getElementById('edit-entry-cancel');
        const loadingEl = document.getElementById('edit-entry-loading');
        const editSpinner = document.getElementById('edit-entry-spinner');
        const editLoadingText = document.getElementById('edit-entry-loading-text');
        const recordBtn = document.getElementById('edit-modal-record-btn');
        const recordingTimer = document.getElementById('edit-modal-recording-timer');
        const recordingTimerWrap = document.getElementById('edit-modal-recording-timer-wrap');
        const contentInput = document.getElementById('edit-entry-content');
        const saveBtn = document.getElementById('edit-entry-save');
        const attachInput = document.getElementById('edit-entry-attachment-input');
        const attachBtn = document.getElementById('edit-entry-attach-btn');
        const attachList = document.getElementById('edit-entry-attachment-list');
        const attachBadge = document.getElementById('edit-entry-attach-badge');

        const rewriteBtn = document.getElementById('rewrite-btn');
        const undoRewriteBtn = document.getElementById('undo-rewrite-btn');
        const rewriteSpinner = document.getElementById('rewrite-spinner');
        const rewriteTemplateSelect = document.getElementById('rewrite-template-select');

        var preRewriteText = null;

        var MAX_FILE_SIZE = 100 * 1024 * 1024;
        var MAX_TOTAL_SIZE = 500 * 1024 * 1024;
        function formatFileSize(bytes) {
            if (bytes === 0) return '0 B';
            var k = 1024, sizes = ['B', 'KB', 'MB'];
            var i = Math.floor(Math.log(bytes) / Math.log(k));
            return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
        }
        function validateEditFile(file) {
            if (file.size > MAX_FILE_SIZE) return file.name + ' exceeds ' + formatFileSize(MAX_FILE_SIZE);
            return null;
        }
        function totalEditSize(arr) {
            return arr.reduce(function(s, f) { return s + f.size; }, 0);
        }
        function renderEditAttachList() {
            if (!attachList) return;
            attachList.innerHTML = '';
            editModalAttachmentFiles.forEach(function(file, idx) {
                var li = document.createElement('li');
                li.className = 'flex items-center justify-between gap-2 text-sm';
                li.innerHTML = '<span class="truncate">' + escapeHtml(file.name) + ' <span class="text-muted-foreground text-xs">(' + formatFileSize(file.size) + ')</span></span>' +
                    '<button type="button" class="text-destructive hover:text-destructive/80 text-xs shrink-0" data-idx="' + idx + '">&#10005;</button>';
                li.querySelector('button').addEventListener('click', function() {
                    editModalAttachmentFiles.splice(idx, 1);
                    renderEditAttachList();
                });
                attachList.appendChild(li);
            });
            if (attachBadge) {
                if (editModalAttachmentFiles.length > 0) {
                    attachBadge.classList.remove('hidden');
                    attachBadge.classList.add('flex');
                    attachBadge.textContent = editModalAttachmentFiles.length;
                } else {
                    attachBadge.classList.add('hidden');
                    attachBadge.classList.remove('flex');
                }
            }
        }
        if (attachBtn && attachInput) {
            attachBtn.addEventListener('click', function() { attachInput.click(); });
            attachInput.addEventListener('change', function(e) {
                var files = Array.from(e.target.files);
                var errors = [], valid = [];
                files.forEach(function(f) {
                    var err = validateEditFile(f);
                    if (err) errors.push(err);
                    else valid.push(f);
                });
                if (errors.length) {
                    if (window.VDTheme) window.VDTheme.showToast(errors[0], 'error');
                    else alert(errors[0]);
                }
                if (valid.length) {
                    if (totalEditSize(editModalAttachmentFiles.concat(valid)) > MAX_TOTAL_SIZE) {
                        if (window.VDTheme) window.VDTheme.showToast('Total file size exceeds limit.', 'error');
                        else alert('Total file size exceeds limit.');
                    } else {
                        editModalAttachmentFiles = editModalAttachmentFiles.concat(valid);
                        renderEditAttachList();
                    }
                }
                attachInput.value = '';
            });
        }

        function resetRewriteState() {
            preRewriteText = null;
            if (undoRewriteBtn) undoRewriteBtn.classList.add('hidden');
        }

        if (rewriteBtn && window.ENTRIES_CAN_REWRITE) {
            rewriteBtn.addEventListener('click', function() {
                if (!contentInput || !contentInput.value.trim()) return;

                preRewriteText = contentInput.value;
                var template = rewriteTemplateSelect ? rewriteTemplateSelect.value : 'grammar';

                rewriteBtn.disabled = true;
                if (rewriteSpinner) rewriteSpinner.classList.remove('hidden');
                if (undoRewriteBtn) undoRewriteBtn.classList.add('hidden');

                fetch('/api/entries/rewrite/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ text: contentInput.value, template: template }),
                }).then(function(response) {
                    return response.json().then(function(data) {
                        if (!response.ok) {
                            throw new Error(data.message || data.error || 'Rewrite failed');
                        }
                        return data;
                    });
                }).then(function(data) {
                    contentInput.value = data.rewritten_text;
                    if (undoRewriteBtn) undoRewriteBtn.classList.remove('hidden');
                }).catch(function(err) {
                    preRewriteText = null;
                    if (window.VDTheme) {
                        window.VDTheme.showToast(err.message || 'Rewrite failed', 'error');
                    } else {
                        alert(err.message || 'Rewrite failed. Please try again.');
                    }
                }).finally(function() {
                    rewriteBtn.disabled = false;
                    if (rewriteSpinner) rewriteSpinner.classList.add('hidden');
                });
            });
        }

        if (undoRewriteBtn) {
            undoRewriteBtn.addEventListener('click', function() {
                if (preRewriteText !== null && contentInput) {
                    contentInput.value = preRewriteText;
                }
                resetRewriteState();
            });
        }

        if (!modal || !form || !cancelBtn) return;

        function showEditSpinner(state) {
            if (!loadingEl) return;
            loadingEl.classList.remove('hidden');
            if (editSpinner) {
                editSpinner.classList.remove('vd-spinner--red', 'vd-spinner--green');
                editSpinner.classList.add(state === 'uploading' ? 'vd-spinner--red' : 'vd-spinner--green');
            }
            if (editLoadingText) {
                editLoadingText.textContent = state === 'uploading'
                    ? (window.ENTRIES_UPLOADING || 'Uploading...')
                    : (state === 'processing' ? (window.ENTRIES_PROCESSING || 'Processing...') : (window.ENTRIES_SAVING || 'Saving...'));
            }
        }
        function hideEditSpinner() {
            if (!loadingEl) return;
            loadingEl.classList.add('hidden');
            if (editSpinner) editSpinner.classList.remove('vd-spinner--red', 'vd-spinner--green');
        }

        function stopEditModalRecorderIfActive() {
            if (editModalRecorder && (editModalRecorder.state === 'recording' || editModalRecorder.state === 'paused')) {
                editModalRecorder.stopRecording().catch(function() {});
                if (recordBtn) {
                    recordBtn.classList.remove('edit-recording');
                    recordBtn.setAttribute('aria-label', window.ENTRIES_RECORD_LABEL || 'Record');
                }
                if (saveBtn) saveBtn.disabled = false;
            }
        }

        cancelBtn.addEventListener('click', function() {
            stopEditModalRecorderIfActive();
            resetRewriteState();
            editModalAttachmentFiles = [];
            renderEditAttachList();
            modal.close();
            currentEditingEntry = null;
        });
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                stopEditModalRecorderIfActive();
                resetRewriteState();
                editModalAttachmentFiles = [];
                renderEditAttachList();
                modal.close();
                currentEditingEntry = null;
            }
        });
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            if (!currentEditingEntry) return;
            const titleInput = document.getElementById('edit-entry-title');
            const contentInput = document.getElementById('edit-entry-content');
            const createNewInput = document.getElementById('edit-entry-create-new');
            const saveBtn = document.getElementById('edit-entry-save');
            const contentText = (contentInput && contentInput.value) || '';
            const title = (titleInput && titleInput.value) ? titleInput.value.trim() : '';
            const createNew = createNewInput && createNewInput.checked;

            const hasFiles = editModalAttachmentFiles.length > 0;
            showEditSpinner(hasFiles ? 'uploading' : 'saving');
            saveBtn.disabled = true;
            const url = '/api/entries/' + currentEditingEntry.id + '/edit/';

            var fetchOpts;
            if (hasFiles) {
                var fd = new FormData();
                fd.append('content_text', contentText);
                fd.append('title', title);
                fd.append('create_new', createNew);
                editModalAttachmentFiles.forEach(function(f) { fd.append('files', f); });
                var csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
                if (csrfEl) fd.append('csrfmiddlewaretoken', csrfEl.value);
                fetchOpts = {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken(), 'Accept': 'application/json' },
                    credentials: 'same-origin',
                    body: fd,
                };
            } else {
                fetchOpts = {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        content_text: contentText,
                        title: title,
                        create_new: createNew,
                    }),
                };
            }
            fetch(url, fetchOpts).then(function(response) {
                return response.json().then(function(data) {
                    if (!response.ok) {
                        throw new Error(data.message || data.error || (window.ENTRIES_SAVE_FAILED || 'Save failed'));
                    }
                    return data;
                });
            }).then(function(data) {
                const editingEntryId = currentEditingEntry ? currentEditingEntry.id : null;
                showEditSpinner('processing');
                setTimeout(function() {
                    hideEditSpinner();
                    saveBtn.disabled = false;
                    const updatedEntry = data.entry;
                    if (data.created_new) {
                        const newCard = createEntryElement(updatedEntry);
                        entriesListEl.insertBefore(newCard, entriesListEl.firstChild);
                        currentTotalCount += 1;
                        updateEntryCountDisplay();
                        if (emptyStateEl && emptyStateEl.classList.contains('hidden') === false) {
                            emptyStateEl.classList.add('hidden');
                        }
                    } else if (editingEntryId) {
                        const oldCard = entriesListEl.querySelector('[data-entry-id="' + editingEntryId + '"]');
                        if (oldCard) {
                            const newCard = createEntryElement(updatedEntry);
                            oldCard.parentNode.replaceChild(newCard, oldCard);
                        }
                    }
                    editModalAttachmentFiles = [];
                    renderEditAttachList();
                    modal.close();
                    currentEditingEntry = null;
                    if (data.calendar_parsing_queued && updatedEntry && updatedEntry.id) {
                        listenForCalendarConflict(updatedEntry.id);
                    }
                }, 400);
            }).catch(function(err) {
                hideEditSpinner();
                saveBtn.disabled = false;
                alert(err.message || (window.ENTRIES_COULD_NOT_SAVE || 'Could not save entry. Please try again.'));
            });
        });

        // Recorder (pro users only, when recorder-config and audio_recorder.js are present)
        var recorderConfigEl = document.getElementById('recorder-config');
        if (recordBtn && recorderConfigEl && typeof window.EditModalRecorder !== 'undefined') {
            var config = JSON.parse(recorderConfigEl.textContent);
            var showTimer = !!config.showTimer;
            if (recordingTimerWrap && !showTimer) {
                recordingTimerWrap.classList.add('hidden');
            }
            function ensureEditModalRecorder() {
                if (!editModalRecorder) {
                    editModalRecorder = new window.EditModalRecorder({
                        uploadUrl: config.uploadUrl,
                        maxDuration: config.maxDuration,
                        maxFileSize: config.maxFileSize,
                        transcribeOnly: true,
                    });
                    editModalRecorder.onTranscriptionReady = function(data) {
                        var text = data.transcribed_text || '';
                        if (text && contentInput) {
                            var ta = contentInput;
                            var start = ta.selectionStart;
                            var end = ta.selectionEnd;
                            var before = ta.value.substring(0, start);
                            var after = ta.value.substring(end);
                            ta.value = before + text + (text.endsWith(' ') ? '' : ' ') + after;
                            ta.selectionStart = ta.selectionEnd = start + text.length + (text.endsWith(' ') ? 0 : 1);
                            ta.focus();
                        }
                        recordBtn.classList.remove('edit-recording');
                        recordBtn.setAttribute('aria-label', window.ENTRIES_RECORD_LABEL || 'Record');
                        if (saveBtn) saveBtn.disabled = false;
                    };
                    editModalRecorder.onError = function(err) {
                        if (window.VDTheme) window.VDTheme.showToast(err.message || 'Transcription failed', 'error');
                        recordBtn.classList.remove('edit-recording');
                        recordBtn.setAttribute('aria-label', window.ENTRIES_RECORD_LABEL || 'Record');
                        if (saveBtn) saveBtn.disabled = false;
                    };
                    editModalRecorder.onTranscriptionDiscarded = function(reason) {
                        if (window.VDTheme) window.VDTheme.showToast(reason, 'info');
                        recordBtn.classList.remove('edit-recording');
                        recordBtn.setAttribute('aria-label', window.ENTRIES_RECORD_LABEL || 'Record');
                        if (saveBtn) saveBtn.disabled = false;
                    };
                    if (showTimer && recordingTimer) {
                        editModalRecorder.onDurationUpdate = function(duration) {
                            recordingTimer.textContent = window.EditModalRecorder.formatDuration(duration);
                        };
                    }
                }
                return editModalRecorder;
            }
            recordBtn.addEventListener('click', function() {
                if (editModalRecorder && (editModalRecorder.state === 'uploading' || editModalRecorder.state === 'processing')) {
                    return;
                }
                if (editModalRecorder && (editModalRecorder.state === 'recording' || editModalRecorder.state === 'paused')) {
                    editModalRecorder.stopRecording().catch(function() {});
                    recordBtn.classList.remove('edit-recording');
                    recordBtn.setAttribute('aria-label', window.ENTRIES_RECORD_LABEL || 'Record');
                    if (saveBtn) saveBtn.disabled = false;
                    return;
                }
                var rec = ensureEditModalRecorder();
                rec.setTemplateType('plain');
                if (showTimer && recordingTimer) recordingTimer.textContent = '00:00';
                rec.startRecording().then(function() {
                    recordBtn.classList.add('edit-recording');
                    recordBtn.setAttribute('aria-label', window.ENTRIES_STOP_LABEL || 'Stop');
                    if (saveBtn) saveBtn.disabled = true;
                }).catch(function(err) {
                    if (window.VDTheme) window.VDTheme.showToast('Could not start recording: ' + err.message, 'error');
                });
            });
        }
    }

    function openEditModal(entry) {
        currentEditingEntry = entry;
        editModalAttachmentFiles = [];
        const titleInput = document.getElementById('edit-entry-title');
        const contentInput = document.getElementById('edit-entry-content');
        const createNewInput = document.getElementById('edit-entry-create-new');
        const undoBtn = document.getElementById('undo-rewrite-btn');
        const attachList = document.getElementById('edit-entry-attachment-list');
        const attachBadge = document.getElementById('edit-entry-attach-badge');
        if (titleInput) titleInput.value = entry.title || '';
        if (contentInput) contentInput.value = entry.content_full || '';
        if (createNewInput) createNewInput.checked = false;
        if (undoBtn) undoBtn.classList.add('hidden');
        if (attachList) attachList.innerHTML = '';
        if (attachBadge) { attachBadge.classList.add('hidden'); attachBadge.classList.remove('flex'); }
        const modal = document.getElementById('edit-entry-modal');
        if (modal) modal.showModal();
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
