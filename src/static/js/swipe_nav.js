/**
 * VoiceDiary Swipe Navigation
 *
 * Handles swipe gestures between Voice and Text input pages.
 * Also supports left/right arrow keys on desktop.
 *
 * Usage: include this script on input pages with data attributes:
 *   <body data-swipe-current="voice" data-swipe-voice-url="/voice/" data-swipe-text-url="/text-input/">
 */
(function () {
  'use strict';

  var SWIPE_THRESHOLD = 50;  // minimum px to trigger navigation (50px for easier mobile triggering)
  var startX = 0;
  var startY = 0;
  var startTime = 0;

  var body = document.body;
  var currentPage = body.getAttribute('data-swipe-current');  // 'voice' or 'text'
  var voiceUrl = body.getAttribute('data-swipe-voice-url');
  var textUrl = body.getAttribute('data-swipe-text-url');

  if (!currentPage || !voiceUrl || !textUrl) return;

  // Apply entry animation based on navigation direction
  var direction = sessionStorage.getItem('vd-swipe-direction');
  if (direction) {
    sessionStorage.removeItem('vd-swipe-direction');
    var content = document.querySelector('[data-swipe-content]');
    if (content) {
      content.classList.add(
        direction === 'left' ? 'animate-slide-in-right' : 'animate-slide-in-left'
      );
    }
  }

  function onTouchStart(e) {
    if (e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    startTime = Date.now();
  }

  function onTouchEnd(e) {
    if (!startX || !e.changedTouches || !e.changedTouches.length) return;

    var endX = e.changedTouches[0].clientX;
    var endY = e.changedTouches[0].clientY;
    var diffX = endX - startX;
    var diffY = endY - startY;
    var elapsed = Date.now() - startTime;

    // Only count horizontal swipes (not vertical scroll)
    if (Math.abs(diffX) < SWIPE_THRESHOLD || Math.abs(diffY) > Math.abs(diffX)) {
      startX = 0;
      return;
    }

    // Ignore slow drags (> 500ms)
    if (elapsed > 500) {
      startX = 0;
      return;
    }

    if (diffX < 0 && currentPage === 'voice') {
      // Swipe left on voice page -> go to text
      sessionStorage.setItem('vd-swipe-direction', 'left');
      window.location.href = textUrl;
    } else if (diffX > 0 && currentPage === 'text') {
      // Swipe right on text page -> go to voice
      sessionStorage.setItem('vd-swipe-direction', 'right');
      window.location.href = voiceUrl;
    }

    startX = 0;
  }

  /* Touch events - on document and content area for reliable swipe on mobile */
  document.addEventListener('touchstart', onTouchStart, { passive: true });
  document.addEventListener('touchend', onTouchEnd, { passive: true });
  var swipeContent = document.querySelector('[data-swipe-content]');
  if (swipeContent) {
    swipeContent.addEventListener('touchstart', onTouchStart, { passive: true });
    swipeContent.addEventListener('touchend', onTouchEnd, { passive: true });
  }

  /* Keyboard navigation (desktop) */
  document.addEventListener('keydown', function (e) {
    // Don't intercept when typing in inputs/textareas
    var tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable) {
      return;
    }

    if (e.key === 'ArrowLeft' && currentPage === 'text') {
      sessionStorage.setItem('vd-swipe-direction', 'right');
      window.location.href = voiceUrl;
    } else if (e.key === 'ArrowRight' && currentPage === 'voice') {
      sessionStorage.setItem('vd-swipe-direction', 'left');
      window.location.href = textUrl;
    }
  });
})();
