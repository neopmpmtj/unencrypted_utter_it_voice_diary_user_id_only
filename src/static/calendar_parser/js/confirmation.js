/**
 * Calendar Event Confirmation Page
 * 
 * Handles time slot selection and event confirmation/cancellation.
 */

document.addEventListener('DOMContentLoaded', function() {
    // DOM elements
    const slotGrid = document.getElementById('slotGrid');
    const btnConfirm = document.getElementById('btnConfirm');
    const btnOverride = document.getElementById('btnOverride');
    const btnCancel = document.getElementById('btnCancel');
    const loadingOverlay = document.getElementById('loadingOverlay');
    const errorMessage = document.getElementById('errorMessage');
    const eventDuration = document.getElementById('eventDuration');
    
    // State
    let selectedSlotIndex = null;
    
    // Calculate and display event duration
    if (eventDuration && window.alternativeSlots && window.alternativeSlots.length > 0) {
        const slot = window.alternativeSlots[0];
        const start = new Date(slot.start);
        const end = new Date(slot.end);
        const durationMs = end - start;
        const durationMinutes = Math.round(durationMs / (1000 * 60));
        
        if (durationMinutes >= 60) {
            const hours = Math.floor(durationMinutes / 60);
            const mins = durationMinutes % 60;
            eventDuration.textContent = mins > 0 ? `${hours}h ${mins}min` : `${hours} hour${hours > 1 ? 's' : ''}`;
        } else {
            eventDuration.textContent = `${durationMinutes} minutes`;
        }
    }
    
    // Slot selection handling
    if (slotGrid) {
        const slotCards = slotGrid.querySelectorAll('.slot-card');
        
        slotCards.forEach(card => {
            card.addEventListener('click', function() {
                // Remove selection from all cards
                slotCards.forEach(c => c.classList.remove('selected'));
                
                // Select this card
                this.classList.add('selected');
                
                // Store selection
                selectedSlotIndex = parseInt(this.dataset.slotIndex);
                
                // Enable confirm button
                btnConfirm.disabled = false;
            });
        });
    }
    
    // Get CSRF token
    function getCsrfToken() {
        const tokenElement = document.querySelector('[name=csrfmiddlewaretoken]');
        if (tokenElement) {
            return tokenElement.value;
        }
        // Try from meta tag
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) {
            return meta.content;
        }
        // Try from cookie
        const name = 'csrftoken';
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                return cookie.substring(name.length + 1);
            }
        }
        return '';
    }
    
    // Show error message
    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.classList.add('visible');
        setTimeout(() => {
            errorMessage.classList.remove('visible');
        }, 5000);
    }
    
    // Show loading state
    function showLoading(show) {
        if (show) {
            loadingOverlay.classList.add('active');
            btnConfirm.disabled = true;
            btnOverride.disabled = true;
            btnCancel.disabled = true;
        } else {
            loadingOverlay.classList.remove('active');
            // Re-enable buttons based on state
            btnConfirm.disabled = selectedSlotIndex === null;
            btnOverride.disabled = false;
            btnCancel.disabled = false;
        }
    }
    
    // Send confirmation request
    async function sendConfirmation(action, slotIndex = null) {
        showLoading(true);
        
        const body = { action };
        
        if (action === 'confirm' && slotIndex !== null) {
            body.slot_index = slotIndex;
        }
        
        try {
            const response = await fetch(`/calendar/api/confirm/${window.calendarEventId}/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify(body)
            });
            
            const data = await response.json();
            
            if (data.success) {
                // Redirect to entries or show success
                if (data.redirect_url) {
                    window.location.href = data.redirect_url;
                } else {
                    window.location.href = '/entries/';
                }
            } else if (data.conflict && data.confirmation_url) {
                // Re-conflict: selected slot is no longer available, redirect to new confirmation page
                window.location.href = data.confirmation_url;
            } else {
                showLoading(false);
                const i18n = window.calendarI18n || {};
                showError(data.error || data.message || (i18n.failed_to_process_request || 'Failed to process request'));
            }
        } catch (error) {
            showLoading(false);
            const i18n = window.calendarI18n || {};
            showError(i18n.network_error || 'Network error. Please try again.');
            console.error('[CalendarConfirmation] Error:', error);
        }
    }
    
    // Button handlers
    btnConfirm.addEventListener('click', function() {
        if (selectedSlotIndex === null) {
            const i18n = window.calendarI18n || {};
            showError(i18n.please_select_time_slot || 'Please select a time slot first');
            return;
        }
        sendConfirmation('confirm', selectedSlotIndex);
    });
    
    btnOverride.addEventListener('click', function() {
        const i18n = window.calendarI18n || {};
        if (confirm(i18n.confirm_create_despite_conflict || 'Are you sure you want to create this event despite the conflict?')) {
            sendConfirmation('override');
        }
    });
    
    btnCancel.addEventListener('click', function() {
        const i18n = window.calendarI18n || {};
        if (confirm(i18n.confirm_cancel_event || 'Are you sure you want to cancel this event?')) {
            sendConfirmation('cancel');
        }
    });
    
    // If no alternative slots, enable confirm button to allow override
    if (!window.alternativeSlots || window.alternativeSlots.length === 0) {
        // No slots available, user must override or cancel
        btnConfirm.style.display = 'none';
    }
});
