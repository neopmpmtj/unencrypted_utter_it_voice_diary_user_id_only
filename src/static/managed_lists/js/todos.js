/**
 * To-Do List JavaScript
 *
 * Handles status filtering, pagination, grouped record cards,
 * record detail modal, create/edit item modal, inline status cycling,
 * checkbox selection, and bulk actions.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // i18n (injected from Django via json_script)
  // ---------------------------------------------------------------------------
  var i18n = JSON.parse((document.getElementById('todos-i18n') || {textContent: '{}'}).textContent || '{}');

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  var currentStatus = 'open';
  var currentPage = 1;
  var totalPages = 1;
  var selectedIds = new Set();
  var editingItemId = null; // null = creating new
  var recordDataCache = {}; // record.id → record object
  var currentRecordData = null; // currently open record in modal

  // ---------------------------------------------------------------------------
  // Priority config
  // ---------------------------------------------------------------------------
  var PRIORITIES = [
    { value: 1, label: i18n.priority_1 || 'Lowest', color: '#6b7280' },
    { value: 2, label: i18n.priority_2 || 'Low',    color: '#3b82f6' },
    { value: 3, label: i18n.priority_3 || 'Medium', color: '#eab308' },
    { value: 4, label: i18n.priority_4 || 'High',   color: '#f97316' },
    { value: 5, label: i18n.priority_5 || 'Urgent', color: '#ef4444' },
  ];

  var STATUS_ICONS = {
    open:        '&#9634;',
    in_progress: '&#9680;',
    on_hold:     '&#9646;&#9646;',
    done:        '&#9745;',
    cancelled:   '&#9746;',
  };

  var STATUS_LABELS = {
    open:        i18n.status_open || 'Open',
    in_progress: i18n.status_in_progress || 'In Progress',
    on_hold:     i18n.status_on_hold || 'On Hold',
    done:        i18n.status_done || 'Done',
    cancelled:   i18n.status_cancelled || 'Cancelled',
  };

  var STATUS_CYCLE = {
    open:        'in_progress',
    in_progress: 'on_hold',
    on_hold:     'done',
    done:        'open',
    cancelled:   'open',
  };

  // ---------------------------------------------------------------------------
  // CSRF helper
  // ---------------------------------------------------------------------------
  function getCsrfToken() {
    var name = 'csrftoken';
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
      var c = cookies[i].trim();
      if (c.startsWith(name + '=')) {
        return decodeURIComponent(c.slice(name.length + 1));
      }
    }
    return '';
  }

  function apiFetch(url, options) {
    options = options || {};
    options.credentials = 'same-origin';
    options.headers = Object.assign({ 'X-CSRFToken': getCsrfToken() }, options.headers || {});
    if (options.body && typeof options.body !== 'string') {
      options.body = JSON.stringify(options.body);
      options.headers['Content-Type'] = 'application/json';
    }
    return fetch(url, options);
  }

  // ---------------------------------------------------------------------------
  // DOM references (set in init)
  // ---------------------------------------------------------------------------
  var todosListEl, loadingEl, emptyEl, errorEl, paginationEl;
  var prevBtn, nextBtn, pageInfoEl;
  var bulkBarEl, bulkCountEl, bulkStatusSel, bulkPrioritySel, bulkDeleteBtn;
  var addTaskBtn, taskModal, taskForm;
  var modalTitle, fieldText, fieldDesc, fieldDueDate, fieldTopic, fieldStatus;
  var modalCloseBtn, modalCancelBtn, modalDeleteBtn, priorityRadiosEl;
  // Record modal
  var recordModal, recordModalTitle, recordModalContext, recordModalSummary;
  var recordModalItemsList, recordModalCloseBtn, recordModalDoneBtn, recordModalDeleteBtn;

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  function init() {
    todosListEl    = document.getElementById('todos-list');
    loadingEl      = document.getElementById('loading-state');
    emptyEl        = document.getElementById('empty-state');
    errorEl        = document.getElementById('error-state');
    paginationEl   = document.getElementById('pagination');
    prevBtn        = document.getElementById('prev-page-btn');
    nextBtn        = document.getElementById('next-page-btn');
    pageInfoEl     = document.getElementById('page-info');
    bulkBarEl      = document.getElementById('bulk-bar');
    bulkCountEl    = document.getElementById('bulk-count');
    bulkStatusSel  = document.getElementById('bulk-status-select');
    bulkPrioritySel = document.getElementById('bulk-priority-select');
    bulkDeleteBtn  = document.getElementById('bulk-delete-btn');
    addTaskBtn     = document.getElementById('add-task-btn');
    taskModal      = document.getElementById('task-modal');
    taskForm       = document.getElementById('task-form');
    modalTitle     = document.getElementById('modal-title');
    fieldText      = document.getElementById('field-text');
    fieldDesc      = document.getElementById('field-description');
    fieldDueDate   = document.getElementById('field-due-date');
    fieldTopic     = document.getElementById('field-topic');
    fieldStatus    = document.getElementById('field-status');
    modalCloseBtn  = document.getElementById('modal-close-btn');
    modalCancelBtn = document.getElementById('modal-cancel-btn');
    modalDeleteBtn = document.getElementById('modal-delete-btn');
    priorityRadiosEl = document.getElementById('priority-radios');

    recordModal          = document.getElementById('record-modal');
    recordModalTitle     = document.getElementById('record-modal-title');
    recordModalContext   = document.getElementById('record-modal-context');
    recordModalSummary   = document.getElementById('record-modal-summary');
    recordModalItemsList = document.getElementById('record-modal-items');
    recordModalCloseBtn  = document.getElementById('record-modal-close-btn');
    recordModalDoneBtn   = document.getElementById('record-modal-done-btn');
    recordModalDeleteBtn = document.getElementById('record-modal-delete-btn');

    buildPriorityRadios();
    setupEventListeners();
    loadTodos();
  }

  // ---------------------------------------------------------------------------
  // Priority radios (JS-rendered)
  // ---------------------------------------------------------------------------
  function buildPriorityRadios() {
    if (!priorityRadiosEl) return;
    priorityRadiosEl.innerHTML = '';
    PRIORITIES.forEach(function (p) {
      var lbl = document.createElement('label');
      lbl.className = 'cursor-pointer priority-radio-label';
      lbl.title = p.label;
      var inp = document.createElement('input');
      inp.type = 'radio';
      inp.name = 'priority';
      inp.value = p.value;
      inp.className = 'sr-only';
      if (p.value === 3) inp.checked = true;
      var span = document.createElement('span');
      span.className = 'priority-dot inline-flex items-center justify-center h-7 w-7 rounded-full border-2 text-xs font-bold transition-all select-none';
      span.style.borderColor = p.color + '66';
      span.style.color = p.color;
      span.textContent = p.value;
      lbl.addEventListener('click', function () {
        priorityRadiosEl.querySelectorAll('.priority-dot').forEach(function (s) {
          s.style.backgroundColor = '';
          s.style.color = s.parentElement.querySelector('input').dataset.color;
        });
        span.style.backgroundColor = p.color + '33';
      });
      inp.dataset.color = p.color;
      lbl.appendChild(inp);
      lbl.appendChild(span);
      priorityRadiosEl.appendChild(lbl);
    });
  }

  function getSelectedPriority() {
    var inp = priorityRadiosEl ? priorityRadiosEl.querySelector('input[name="priority"]:checked') : null;
    return inp ? parseInt(inp.value, 10) : 3;
  }

  function setSelectedPriority(val) {
    if (!priorityRadiosEl) return;
    priorityRadiosEl.querySelectorAll('input[name="priority"]').forEach(function (inp) {
      inp.checked = (parseInt(inp.value, 10) === parseInt(val, 10));
      var dot = inp.nextElementSibling;
      var color = inp.dataset.color;
      if (inp.checked) {
        dot.style.backgroundColor = color + '33';
      } else {
        dot.style.backgroundColor = '';
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Event listeners
  // ---------------------------------------------------------------------------
  function setupEventListeners() {
    // Status tabs
    document.querySelectorAll('.status-tab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        currentStatus = btn.dataset.status;
        currentPage = 1;
        selectedIds.clear();
        updateBulkBar();
        document.querySelectorAll('.status-tab').forEach(function (b) {
          b.classList.remove('vd-btn-accent');
          b.classList.add('vd-btn-ghost');
        });
        btn.classList.add('vd-btn-accent');
        btn.classList.remove('vd-btn-ghost');
        loadTodos();
      });
    });

    // Pagination
    if (prevBtn) prevBtn.addEventListener('click', function () { if (currentPage > 1) { currentPage--; loadTodos(); } });
    if (nextBtn) nextBtn.addEventListener('click', function () { if (currentPage < totalPages) { currentPage++; loadTodos(); } });

    // Bulk actions
    if (bulkStatusSel) bulkStatusSel.addEventListener('change', function () {
      var val = bulkStatusSel.value;
      if (!val) return;
      bulkStatusSel.value = '';
      bulkAction('status', val);
    });
    if (bulkPrioritySel) bulkPrioritySel.addEventListener('change', function () {
      var val = bulkPrioritySel.value;
      if (!val) return;
      bulkPrioritySel.value = '';
      bulkAction('priority', parseInt(val, 10));
    });
    if (bulkDeleteBtn) bulkDeleteBtn.addEventListener('click', function () {
      var msg = (i18n.delete_tasks_confirm || 'Delete %(count)s task(s)?').replace('%(count)s', selectedIds.size);
      if (!confirm(msg)) return;
      bulkAction('delete', null);
    });

    // Add task button
    if (addTaskBtn) addTaskBtn.addEventListener('click', openCreateModal);

    // Task modal close / delete
    if (modalCloseBtn) modalCloseBtn.addEventListener('click', closeModal);
    if (modalCancelBtn) modalCancelBtn.addEventListener('click', closeModal);
    if (modalDeleteBtn) modalDeleteBtn.addEventListener('click', function () {
      if (!editingItemId || !confirm(i18n.delete_task_confirm || 'Delete this task?')) return;
      deleteItemAndRefreshModal(editingItemId);
      closeModal();
    });
    if (taskModal) taskModal.addEventListener('click', function (e) {
      if (e.target === taskModal) closeModal();
    });

    // Task form submit
    if (taskForm) taskForm.addEventListener('submit', function (e) {
      e.preventDefault();
      saveItem();
    });

    // Record modal close
    if (recordModalCloseBtn) recordModalCloseBtn.addEventListener('click', closeRecordModal);
    if (recordModalDoneBtn) recordModalDoneBtn.addEventListener('click', closeRecordModal);
    if (recordModal) recordModal.addEventListener('click', function (e) {
      if (e.target === recordModal) closeRecordModal();
    });

    // Record modal delete
    if (recordModalDeleteBtn) recordModalDeleteBtn.addEventListener('click', function () {
      if (!currentRecordData) return;
      if (!confirm(i18n.delete_list_confirm || 'Delete this entire list and all its tasks?')) return;
      var url = window.TODOS_URLS.record.replace('{id}', currentRecordData.id);
      apiFetch(url, { method: 'DELETE' })
        .then(function (r) { if (!r.ok) throw new Error(); })
        .then(function () { closeRecordModal(); loadTodos(); })
        .catch(function () { alert(i18n.failed_delete_list || 'Failed to delete list.'); });
    });
  }

  // ---------------------------------------------------------------------------
  // Load & render
  // ---------------------------------------------------------------------------
  function loadTodos() {
    showState('loading');
    var url = window.TODOS_URLS.list + '?status=' + encodeURIComponent(currentStatus) + '&page=' + currentPage;
    apiFetch(url)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        totalPages = data.total_pages || 1;
        renderStatusCounts(data.status_counts || {});
        renderPagination(data.page, data.total_pages, data.total_records);

        // Update cache with fresh record data
        if (data.records) {
          data.records.forEach(function (rec) {
            recordDataCache[rec.id] = rec;
          });
        }

        if (!data.records || data.records.length === 0) {
          showState('empty');
          return;
        }
        showState('list');
        todosListEl.innerHTML = '';
        data.records.forEach(function (record) {
          todosListEl.insertAdjacentHTML('beforeend', renderRecord(record));
        });
        bindRecordEvents();

        // If record modal is open, refresh its items from updated cache
        if (currentRecordData && recordDataCache[currentRecordData.id]) {
          currentRecordData = recordDataCache[currentRecordData.id];
          renderRecordModalItems(currentRecordData.items);
          renderRecordModalSummary(currentRecordData.status_counts);
        }
      })
      .catch(function () { showState('error'); });
  }

  // ---------------------------------------------------------------------------
  // Record card rendering
  // ---------------------------------------------------------------------------
  function renderRecord(record) {
    var isMulti = record.item_count > 1;
    var allItemIds = (record.items || []).map(function (i) { return i.id; });
    var allSelected = allItemIds.length > 0 && allItemIds.every(function (id) { return selectedIds.has(id); });

    // Status summary text
    var openCount = record.status_counts.open || 0;
    var doneCount = record.status_counts.done || 0;
    var summaryParts = [];
    if (openCount > 0) summaryParts.push(openCount + ' ' + (i18n.status_summary_open || 'open'));
    if (doneCount > 0) summaryParts.push(doneCount + ' ' + (i18n.status_summary_done || 'done'));
    var inProg = record.status_counts.in_progress || 0;
    var onHold = record.status_counts.on_hold || 0;
    var cancelled = record.status_counts.cancelled || 0;
    if (inProg > 0) summaryParts.push(inProg + ' ' + (i18n.status_summary_in_progress || 'in progress'));
    if (onHold > 0) summaryParts.push(onHold + ' ' + (i18n.status_summary_on_hold || 'on hold'));
    if (cancelled > 0) summaryParts.push(cancelled + ' ' + (i18n.status_summary_cancelled || 'cancelled'));
    var itemLabel = record.item_count !== 1 ? (i18n.items || 'items') : (i18n.item || 'item');
    var summaryText = summaryParts.join(' · ') || record.item_count + ' ' + itemLabel;

    // Preview items (first 3)
    var displayItems = record.items || [];
    var previewItems = displayItems.slice(0, 3);
    var moreCount = displayItems.length - previewItems.length;

    var previewHtml = '';
    previewItems.forEach(function (item) {
      var isDone = item.completion_status === 'done' || item.completion_status === 'cancelled';
      var priInfo = PRIORITIES.find(function (p) { return p.value === item.priority; }) || PRIORITIES[2];
      previewHtml += '<div class="flex items-center gap-1.5 py-0.5">' +
        '<span class="text-sm leading-none flex-shrink-0">' + getStatusIcon(item.completion_status) + '</span>' +
        '<span class="w-2 h-2 rounded-full flex-shrink-0" style="background-color:' + priInfo.color + '"></span>' +
        '<span class="text-xs text-foreground truncate' + (isDone ? ' line-through text-muted-foreground' : '') + '">' +
          escHtml(item.text) +
        '</span>' +
      '</div>';
    });
    if (moreCount > 0) {
      previewHtml += '<div class="text-xs text-muted-foreground pt-0.5">+' + moreCount + ' ' + (i18n.more || 'more') + '</div>';
    }

    var shadowLayers = '';
    if (isMulti) {
      shadowLayers =
        '<div class="absolute inset-x-2 -bottom-1 h-full bg-card border border-border rounded-xl opacity-60 -z-10 pointer-events-none"></div>' +
        '<div class="absolute inset-x-4 -bottom-2 h-full bg-card border border-border rounded-xl opacity-30 -z-20 pointer-events-none"></div>';
    }

    var manualBadge = record.is_manual
      ? '<span class="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">' + escHtml(i18n.manual || 'Manual') + '</span>'
      : '';

    return '<li class="todo-record-card flex items-start gap-2 mb-4" data-record-id="' + record.id + '">' +
      '<input type="checkbox" class="record-checkbox mt-1.5 h-4 w-4 rounded border-border cursor-pointer flex-shrink-0"' +
        (allSelected ? ' checked' : '') + '>' +
      '<div class="flex-1 relative">' +
        shadowLayers +
        '<div class="vd-card p-3 relative z-10 cursor-pointer hover:shadow-md transition-all record-card-trigger">' +
          '<div class="flex items-start gap-2">' +
            '<div class="flex-1 min-w-0">' +
              '<div class="flex items-center gap-2 flex-wrap">' +
                '<span class="text-sm font-medium text-foreground truncate">' + escHtml(record.name) + '</span>' +
                '<span class="text-xs text-muted-foreground flex-shrink-0">' + record.item_count + ' ' + (record.item_count !== 1 ? (i18n.items || 'items') : (i18n.item || 'item')) + '</span>' +
                manualBadge +
              '</div>' +
              '<p class="text-xs text-muted-foreground mt-0.5">' + escHtml(summaryText) + '</p>' +
              (previewHtml ? '<div class="mt-2 space-y-0">' + previewHtml + '</div>' : '') +
            '</div>' +
            '<span class="text-muted-foreground flex-shrink-0 self-center pl-1">&#8250;</span>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</li>';
  }

  function bindRecordEvents() {
    if (!todosListEl) return;

    todosListEl.querySelectorAll('.record-card-trigger').forEach(function (card) {
      card.addEventListener('click', function (e) {
        // Ignore clicks on the checkbox
        if (e.target.classList.contains('record-checkbox') || e.target.closest('.record-checkbox')) return;
        var li = card.closest('li[data-record-id]');
        var id = li ? li.dataset.recordId : null;
        if (id && recordDataCache[id]) {
          openRecordModal(recordDataCache[id]);
        }
      });
    });

    todosListEl.querySelectorAll('.record-checkbox').forEach(function (cb) {
      cb.addEventListener('change', function (e) {
        e.stopPropagation();
        var li = cb.closest('li[data-record-id]');
        var id = li ? li.dataset.recordId : null;
        if (!id || !recordDataCache[id]) return;
        var itemIds = (recordDataCache[id].items || []).map(function (i) { return i.id; });
        if (cb.checked) {
          itemIds.forEach(function (iid) { selectedIds.add(iid); });
        } else {
          itemIds.forEach(function (iid) { selectedIds.delete(iid); });
        }
        updateBulkBar();
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Record modal
  // ---------------------------------------------------------------------------
  function openRecordModal(record) {
    currentRecordData = record;
    if (recordModalTitle) recordModalTitle.textContent = record.name || (i18n.untitled_list || 'Untitled List');
    if (recordModalContext) {
      if (record.context) {
        recordModalContext.textContent = record.context;
        recordModalContext.classList.remove('hidden');
      } else {
        recordModalContext.classList.add('hidden');
      }
    }
    renderRecordModalSummary(record.status_counts);
    renderRecordModalItems(record.items || []);
    if (recordModal) recordModal.showModal();
  }

  function renderRecordModalSummary(statusCounts) {
    if (!recordModalSummary) return;
    var chips = '';
    Object.keys(STATUS_LABELS).forEach(function (key) {
      var cnt = statusCounts[key] || 0;
      if (cnt === 0) return;
      chips += '<span class="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">' +
        escHtml(STATUS_LABELS[key]) + ': ' + cnt +
      '</span>';
    });
    recordModalSummary.innerHTML = chips || '<span class="text-xs text-muted-foreground">' + escHtml(i18n.no_items || 'No items') + '</span>';
  }

  function renderRecordModalItems(items) {
    if (!recordModalItemsList) return;
    if (!items || items.length === 0) {
      recordModalItemsList.innerHTML = '<li class="px-5 py-6 text-center text-sm text-muted-foreground">' + escHtml(i18n.no_items_to_show || 'No items to show.') + '</li>';
      return;
    }
    var html = '';
    items.forEach(function (item) {
      var isDone = item.completion_status === 'done' || item.completion_status === 'cancelled';
      var priInfo = PRIORITIES.find(function (p) { return p.value === item.priority; }) || PRIORITIES[2];
      var isSelected = selectedIds.has(item.id);
      var statusTitle = STATUS_LABELS[item.completion_status] || item.completion_status;
      var nextStatus = STATUS_CYCLE[item.completion_status] || 'open';

      var dueStr = item.due_date
        ? '<span class="text-xs text-muted-foreground ml-1">&#128197; ' + escHtml(item.due_date) + '</span>'
        : '';

      html += '<li class="modal-item-row flex items-start gap-3 px-5 py-3 hover:bg-muted/30 transition-colors cursor-pointer" data-item-id="' + item.id + '">' +
        '<input type="checkbox" class="modal-item-checkbox mt-0.5 h-4 w-4 rounded border-border cursor-pointer flex-shrink-0"' +
          (isSelected ? ' checked' : '') + '>' +
        '<button type="button" class="modal-status-toggle mt-0.5 text-lg leading-none flex-shrink-0 hover:opacity-70 transition-opacity" ' +
          'title="' + escHtml(statusTitle) + '" data-next-status="' + nextStatus + '">' +
          getStatusIcon(item.completion_status) +
        '</button>' +
        '<div class="flex-1 min-w-0">' +
          '<div class="flex items-start gap-2">' +
            '<span class="w-2 h-2 rounded-full mt-1.5 flex-shrink-0" style="background-color:' + priInfo.color + '" title="' + escHtml((i18n.priority_title || 'Priority') + ' ' + item.priority) + '"></span>' +
            '<span class="text-sm text-foreground break-words' + (isDone ? ' line-through text-muted-foreground' : '') + '">' +
              escHtml(item.text) +
            '</span>' +
          '</div>' +
          (dueStr ? '<div class="pl-4 mt-0.5">' + dueStr + '</div>' : '') +
        '</div>' +
        '<span class="text-muted-foreground flex-shrink-0 self-center pl-1 text-xs">&#8250;</span>' +
      '</li>';
    });
    recordModalItemsList.innerHTML = html;
    bindRecordModalItemEvents();
  }

  function bindRecordModalItemEvents() {
    if (!recordModalItemsList) return;

    recordModalItemsList.querySelectorAll('.modal-item-checkbox').forEach(function (cb) {
      cb.addEventListener('change', function () {
        var li = cb.closest('li[data-item-id]');
        var id = li ? li.dataset.itemId : null;
        if (!id) return;
        if (cb.checked) selectedIds.add(id); else selectedIds.delete(id);
        updateBulkBar();
      });
    });

    recordModalItemsList.querySelectorAll('.modal-status-toggle').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var li = btn.closest('li[data-item-id]');
        var id = li ? li.dataset.itemId : null;
        var nextStatus = btn.dataset.nextStatus || 'open';
        if (id) cycleStatusAndRefreshModal(id, nextStatus);
      });
    });

    recordModalItemsList.querySelectorAll('.modal-item-row').forEach(function (row) {
      row.addEventListener('click', function (e) {
        if (e.target.classList.contains('modal-item-checkbox') || e.target.closest('.modal-item-checkbox')) return;
        if (e.target.classList.contains('modal-status-toggle') || e.target.closest('.modal-status-toggle')) return;
        var id = row.dataset.itemId;
        if (id) openEditModal(id);
      });
    });
  }

  function cycleStatusAndRefreshModal(id, nextStatus) {
    var url = window.TODOS_URLS.item.replace('{id}', id);
    apiFetch(url, { method: 'PATCH', body: { completion_status: nextStatus } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (updatedItem) {
        if (currentRecordData) {
          // Update the item in currentRecordData.items in memory
          currentRecordData.items = currentRecordData.items.map(function (i) {
            return i.id === id ? updatedItem : i;
          });
          // Recalculate status_counts
          var counts = {};
          Object.keys(STATUS_LABELS).forEach(function (k) { counts[k] = 0; });
          // Use cached record's full item list to update counts accurately
          if (recordDataCache[currentRecordData.id]) {
            recordDataCache[currentRecordData.id].items = recordDataCache[currentRecordData.id].items.map(function (i) {
              return i.id === id ? updatedItem : i;
            });
          }
          currentRecordData.items.forEach(function (i) {
            counts[i.completion_status] = (counts[i.completion_status] || 0) + 1;
          });
          currentRecordData.status_counts = counts;
          renderRecordModalItems(currentRecordData.items);
          renderRecordModalSummary(currentRecordData.status_counts);
        }
        loadTodos(); // Background refresh
      })
        .catch(function () { alert(i18n.failed_update_status || 'Failed to update status.'); });
  }

  function deleteItemAndRefreshModal(id) {
    var url = window.TODOS_URLS.item.replace('{id}', id);
    apiFetch(url, { method: 'DELETE' })
      .then(function (r) {
        if (!r.ok) throw new Error();
        selectedIds.delete(id);
        updateBulkBar();
        if (currentRecordData) {
          currentRecordData.items = currentRecordData.items.filter(function (i) { return i.id !== id; });
          if (currentRecordData.items.length === 0) {
            closeRecordModal();
          } else {
            renderRecordModalItems(currentRecordData.items);
            renderRecordModalSummary(currentRecordData.status_counts);
          }
        }
        loadTodos();
      })
      .catch(function () { alert(i18n.failed_delete_task || 'Failed to delete task.'); });
  }

  function closeRecordModal() {
    if (recordModal) recordModal.close();
    currentRecordData = null;
  }

  // ---------------------------------------------------------------------------
  // Status counts
  // ---------------------------------------------------------------------------
  function renderStatusCounts(counts) {
    document.querySelectorAll('.status-count').forEach(function (span) {
      var forStatus = span.dataset.for;
      var count = 0;
      if (forStatus === 'all') {
        count = Object.values(counts).reduce(function (a, b) { return a + b; }, 0);
      } else {
        count = counts[forStatus] || 0;
      }
      if (count > 0) {
        span.textContent = '(' + count + ')';
        span.classList.remove('hidden');
      } else {
        span.classList.add('hidden');
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Pagination
  // ---------------------------------------------------------------------------
  function renderPagination(page, total, totalItems) {
    if (total <= 1) {
      paginationEl && paginationEl.classList.add('hidden');
      return;
    }
    paginationEl && paginationEl.classList.remove('hidden');
    if (pageInfoEl) pageInfoEl.textContent = (i18n.page_of || 'Page %(page)s of %(total)s (%(count)s lists)')
      .replace('%(page)s', page).replace('%(total)s', total).replace('%(count)s', totalItems || 0);
    if (prevBtn) prevBtn.disabled = (page <= 1);
    if (nextBtn) nextBtn.disabled = (page >= total);
  }

  // ---------------------------------------------------------------------------
  // Bulk bar
  // ---------------------------------------------------------------------------
  function updateBulkBar() {
    if (!bulkBarEl) return;
    if (selectedIds.size === 0) {
      bulkBarEl.classList.add('hidden');
      bulkBarEl.classList.remove('flex');
    } else {
      bulkBarEl.classList.remove('hidden');
      bulkBarEl.classList.add('flex');
      if (bulkCountEl) bulkCountEl.textContent = selectedIds.size + ' ' + (i18n.selected || 'selected');
    }
  }

  // ---------------------------------------------------------------------------
  // Show/hide state panels
  // ---------------------------------------------------------------------------
  function showState(state) {
    loadingEl && loadingEl.classList.add('hidden');
    emptyEl   && emptyEl.classList.add('hidden');
    errorEl   && errorEl.classList.add('hidden');
    if (todosListEl) todosListEl.innerHTML = '';
    paginationEl && paginationEl.classList.add('hidden');

    if (state === 'loading') loadingEl && loadingEl.classList.remove('hidden');
    else if (state === 'empty') emptyEl && emptyEl.classList.remove('hidden');
    else if (state === 'error') errorEl && errorEl.classList.remove('hidden');
  }

  // ---------------------------------------------------------------------------
  // Task create/edit modal
  // ---------------------------------------------------------------------------
  function openCreateModal() {
    editingItemId = null;
    if (modalTitle) modalTitle.textContent = i18n.new_task || 'New Task';
    if (taskForm) taskForm.reset();
    setSelectedPriority(3);
    if (fieldStatus) fieldStatus.value = 'open';
    if (modalDeleteBtn) modalDeleteBtn.classList.add('hidden');
    if (taskModal) taskModal.showModal();
    if (fieldText) fieldText.focus();
  }

  function openEditModal(id) {
    var url = window.TODOS_URLS.item.replace('{id}', id);
    apiFetch(url)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (item) {
        editingItemId = id;
        if (modalTitle) modalTitle.textContent = i18n.edit_task || 'Edit Task';
        if (fieldText) fieldText.value = item.text || '';
        if (fieldDesc) fieldDesc.value = item.description || '';
        if (fieldDueDate) fieldDueDate.value = item.due_date || '';
        if (fieldTopic) fieldTopic.value = item.topic || '';
        if (fieldStatus) fieldStatus.value = item.completion_status || 'open';
        setSelectedPriority(item.priority || 3);
        if (modalDeleteBtn) modalDeleteBtn.classList.remove('hidden');
        if (taskModal) taskModal.showModal();
        if (fieldText) fieldText.focus();
      })
      .catch(function () { alert(i18n.failed_load_task || 'Failed to load task.'); });
  }

  function closeModal() {
    if (taskModal) taskModal.close();
    editingItemId = null;
  }

  function saveItem() {
    var text = fieldText ? fieldText.value.trim() : '';
    if (!text) { if (fieldText) fieldText.focus(); return; }

    var payload = {
      text: text,
      description: fieldDesc ? fieldDesc.value.trim() : '',
      priority: getSelectedPriority(),
      due_date: fieldDueDate ? fieldDueDate.value : '',
      topic: fieldTopic ? fieldTopic.value.trim() : '',
      completion_status: fieldStatus ? fieldStatus.value : 'open',
    };

    var url, method;
    if (editingItemId) {
      url = window.TODOS_URLS.item.replace('{id}', editingItemId);
      method = 'PATCH';
    } else {
      url = window.TODOS_URLS.create;
      method = 'POST';
    }

    var saveBtn = document.getElementById('modal-save-btn');
    if (saveBtn) saveBtn.disabled = true;

    apiFetch(url, { method: method, body: payload })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function (updatedItem) {
        closeModal();
        // If record modal is open and this was an edit, update in-memory data
        if (currentRecordData && editingItemId && updatedItem) {
          currentRecordData.items = currentRecordData.items.map(function (i) {
            return i.id === editingItemId ? updatedItem : i;
          });
          renderRecordModalItems(currentRecordData.items);
        }
        loadTodos();
      })
      .catch(function (err) {
        alert(typeof err === 'string' ? err : (i18n.failed_save_task || 'Failed to save task.'));
      })
      .finally(function () {
        if (saveBtn) saveBtn.disabled = false;
      });
  }

  // ---------------------------------------------------------------------------
  // Bulk actions
  // ---------------------------------------------------------------------------
  function bulkAction(action, value) {
    var ids = Array.from(selectedIds);
    if (!ids.length) return;
    var payload = { action: action, item_ids: ids };
    if (value !== null && value !== undefined) payload.value = value;

    apiFetch(window.TODOS_URLS.bulk, { method: 'POST', body: payload })
      .then(function (r) { if (!r.ok) throw new Error(); selectedIds.clear(); updateBulkBar(); loadTodos(); })
      .catch(function () { alert(i18n.bulk_action_failed || 'Bulk action failed.'); });
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  function getStatusIcon(status) {
    var icons = {
      open:        '<span style="font-size:1.1em">&#9634;</span>',
      in_progress: '<span style="font-size:1.1em">&#9680;</span>',
      on_hold:     '<span style="font-size:0.85em;letter-spacing:-1px">&#9646;&#9646;</span>',
      done:        '<span style="font-size:1.1em">&#9745;</span>',
      cancelled:   '<span style="font-size:1.1em">&#9746;</span>',
    };
    return icons[status] || icons.open;
  }

  function escHtml(str) {
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(str || ''));
    return d.innerHTML;
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
