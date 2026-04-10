/**
 * Financial Entries JavaScript
 *
 * Handles type filtering, pagination, grouped record cards,
 * record detail modal, create entry modal, inline item add/edit/delete.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // i18n (injected from Django via json_script)
  // ---------------------------------------------------------------------------
  var i18n = JSON.parse((document.getElementById('financials-i18n') || {textContent: '{}'}).textContent || '{}');

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  var currentType = 'expense';
  var currentPage = 1;
  var totalPages = 1;
  var recordDataCache = {};
  var currentRecordId = null;
  var editingItemId = null;

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
  // DOM references
  // ---------------------------------------------------------------------------
  var financialsListEl, loadingEl, emptyEl, errorEl, paginationEl, prevBtn, nextBtn, pageInfoEl;
  var addEntryBtn;
  // Create modal
  var createModal, createForm, createName, createContext, createItemsContainer, addItemRowBtn;
  var createModalCloseBtn, createModalCancelBtn, createModalSaveBtn;
  // Record detail modal
  var recordModal, recordModalName, recordModalContext, recordModalSaveMetaBtn;
  var recordModalDeleteBtn, recordModalCloseBtn, recordModalSummary;
  var recordModalItemsList;
  var recordModalNewType, recordModalNewAmount, recordModalNewCurrency, recordModalAddItemBtn;
  // Item edit modal
  var itemModal, itemForm, itemFieldType, itemFieldAmount, itemFieldCurrency, itemFieldDate;
  var itemFieldCategory, itemFieldMerchant, itemFieldPaymentMethod, itemFieldDescription;
  var itemModalCloseBtn, itemModalCancelBtn, itemModalDeleteBtn, itemModalSaveBtn;

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  function init() {
    financialsListEl = document.getElementById('financials-list');
    loadingEl    = document.getElementById('loading-state');
    emptyEl      = document.getElementById('empty-state');
    errorEl      = document.getElementById('error-state');
    paginationEl = document.getElementById('pagination');
    prevBtn      = document.getElementById('prev-page-btn');
    nextBtn      = document.getElementById('next-page-btn');
    pageInfoEl   = document.getElementById('page-info');

    addEntryBtn = document.getElementById('add-entry-btn');

    createModal          = document.getElementById('create-modal');
    createForm           = document.getElementById('create-form');
    createName           = document.getElementById('create-name');
    createContext        = document.getElementById('create-context');
    createItemsContainer = document.getElementById('create-items-container');
    addItemRowBtn        = document.getElementById('add-item-row-btn');
    createModalCloseBtn  = document.getElementById('create-modal-close-btn');
    createModalCancelBtn = document.getElementById('create-modal-cancel-btn');
    createModalSaveBtn   = document.getElementById('create-modal-save-btn');

    recordModal            = document.getElementById('record-modal');
    recordModalName        = document.getElementById('record-modal-name');
    recordModalContext     = document.getElementById('record-modal-context');
    recordModalSaveMetaBtn = document.getElementById('record-modal-save-meta-btn');
    recordModalDeleteBtn   = document.getElementById('record-modal-delete-btn');
    recordModalCloseBtn    = document.getElementById('record-modal-close-btn');
    recordModalSummary     = document.getElementById('record-modal-summary');
    recordModalItemsList   = document.getElementById('record-modal-items');
    recordModalNewType     = document.getElementById('record-modal-new-type');
    recordModalNewAmount   = document.getElementById('record-modal-new-amount');
    recordModalNewCurrency = document.getElementById('record-modal-new-currency');
    recordModalAddItemBtn  = document.getElementById('record-modal-add-item-btn');

    itemModal              = document.getElementById('item-modal');
    itemForm               = document.getElementById('item-form');
    itemFieldType          = document.getElementById('item-field-type');
    itemFieldAmount        = document.getElementById('item-field-amount');
    itemFieldCurrency      = document.getElementById('item-field-currency');
    itemFieldDate          = document.getElementById('item-field-date');
    itemFieldCategory      = document.getElementById('item-field-category');
    itemFieldMerchant      = document.getElementById('item-field-merchant');
    itemFieldPaymentMethod = document.getElementById('item-field-payment-method');
    itemFieldDescription   = document.getElementById('item-field-description');
    itemModalCloseBtn      = document.getElementById('item-modal-close-btn');
    itemModalCancelBtn     = document.getElementById('item-modal-cancel-btn');
    itemModalDeleteBtn     = document.getElementById('item-modal-delete-btn');
    itemModalSaveBtn       = document.getElementById('item-modal-save-btn');

    setupEventListeners();
    loadFinancials();
  }

  // ---------------------------------------------------------------------------
  // Event listeners
  // ---------------------------------------------------------------------------
  function setupEventListeners() {
    // Type tabs
    document.querySelectorAll('.type-tab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        currentType = btn.dataset.type;
        currentPage = 1;
        document.querySelectorAll('.type-tab').forEach(function (b) {
          b.classList.remove('vd-btn-accent');
          b.classList.add('vd-btn-ghost', 'border', 'border-input');
        });
        btn.classList.add('vd-btn-accent');
        btn.classList.remove('vd-btn-ghost', 'border', 'border-input');
        loadFinancials();
      });
    });

    if (prevBtn) prevBtn.addEventListener('click', function () { if (currentPage > 1) { currentPage--; loadFinancials(); } });
    if (nextBtn) nextBtn.addEventListener('click', function () { if (currentPage < totalPages) { currentPage++; loadFinancials(); } });

    if (addEntryBtn) addEntryBtn.addEventListener('click', openCreateModal);

    // Create modal
    if (createModalCloseBtn) createModalCloseBtn.addEventListener('click', closeCreateModal);
    if (createModalCancelBtn) createModalCancelBtn.addEventListener('click', closeCreateModal);
    if (createModal) createModal.addEventListener('click', function (e) { if (e.target === createModal) closeCreateModal(); });
    if (addItemRowBtn) addItemRowBtn.addEventListener('click', function () { addItemRow(); });
    if (createForm) createForm.addEventListener('submit', function (e) { e.preventDefault(); submitCreateEntry(); });

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
        if (!confirm(i18n.delete_entry_confirm || 'Delete this financial entry?')) return;
        var url = window.FINANCIALS_URLS.record.replace('{id}', currentRecordId);
        apiFetch(url, { method: 'DELETE' })
          .then(function (r) { if (!r.ok) throw new Error(); closeRecordModal(); loadFinancials(); })
          .catch(function () { alert(i18n.failed_delete_entry || 'Failed to delete entry.'); });
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
  function loadFinancials() {
    showState('loading');
    var url = window.FINANCIALS_URLS.list + '?type=' + encodeURIComponent(currentType) + '&page=' + currentPage;
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
        financialsListEl.innerHTML = '';
        data.records.forEach(function (record) {
          financialsListEl.insertAdjacentHTML('beforeend', renderRecordCard(record));
        });
        bindCardEvents();

        if (currentRecordId && recordDataCache[currentRecordId]) {
          renderRecordModalItems(recordDataCache[currentRecordId].items || []);
          renderRecordModalSummary(recordDataCache[currentRecordId]);
        }
      })
      .catch(function () { showState('error'); });
  }

  // ---------------------------------------------------------------------------
  // Record card rendering
  // ---------------------------------------------------------------------------
  function renderRecordCard(record) {
    var items = record.items || [];
    var previewItems = items.slice(0, 3);
    var moreCount = (record.item_count || 0) - previewItems.length;
    if (moreCount < 0) moreCount = 0;

    var previewHtml = '';
    previewItems.forEach(function (item) {
      var typeBadge = item.type === 'expense'
        ? '<span class="text-xs bg-destructive/10 text-destructive px-1.5 py-0.5 rounded mr-1">-</span>'
        : '<span class="text-xs bg-green-500/10 text-green-600 px-1.5 py-0.5 rounded mr-1">+</span>';
      previewHtml +=
        '<div class="flex items-center gap-1.5 py-0.5">' +
          typeBadge +
          '<span class="text-xs font-medium text-foreground">' + escHtml(item.amount) + ' ' + escHtml(item.currency) + '</span>' +
          (item.merchant ? '<span class="text-xs text-muted-foreground truncate ml-1">' + escHtml(item.merchant) + '</span>' : '') +
        '</div>';
    });
    if (moreCount > 0) {
      previewHtml += '<div class="text-xs text-muted-foreground pt-0.5">+' + moreCount + ' ' + (i18n.more || 'more') + '</div>';
    }

    var summaryParts = [];
    if (parseFloat(record.total_expense) > 0) summaryParts.push((i18n.expenses_label || 'Expenses') + ': ' + record.total_expense);
    if (parseFloat(record.total_income) > 0) summaryParts.push((i18n.income_label || 'Income') + ': ' + record.total_income);
    var itemLabel = record.item_count !== 1 ? (i18n.items || 'items') : (i18n.item || 'item');
    var summaryText = summaryParts.join(' · ') || record.item_count + ' ' + itemLabel;

    var manualBadge = record.is_manual
      ? '<span class="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">' + escHtml(i18n.manual || 'Manual') + '</span>'
      : '';

    return '<li class="financial-record-card mb-3" data-record-id="' + record.id + '">' +
      '<div class="vd-card p-3 cursor-pointer hover:shadow-md transition-all record-card-trigger">' +
        '<div class="flex items-start gap-2">' +
          '<div class="flex-1 min-w-0">' +
            '<div class="flex items-center gap-2 flex-wrap">' +
              '<span class="text-sm font-medium text-foreground">' + escHtml(record.name) + '</span>' +
              '<span class="text-xs text-muted-foreground">' + record.item_count + ' ' + (record.item_count !== 1 ? (i18n.items || 'items') : (i18n.item || 'item')) + '</span>' +
              manualBadge +
            '</div>' +
            '<p class="text-xs text-muted-foreground mt-0.5">' + escHtml(summaryText) + '</p>' +
            (previewHtml ? '<div class="mt-2 space-y-0">' + previewHtml + '</div>' : '') +
          '</div>' +
          '<span class="text-muted-foreground flex-shrink-0 self-center pl-1">&#8250;</span>' +
        '</div>' +
      '</div>' +
    '</li>';
  }

  function bindCardEvents() {
    if (!financialsListEl) return;
    financialsListEl.querySelectorAll('.record-card-trigger').forEach(function (card) {
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
    addItemRow();
    if (createModal) createModal.showModal();
    if (createName) createName.focus();
  }

  function closeCreateModal() {
    if (createModal) createModal.close();
  }

  function addItemRow() {
    if (!createItemsContainer) return;
    var row = document.createElement('div');
    row.className = 'grid grid-cols-5 gap-2 items-center';
    row.innerHTML =
      '<select class="vd-input text-sm item-row-type col-span-1">' +
        '<option value="expense">' + escHtml(i18n.expense || 'Expense') + '</option>' +
        '<option value="income">' + escHtml(i18n.income || 'Income') + '</option>' +
      '</select>' +
      '<input type="number" step="any" class="vd-input text-sm item-row-amount col-span-1" placeholder="' + escHtml(i18n.amount || 'Amount') + '">' +
      '<input type="text" class="vd-input text-sm item-row-currency col-span-1" placeholder="EUR" value="EUR">' +
      '<input type="text" class="vd-input text-sm item-row-merchant col-span-1" placeholder="' + escHtml(i18n.merchant || 'Merchant') + '">' +
      '<button type="button" class="vd-btn vd-btn-ghost px-2 py-1 text-xs text-destructive remove-row-btn col-span-1">&#215;</button>';
    row.querySelector('.remove-row-btn').addEventListener('click', function () { row.remove(); });
    createItemsContainer.appendChild(row);
  }

  function submitCreateEntry() {
    var name = createName ? createName.value.trim() : '';
    if (!name) { if (createName) createName.focus(); return; }

    var context = createContext ? createContext.value.trim() : '';
    var items = [];
    if (createItemsContainer) {
      createItemsContainer.querySelectorAll('.grid').forEach(function (row) {
        var amount = ((row.querySelector('.item-row-amount') || {}).value || '').trim();
        if (!amount) return;
        var type = ((row.querySelector('.item-row-type') || {}).value || 'expense');
        var currency = ((row.querySelector('.item-row-currency') || {}).value || 'EUR').trim() || 'EUR';
        var merchant = ((row.querySelector('.item-row-merchant') || {}).value || '').trim();
        items.push({ type: type, amount: amount, currency: currency, merchant: merchant });
      });
    }

    if (createModalSaveBtn) createModalSaveBtn.disabled = true;
    apiFetch(window.FINANCIALS_URLS.create, { method: 'POST', body: { name: name, context: context, items: items } })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function () { closeCreateModal(); loadFinancials(); })
      .catch(function (err) { alert(typeof err === 'string' ? err : (i18n.failed_create_entry || 'Failed to create entry.')); })
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
    if (recordModalNewAmount) recordModalNewAmount.value = '';
    if (recordModalNewCurrency) recordModalNewCurrency.value = 'EUR';
    renderRecordModalSummary(record);
    renderRecordModalItems(record.items || []);
    if (recordModal) recordModal.showModal();
  }

  function closeRecordModal() {
    if (recordModal) recordModal.close();
    currentRecordId = null;
  }

  function renderRecordModalSummary(record) {
    if (!recordModalSummary) return;
    var parts = [];
    if (parseFloat(record.total_expense) > 0) parts.push('<span class="text-destructive">' + escHtml(i18n.expenses_label || 'Expenses') + ': ' + escHtml(record.total_expense) + '</span>');
    if (parseFloat(record.total_income) > 0) parts.push('<span class="text-green-600">' + escHtml(i18n.income_label || 'Income') + ': ' + escHtml(record.total_income) + '</span>');
    recordModalSummary.innerHTML = parts.join('<span class="mx-2">·</span>') || '<span class="text-muted-foreground">' + escHtml(i18n.no_items || 'No items') + '</span>';
  }

  function renderRecordModalItems(items) {
    if (!recordModalItemsList) return;
    if (!items || items.length === 0) {
      recordModalItemsList.innerHTML = '<li class="px-5 py-6 text-center text-sm text-muted-foreground">' + escHtml(i18n.no_items_yet || 'No items yet.') + '</li>';
      return;
    }
    var html = '';
    items.forEach(function (item) {
      var typeBadge = item.type === 'expense'
        ? '<span class="text-xs bg-destructive/10 text-destructive px-1.5 py-0.5 rounded">' + escHtml(i18n.expense || 'Expense') + '</span>'
        : '<span class="text-xs bg-green-500/10 text-green-600 px-1.5 py-0.5 rounded">' + escHtml(i18n.income || 'Income') + '</span>';
      var dateStr = item.transaction_date
        ? '<span class="text-xs text-muted-foreground ml-1">&#128197; ' + escHtml(item.transaction_date) + '</span>'
        : '';
      html +=
        '<li class="modal-item-row flex items-center gap-3 px-5 py-3 hover:bg-muted/30 cursor-pointer" data-item-id="' + item.id + '">' +
          typeBadge +
          '<div class="flex-1 min-w-0">' +
            '<span class="text-sm font-medium text-foreground">' + escHtml(item.amount) + ' ' + escHtml(item.currency) + '</span>' +
            (item.merchant ? '<span class="text-xs text-muted-foreground ml-2">' + escHtml(item.merchant) + '</span>' : '') +
            dateStr +
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
    var url = window.FINANCIALS_URLS.record.replace('{id}', currentRecordId);
    apiFetch(url, { method: 'PATCH', body: { name: name, context: context } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (updated) {
        if (recordModalSaveMetaBtn) recordModalSaveMetaBtn.classList.add('hidden');
        if (recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].name = updated.name;
          recordDataCache[currentRecordId].context = updated.context;
        }
        loadFinancials();
      })
      .catch(function () { alert(i18n.failed_save || 'Failed to save.'); });
  }

  function addItemToCurrentRecord() {
    if (!currentRecordId) return;
    var amount = recordModalNewAmount ? recordModalNewAmount.value.trim() : '';
    if (!amount) { if (recordModalNewAmount) recordModalNewAmount.focus(); return; }
    var type = recordModalNewType ? recordModalNewType.value : 'expense';
    var currency = recordModalNewCurrency ? (recordModalNewCurrency.value.trim() || 'EUR') : 'EUR';

    var url = window.FINANCIALS_URLS.items_add.replace('{id}', currentRecordId);
    apiFetch(url, { method: 'POST', body: { type: type, amount: amount, currency: currency } })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function (data) {
        if (recordModalNewAmount) recordModalNewAmount.value = '';
        if (currentRecordId && recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].items.push(data.item);
          recordDataCache[currentRecordId].item_count = (recordDataCache[currentRecordId].item_count || 0) + 1;
          // Recalculate totals
          recalcTotals(recordDataCache[currentRecordId]);
          renderRecordModalItems(recordDataCache[currentRecordId].items);
          renderRecordModalSummary(recordDataCache[currentRecordId]);
        }
        loadFinancials();
      })
      .catch(function (err) { alert(typeof err === 'string' ? err : (i18n.failed_add_item || 'Failed to add item.')); });
  }

  function recalcTotals(record) {
    var totalExpense = 0, totalIncome = 0;
    (record.items || []).forEach(function (i) {
      var a = parseFloat(i.amount) || 0;
      if (i.type === 'expense') totalExpense += a;
      else totalIncome += a;
    });
    record.total_expense = totalExpense.toFixed(2);
    record.total_income = totalIncome.toFixed(2);
  }

  // ---------------------------------------------------------------------------
  // Item edit modal
  // ---------------------------------------------------------------------------
  function openItemEditModal(id) {
    editingItemId = id;
    var rec = recordDataCache[currentRecordId];
    var item = rec ? (rec.items || []).find(function (i) { return i.id === id; }) : null;
    if (!item) return;

    if (itemFieldType) itemFieldType.value = item.type || 'expense';
    if (itemFieldAmount) itemFieldAmount.value = item.amount || '';
    if (itemFieldCurrency) itemFieldCurrency.value = item.currency || 'EUR';
    if (itemFieldDate) itemFieldDate.value = item.transaction_date || '';
    if (itemFieldCategory) itemFieldCategory.value = item.category || '';
    if (itemFieldMerchant) itemFieldMerchant.value = item.merchant || '';
    if (itemFieldPaymentMethod) itemFieldPaymentMethod.value = item.payment_method || '';
    if (itemFieldDescription) itemFieldDescription.value = item.description || '';
    if (itemModal) itemModal.showModal();
  }

  function closeItemModal() {
    if (itemModal) itemModal.close();
    editingItemId = null;
  }

  function saveItemEdit() {
    if (!editingItemId) return;
    var amount = itemFieldAmount ? itemFieldAmount.value.trim() : '';
    if (!amount) { if (itemFieldAmount) itemFieldAmount.focus(); return; }

    var payload = {
      type: itemFieldType ? itemFieldType.value : 'expense',
      amount: amount,
      currency: itemFieldCurrency ? (itemFieldCurrency.value.trim() || 'EUR') : 'EUR',
      transaction_date: itemFieldDate ? itemFieldDate.value : '',
      category: itemFieldCategory ? itemFieldCategory.value.trim() : '',
      merchant: itemFieldMerchant ? itemFieldMerchant.value.trim() : '',
      payment_method: itemFieldPaymentMethod ? itemFieldPaymentMethod.value.trim() : '',
      description: itemFieldDescription ? itemFieldDescription.value.trim() : '',
    };

    if (itemModalSaveBtn) itemModalSaveBtn.disabled = true;
    var url = window.FINANCIALS_URLS.item.replace('{id}', editingItemId);
    apiFetch(url, { method: 'PATCH', body: payload })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { return Promise.reject(e.error || 'Error'); }); })
      .then(function (updatedItem) {
        closeItemModal();
        if (currentRecordId && recordDataCache[currentRecordId]) {
          recordDataCache[currentRecordId].items = recordDataCache[currentRecordId].items.map(function (i) {
            return i.id === updatedItem.id ? updatedItem : i;
          });
          recalcTotals(recordDataCache[currentRecordId]);
          renderRecordModalItems(recordDataCache[currentRecordId].items);
          renderRecordModalSummary(recordDataCache[currentRecordId]);
        }
        loadFinancials();
      })
      .catch(function (err) { alert(typeof err === 'string' ? err : (i18n.failed_save_item || 'Failed to save item.')); })
      .finally(function () { if (itemModalSaveBtn) itemModalSaveBtn.disabled = false; });
  }

  function deleteItem(id) {
    var url = window.FINANCIALS_URLS.item.replace('{id}', id);
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
            recalcTotals(recordDataCache[currentRecordId]);
            renderRecordModalItems(remaining);
            renderRecordModalSummary(recordDataCache[currentRecordId]);
          }
        }
        loadFinancials();
      })
      .catch(function () { alert(i18n.failed_delete_item || 'Failed to delete item.'); });
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
    if (pageInfoEl) pageInfoEl.textContent = (i18n.page_of || 'Page %(page)s of %(total)s (%(count)s entries)')
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
    if (financialsListEl) financialsListEl.innerHTML = '';
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
