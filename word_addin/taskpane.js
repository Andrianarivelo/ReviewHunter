/*
 * ReviewHunter Word add-in: task pane user interface.
 *
 * Boot sequence:
 *   1. detect the host (Office.onReady); Word -> WordAdapter, anything else -> MockAdapter
 *      (dev mode with a simulated document next to the pane);
 *   2. create ApiClient + RHCitations + RH.Controller and load the document state;
 *   3. wire the search box, citation builder, document tools and preferences sheet.
 *
 * All document logic lives in js/rh-core.js; this file only renders and reacts.
 * window.rhApp exposes {controller, adapter, api, ui} for debugging and tests.
 */
(function () {
  'use strict';

  const $ = sel => document.querySelector(sel);
  const esc = s => RH.util.escapeHtml(s);

  // ------------------------------------------------------------------ configuration
  const CONFIG = {
    searchDebounceMs: 170,     // delay between keystrokes and the search request
    searchLimit: 25,           // results shown per search
    previewDebounceMs: 160,    // delay before re-rendering the preview
    selectionDebounceMs: 250,  // delay before reading the Word selection
    pingOnlineMs: 15000,       // health check period while online
    pingOfflineMs: 5000,       // health check period while offline
    hostTimeoutMs: 5000,       // give up waiting for Office.onReady after this
  };

  /** Mutable UI state. */
  const ui = {
    results: [],               // last search results
    active: -1,                // keyboard-highlighted result index
    cluster: [],               // [{id, authors, year, title, locator, label, prefix, suffix, suppress, open}]
    csl: {},                   // id -> CSL-JSON for items in the cluster
    editingId: null,           // citationID being edited, null when building a new citation
    dirty: false,              // cluster changed since it was loaded
    pendingEditId: null,       // selection is in a citation but the draft is busy
    busy: false,
    styles: [],
    prefsDraft: null,
    searchAbort: null,
    searchSeq: 0,
  };

  let controller = null;
  let adapter = null;
  let api = null;

  // ================================================================== boot

  /**
   * Resolve with Office host info when running inside Word, else null.
   * @returns {Promise<object|null>}
   */
  function detectHost() {
    return new Promise(resolve => {
      if (/[?&]mock=1\b/.test(location.search) || typeof Office === 'undefined') return resolve(null);
      let settled = false;
      const timer = setTimeout(() => { if (!settled) { settled = true; resolve(null); } }, CONFIG.hostTimeoutMs);
      Office.onReady(info => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(info && info.host === Office.HostType.Word ? info : null);
      });
    });
  }

  /** Follow the Office theme when available (else CSS prefers-color-scheme applies). */
  function applyTheme() {
    try {
      const theme = Office.context && Office.context.officeTheme;
      if (!theme || !theme.bodyBackgroundColor) return;
      const hex = theme.bodyBackgroundColor.replace('#', '');
      const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.substr(i, 2), 16) / 255);
      const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      document.documentElement.dataset.theme = luminance < 0.5 ? 'dark' : 'light';
    } catch (e) { /* not in Office */ }
  }

  /** Entry point. */
  async function boot() {
    const host = await detectHost();
    api = new RH.ApiClient();
    api.onStatus(renderStatus);
    if (host) {
      applyTheme();
      adapter = new RHWordAdapter();
    } else {
      document.body.classList.add('dev');
      $('#mockHost').hidden = false;
      $('#devBadge').hidden = false;
      adapter = new RHMockAdapter({ root: $('#mockDoc'), footnotesRoot: $('#mockNotes'), footnotes: true });
    }
    controller = new RH.Controller({ adapter, api, engine: new RHCitations() });
    window.rhApp = { controller, adapter, api, ui, CONFIG };

    controller.on('busy', setBusy);
    controller.on('change', renderSummary);
    wireSearch();
    wireCluster();
    wireDocumentTools();
    wirePrefs();
    adapter.onSelectionChanged(RH.util.debounce(onSelectionChanged, CONFIG.selectionDebounceMs));

    await api.ping();
    renderStatus(api.online);
    schedulePing();
    try {
      await controller.init();
    } catch (e) {
      showError(e, 'Could not load this document');
    }
    renderStyleChip();
    renderCluster();
    $('#app').setAttribute('aria-busy', 'false');
    $('#q').focus();
    loadStyles();                     // background: titles for the chip and the preferences
  }

  /** Periodic health check; faster while offline so the pill recovers quickly. */
  function schedulePing() {
    setTimeout(async () => {
      const wasOnline = api.online;
      await api.ping();
      if (api.online && !wasOnline) {
        loadStyles();
        if (controller.order.length) controller.refresh({ force: false }).catch(() => {});
      }
      schedulePing();
    }, api.online ? CONFIG.pingOnlineMs : CONFIG.pingOfflineMs);
  }

  // ================================================================== status, busy, messages

  /** @param {boolean|null} online */
  function renderStatus(online) {
    const pill = $('#statusPill');
    pill.classList.toggle('online', online === true);
    pill.classList.toggle('offline', online === false);
    $('#statusText').textContent = online === null ? 'Connecting' : online ? 'Online' : 'Offline';
    if (online === false) {
      showBanner('offline', '<b>ReviewHunter is not running.</b> Start it (ReviewHunter.bat or the desktop shortcut) '
        + 'to search your library. Citations already in this document still refresh from the copies saved inside it.');
    } else {
      hideBanner('offline');
    }
  }

  /** @param {boolean} busy */
  function setBusy(busy) {
    ui.busy = busy;
    $('#progress').hidden = !busy;
    updateActionState();
  }

  let bannerKind = null;

  /**
   * Show a dismissible banner.
   * @param {string} kind identifier, so a later hideBanner(kind) only hides its own banner
   * @param {string} html trusted HTML
   */
  function showBanner(kind, html) {
    bannerKind = kind;
    $('#bannerBody').innerHTML = html;
    $('#banner').hidden = false;
  }

  /** @param {string} kind */
  function hideBanner(kind) {
    if (bannerKind === kind) {
      $('#banner').hidden = true;
      bannerKind = null;
    }
  }

  let toastTimer = null;

  /**
   * Transient message.
   * @param {string} text
   * @param {{kind?: 'ok'|'error', action?: {label: string, run: Function}, ms?: number}} [opts]
   */
  function toast(text, opts = {}) {
    const el = $('#toast');
    el.className = 'toast ' + (opts.kind || '');
    el.textContent = text;
    if (opts.action) {
      const b = document.createElement('button');
      b.className = 'link-btn';
      b.style.color = 'inherit';
      b.style.marginLeft = '8px';
      b.textContent = opts.action.label;
      b.onclick = () => { el.hidden = true; opts.action.run(); };
      el.appendChild(b);
    }
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, opts.ms || (opts.kind === 'error' ? 7000 : 3200));
  }

  /**
   * Report an error readably (Office errors carry debugInfo).
   * @param {Error} e
   * @param {string} context
   */
  function showError(e, context) {
    console.error(context, e);
    const detail = (e && e.debugInfo && e.debugInfo.message) || (e && e.message) || String(e);
    toast(`${context}: ${detail}`, { kind: 'error' });
  }

  /** Enable or disable buttons according to busy state and cluster content. */
  function updateActionState() {
    const has = ui.cluster.length > 0;
    $('#btnInsert').disabled = ui.busy || (!has && !ui.editingId);
    $('#btnInsertText').textContent = ui.editingId ? (has ? 'Update citation' : 'Remove citation') : 'Insert citation';
    $('#btnRemove').hidden = !ui.editingId;
    ['#btnBib', '#btnRefresh', '#btnApplyPrefs'].forEach(s => { $(s).disabled = ui.busy; });
  }

  // ================================================================== search

  /** Wire the type-ahead search box and keyboard navigation. */
  function wireSearch() {
    const input = $('#q');
    const run = RH.util.debounce(() => search(input.value), CONFIG.searchDebounceMs);
    input.addEventListener('input', run);
    input.addEventListener('focus', () => { if (!ui.results.length) search(input.value); });
    // Hide the result list when focus leaves the pane's search (results keep focus via mousedown).
    input.addEventListener('blur', () => setTimeout(() => {
      if (document.activeElement !== input) {
        ui.searchSeq++;                 // drop answers still in flight
        clearResults();
      }
    }, 120));
    input.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (!ui.results.length) return;
        const step = ev.key === 'ArrowDown' ? 1 : -1;
        ui.active = (ui.active + step + ui.results.length) % ui.results.length;
        renderResults();
      } else if (ev.key === 'Enter') {
        ev.preventDefault();
        if ((ev.ctrlKey || ev.metaKey) && (ui.cluster.length || ui.editingId)) {
          commitCitation();
        } else if (ui.active >= 0 && ui.results[ui.active]) {
          addToCluster(ui.results[ui.active]);
        } else if (!input.value.trim() && ui.cluster.length) {
          commitCitation();
        }
      } else if (ev.key === 'Escape') {
        if (input.value) {
          input.value = '';
          search('');
        } else {
          clearResults();
        }
      }
    });
    $('#results').addEventListener('mousedown', ev => ev.preventDefault());    // keep focus in the input
    $('#results').addEventListener('click', ev => {
      const li = ev.target.closest('.result');
      if (li) addToCluster(ui.results[Number(li.dataset.idx)]);
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === '/' && document.activeElement && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)
          && !document.activeElement.isContentEditable) {
        ev.preventDefault();
        input.focus();
      }
    });
  }

  /**
   * Query the library; stale responses are ignored.
   * @param {string} q
   */
  async function search(q) {
    const seq = ++ui.searchSeq;
    if (ui.searchAbort) ui.searchAbort.abort();
    ui.searchAbort = new AbortController();
    $('#searchSpin').hidden = false;
    try {
      const rows = await api.search(q.trim(), CONFIG.searchLimit, ui.searchAbort.signal);
      if (seq !== ui.searchSeq) return;
      ui.results = rows;
      ui.active = rows.length ? 0 : -1;
      renderResults(q.trim() ? `No match for <b>${esc(q.trim())}</b>` : 'Your library is empty');
    } catch (e) {
      if (e.name === 'AbortError' || seq !== ui.searchSeq) return;
      ui.results = [];
      ui.active = -1;
      renderResults(api.online === false
        ? 'Search needs ReviewHunter running. Start the app, then try again.'
        : `Search failed: ${esc(e.message)}`);
    } finally {
      if (seq === ui.searchSeq) $('#searchSpin').hidden = true;
    }
  }

  function clearResults() {
    ui.results = [];
    ui.active = -1;
    $('#results').innerHTML = '';
  }

  /** Inline SVG icon for a ReviewHunter item type. @param {string} type @returns {string} */
  function typeIcon(type) {
    const book = /book|thesis|manuscript|report/i.test(type);
    const cls = book ? 't-book' : /article|preprint|conference/i.test(type) ? 't-article' : '';
    const paths = {
      book: '<path d="M3 3.5h4.5a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 0-1.5-1.5H3zM13 3.5H9.5"/><path d="M13 3.5v8H9"/>',
      chapter: '<path d="M4 2.5h6l2.5 2.5v8.5H4z"/><path d="M6 8h4M6 10.5h3"/>',
      web: '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c2 2 2 9 0 11M8 2.5c-2 2-2 9 0 11"/>',
      article: '<path d="M4 2.5h8v11H4z"/><path d="M6 5.5h4M6 8h4M6 10.5h2.5"/>',
    };
    const key = /section|chapter/i.test(type) ? 'chapter' : book ? 'book' : /web|blog|forum/i.test(type) ? 'web' : 'article';
    return `<span class="type-icon ${cls}" title="${esc(type)}"><svg viewBox="0 0 16 16">${paths[key]}</svg></span>`;
  }

  /** @param {string} [emptyHtml] message when there are no results */
  function renderResults(emptyHtml) {
    const ul = $('#results');
    if (!ui.results.length) {
      ul.innerHTML = emptyHtml && document.activeElement === $('#q') ? `<li class="results-state">${emptyHtml}</li>` : '';
      return;
    }
    const inCluster = new Set(ui.cluster.map(c => String(c.id)));
    ul.innerHTML = ui.results.map((r, i) => `
      <li class="result${i === ui.active ? ' active' : ''}${inCluster.has(String(r.id)) ? ' in-cluster' : ''}"
          role="option" aria-selected="${i === ui.active}" data-idx="${i}" id="res-${i}">
        ${typeIcon(r.item_type || '')}
        <div class="r-body">
          <div class="r-meta"><b>${esc(RH.util.shortAuthors(r.creators) || 'No author')}</b>${r.year ? ` · ${esc(r.year)}` : ''}</div>
          <div class="r-title">${esc(r.title || 'Untitled')}</div>
          ${r.journal ? `<div class="r-journal">${esc(r.journal)}</div>` : ''}
        </div>
      </li>`).join('');
    $('#q').setAttribute('aria-activedescendant', ui.active >= 0 ? `res-${ui.active}` : '');
    const active = ul.querySelector('.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  // ================================================================== citation builder

  /**
   * Add a search result to the cluster (ignored if already present).
   * @param {object} r search result
   */
  function addToCluster(r) {
    if (!r) return;
    if (ui.cluster.some(c => String(c.id) === String(r.id))) {
      toast('Already in this citation');
      return;
    }
    ui.cluster.push({
      id: String(r.id), authors: RH.util.shortAuthors(r.creators), year: r.year || '', title: r.title || '',
      locator: '', label: 'page', prefix: '', suffix: '', suppress: false, open: false,
    });
    if (r.csl) ui.csl[String(r.id)] = r.csl;
    ui.dirty = true;
    $('#q').value = '';
    clearResults();
    renderCluster();
    $('#q').focus();
  }

  /** Wire cluster interactions (buttons, fields, drag and drop). */
  function wireCluster() {
    const ol = $('#cluster');
    ol.addEventListener('click', ev => {
      const li = ev.target.closest('.ci');
      if (!li) return;
      const i = Number(li.dataset.idx);
      const act = ev.target.closest('[data-act]');
      const action = act ? act.dataset.act : (ev.target.closest('.ci-main') ? 'toggle' : null);
      if (!action) return;
      if (action === 'up' && i > 0) move(i, i - 1);
      else if (action === 'down' && i < ui.cluster.length - 1) move(i, i + 1);
      else if (action === 'remove') { ui.cluster.splice(i, 1); ui.dirty = true; renderCluster(); }
      else if (action === 'toggle') {
        ui.cluster[i].open = !ui.cluster[i].open;
        renderCluster();
        if (ui.cluster[i].open) {
          const loc = ol.querySelector(`.ci[data-idx="${i}"] [data-field="locator"]`);
          if (loc) loc.focus();
        }
      }
    });
    const onField = ev => {
      const f = ev.target.dataset.field;
      if (!f) return;
      const li = ev.target.closest('.ci');
      const i = li ? Number(li.dataset.idx) : -1;
      if (!ui.cluster[i]) return;          // late event from a card that was re-rendered away
      ui.cluster[i][f] =ev.target.type === 'checkbox' ? ev.target.checked : ev.target.value;
      ui.dirty = true;
      updateClusterHead(i);
      schedulePreview();
    };
    ol.addEventListener('input', onField);
    ol.addEventListener('change', onField);
    ol.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' && ev.target.matches('.input')) {
        ev.preventDefault();
        commitCitation();
      }
    });

    // Drag and drop reordering (handle only, so text fields stay selectable).
    let from = null;
    ol.addEventListener('dragstart', ev => {
      const li = ev.target.closest('.ci');
      if (!li) return;
      from = Number(li.dataset.idx);
      li.classList.add('dragging');
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', String(from));
    });
    ol.addEventListener('dragover', ev => {
      const li = ev.target.closest('.ci');
      if (from === null || !li) return;
      ev.preventDefault();
      ol.querySelectorAll('.drop-before,.drop-after').forEach(x => x.classList.remove('drop-before', 'drop-after'));
      const rect = li.getBoundingClientRect();
      li.classList.add(ev.clientY < rect.top + rect.height / 2 ? 'drop-before' : 'drop-after');
    });
    ol.addEventListener('drop', ev => {
      const li = ev.target.closest('.ci');
      if (from === null || !li) return;
      ev.preventDefault();
      let to = Number(li.dataset.idx) + (li.classList.contains('drop-after') ? 1 : 0);
      if (to > from) to--;
      move(from, to);
    });
    ol.addEventListener('dragend', () => {
      from = null;
      ol.querySelectorAll('.dragging,.drop-before,.drop-after')
        .forEach(x => x.classList.remove('dragging', 'drop-before', 'drop-after'));
    });

    $('#btnInsert').addEventListener('click', commitCitation);
    $('#btnRemove').addEventListener('click', removeEditing);
    $('#btnCancelEdit').addEventListener('click', () => resetBuilder());
    $('#btnGoto').addEventListener('click', () => {
      const id = ui.editingId || ui.pendingEditId;
      if (ui.pendingEditId && !ui.editingId) loadForEdit(ui.pendingEditId);
      else if (id) adapter.selectCitation(id);
    });
  }

  /** Move a cluster item. @param {number} a @param {number} b */
  function move(a, b) {
    const [it] = ui.cluster.splice(a, 1);
    ui.cluster.splice(b, 0, it);
    ui.dirty = true;
    renderCluster();
  }

  /** Short label for a cluster item with its options. @param {object} c @returns {string} */
  function clusterHeadHtml(c) {
    const opts = [];
    if (c.prefix) opts.push(esc(c.prefix));
    const main = `${esc(c.authors || 'No author')}${c.year ? ` ${esc(c.year)}` : ''}`;
    const loc = c.locator ? `, ${esc(labelShort(c.label))} ${esc(c.locator)}` : '';
    const suf = c.suffix ? ` ${esc(c.suffix)}` : '';
    const sup = c.suppress ? ' <span class="opt">(no author)</span>' : '';
    return `${opts.length ? `<span class="opt">${opts.join(' ')} </span>` : ''}${main}<span class="opt">${loc}${suf}</span>${sup}`;
  }

  /** @param {string} label CSL locator label @returns {string} short form */
  function labelShort(label) {
    return ({ page: 'p.', chapter: 'ch.', section: 'sec.', paragraph: 'para.', figure: 'fig.', table: 'tab.',
      volume: 'vol.', issue: 'no.', line: 'l.', note: 'n.', equation: 'eq.' })[label] || label;
  }

  /** Update only the head of one item (keeps focus in its fields). @param {number} i */
  function updateClusterHead(i) {
    const el = $(`#cluster .ci[data-idx="${i}"] .ci-cite`);
    if (el) el.innerHTML = clusterHeadHtml(ui.cluster[i]);
  }

  /** Render the cluster list, empty state, counts and preview. */
  function renderCluster() {
    const ol = $('#cluster');
    const n = ui.cluster.length;
    const gripSvg = '<svg viewBox="0 0 12 12"><circle cx="4" cy="2.5" r="1"/><circle cx="8" cy="2.5" r="1"/>'
      + '<circle cx="4" cy="6" r="1"/><circle cx="8" cy="6" r="1"/><circle cx="4" cy="9.5" r="1"/><circle cx="8" cy="9.5" r="1"/></svg>';
    const labelOptions = sel => RH.LOCATOR_LABELS
      .map(([v, t]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${t}</option>`).join('');
    ol.innerHTML = ui.cluster.map((c, i) => `
      <li class="ci${c.open ? ' open' : ''}" data-idx="${i}">
        <div class="ci-head">
          <span class="grip" draggable="true" title="Drag to reorder">${gripSvg}</span>
          <div class="ci-main" title="Show page, prefix and suffix">
            <div class="ci-cite">${clusterHeadHtml(c)}</div>
            <div class="ci-title">${esc(c.title)}</div>
          </div>
          <button class="icon-btn" data-act="up" type="button" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>
            <svg viewBox="0 0 16 16"><path d="M4 10l4-4 4 4"/></svg></button>
          <button class="icon-btn" data-act="down" type="button" aria-label="Move down" ${i === n - 1 ? 'disabled' : ''}>
            <svg viewBox="0 0 16 16"><path d="M4 6l4 4 4-4"/></svg></button>
          <button class="icon-btn" data-act="toggle" type="button" aria-label="Options" aria-expanded="${c.open}">
            <svg viewBox="0 0 16 16"><path d="M3 5h10M3 11h10"/><circle cx="6" cy="5" r="1.4"/><circle cx="10" cy="11" r="1.4"/></svg></button>
          <button class="icon-btn remove" data-act="remove" type="button" aria-label="Remove from citation">
            <svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
        </div>
        ${c.open ? `
        <div class="ci-opts">
          <label class="full">Locator
            <span class="loc-row">
              <select class="select" data-field="label" aria-label="Locator type">${labelOptions(c.label)}</select>
              <input class="input" data-field="locator" value="${esc(c.locator)}" placeholder="e.g. 12-15" aria-label="Locator">
            </span>
          </label>
          <label>Prefix<input class="input" data-field="prefix" value="${esc(c.prefix)}" placeholder="see"></label>
          <label>Suffix<input class="input" data-field="suffix" value="${esc(c.suffix)}" placeholder="for a review"></label>
          <label class="check full"><input type="checkbox" data-field="suppress" ${c.suppress ? 'checked' : ''}>Suppress author</label>
        </div>` : ''}
      </li>`).join('');
    // the draggable handle drags its whole card
    ol.querySelectorAll('.grip').forEach(g => g.addEventListener('dragstart', ev => {
      ev.dataTransfer.setDragImage(g.closest('.ci'), 12, 12);
    }));
    $('#clusterEmpty').hidden = n > 0;
    $('#clusterCount').textContent = n ? `${n} reference${n > 1 ? 's' : ''}` : '';
    $('#editBar').hidden = !(ui.editingId || ui.pendingEditId);
    if (ui.pendingEditId && !ui.editingId) {
      $('#editBar span').textContent = 'The cursor is in a citation';
      $('#btnGoto').textContent = 'Edit it';
      $('#btnCancelEdit').hidden = true;
    } else {
      $('#editBar span').textContent = 'Editing the selected citation';
      $('#btnGoto').textContent = 'Show';
      $('#btnCancelEdit').hidden = false;
    }
    updateActionState();
    schedulePreview();
  }

  /** @returns {Array<object>} cluster as citation items for the controller */
  function clusterItems() {
    return ui.cluster.map(c => ({
      id: c.id, locator: c.locator, label: c.label, prefix: c.prefix, suffix: c.suffix, 'suppress-author': c.suppress,
    }));
  }

  let previewSeq = 0;
  const schedulePreview = RH.util.debounce(async () => {
    const seq = ++previewSeq;
    const box = $('#previewBox');
    const out = $('#preview');
    if (!ui.cluster.length) {
      box.hidden = true;
      return;
    }
    try {
      const html = await controller.preview(clusterItems(), ui.csl, ui.editingId);
      if (seq !== previewSeq) return;
      out.classList.remove('error');
      out.innerHTML = html || '<span style="opacity:.6">(nothing to show)</span>';
    } catch (e) {
      if (seq !== previewSeq) return;
      out.classList.add('error');
      out.textContent = e.message;
    }
    box.hidden = false;
  }, CONFIG.previewDebounceMs);

  /** Insert the draft as a new citation, or save the citation being edited. */
  async function commitCitation() {
    if (ui.busy) return;
    const items = clusterItems();
    try {
      if (ui.editingId) {
        if (!items.length) return removeEditing();
        await controller.updateCitation(ui.editingId, items, ui.csl);
        toast('Citation updated', { kind: 'ok' });
      } else {
        if (!items.length) return;
        await controller.insertCitation(items, ui.csl);
        toast(controller.useFootnotes ? 'Citation inserted in a footnote' : 'Citation inserted', { kind: 'ok' });
      }
      resetBuilder();
    } catch (e) {
      showError(e, 'Could not insert the citation');
    }
  }

  /** Remove the citation being edited from the document. */
  async function removeEditing() {
    if (!ui.editingId) return;
    try {
      await controller.removeCitation(ui.editingId);
      toast('Citation removed', { kind: 'ok' });
      resetBuilder();
    } catch (e) {
      showError(e, 'Could not remove the citation');
    }
  }

  /**
   * Back to an empty "new citation" builder.
   * @param {{focus?: boolean}} [opts] focus the search box (not when reacting to the document selection)
   */
  function resetBuilder(opts = {}) {
    ui.cluster = [];
    ui.csl = {};
    ui.editingId = null;
    ui.pendingEditId = null;
    ui.dirty = false;
    renderCluster();
    if (opts.focus !== false) $('#q').focus();
  }

  // ================================================================== selection / edit mode

  /** React to the document selection: offer or start editing the citation under the cursor. */
  async function onSelectionChanged() {
    if (!controller) return;
    let found = null;
    try {
      found = await controller.selectionCluster();
    } catch (e) {
      return;                         // selection reads can fail while Word is busy
    }
    if (!found) {
      if (ui.editingId && !ui.dirty) resetBuilder({ focus: false });
      else if (ui.pendingEditId) { ui.pendingEditId = null; renderCluster(); }
      return;
    }
    if (found.citationID === ui.editingId) return;
    if (ui.cluster.length && ui.dirty) {
      ui.pendingEditId = found.citationID;      // do not overwrite a draft in progress
      renderCluster();
      return;
    }
    loadForEdit(found.citationID, found);
  }

  /**
   * Load an existing citation into the builder.
   * @param {string} citationID
   * @param {object} [found] result of controller.selectionCluster()
   */
  async function loadForEdit(citationID, found) {
    if (!found) {
      const cluster = controller.state.citations[citationID];
      if (!cluster) return;
      found = { citationID, cluster: RH.util.clone(cluster), csl: {} };
      cluster.items.forEach(it => { if (controller.state.items[it.id]) found.csl[it.id] = controller.state.items[it.id]; });
    }
    ui.editingId = citationID;
    ui.pendingEditId = null;
    ui.csl = found.csl;
    ui.cluster = found.cluster.items.map(it => {
      const csl = found.csl[it.id] || {};
      return {
        id: String(it.id), authors: RH.util.cslShortAuthors(csl) || '[missing item]', year: RH.util.cslYear(csl),
        title: csl.title || '', locator: it.locator || '', label: it.label || 'page', prefix: (it.prefix || '').trim(),
        suffix: (it.suffix || '').trim(), suppress: !!it['suppress-author'], open: !!(it.locator || it.prefix || it.suffix),
      };
    });
    ui.dirty = false;
    renderCluster();
  }

  // ================================================================== document tools

  /** Wire bibliography, refresh and preferences buttons. */
  function wireDocumentTools() {
    $('#btnBib').addEventListener('click', async () => {
      try {
        const result = await controller.insertBibliography();
        toast(result === 'created' ? 'Bibliography inserted' : 'Bibliography updated', {
          kind: 'ok', action: { label: 'Go to bibliography', run: () => controller.goToBibliography() },
        });
      } catch (e) {
        showError(e, 'Could not insert the bibliography');
      }
    });
    $('#btnRefresh').addEventListener('click', async () => {
      try {
        const s = await controller.refresh();
        toast(`Refreshed ${s.citations} citation${s.citations === 1 ? '' : 's'}`
          + (s.styleSource === 'cache' ? ' (offline copy of the style)' : ''), { kind: 'ok' });
      } catch (e) {
        showError(e, 'Refresh failed');
      }
    });
    $('#btnPrefs').addEventListener('click', openPrefs);
    $('#styleChip').addEventListener('click', openPrefs);
    $('#bannerClose').addEventListener('click', () => { $('#banner').hidden = true; });
  }

  /** @param {object} s controller summary */
  function renderSummary(s) {
    if (!s) return;
    const parts = [];
    parts.push(s.citations ? `${s.citations} citation${s.citations === 1 ? '' : 's'}` : 'No citations yet');
    if (s.sources) parts.push(`${s.sources} source${s.sources === 1 ? '' : 's'}`);
    if (s.bibliography) parts.push('bibliography');
    if (s.styleSource === 'cache') parts.push('offline style');
    $('#docStats').textContent = parts.join(' · ');
    $('#btnBib').title = s.bibliography ? 'Update the bibliography (and show where it is)' : 'Insert the bibliography at the cursor';
    renderStyleChip();
    const warn = (s.warnings || [])[0];
    if (warn) showBanner('warning', esc(warn));
    else hideBanner('warning');
    if (s.missing) {
      showBanner('missing', `${s.missing} citation${s.missing > 1 ? 's have' : ' has'} no saved data (copied from another document?). Edit or remove ${s.missing > 1 ? 'them' : 'it'}.`);
    } else {
      hideBanner('missing');
    }
  }

  /** Update the style name under the logo. */
  function renderStyleChip() {
    if (!controller) return;
    const p = controller.prefs;
    const meta = ui.styles.find(s => s.id === p.style);
    $('#styleName').textContent = `${(meta && meta.title) || p.styleTitle || p.style} · ${p.locale}`;
  }

  // ================================================================== preferences

  /** Fetch the installed styles (quietly ignored when offline). */
  async function loadStyles() {
    try {
      ui.styles = await api.styles();
      renderStyleChip();
      if (!$('#sheet').hidden) renderStyleList();
    } catch (e) { /* offline */ }
  }

  /** Wire the preferences sheet. */
  function wirePrefs() {
    $('#localeSelect').innerHTML = RH.LOCALES.map(([v, t]) => `<option value="${v}">${t}</option>`).join('');
    $('#sheetClose').addEventListener('click', closePrefs);
    $('#sheet').addEventListener('keydown', ev => { if (ev.key === 'Escape') closePrefs(); });
    $('#styleFilter').addEventListener('input', renderStyleList);
    $('#styleList').addEventListener('click', ev => {
      const b = ev.target.closest('.style-opt');
      if (!b) return;
      ui.prefsDraft.style = b.dataset.id;
      renderStyleList();
      renderPrefsHint();
    });
    $('#localeSelect').addEventListener('change', ev => { ui.prefsDraft.locale = ev.target.value; });
    $('#appearanceSelect').addEventListener('change', ev => { ui.prefsDraft.appearance = ev.target.value; });
    $('#btnApplyPrefs').addEventListener('click', applyPrefs);

    $('#btnUnlink').addEventListener('click', () => {
      $('#unlinkConfirm').hidden = false;
      $('#btnUnlink').hidden = true;
      $('#unlinkWord').value = '';
      $('#btnUnlinkGo').disabled = true;
      $('#unlinkWord').focus();
    });
    $('#unlinkWord').addEventListener('input', ev => {
      $('#btnUnlinkGo').disabled = ev.target.value.trim().toUpperCase() !== 'UNLINK';
    });
    $('#btnUnlinkCancel').addEventListener('click', resetUnlink);
    $('#btnUnlinkGo').addEventListener('click', async () => {
      try {
        const n = await controller.unlink();
        resetUnlink();
        closePrefs();
        resetBuilder();
        toast(`Unlinked ${n} field${n === 1 ? '' : 's'}. The document now contains plain text only.`, { kind: 'ok', ms: 5000 });
      } catch (e) {
        showError(e, 'Unlink failed');
      }
    });
  }

  function resetUnlink() {
    $('#unlinkConfirm').hidden = true;
    $('#btnUnlink').hidden = false;
  }

  function openPrefs() {
    ui.prefsDraft = Object.assign({}, controller.prefs);
    $('#localeSelect').value = ui.prefsDraft.locale;
    $('#appearanceSelect').value = ui.prefsDraft.appearance || 'BoundingBox';
    $('#styleFilter').value = '';
    $('#sheet').hidden = false;
    resetUnlink();
    renderStyleList();
    renderPrefsHint();
    if (!ui.styles.length) loadStyles();
    const sel = $('#styleList .selected');
    if (sel) sel.scrollIntoView({ block: 'center' });
    $('#styleFilter').focus();
  }

  function closePrefs() {
    $('#sheet').hidden = true;
    $('#q').focus();
  }

  /** Styles grouped by citation format, filtered by the search box. */
  function renderStyleList() {
    const box = $('#styleList');
    const q = $('#styleFilter').value.trim().toLowerCase();
    const current = ui.prefsDraft ? ui.prefsDraft.style : controller.prefs.style;
    let styles = ui.styles.slice();
    if (!styles.length) {
      const p = controller.prefs;
      styles = [{ id: p.style, title: p.styleTitle || p.style, format: p.styleFormat || '', source: 'document' }];
    }
    const groups = [['author-date', 'Author-date'], ['numeric', 'Numeric'], ['note', 'Notes (footnotes)'],
      ['author', 'Author'], ['', 'Other']];
    const matches = s => !q || s.title.toLowerCase().includes(q) || s.id.includes(q);
    const html = groups.map(([fmt, label]) => {
      const list = styles.filter(s => (fmt ? s.format === fmt : !['author-date', 'numeric', 'note', 'author'].includes(s.format)) && matches(s));
      if (!list.length) return '';
      return `<div class="style-group">${label}</div>` + list.map(s => `
        <button type="button" class="style-opt${s.id === current ? ' selected' : ''}" role="radio"
                aria-checked="${s.id === current}" data-id="${esc(s.id)}">
          <span class="radio"></span><span class="s-title">${esc(s.title)}</span>
          ${s.source === 'user' ? '<span class="s-src">installed</span>' : ''}
        </button>`).join('');
    }).join('');
    box.innerHTML = html || '<div class="results-state">No style matches.</div>';
  }

  function renderPrefsHint() {
    const meta = ui.styles.find(s => s.id === ui.prefsDraft.style) || {};
    let hint = '';
    if (meta.format === 'note') {
      hint = adapter.supportsFootnotes
        ? 'Note style: citations are placed in footnotes; existing in-text citations will be moved.'
        : 'Note style: this Word version cannot create footnotes from add-ins, citations stay in the text.';
    } else if (controller.engine.isNoteStyle) {
      hint = 'Citations currently in footnotes will be moved back into the text.';
    } else if (api.online === false) {
      hint = 'Offline: only styles already used on this computer can be applied.';
    }
    $('#prefsHint').textContent = hint;
  }

  async function applyPrefs() {
    const d = ui.prefsDraft;
    const meta = ui.styles.find(s => s.id === d.style) || {};
    try {
      const s = await controller.setPrefs({
        style: d.style, styleTitle: meta.title || d.styleTitle || d.style, styleFormat: meta.format || d.styleFormat || '',
        locale: d.locale, appearance: d.appearance,
      });
      closePrefs();
      toast(`Style set to ${meta.title || d.style}` + (s.moved ? `, ${s.moved} citation${s.moved > 1 ? 's' : ''} moved` : ''), { kind: 'ok' });
      renderCluster();
    } catch (e) {
      showError(e, 'Could not change the style');
    }
  }

  boot().catch(e => showError(e, 'ReviewHunter failed to start'));
})();
