/**
 * My Lists JavaScript
 *
 * Handles pagination, grouped record cards, record detail modal,
 * create list modal, inline item add/edit/delete.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // i18n strings (injected from Django via json_script)
  // ---------------------------------------------------------------------------
  var i18n = JSON.parse((document.getElementById('lists-i18n') || {textContent: '{}'}).textContent || '{}');

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  var currentPage = 1;
  var totalPages = 1;
  var recordDataCache = {}; // record.id → record object
  var currentRecordId = null; // currently open record in detail modal
  var editingItemId = null;   // item being edited in item-modal

  // ---------------------------------------------------------------------------
  // CSRF helper
  // ---------------------------------------------------------------------------
  function getCsrfToken() {
    var name = 'csrftoken';
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
      var c = cookies[i].trim();
      if (c.startsWith(name + '=')) return decodeURIComponent(c.slice(name.length + 1));
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
  var listsListEl, loadingEl, emptyEl, errorEl, paginationEl, prevBtn, nextBtn, pageInfoEl;
  var addListBtn;
  // Create modal
  var createModal, createForm, createName, createContext, createItemsContainer, addItemRowBtn;
  var createModalCloseBtn, createModalCancelBtn, createModalSaveBtn;
  // Record detail modal
  var recordModal, recordModalName, recordModalContext, recordModalSaveMetaBtn;
  var recordModalDeleteBtn, recordModalCloseBtn;
  var recordModalItemsList, recordModalNewText, recordModalNewQty, recordModalNewUnit, recordModalAddItemBtn;
  // Item edit modal
  var itemModal, itemForm, itemFieldText, itemFieldDesc, itemFieldDueDate, itemFieldQty, itemFieldUnit;
  var itemModalCloseBtn, itemModalCancelBtn, itemModalDeleteBtn, itemModalSaveBtn;

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  function init() {
    listsListEl  = document.getElementById('lists-list');
    loadingEl    = document.getElementById('loading-state');
    emptyEl      = document.getElementById('empty-state');
    errorEl      = document.getElementById('error-state');
    paginationEl = document.getElementById('pagination');
    prevBtn      = document.getElementById('prev-page-btn');
    nextBtn      = document.getElementById('next-page-btn');
    pageInfoEl   = document.getElementById('page-info');

    addListBtn = document.getElementById('add-list-btn');

    createModal          = document.getElementById('create-modal');
    createForm           = document.getElementById('create-form');
    createName           = document.getElementById('create-name');
    createContext        = document.getElementById('create-context');
    createItemsContainer = document.getElementById('create-items-container');
    addItemRowBtn        = document.getElementById('add-item-row-btn');
    createModalCloseBtn  = document.getElementById('create-modal-close-btn');
    createModalCancelBtn = document.getElementById('create-modal-cancel-btn');
    createModalSaveBtn   = document.getElementById('create-modal-save-btn');

    recordModal           = document.getElementById('record-modal');
    recordModalName       = document.getElementById('record-modal-name');
    recordModalContext    = document.getElementById('record-modal-context');
    recordModalSaveMetaBtn = document.getElementById('record-modal-save-meta-btn');
    recordModalDeleteBtn  = document.getElementById('record-modal-delete-btn');
    recordModalCloseBtn   = document.getElementById('record-modal-close-btn');
    recordModalItemsList  = document.getElementById('record-modal-items');
    recordModalNewText    = document.getElementById('record-modal-new-item-text');
    recordModalNewQty     = document.getElementById('record-modal-new-item-qty');
    recordModalNewUnit    = document.getElementById('record-modal-new-item-unit');
    recordModalAddItemBtn = document.getElementById('record-modal-add-item-btn');

    itemModal          = document.getElementById('item-modal');
    itemForm           = document.getElementById('item-form');
    itemFieldText      = document.getElementById('item-field-text');
    itemFieldDesc      = document.getElementById('item-field-description');
    itemFieldDueDate   = document.getElementById('item-field-due-date');
    itemFieldQty       = document.getElementById('item-field-quantity');
    itemFieldUnit      = document.getElementById('item-field-unit');
    itemModalCloseBtn  = document.getElementById('item-modal-close-btn');
    itemModalCancelBtn = document.getElementById('item-modal-cancel-btn');
    itemModalDeleteBtn = document.getElementById('item-modal-delete-btn');
    itemModalSaveBtn   = document.getElementById('item-modal-save-btn');

    setupEventListeners();
    loadLists();
  }

  // ---------------------------------------------------------------------------
  // Event listeners
  // ---------------------------------------------------------------------------
  function setupEventListeners() {
    if (prevBtn) prevBtn.addEventListener('click', function () { if (currentPage > 1) { currentPage--; loadLists(); } });
    if (nextBtn) nextBtn.addEventListener('click', function () { if (currentPage < totalPages) { currentPage++; loadLists(); } });

    if (addListBtn) addListBtn.addEventListener('click', openCreateModal);

    // Create modal
    if (createModalCloseBtn) createModalCloseBtn.addEventListener('click', closeCreateModal);
    if (createModalCancelBtn) createModalCancelBtn.addEventListener('click', closeCreateModal);
    if (createModal) createModal.addEventListener('click', function (e) { if (e.target === createModal) closeCreateModal(); });
    if (addItemRowBtn) addItemRowBtn.addEventListener('click', function () { addItemRow(); });
    if (createForm) createForm.addEventListener('submit', function (e) { e.preventDefault(); submitCreateList(); });

    // Record modal
    if (recordModalCloseBtn) recordModalCloseBtn.addEventListener('click', closeRecordModal);
    if (recordModal) recordModal.addEventListener('click', function (e) { if (e.target === recordModal) closeRecordModal(); });

    if (recordModalName) {
      recordModalName.addEventListener('input', function () {
        if (recordModalSaveMetaBtn) recordModalSaveMetaBtn.classList.remove('hidden');
      });
    }
    if (recordModalContext) {
      recordModalContext.addEventListener('input', function () {
        if (recordModalSaveMetaBtn) recordModalSaveMetaBtn.classList.remove('hidden');
      });
    }
    if (recordModalSaveMetaBtn) {
      recordModalSaveMetaBtn.addEventListener('click', saveRecordMeta);
    }
    if (recordModalDeleteBtn) {
      recordModalDeleteBtn.addEventListener('click', function () {
        if (!currentRecordId) return;
        if (!confirm(i18n.delete_list_confirm || 'Delete this list?')) return;
        var url = window.LISTS_URLS.record.replace('{id}', currentRecordId);
        apiFetch(url, { method: 'DELETE' })
          .then(function (r) { if (!r.ok) throw new Error(); closeRecordModal(); loadLists(); })
          .catch(function () { alert(i18n.delete_list_failed || 'Failed to delete list.'); });
      });
    }
    if (recordModalAddItemBtn) {
      recordModalAddItemBtn.addEventListener('click', addItemToCurrentRecord);
    }

    // Item modal
    if (itemModalCloseBtn) itemModalCloseBtn.addEventListener('click', closeItemModal);
    if (itemModalCancelBtn) itemModalCancelBtn.addEventListener('click', closeItemModal);
    if (itemModal) itemModal.addEventListener('click', function (e) { if (e.target === itemModal) closeItemModal(); });
    if (itemModalDeleteBtn) {
      itemModalDeleteBtn.addEventListener('click', function () {
        if (!editingItemId) return;
        if (!confirm(i18n.delete_item_confirm || 'Delete this item?')) return;
        deleteItem(editingItemId);
      });
    }
    if (itemForm) itemForm.addEventListener('submit', function (e) { e.preventDefault(); saveItemEdit(); });
  }

  // ---------------------------------------------------------------------------
  // Load & render
  // ---------------------------------------------------------------------------
  function loadLists() {
    showState('loading');
    var url = window.LISTS_URLS.list + '?page=' + currentPage;
    apiFetch(url)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        totalPages = data.total_pages || 1;
        renderPagination(data.page, data.total_pages, data.total_records);

        if (data.records) {
          data.records.forEach(function (rec) { recordDataCache[rec.id] = rec; });
        }

        if (!data.records || data.records.length === 0) {
          showState('empty');
          return;
        }
        showState('list');
        listsListEl.innerHTML = '';
        data.records.forEach(function (record) {
          listsListEl.insertAdjacentHTML('beforeend', renderRecordCard(record));
        });
        bindCardEvents();

        // Refresh open record modal if applicable
        if (currentRecordId && recordDataCache[currentRecordId]) {
          renderRecordModalItems(recordDataCache[currentRecordId].items || []);
        }
      })
      .catch(function () { showState('error'); });
  }

  // ---------------------------------------------------------------------------
  // Record card rendering
  // ---------------------------------------------------------------------------
  function renderRecordCard(record) {
    var previewItems = (record.items || []).slice(0, 3);
    var moreCount = (record.item_count || 0) - previewItems.length;
    if (moreCount < 0) moreCount = 0;

    var previewHtml = '';
    previewItems.forEach(function (item) {
      var qtyBadge = '';
      if (item.quantity || item.unit) {
        qtyBadge = '<span class="ml-1 text-xs bg-accent/10 text-accent px-1.5 py-0.5 rounded">' +
          escHtml((item.quantity || '') + (item.unit ? ' ' + item.unit : '')) +
        '</span>';
      }
      previewHtml +=
        '<div class="flex items-center gap-1.5 py-0.5">' +
          '<span class="text-xs text-muted-foreground flex-shrink-0">&#8226;</span>' +
          '<span class="text-xs text-foreground truncate">' + escHtml(item.text) + '</span>' +
          qtyBadge +
        '</div>';
    });
    if (moreCount > 0) {
      previewHtml += '<div class="text-xs text-muted-foreground pt-0.5">+' + moreCount + ' ' + (i18n.more || 'more') + '</div>';
    }

    var manualBadge = record.is_manual
      ? '<span class="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">' + escHtml(i18n.manual || 'Manual') + '</span>'
      : '';

    return '<li class="list-record-card mb-3" data-record-id="' + record.id + '">' +
      '<div class="vd-card p-3 cursor-pointer hover:shadow-md transition-all record-card-trigger">' +
        '<div class="flex items-start gap-2">' +
          '<div class="flex-1 min-w-0">' +
            '<div class="flex items-center gap-2 flex-wrap">' +
              '<span class="text-sm font-medium text-foreground">' + escHtml(record.name) + '</span>' +
              '<span class="text-xs text-muted-foreground">' + record.item_count + ' ' + (record.item_count !== 1 ? (i18n.items || 'items') : (i18n.item || 'item')) + '</span>' +
              manualBadge +
            '</div>' +
            (record.context ? '<p class="text-xs text-muted-foreground mt-0.5">' + escHtml(record.context) + '</p>' : '') +
            (previewHtml ? '<div class="mt-2 space-y-0">' + previewHtml + '</div>' : '') +
          '</div>' +
          '<span class="text-muted-foreground flex-shrink-0 self-center pl-1">&#8250;</span>' +
        '</div>' +
      '</div>' +
    '</li>';
  }

  function bindCardEvents() {
    if (!listsListEl) return;
    listsListEl.querySelectorAll('.record-card-trigger').forEach(function (card) {
      card.addEventListener('click', function () {
        var li = card.closest('li[data-record-id]');
        var id = li ? li.dataset.recordId : null;
        if (id && recordDataCache[id]) openRecordModal(recordDataCache[id]);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Create modal
  // ---------------------------------------------------------------------------
  function openCreateModal() {
    if (createForm) createForm.reset();
    if (createItemsContainer) createItemsContainer.innerHTML = '';
    addItemRow(); // start with one row
    if (createModal) createModal.showModal();
    if (createName) createName.focus();
  }

  function closeCreateModal() {
    if (createModal) createModal.close();
  }

  function addItemRow() {
    if (!createItemsContainer) return;
    var idx = createItemsContainer.children.length;
    var row = document.createElement('div');
    row.className = 'flex items-center gap-2';
    row.dataset.rowIdx = idx;
    row.innerHTML =
      '<input type="text" class="vd-input flex-1 text-sm item-row-text" placeholder="' + (i18n.placeholder_item_text || 'Item text...') + '">' +
      '<input type="text" class="vd-input w-16 text-sm item-row-qty" placeholder="' + (i18n.placeholder_qty || 'Qty') + '">' +
      '<input type="text" class="vd-input w-16 text-sm item-row-unit" placeholder="' + (i18n.placeholder_unit || 'Unit') + '">' +
      '<button type="button" class="vd-btn vd-btn-ghost px-2 py-1 text-xs text-destructive remove-row-btn">&#215;</button>';
    row.querySelector('.remove-row-btn').addEventListener('click', function () {
      row.remove();
    });
    createItemsContainer.appendChild(row);
  }

  function submitCreateList() {
    var name = createName ? createName.value.trim() : '';
    if (!name) { if (createName) createName.focus(); return; }

    var context = createContext ? createContext.value.trim() : '';
    var items = [];
    if (createItemsContainer) {
      createItemsContainer.querySelectorAll('[data-row-idx]').forEach(function (row) {
        var text = (row.querySelector('.item-row-text') || {}).value || '';
        text = text.trim();
        if (!text) return;
        var qty = ((row.querySelector('.item-row-qty') || {}).value || '').trim();
        var unit = ((row.querySelector('.item-row-unit') || {}).value || '').trim();
        items.push({ text: text, quantity: qty || null, unit: unit });
      });
    }

    if (createModalSaveBtn) createModalSaveBtn.disabled = true;
    apiFetch(window.LISTS_URLS.create, { method: 'POST', body: { name: name, context: context, items: items } })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function () { closeCreateModal(); loadLists(); })
      .catch(function (err) { alert(typeof err === 'string' ? err : (i18n.create_failed || 'Failed to create list.')); })
      .finally(function () { if (createModalSaveBtn) createModalSaveBtn.disabled = false; });
  }

  // ---------------------------------------------------------------------------
  // Record detail modal
  // ---------------------------------------------------------------------------
  function openRecordModal(record) {
    currentRecordId = record.id;
    if (recordModalName) recordModalName.value = record.name || '';
    if (recordModalContext) recordModalContext.value = record.context || '';
    if (recordModalSaveMetaBtn) recordModalSaveMetaBtn.classList.add('hidden');
    if (recordModalNewText) recordModalNewText.value = '';
    if (recordModalNewQty) recordModalNewQty.value = '';
    if (recordModalNewUnit) recordModalNewUnit.value = '';
    renderRecordModalItems(record.items || []);
    if (recordModal) recordModal.showModal();
  }

  function closeRecordModal() {
    if (recordModal) recordModal.close();
    currentRecordId = null;
  }

  function renderRecordModalItems(items) {
    if (!recordModalItemsList) return;
    if (!items || items.length === 0) {
      recordModalItemsList.innerHTML = '<li class="px-5 py-6 text-center text-sm text-muted-foreground">' + (i18n.no_items_yet || 'No items yet.') + '</li>';
      return;
    }
    var html = '';
    items.forEach(function (item) {
      var qtyBadge = '';
      if (item.quantity || item.unit) {
        qtyBadge = '<span class="ml-2 text-xs bg-accent/10 text-accent px-1.5 py-0.5 rounded">' +
          escHtml((item.quantity || '') + (item.unit ? ' ' + item.unit : '')) +
        '</span>';
      }
      var dueStr = item.due_date
        ? '<span class="text-xs text-muted-foreground ml-1">&#128197; ' + escHtml(item.due_date) + '</span>'
        : '';
      html +=
        '<li class="modal-item-row flex items-center gap-3 px-5 py-3 hover:bg-muted/30 cursor-pointer" data-item-id="' + item.id + '">' +
          '<div class="flex-1 min-w-0">' +
            '<span class="text-sm text-foreground">' + escHtml(item.text) + '</span>' +
            qtyBadge + dueStr +
          '</div>' +
          '<span class="text-muted-foreground flex-shrink-0 text-xs">&#8250;</span>' +
        '</li>';
    });
    recordModalItemsList.innerHTML = html;

    recordModalItemsList.querySelectorAll('.modal-item-row').forEach(function (row) {
      row.addEventListener('click', function () {
        var id = row.dataset.itemId;
        if (id) openItemEditModal(id);
      });
    });
  }

  function saveRecordMeta() {
    if (!currentRecordId) return;
    var name = recordModalName ? recordModalName.value.trim() : '';
    var context = recordModalContext ? recordModalContext.value.trim() : '';
    var url = window.LISTS_URLS.record.replace('{id}', currentRecordId);
    apiFetch(url, { method: 'PATCH', body: { name: name, context: context } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (updated) {
        if (recordModalSaveMetaBtn) recordModalSaveMetaBtn.classList.add('hidden');
        if (recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].name = updated.name;
          recordDataCache[currentRecordId].context = updated.context;
        }
        loadLists();
      })
      .catch(function () { alert(i18n.save_failed || 'Failed to save.'); });
  }

  function addItemToCurrentRecord() {
    if (!currentRecordId) return;
    var text = recordModalNewText ? recordModalNewText.value.trim() : '';
    if (!text) { if (recordModalNewText) recordModalNewText.focus(); return; }
    var qty = recordModalNewQty ? recordModalNewQty.value.trim() : '';
    var unit = recordModalNewUnit ? recordModalNewUnit.value.trim() : '';

    var url = window.LISTS_URLS.items_add.replace('{id}', currentRecordId);
    apiFetch(url, { method: 'POST', body: { text: text, quantity: qty || null, unit: unit } })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function (data) {
        if (recordModalNewText) recordModalNewText.value = '';
        if (recordModalNewQty) recordModalNewQty.value = '';
        if (recordModalNewUnit) recordModalNewUnit.value = '';
        // Update cache
        if (recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].items.push(data.item);
          recordDataCache[currentRecordId].item_count = (recordDataCache[currentRecordId].item_count || 0) + 1;
          renderRecordModalItems(recordDataCache[currentRecordId].items);
        }
        loadLists();
      })
      .catch(function (err) { alert(typeof err === 'string' ? err : (i18n.add_item_failed || 'Failed to add item.')); });
  }

  // ---------------------------------------------------------------------------
  // Item edit modal
  // ---------------------------------------------------------------------------
  function openItemEditModal(id) {
    editingItemId = id;
    // Find item in current record cache
    var rec = recordDataCache[currentRecordId];
    var item = rec ? (rec.items || []).find(function (i) { return i.id === id; }) : null;
    if (!item) return;

    if (itemFieldText) itemFieldText.value = item.text || '';
    if (itemFieldDesc) itemFieldDesc.value = item.description || '';
    if (itemFieldDueDate) itemFieldDueDate.value = item.due_date || '';
    if (itemFieldQty) itemFieldQty.value = item.quantity || '';
    if (itemFieldUnit) itemFieldUnit.value = item.unit || '';
    if (itemModal) itemModal.showModal();
  }

  function closeItemModal() {
    if (itemModal) itemModal.close();
    editingItemId = null;
  }

  function saveItemEdit() {
    if (!editingItemId) return;
    var text = itemFieldText ? itemFieldText.value.trim() : '';
    if (!text) { if (itemFieldText) itemFieldText.focus(); return; }

    var payload = {
      text: text,
      description: itemFieldDesc ? itemFieldDesc.value.trim() : '',
      due_date: itemFieldDueDate ? itemFieldDueDate.value : '',
      quantity: itemFieldQty ? itemFieldQty.value.trim() : '',
      unit: itemFieldUnit ? itemFieldUnit.value.trim() : '',
    };

    if (itemModalSaveBtn) itemModalSaveBtn.disabled = true;
    var url = window.LISTS_URLS.item.replace('{id}', editingItemId);
    apiFetch(url, { method: 'PATCH', body: payload })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function (updatedItem) {
        closeItemModal();
        // Update cache
        if (currentRecordId && recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].items = recordDataCache[currentRecordId].items.map(function (i) {
            return i.id === updatedItem.id ? updatedItem : i;
          });
          renderRecordModalItems(recordDataCache[currentRecordId].items);
        }
        loadLists();
      })
      .catch(function (err) { alert(typeof err === 'string' ? err : (i18n.save_item_failed || 'Failed to save item.')); })
      .finally(function () { if (itemModalSaveBtn) itemModalSaveBtn.disabled = false; });
  }

  function deleteItem(id) {
    var url = window.LISTS_URLS.item.replace('{id}', id);
    apiFetch(url, { method: 'DELETE' })
      .then(function (r) { if (!r.ok) throw new Error(); })
      .then(function () {
        closeItemModal();
        if (currentRecordId && recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].items = recordDataCache[currentRecordId].items.filter(function (i) { return i.id !== id; });
          var remaining = recordDataCache[currentRecordId].items;
          if (remaining.length === 0) {
            closeRecordModal();
          } else {
            recordDataCache[currentRecordId].item_count = remaining.length;
            renderRecordModalItems(remaining);
          }
        }
        loadLists();
      })
      .catch(function () { alert(i18n.delete_item_failed || 'Failed to delete item.'); });
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
  // State panels
  // ---------------------------------------------------------------------------
  function showState(state) {
    loadingEl && loadingEl.classList.add('hidden');
    emptyEl   && emptyEl.classList.add('hidden');
    errorEl   && errorEl.classList.add('hidden');
    if (listsListEl) listsListEl.innerHTML = '';
    paginationEl && paginationEl.classList.add('hidden');

    if (state === 'loading') loadingEl && loadingEl.classList.remove('hidden');
    else if (state === 'empty') emptyEl && emptyEl.classList.remove('hidden');
    else if (state === 'error') errorEl && errorEl.classList.remove('hidden');
  }

  // ---------------------------------------------------------------------------
  // Utility
  // ---------------------------------------------------------------------------
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
