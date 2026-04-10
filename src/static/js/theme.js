/**
 * VoiceDiary Theme Manager
 *
 * Manages dark/light mode and accent color themes.
 * Persists to localStorage and syncs to server via API.
 */
(function () {
  'use strict';

  const STORAGE_KEY_DARK = 'vd-dark-mode';
  const STORAGE_KEY_ACCENT = 'vd-accent-theme';
  const STORAGE_KEY_TRANSCRIPTION_SIZE = 'vd-transcription-size';
  const VALID_ACCENTS = ['green', 'blue', 'indigo', 'purple', 'red', 'orange', 'yellow'];
  const VALID_TRANSCRIPTION_SIZES = ['small', 'medium', 'large'];
  const DEFAULT_ACCENT = 'green';
  const DEFAULT_TRANSCRIPTION_SIZE = 'small';

  const html = document.documentElement;

  /* ------------------------------------------------------------------
     Initialise (called immediately, also on DOMContentLoaded)
     ------------------------------------------------------------------ */

  function init() {
    // Dark mode: prefer localStorage, then server-rendered data-attr, then system
    const stored = localStorage.getItem(STORAGE_KEY_DARK);
    if (stored !== null) {
      setDarkMode(stored === '1');
    } else {
      const serverPref = html.dataset.darkMode;
      if (serverPref !== undefined && serverPref !== '') {
        setDarkMode(serverPref === 'true');
      }
      // else: leave as-is (server already rendered the class or not)
    }

    // Accent theme
    const storedAccent = localStorage.getItem(STORAGE_KEY_ACCENT);
    if (storedAccent && VALID_ACCENTS.includes(storedAccent)) {
      setAccent(storedAccent);
    } else {
      const serverAccent = html.dataset.accentTheme;
      if (serverAccent && VALID_ACCENTS.includes(serverAccent)) {
        setAccent(serverAccent);
      }
    }

    // Transcription text size
    const storedSize = localStorage.getItem(STORAGE_KEY_TRANSCRIPTION_SIZE);
    if (storedSize && VALID_TRANSCRIPTION_SIZES.includes(storedSize)) {
      setTranscriptionSize(storedSize);
    } else {
      const serverSize = html.dataset.transcriptionSize;
      if (serverSize && VALID_TRANSCRIPTION_SIZES.includes(serverSize)) {
        setTranscriptionSize(serverSize);
      }
    }

    // Auto-dismiss toasts
    initToastAutoDismiss();
  }

  /* ------------------------------------------------------------------
     Dark Mode
     ------------------------------------------------------------------ */

  function isDark() {
    return html.classList.contains('dark');
  }

  function setDarkMode(on) {
    html.classList.toggle('dark', on);
    localStorage.setItem(STORAGE_KEY_DARK, on ? '1' : '0');
  }

  function toggleDarkMode() {
    const newVal = !isDark();
    setDarkMode(newVal);
    syncThemeToServer();
    return newVal;
  }

  /* ------------------------------------------------------------------
     Accent Theme
     ------------------------------------------------------------------ */

  function getAccent() {
    for (const cls of html.classList) {
      if (cls.startsWith('theme-')) return cls.replace('theme-', '');
    }
    return DEFAULT_ACCENT;
  }

  function setAccent(accent) {
    if (!VALID_ACCENTS.includes(accent)) return;
    // Remove existing theme classes
    VALID_ACCENTS.forEach(a => html.classList.remove('theme-' + a));
    html.classList.add('theme-' + accent);
    localStorage.setItem(STORAGE_KEY_ACCENT, accent);
  }

  function selectAccent(accent) {
    setAccent(accent);
    syncThemeToServer();
  }

  /* ------------------------------------------------------------------
     Transcription text size
     ------------------------------------------------------------------ */

  function getTranscriptionSize() {
    return html.dataset.transcriptionSize || DEFAULT_TRANSCRIPTION_SIZE;
  }

  function setTranscriptionSize(size) {
    if (!VALID_TRANSCRIPTION_SIZES.includes(size)) return;
    html.dataset.transcriptionSize = size;
    localStorage.setItem(STORAGE_KEY_TRANSCRIPTION_SIZE, size);
  }

  function selectTranscriptionSize(size) {
    setTranscriptionSize(size);
    syncThemeToServer();
  }

  /* ------------------------------------------------------------------
     Server sync
     ------------------------------------------------------------------ */

  function syncThemeToServer() {
    const csrfToken = getCsrfToken();
    if (!csrfToken) return;

    fetch('/src.accounts/api/theme/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({
        dark_mode: isDark(),
        accent_theme: getAccent(),
        transcription_text_size: getTranscriptionSize(),
      }),
    }).catch(function () {
      // Silently fail -- localStorage is the source of truth
    });
  }

  function getCsrfToken() {
    // Cookie
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    if (match) return match[1];
    // Meta
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.content;
    // Hidden input
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input) return input.value;
    return '';
  }

  /* ------------------------------------------------------------------
     Toast auto-dismiss
     ------------------------------------------------------------------ */

  function initToastAutoDismiss() {
    document.querySelectorAll('[data-toast-auto-dismiss]').forEach(function (el) {
      var delay = parseInt(el.getAttribute('data-toast-auto-dismiss'), 10) || 5000;
      setTimeout(function () {
        el.classList.add('animate-toast-out');
        el.addEventListener('animationend', function () {
          el.remove();
        });
      }, delay);
    });
  }

  /** Show a programmatic toast */
  function showToast(message, type) {
    type = type || 'success';
    var container = document.getElementById('toastContainer');
    if (!container) return;

    var toast = document.createElement('div');
    toast.className = 'vd-toast pointer-events-auto ' +
      (type === 'error' ? 'vd-toast-error' : 'vd-toast-success');
    toast.setAttribute('role', 'alert');
    toast.setAttribute('data-toast-auto-dismiss', '5000');

    toast.innerHTML =
      '<div class="flex items-center justify-between gap-3">' +
        '<span>' + escapeHtml(message) + '</span>' +
        '<button type="button" onclick="this.closest(\'[data-toast-auto-dismiss]\').remove()" ' +
          'class="shrink-0 text-muted-foreground hover:text-foreground transition-colors" aria-label="Dismiss">' +
          '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">' +
            '<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>' +
          '</svg>' +
        '</button>' +
      '</div>';

    container.appendChild(toast);

    setTimeout(function () {
      toast.classList.add('animate-toast-out');
      toast.addEventListener('animationend', function () {
        toast.remove();
      });
    }, 5000);
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /* ------------------------------------------------------------------
     Run init immediately (script should be in <head> or before </body>)
     ------------------------------------------------------------------ */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* ------------------------------------------------------------------
     Public API
     ------------------------------------------------------------------ */
  window.VDTheme = {
    isDark: isDark,
    toggleDarkMode: toggleDarkMode,
    setDarkMode: setDarkMode,
    getAccent: getAccent,
    selectAccent: selectAccent,
    getTranscriptionSize: getTranscriptionSize,
    setTranscriptionSize: setTranscriptionSize,
    selectTranscriptionSize: selectTranscriptionSize,
    showToast: showToast,
    VALID_ACCENTS: VALID_ACCENTS,
    VALID_TRANSCRIPTION_SIZES: VALID_TRANSCRIPTION_SIZES,
  };
})();
