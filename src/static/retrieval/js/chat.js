(function () {
  'use strict';

  var currentSessionId = null;
  var isSending = false;

  var messagesEl = document.getElementById('chat-messages');
  var welcomeEl = document.getElementById('chat-welcome');
  var chatForm = document.getElementById('chat-form');
  var chatInput = document.getElementById('chat-input');
  var sendBtn = document.getElementById('chat-send-btn');

  var latestSession = null;
  var chatI18n = {};
  try {
    var dataEl = document.getElementById('chat-latest-session');
    if (dataEl) latestSession = JSON.parse(dataEl.textContent);
    var i18nEl = document.getElementById('chat-i18n');
    if (i18nEl) chatI18n = JSON.parse(i18nEl.textContent);
  } catch (_) {}

  function getCsrfToken() {
    var name = 'csrftoken';
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
      var cookie = cookies[i].trim();
      if (cookie.indexOf(name + '=') === 0) {
        return cookie.substring(name.length + 1);
      }
    }
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function parseJsonResponse(r) {
    if (!r.ok) {
      return r.text().then(function (body) {
        try {
          var data = JSON.parse(body);
          var reqFailed = (chatI18n.request_failed || 'Request failed (status %(status)s)').replace('%(status)s', r.status);
          throw { status: r.status, error: data.error || reqFailed };
        } catch (e) {
          if (e.status) throw e;
          var swWrong = (chatI18n.something_went_wrong || 'Something went wrong (status %(status)s)').replace('%(status)s', r.status);
          throw { status: r.status, error: swWrong };
        }
      });
    }
    return r.json();
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function autoResizeInput() {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 128) + 'px';
  }

  function setLoading(loading) {
    isSending = loading;
    sendBtn.disabled = loading;
    chatInput.disabled = loading;
    if (loading) {
      sendBtn.classList.add('opacity-50');
    } else {
      sendBtn.classList.remove('opacity-50');
      chatInput.focus();
    }
  }

  // -----------------------------------------------------------------------
  // Message rendering
  // -----------------------------------------------------------------------

  function addUserBubble(text) {
    if (welcomeEl) welcomeEl.classList.add('hidden');
    var wrapper = document.createElement('div');
    wrapper.className = 'flex justify-end';
    wrapper.innerHTML =
      '<div class="min-w-0 max-w-[80%] rounded-2xl rounded-br-md px-4 py-2.5 bg-accent text-accent-foreground text-sm break-words whitespace-pre-wrap">' +
      escapeHtml(text) +
      '</div>';
    messagesEl.appendChild(wrapper);
    scrollToBottom();
  }

  function addAssistantBubble(text, sources) {
    var wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start';

    var inner =
      '<div class="min-w-0 max-w-[80%] rounded-2xl rounded-bl-md px-4 py-2.5 bg-muted text-foreground text-sm break-words">' +
      '<div class="whitespace-pre-wrap break-words">' + escapeHtml(text) + '</div>';

    if (sources && sources.length > 0) {
      var seen = {};
      var entryIds = [];
      for (var i = 0; i < sources.length; i++) {
        var eid = sources[i].entry_id;
        if (eid && !seen[eid]) {
          seen[eid] = true;
          entryIds.push(eid);
        }
      }
      if (entryIds.length > 0) {
        var idsStr = escapeHtml(entryIds.join(','));
        inner += '<div class="mt-2 pt-2 border-t border-border/50 space-y-1 chat-sources-container" data-entry-ids="' + idsStr + '">';
        inner += '<p class="text-xs font-medium text-muted-foreground">' + escapeHtml(chatI18n.sources || 'Sources:') + '</p>';
        for (var j = 0; j < sources.length; j++) {
          var s = sources[j];
          var dateLabel = s.occurred_at ? s.occurred_at.substring(0, 10) : '?';
          var tag = s.classification ? ' [' + escapeHtml(s.classification) + ']' : '';
          inner +=
            '<button type="button" class="block w-full text-left text-xs text-accent hover:underline break-words chat-source-link">' +
            escapeHtml(dateLabel + tag + ': ' + (s.summary || '')) +
            '</button>';
        }
        inner += '</div>';
      }
    }
    inner += '</div>';
    wrapper.innerHTML = inner;
    messagesEl.appendChild(wrapper);
    var container = wrapper.querySelector('.chat-sources-container');
    if (container) {
      container.addEventListener('click', function (e) {
        if (e.target.classList.contains('chat-source-link')) {
          e.preventDefault();
          openSourcesModal(container.getAttribute('data-entry-ids'));
        }
      });
    }
    scrollToBottom();
  }

  function addLoadingBubble() {
    var wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start';
    wrapper.id = 'loading-bubble';
    wrapper.innerHTML =
      '<div class="min-w-0 max-w-[80%] rounded-2xl rounded-bl-md px-4 py-3 bg-muted">' +
      '<div class="flex items-center gap-1.5">' +
      '<span class="h-2 w-2 rounded-full bg-muted-foreground/40 animate-bounce" style="animation-delay:0ms"></span>' +
      '<span class="h-2 w-2 rounded-full bg-muted-foreground/40 animate-bounce" style="animation-delay:150ms"></span>' +
      '<span class="h-2 w-2 rounded-full bg-muted-foreground/40 animate-bounce" style="animation-delay:300ms"></span>' +
      '</div></div>';
    messagesEl.appendChild(wrapper);
    scrollToBottom();
  }

  function removeLoadingBubble() {
    var el = document.getElementById('loading-bubble');
    if (el) el.remove();
  }

  // -----------------------------------------------------------------------
  // Sources modal
  // -----------------------------------------------------------------------

  var sourcesModalEntries = [];
  var sourcesModalIndex = 0;

  function formatDate(date) {
    var datePart = date.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
    var timePart = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return datePart + ' ' + timePart;
  }

  function getTypeBadgeClass(itemType) {
    if (!itemType) return 'bg-muted text-muted-foreground';
    switch (String(itemType).toLowerCase()) {
      case 'audio': return 'bg-blue-500/10 text-blue-600 dark:text-blue-400';
      case 'text': return 'bg-green-500/10 text-green-600 dark:text-green-400';
      case 'email': return 'bg-purple-500/10 text-purple-600 dark:text-purple-400';
      default: return 'bg-muted text-muted-foreground';
    }
  }

  function getDrivePreviewUrl(storageUrl) {
    var url = (storageUrl || '').trim();
    if (!url || url.indexOf('http') !== 0) return null;
    if (url.indexOf('drive.google.com') === -1) return null;
    var previewFromView = url.replace(/\/view(\?|$)/, '/preview$1');
    if (previewFromView !== url) return previewFromView;
    var DRIVE_D_RE = /\/d\/([^/?]+)/;
    var DRIVE_ID_RE = /[?&]id=([^&]+)/;
    var m = url.match(DRIVE_D_RE);
    var driveId = m ? m[1] : null;
    if (!driveId) {
      m = url.match(DRIVE_ID_RE);
      driveId = m ? m[1] : null;
    }
    if (!driveId) return null;
    return 'https://drive.google.com/file/d/' + encodeURIComponent(driveId) + '/preview';
  }

  function buildAttachmentSlideHtml(att) {
    var url = (att.storage_url || '').trim();
    var filename = att.filename || 'File';
    var IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i;
    var VIDEO_EXT = /\.(mp4|webm|mov|avi|mkv|m4v)(\?|$)/i;
    var type = 'other';
    if (filename && IMAGE_EXT.test(filename)) type = 'image';
    else if (filename && VIDEO_EXT.test(filename)) type = 'video';
    var drivePreviewUrl = getDrivePreviewUrl(url);

    if (!url || url.indexOf('http') !== 0) {
      return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center min-h-[200px] p-4"><span class="text-sm text-muted-foreground">' + escapeHtml(filename) + ' (uploading...)</span></div>';
    }
    if (type === 'image') {
      if (drivePreviewUrl) {
        var openLink = '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="vd-btn vd-btn-ghost px-3 py-1.5 text-xs mt-2">Open in new tab</a>';
        return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex flex-col items-center justify-center p-2"><iframe src="' + escapeHtml(drivePreviewUrl) + '" class="w-full min-h-[400px] max-h-[60vh] rounded border-0" title="' + escapeHtml(filename) + '"></iframe>' + openLink + '</div>';
      }
      return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center p-2"><img src="' + escapeHtml(url) + '" alt="' + escapeHtml(filename) + '" class="max-w-full max-h-[60vh] object-contain rounded"></div>';
    }
    if (type === 'video') {
      if (drivePreviewUrl) {
        var openLink2 = '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="vd-btn vd-btn-ghost px-3 py-1.5 text-xs mt-2">Open in new tab</a>';
        return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex flex-col items-center justify-center p-2"><iframe src="' + escapeHtml(drivePreviewUrl) + '" class="w-full aspect-video max-h-[60vh] rounded border-0" allow="autoplay" allowfullscreen></iframe>' + openLink2 + '</div>';
      }
      return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex items-center justify-center p-2"><video src="' + escapeHtml(url) + '" controls playsinline class="max-w-full max-h-[60vh] rounded" data-attachment-video></video></div>';
    }
    return '<div class="attachment-slide flex-shrink-0 w-full snap-center flex flex-col items-center justify-center min-h-[200px] p-4 gap-2"><span class="text-sm text-muted-foreground">' + escapeHtml(filename) + '</span><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="vd-btn vd-btn-accent px-4 py-2 text-sm">Open in new tab</a></div>';
  }

  function openAttachmentPreviewModal(entry, startIndex) {
    var modal = document.getElementById('chat-attachment-preview-modal');
    var textEl = document.getElementById('chat-attachment-preview-text');
    var slidesEl = document.getElementById('chat-attachment-preview-slides');
    var carouselEl = document.getElementById('chat-attachment-preview-carousel');
    var prevBtn = document.getElementById('chat-attachment-preview-prev');
    var nextBtn = document.getElementById('chat-attachment-preview-next');
    if (!modal || !textEl || !slidesEl) return;

    var attachments = entry.attachments || [];
    var viewable = attachments.filter(function (a) { return (a.storage_url || '').trim().indexOf('http') === 0; });
    var count = viewable.length;

    textEl.textContent = entry.content_full || '';
    slidesEl.innerHTML = '';
    viewable.forEach(function (a) {
      var div = document.createElement('div');
      div.className = 'attachment-slide-wrapper flex-shrink-0 snap-center';
      div.innerHTML = buildAttachmentSlideHtml(a);
      slidesEl.appendChild(div);
    });

    prevBtn.classList.toggle('hidden', count <= 1);
    nextBtn.classList.toggle('hidden', count <= 1);
    modal.showModal();

    requestAnimationFrame(function () {
      var slideWidth = carouselEl ? carouselEl.offsetWidth : 0;
      slidesEl.querySelectorAll('.attachment-slide-wrapper').forEach(function (w) {
        w.style.width = slideWidth + 'px';
        w.style.minWidth = slideWidth + 'px';
      });
      if (count > 0) {
        var targetScroll = Math.min(startIndex, count - 1) * slideWidth;
        carouselEl.scrollLeft = targetScroll;
      }
    });
  }

  function renderSourcesEntry(entry) {
    var container = document.getElementById('chat-sources-entry');
    if (!container) return;
    var date = entry.occurred_at ? new Date(entry.occurred_at) : null;
    var dateStr = date ? formatDate(date) : 'Unknown date';
    var typeBadge = getTypeBadgeClass(entry.item_type);
    var tags = entry.tags || [];
    var tagsHtml = tags.length > 0
      ? '<span class="inline-block px-1.5 py-0.5 rounded text-[13px] bg-muted text-muted-foreground">' + escapeHtml(tags.join(', ')) + '</span>'
      : '';
    var attachHtml = '';
    var attachments = entry.attachments || [];
    var viewableAttachments = attachments.filter(function (a) { return (a.storage_url || '').trim().indexOf('http') === 0; });
    if (attachments.length > 0) {
      attachHtml = '<div class="mt-3 pt-3 border-t border-border"><p class="text-xs font-medium text-muted-foreground mb-1">Attachments:</p><ul class="space-y-0.5">';
      attachments.forEach(function (a) {
        var label = escapeHtml(a.filename || 'File');
        var url = (a.storage_url || '').trim();
        var isHttp = url.indexOf('http://') === 0 || url.indexOf('https://') === 0;
        if (isHttp) {
          var viewableIdx = viewableAttachments.indexOf(a);
          attachHtml += '<li><button type="button" class="text-xs text-accent hover:underline inline-block py-1 chat-attachment-link" data-idx="' + viewableIdx + '">' + label + '</button></li>';
        } else if (url) {
          attachHtml += '<li><span class="text-xs text-muted-foreground">' + label + ' (link unavailable)</span></li>';
        } else {
          attachHtml += '<li><span class="text-xs text-muted-foreground">' + label + ' (uploading...)</span></li>';
        }
      });
      attachHtml += '</ul></div>';
    }
    container.innerHTML =
      '<div class="rounded-lg border border-border bg-card overflow-hidden">' +
        '<div class="p-4">' +
          '<h3 class="text-sm font-medium text-foreground">' + escapeHtml(entry.title) + '</h3>' +
          '<div class="flex items-center gap-1.5 mt-1">' +
            '<span class="inline-block px-1.5 py-0.5 rounded text-[13px] font-medium ' + typeBadge + '">' + escapeHtml(entry.item_type || 'entry') + '</span>' +
            tagsHtml +
          '</div>' +
          '<span class="text-[13px] text-muted-foreground/70 mt-1 inline-block">' + dateStr + '</span>' +
          '<div class="vd-transcription-text text-foreground whitespace-pre-wrap leading-relaxed mt-3">' + escapeHtml(entry.content_full || '') + '</div>' +
          attachHtml +
        '</div>' +
      '</div>';
    container.classList.remove('hidden');
    container.querySelectorAll('.chat-attachment-link').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(this.getAttribute('data-idx'), 10);
        if (!isNaN(idx) && idx >= 0) {
          openAttachmentPreviewModal(entry, idx);
        }
      });
    });
  }

  function openSourcesModal(entryIdsStr) {
    var modal = document.getElementById('chat-sources-modal');
    var loadingEl = document.getElementById('chat-sources-loading');
    var errorEl = document.getElementById('chat-sources-error');
    var entryEl = document.getElementById('chat-sources-entry');
    var currentEl = document.getElementById('chat-sources-current');
    var totalEl = document.getElementById('chat-sources-total');
    var prevBtn = document.getElementById('chat-sources-prev');
    var nextBtn = document.getElementById('chat-sources-next');
    if (!modal || !loadingEl || !errorEl || !entryEl) return;

    var ids = (entryIdsStr || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (ids.length === 0) return;

    sourcesModalEntries = [];
    sourcesModalIndex = 0;
    loadingEl.classList.remove('hidden');
    errorEl.classList.add('hidden');
    entryEl.classList.add('hidden');
    totalEl.textContent = String(ids.length);
    currentEl.textContent = '1';
    modal.showModal();

    fetch('/api/entries/?ids=' + encodeURIComponent(ids.join(',')), {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin',
    })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (d) { throw new Error(d.error || 'Request failed'); }); })
      .then(function (data) {
        loadingEl.classList.add('hidden');
        sourcesModalEntries = data.entries || [];
        if (sourcesModalEntries.length === 0) {
          errorEl.textContent = 'No entries found.';
          errorEl.classList.remove('hidden');
        } else {
          totalEl.textContent = String(sourcesModalEntries.length);
          sourcesModalIndex = 0;
          renderSourcesEntry(sourcesModalEntries[0]);
          var showNav = sourcesModalEntries.length > 1;
          prevBtn.classList.toggle('hidden', !showNav);
          nextBtn.classList.toggle('hidden', !showNav);
        }
      })
      .catch(function (err) {
        loadingEl.classList.add('hidden');
        errorEl.textContent = err.message || 'Could not load entries.';
        errorEl.classList.remove('hidden');
      });
  }

  function setupSourcesModal() {
    var modal = document.getElementById('chat-sources-modal');
    var closeBtn = document.getElementById('chat-sources-close');
    var prevBtn = document.getElementById('chat-sources-prev');
    var nextBtn = document.getElementById('chat-sources-next');
    if (!modal) return;

    if (closeBtn) closeBtn.addEventListener('click', function () { modal.close(); });
    modal.addEventListener('click', function (e) { if (e.target === modal) modal.close(); });
    modal.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') modal.close();
      if (sourcesModalEntries.length === 0) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        prevBtn.click();
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        nextBtn.click();
      }
    });

    prevBtn.addEventListener('click', function () {
      if (sourcesModalEntries.length <= 1) return;
      sourcesModalIndex = (sourcesModalIndex - 1 + sourcesModalEntries.length) % sourcesModalEntries.length;
      document.getElementById('chat-sources-current').textContent = String(sourcesModalIndex + 1);
      renderSourcesEntry(sourcesModalEntries[sourcesModalIndex]);
    });

    nextBtn.addEventListener('click', function () {
      if (sourcesModalEntries.length <= 1) return;
      sourcesModalIndex = (sourcesModalIndex + 1) % sourcesModalEntries.length;
      document.getElementById('chat-sources-current').textContent = String(sourcesModalIndex + 1);
      renderSourcesEntry(sourcesModalEntries[sourcesModalIndex]);
    });
  }

  function setupAttachmentPreviewModal() {
    var modal = document.getElementById('chat-attachment-preview-modal');
    var closeBtn = document.getElementById('chat-attachment-preview-close');
    var carouselEl = document.getElementById('chat-attachment-preview-carousel');
    var prevBtn = document.getElementById('chat-attachment-preview-prev');
    var nextBtn = document.getElementById('chat-attachment-preview-next');
    var slidesEl = document.getElementById('chat-attachment-preview-slides');
    if (!modal || !carouselEl || !prevBtn || !nextBtn || !slidesEl) return;

    if (closeBtn) closeBtn.addEventListener('click', function () {
      slidesEl.querySelectorAll('video[data-attachment-video]').forEach(function (v) { v.pause(); });
      modal.close();
    });
    modal.addEventListener('click', function (e) {
      if (e.target === modal) {
        slidesEl.querySelectorAll('video[data-attachment-video]').forEach(function (v) { v.pause(); });
        modal.close();
      }
    });
    prevBtn.addEventListener('click', function () {
      carouselEl.scrollBy({ left: -carouselEl.offsetWidth, behavior: 'smooth' });
    });
    nextBtn.addEventListener('click', function () {
      carouselEl.scrollBy({ left: carouselEl.offsetWidth, behavior: 'smooth' });
    });
  }

  // -----------------------------------------------------------------------
  // Send message
  // -----------------------------------------------------------------------

  function sendMessage(text) {
    if (!text.trim() || isSending) return;

    addUserBubble(text);
    addLoadingBubble();
    setLoading(true);

    fetch('/chat/api/chat/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        'Accept': 'application/json',
      },
      body: JSON.stringify({
        message: text,
        session_id: currentSessionId,
      }),
    })
      .then(parseJsonResponse)
      .then(function (data) {
        removeLoadingBubble();
        if (data.error) {
          addAssistantBubble('Error: ' + data.error, []);
        } else {
          addAssistantBubble(data.answer || '', data.sources || []);
          if (data.session_id) {
            currentSessionId = data.session_id;
          }
        }
      })
      .catch(function (e) {
        removeLoadingBubble();
        var msg = (e && e.error) ? 'Error: ' + e.error : 'Network error. Please try again.';
        addAssistantBubble(msg, []);
      })
      .finally(function () {
        setLoading(false);
      });
  }

  // -----------------------------------------------------------------------
  // Event listeners
  // -----------------------------------------------------------------------

  chatForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = chatInput.value;
    chatInput.value = '';
    autoResizeInput();
    sendMessage(text);
  });

  chatInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event('submit'));
    }
  });

  chatInput.addEventListener('input', autoResizeInput);

  setupSourcesModal();
  setupAttachmentPreviewModal();

  // Init from latest_session
  if (latestSession && latestSession.id) {
    currentSessionId = latestSession.id;
    var msgs = latestSession.messages || [];
    if (msgs.length > 0) {
      messagesEl.innerHTML = '';
      if (welcomeEl) {
        welcomeEl.classList.add('hidden');
        messagesEl.appendChild(welcomeEl);
      }
      for (var i = 0; i < msgs.length; i++) {
        var m = msgs[i];
        if (m.role === 'user') {
          addUserBubble(m.content);
        } else {
          addAssistantBubble(m.content, m.source_entries);
        }
      }
    }
  }

  chatInput.focus();
})();
