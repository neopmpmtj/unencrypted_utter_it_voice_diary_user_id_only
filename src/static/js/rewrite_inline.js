(function(global) {
  'use strict';

  function getCsrfToken() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    if (m) return m[1];
    var inp = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return inp ? inp.value : '';
  }

  function initRewriteInline(options) {
    var rewriteUrl = options.rewriteUrl;
    var selectEl = options.selectEl;
    var btnEl = options.btnEl;
    var undoBtn = options.undoBtn;
    var spinnerEl = options.spinnerEl;
    var getText = options.getText;
    var setText = options.setText;
    var isDisabledExtra = options.isDisabledExtra || function() { return false; };
    var onQuotaBlocked = options.onQuotaBlocked || function() { return false; };

    var preRewriteText = null;

    function resetUndo() {
      preRewriteText = null;
      if (undoBtn) undoBtn.classList.add('hidden');
    }

    function updateEnabled() {
      if (!btnEl) return;
      var blocked = onQuotaBlocked();
      var raw = getText ? getText() : '';
      var empty = !String(raw || '').trim();
      btnEl.disabled = blocked || empty || isDisabledExtra();
    }

    if (btnEl && rewriteUrl) {
      btnEl.addEventListener('click', function() {
        var text = getText ? String(getText()).trim() : '';
        if (!text) return;
        preRewriteText = getText();
        var template = selectEl && selectEl.value ? selectEl.value : 'grammar';
        btnEl.disabled = true;
        if (spinnerEl) spinnerEl.classList.remove('hidden');
        if (undoBtn) undoBtn.classList.add('hidden');

        fetch(rewriteUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCsrfToken(),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
          },
          credentials: 'same-origin',
          body: JSON.stringify({ text: text, template: template }),
        }).then(function(response) {
          return response.json().then(function(data) {
            if (!response.ok) {
              throw new Error(data.message || data.error || 'Rewrite failed');
            }
            return data;
          });
        }).then(function(data) {
          if (setText) setText(data.rewritten_text);
          if (undoBtn) undoBtn.classList.remove('hidden');
        }).catch(function(err) {
          preRewriteText = null;
          if (global.VDTheme) {
            global.VDTheme.showToast(err.message || 'Rewrite failed', 'error');
          } else {
            alert(err.message || 'Rewrite failed. Please try again.');
          }
        }).finally(function() {
          updateEnabled();
          if (spinnerEl) spinnerEl.classList.add('hidden');
        });
      });
    }

    if (undoBtn) {
      undoBtn.addEventListener('click', function() {
        if (preRewriteText !== null && setText) setText(preRewriteText);
        resetUndo();
        updateEnabled();
      });
    }

    return { updateEnabled: updateEnabled, resetUndo: resetUndo };
  }

  global.initRewriteInline = initRewriteInline;
})(window);
