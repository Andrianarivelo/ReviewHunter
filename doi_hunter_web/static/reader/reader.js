/**
 * reader.js - Entry point of the ReviewHunter PDF reader (/reader?attachment=ID).
 *
 * Responsibilities:
 *   1. Boot: validate the attachment, fetch metadata, load the PDF with
 *      progress and password support, and show clear error states.
 *   2. Toolbar: page navigation, zoom, tools, color, theme, sidebars.
 *   3. Keyboard shortcuts.
 *   4. Per-attachment view state persistence (page, zoom, sidebars).
 *
 * Modules: viewer.js (pages, thumbnails, outline), annotations.js, find.js, util.js.
 */

import { COLORS, api, ApiError, applyIcons, icon, toast, el, loadJSON, saveJSON, debounce, copyText, formatAuthors } from './util.js';
import { PdfViewer, ThumbnailView, buildOutline } from './viewer.js';
import { AnnotationController } from './annotations.js';
import { FindController } from './find.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';

/** Shorthand for getElementById. */
const $ = (id) => document.getElementById(id);

/** Mutable reader state shared by the toolbar handlers. */
const state = {
  attachmentId: null,
  attachment: null,
  item: null,
  pdf: null,
  tool: 'select',
  color: COLORS[0].hex,
  view: null,
  viewer: null,
  thumbs: null,
  annotations: null,
  find: null,
  outlineBuilt: false,
};

/** Default per-attachment view state. */
const DEFAULT_VIEW = { page: 1, offset: 0, zoomMode: 'auto', left: false, right: true, leftTab: 'thumbs' };

// =================================================================== boot

/** Start the reader: parse the URL, then load. */
function main() {
  applyIcons();
  const params = new URLSearchParams(location.search);
  const raw = params.get('attachment');
  const id = raw && /^\d+$/.test(raw) ? Number(raw) : null;
  state.color = localStorage.getItem('rhReaderColor') || COLORS[0].hex;
  buildColorMenu();
  bindThemeMenu();
  if (!id) {
    setTitle('No document', '');
    showState({
      iconName: 'file',
      title: 'No attachment selected',
      text: 'Open a PDF from the library to read and annotate it here.',
      actions: [{ label: 'Back to library', href: '/' }],
    });
    return;
  }
  state.attachmentId = id;
  state.view = loadJSON(`rhReader:${id}`, DEFAULT_VIEW);
  applySidebarState();
  boot();
}

/** Load attachment metadata and the PDF; safe to call again for "Retry". */
async function boot() {
  hideState();
  showSkeleton();
  setProgress(0.02);
  let att;
  try {
    att = await api(`/api/attachments/${state.attachmentId}`);
  } catch (err) {
    setProgress(null);
    return handleBootError(err);
  }
  state.attachment = att;
  document.title = `${att.title || 'Attachment'} - ReviewHunter`;
  loadItemMeta(att.item_id);

  const isPdf = /pdf/i.test(att.mime || '') || /\.pdf$/i.test(att.path || '') || /\.pdf($|\?)/i.test(att.url || '');
  if (att.kind && /link/i.test(att.kind) && !att.path) {
    setProgress(null);
    return showState({
      iconName: 'external', title: 'This attachment is a link',
      text: 'Linked URLs cannot be displayed in the reader.',
      actions: [att.url ? { label: 'Open link', href: att.url, external: true } : null, { label: 'Back to library', href: '/' }].filter(Boolean),
    });
  }
  if (!isPdf) {
    setProgress(null);
    return showState({
      iconName: 'file', title: 'Not a PDF',
      text: `The built-in reader only supports PDF files${att.mime ? ` (this file is ${att.mime})` : ''}.`,
      actions: [{ label: 'Open externally', primary: true, onClick: openExternally }, { label: 'Back to library', href: '/' }],
    });
  }
  if (att.exists === false) {
    setProgress(null);
    return showState({
      iconName: 'alert', title: 'File missing on disk',
      text: `ReviewHunter cannot find this file${att.path ? `:\n${att.path}` : '.'}`,
      actions: [{ label: 'Retry', primary: true, onClick: boot }, { label: 'Back to library', href: '/' }],
    });
  }
  await loadPdf();
}

/**
 * Show a friendly state for API failures during boot.
 * @param {ApiError} err
 */
function handleBootError(err) {
  if (err.status === 0) {
    return showState({
      iconName: 'wifiOff', title: 'Server offline',
      text: 'The ReviewHunter server is not responding. Start the app, then retry.',
      actions: [{ label: 'Retry', primary: true, onClick: boot }],
    });
  }
  if (err.status === 404) {
    setTitle('Attachment not found', '');
    return showState({
      iconName: 'file', title: 'Attachment not found',
      text: `There is no attachment with id ${state.attachmentId}. It may have been deleted.`,
      actions: [{ label: 'Back to library', href: '/' }],
    });
  }
  return showState({
    iconName: 'alert', title: 'Could not open attachment', text: err.message,
    actions: [{ label: 'Retry', primary: true, onClick: boot }, { label: 'Back to library', href: '/' }],
  });
}

/**
 * Fetch parent item metadata for the toolbar title (non-blocking).
 * @param {number|null} itemId
 */
async function loadItemMeta(itemId) {
  if (!itemId) { setTitle(state.attachment.title || 'Attachment', ''); return; }
  try {
    const item = await api(`/api/items/${itemId}`);
    state.item = item;
    const authors = formatAuthors(item.creators && item.creators.length ? item.creators : item.authors);
    const sub = [authors, item.year, item.journal].filter(Boolean).join(' · ');
    setTitle(item.title || state.attachment.title || 'Untitled', sub);
    document.title = `${item.title || 'Reader'} - ReviewHunter`;
  } catch (err) {
    setTitle(state.attachment.title || 'Attachment', '');
  }
}

/** Load the PDF bytes with progress and password prompts, then build the UI. */
async function loadPdf() {
  const task = pdfjsLib.getDocument({
    url: `/api/attachments/${state.attachmentId}/file`,
    isEvalSupported: false,
    enableXfa: false,
  });
  task.onProgress = ({ loaded, total }) => {
    const t = total || (state.attachment && state.attachment.size) || 0;
    if (t) setProgress(Math.min(0.95, loaded / t));
    const label = document.querySelector('.rh-skel-label');
    if (label && t) label.textContent = `Loading ${Math.round((loaded / t) * 100)}%`;
  };
  task.onPassword = (update, reason) => askPassword(update, reason, task);
  let pdf;
  try {
    pdf = await task.promise;
  } catch (err) {
    setProgress(null);
    return handlePdfError(err);
  }
  setProgress(1);
  setTimeout(() => setProgress(null), 350);
  state.pdf = pdf;
  await setupDocument(pdf);
}

/**
 * Map pdf.js loading errors to states.
 * @param {any} err
 */
function handlePdfError(err) {
  const name = err && err.name;
  if (name === 'PasswordException') {
    return showState({ iconName: 'lock', title: 'Password required', text: 'The document could not be unlocked.', actions: [{ label: 'Try again', primary: true, onClick: boot }] });
  }
  if (name === 'MissingPDFException' || (err && err.status === 404)) {
    return showState({ iconName: 'alert', title: 'File missing on disk', text: 'The server could not read the PDF file for this attachment.', actions: [{ label: 'Retry', primary: true, onClick: boot }, { label: 'Back to library', href: '/' }] });
  }
  if (name === 'InvalidPDFException') {
    return showState({ iconName: 'file', title: 'Invalid or corrupted PDF', text: 'pdf.js could not parse this file. Try your system PDF app.', actions: [{ label: 'Open externally', primary: true, onClick: openExternally }] });
  }
  if (name === 'UnexpectedResponseException' && err.status === 0) {
    return handleBootError(new ApiError('offline', 0));
  }
  return showState({ iconName: 'alert', title: 'Could not load PDF', text: (err && err.message) || String(err), actions: [{ label: 'Retry', primary: true, onClick: boot }, { label: 'Open externally', onClick: openExternally }] });
}

/**
 * Password dialog used by pdf.js onPassword.
 * @param {(pw: string) => void} update - pdf.js callback
 * @param {number} reason - 1 need password, 2 incorrect password
 * @param {any} task - loading task (destroyed on cancel)
 */
function askPassword(update, reason, task) {
  const incorrect = reason === pdfjsLib.PasswordResponses.INCORRECT_PASSWORD;
  const card = showState({
    iconName: 'lock',
    title: 'This PDF is password protected',
    text: incorrect ? 'Incorrect password, please try again.' : 'Enter the password to open the document.',
    actions: [],
  });
  const form = el('form', 'rh-pw-form');
  const input = el('input', 'rh-input rh-pw-input');
  input.type = 'password';
  input.placeholder = 'Password';
  input.autocomplete = 'off';
  const ok = el('button', 'rh-cta rh-cta-primary', 'Unlock');
  ok.type = 'submit';
  const cancel = el('button', 'rh-cta', 'Cancel');
  cancel.type = 'button';
  cancel.addEventListener('click', () => { task.destroy(); showState({ iconName: 'lock', title: 'Password required', text: 'The document stays locked.', actions: [{ label: 'Try again', primary: true, onClick: boot }] }); });
  form.append(input, ok, cancel);
  form.addEventListener('submit', (e) => { e.preventDefault(); hideState(); showSkeleton(); update(input.value); });
  card.append(form);
  if (incorrect) card.classList.add('is-shake');
  setTimeout(() => input.focus(), 0);
}

/**
 * Build the viewer, sidebars and controllers for a loaded document.
 * @param {any} pdf
 */
async function setupDocument(pdf) {
  // A password prompt or a retried error may have disabled the toolbar.
  document.querySelectorAll('.rh-toolbar :disabled').forEach((n) => { n.disabled = false; });
  applySidebarState();
  const viewer = new PdfViewer($('viewer'), $('pages'));
  state.viewer = viewer;
  window.rhReader = state; // handy for debugging and automated tests
  $('page-total').textContent = `/ ${pdf.numPages}`;
  $('page-input').style.width = `${Math.max(2, String(pdf.numPages).length) + 1.6}ch`;

  state.annotations = new AnnotationController({
    attachmentId: state.attachmentId,
    viewer,
    listEl: $('ann-list'),
    totalEl: $('ann-total'),
    filtersEl: $('ann-filters'),
    searchEl: $('ann-search'),
    getTool: () => state.tool,
    getColor: () => state.color,
    setColor,
  });
  state.annotations.addEventListener('notecreated', () => setTool('select'));

  state.find = new FindController({
    viewer, bar: $('findbar'), input: $('find-input'), countEl: $('find-count'),
    prevBtn: $('find-prev'), nextBtn: $('find-next'), closeBtn: $('find-close'),
    onToggle: (open) => $('btn-find').classList.toggle('is-active', open),
  });

  viewer.addEventListener('pagechange', (e) => {
    const i = e.detail.index;
    $('page-input').value = String(i + 1);
    $('btn-prev').disabled = i === 0;
    $('btn-next').disabled = i === pdf.numPages - 1;
    if (state.thumbs) state.thumbs.setCurrent(i);
  });
  viewer.addEventListener('scalechange', (e) => syncZoomUi(e.detail));
  viewer.addEventListener('scrolled', persistView);

  await viewer.setDocument(pdf, { zoomMode: state.view.zoomMode, page: state.view.page, offset: state.view.offset });
  clearSkeleton();

  state.thumbs = new ThumbnailView($('panel-thumbs'), viewer);
  state.thumbs.build();
  state.thumbs.setCurrent(viewer.currentIndex);
  if (state.view.leftTab === 'outline') selectLeftTab('outline');
  else buildOutlineOnce();

  try {
    await state.annotations.load();
  } catch (err) {
    toast(`Could not load annotations: ${err.message}`, 'error', 5000);
  }
  bindToolbar();
  bindKeyboard();
  viewer.container.focus({ preventScroll: true });
}

// =================================================================== toolbar

/** Attach all toolbar listeners (once the document exists). */
function bindToolbar() {
  const v = state.viewer;
  $('btn-prev').addEventListener('click', () => v.scrollToPage(v.currentIndex - 1));
  $('btn-next').addEventListener('click', () => v.scrollToPage(v.currentIndex + 1));
  const pageInput = $('page-input');
  pageInput.addEventListener('focus', () => pageInput.select());
  pageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const n = parseInt(pageInput.value, 10);
      if (Number.isFinite(n)) v.scrollToPage(n - 1);
      else pageInput.value = String(v.currentIndex + 1);
      v.container.focus({ preventScroll: true });
    } else if (e.key === 'Escape') {
      pageInput.value = String(v.currentIndex + 1);
      v.container.focus({ preventScroll: true });
    }
  });
  pageInput.addEventListener('blur', () => { pageInput.value = String(v.currentIndex + 1); });

  $('btn-zoom-in').addEventListener('click', () => v.zoomBy(1.1));
  $('btn-zoom-out').addEventListener('click', () => v.zoomBy(0.9));
  $('zoom-select').addEventListener('change', (e) => {
    const val = e.target.value;
    if (val === 'custom') return;
    v.setZoom(Number.isNaN(Number(val)) ? val : Number(val));
    v.container.focus({ preventScroll: true });
  });

  // Ctrl + wheel zooms around the cursor.
  v.container.addEventListener('wheel', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const now = Date.now();
    if (now - (v._lastWheelZoom || 0) < 60) return;
    v._lastWheelZoom = now;
    v.zoomBy(e.deltaY < 0 ? 1.1 : 0.9, { x: e.clientX, y: e.clientY });
  }, { passive: false });

  document.querySelectorAll('.rh-tool').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));
  $('btn-find').addEventListener('click', () => (state.find.isOpen ? state.find.close() : state.find.open()));
  $('btn-left-sidebar').addEventListener('click', () => toggleSidebar('left'));
  $('btn-right-sidebar').addEventListener('click', () => toggleSidebar('right'));
  document.querySelectorAll('.rh-tab').forEach((t) => t.addEventListener('click', () => selectLeftTab(t.dataset.tab)));

  $('btn-to-note').addEventListener('click', addNoteFromAnnotations);
  $('btn-copy-md').addEventListener('click', async () => {
    const md = state.annotations.toMarkdown();
    if (!md) { toast('No annotations to copy', 'info', 1800); return; }
    if (await copyText(md)) toast(`Copied ${state.annotations.filtered().length} annotations as Markdown`, 'success');
    else toast('Clipboard unavailable', 'error');
  });

  window.addEventListener('beforeunload', () => { state.annotations.flushAll(); persistView.flush(); });
}

/** Controls that make no sense without a document. */
function disableDocumentControls() {
  const external = state.attachment ? '' : ', #btn-open-external';
  document.querySelectorAll(`.rh-tb-center button, .rh-tb-center input, .rh-tb-center select, #btn-find, #btn-left-sidebar, #btn-right-sidebar${external}`)
    .forEach((n) => { n.disabled = true; });
  $('sidebar-left').classList.add('is-collapsed');
  $('sidebar-right').classList.add('is-collapsed');
}

/** "Open externally" also works in error states (before the document loads). */
$('btn-open-external').addEventListener('click', () => openExternally());

/** Ask the server to open the file in the system PDF application. */
async function openExternally() {
  if (!state.attachmentId) return;
  try {
    await api(`/api/attachments/${state.attachmentId}/open`, { method: 'POST' });
    toast('Opened in your system PDF app', 'success', 2000);
  } catch (err) {
    toast(`Could not open file: ${err.message}`, 'error');
  }
}

/** POST to-note and confirm with a toast. */
async function addNoteFromAnnotations() {
  if (!state.annotations.items.length) { toast('Add some annotations first', 'info', 2000); return; }
  state.annotations.flushAll();
  const btn = $('btn-to-note');
  btn.disabled = true;
  try {
    await api(`/api/attachments/${state.attachmentId}/annotations/to-note`, { method: 'POST' });
    toast('Note created in ReviewHunter', 'success');
  } catch (err) {
    toast(`Could not create note: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

/**
 * Activate an annotation tool.
 * @param {'select'|'highlight'|'underline'|'note'} tool
 */
function setTool(tool) {
  state.tool = tool;
  document.querySelectorAll('.rh-tool').forEach((b) => {
    const on = b.dataset.tool === tool;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-checked', String(on));
  });
  const v = $('viewer');
  v.dataset.tool = tool;
  if (state.annotations) state.annotations.hideSelectionPopup();
}

/** Build the color dropdown. */
function buildColorMenu() {
  const menu = $('color-menu');
  menu.textContent = '';
  for (const c of COLORS) {
    const item = el('button', 'rh-menu-item rh-color-item');
    item.dataset.color = c.hex;
    const dot = el('span', 'rh-dot rh-dot-static');
    dot.style.setProperty('--dot', c.hex);
    item.append(dot, document.createTextNode(c.name));
    item.addEventListener('click', () => { setColor(c.hex); closeMenus(); });
    menu.append(item);
  }
  $('btn-color').addEventListener('click', (e) => { e.stopPropagation(); toggleMenu('color-menu'); });
  setColor(state.color);
}

/**
 * Change the current annotation color.
 * @param {string} hex
 */
function setColor(hex) {
  state.color = hex;
  $('color-swatch').style.background = hex;
  document.querySelectorAll('.rh-color-item').forEach((i) => i.classList.toggle('is-active', i.dataset.color === hex));
  try { localStorage.setItem('rhReaderColor', hex); } catch (e) { /* ignore */ }
}

/**
 * Open one dropdown menu (closing others).
 * @param {string} id
 */
function toggleMenu(id) {
  const menu = $(id);
  const willOpen = menu.hidden;
  closeMenus();
  menu.hidden = !willOpen;
}

/** Close every dropdown menu. */
function closeMenus() {
  document.querySelectorAll('.rh-menu').forEach((m) => { m.hidden = true; });
}
document.addEventListener('click', (e) => { if (!e.target.closest('.rh-menu')) closeMenus(); });

/** Theme dropdown: light/dark shared with the library (localStorage.dhDark) + page inversion. */
function bindThemeMenu() {
  const root = document.documentElement;
  /** Reflect the theme in the button icon and menu state. */
  const sync = () => {
    const dark = root.classList.contains('dark');
    const btn = $('btn-theme');
    btn.innerHTML = icon(dark ? 'moon' : 'sun');
    document.querySelectorAll('[data-theme]').forEach((b) => b.classList.toggle('is-active', (b.dataset.theme === 'dark') === dark));
    $('invert-toggle').checked = root.classList.contains('invert-pages');
  };
  $('btn-theme').addEventListener('click', (e) => { e.stopPropagation(); toggleMenu('theme-menu'); });
  document.querySelectorAll('[data-theme]').forEach((b) => b.addEventListener('click', () => {
    root.classList.toggle('dark', b.dataset.theme === 'dark');
    try { localStorage.setItem('dhDark', String(b.dataset.theme === 'dark')); } catch (e) { /* ignore */ }
    sync();
    closeMenus();
  }));
  $('invert-toggle').addEventListener('change', (e) => {
    root.classList.toggle('invert-pages', e.target.checked);
    try { localStorage.setItem('rhReaderInvert', String(e.target.checked)); } catch (err) { /* ignore */ }
  });
  $('theme-menu').addEventListener('click', (e) => e.stopPropagation());
  sync();
}

/**
 * Update the zoom select to the current zoom.
 * @param {{zoom: number, mode: string|number}} detail
 */
function syncZoomUi({ zoom, mode }) {
  const select = $('zoom-select');
  const custom = $('zoom-custom');
  const pct = `${Math.round(zoom * 100)}%`;
  if (typeof mode === 'string') {
    select.value = mode;
    custom.hidden = true;
  } else {
    const match = [...select.options].find((o) => o.value !== 'custom' && Number(o.value) === mode);
    if (match) { select.value = match.value; custom.hidden = true; }
    else { custom.textContent = pct; custom.hidden = false; select.value = 'custom'; }
  }
  select.title = `Zoom ${pct}`;
  persistView();
}

// =================================================================== sidebars

/** Apply stored sidebar visibility. */
function applySidebarState() {
  $('sidebar-left').classList.toggle('is-collapsed', !state.view.left);
  $('sidebar-right').classList.toggle('is-collapsed', !state.view.right);
  $('btn-left-sidebar').classList.toggle('is-active', state.view.left);
  $('btn-right-sidebar').classList.toggle('is-active', state.view.right);
}

/**
 * Show or hide a sidebar.
 * @param {'left'|'right'} side
 */
function toggleSidebar(side) {
  state.view[side] = !state.view[side];
  applySidebarState();
  if (side === 'left' && state.thumbs) state.thumbs.refresh();
  persistView();
}

/**
 * Switch the left sidebar tab.
 * @param {'thumbs'|'outline'} tab
 */
function selectLeftTab(tab) {
  state.view.leftTab = tab;
  document.querySelectorAll('.rh-tab').forEach((t) => t.classList.toggle('is-active', t.dataset.tab === tab));
  $('panel-thumbs').hidden = tab !== 'thumbs';
  $('panel-outline').hidden = tab !== 'outline';
  if (tab === 'outline') buildOutlineOnce();
  if (tab === 'thumbs' && state.thumbs) { state.thumbs.setCurrent(state.viewer.currentIndex); state.thumbs.refresh(); }
  persistView();
}

/** Build the outline the first time it is needed. */
async function buildOutlineOnce() {
  if (state.outlineBuilt || !state.pdf) return;
  state.outlineBuilt = true;
  await buildOutline(state.pdf, $('panel-outline'), state.viewer);
}

// =================================================================== keyboard

/** Global keyboard shortcuts (ignored while typing in inputs, except Escape/Ctrl+F). */
function bindKeyboard() {
  const v = state.viewer;
  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);

    if (mod && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); state.find.open(); return; }
    if (mod && (e.key === '+' || e.key === '=')) { e.preventDefault(); v.zoomBy(1.1); return; }
    if (mod && (e.key === '-' || e.key === '_')) { e.preventDefault(); v.zoomBy(0.9); return; }
    if (mod && e.key === '0') { e.preventDefault(); v.setZoom('auto'); return; }
    if (typing) return;
    if (e.key === 'F3') { e.preventDefault(); state.find.isOpen ? state.find.step(e.shiftKey ? -1 : 1) : state.find.open(); return; }
    if (mod || e.altKey) return;

    const c = v.container;
    switch (e.key) {
      case 'ArrowDown': c.scrollBy({ top: 48 }); break;
      case 'ArrowUp': c.scrollBy({ top: -48 }); break;
      case 'ArrowRight': if (c.scrollWidth > c.clientWidth + 2) return; v.scrollToPage(v.currentIndex + 1); break;
      case 'ArrowLeft': if (c.scrollWidth > c.clientWidth + 2) return; v.scrollToPage(v.currentIndex - 1); break;
      case 'PageDown': case ' ': c.scrollBy({ top: (e.shiftKey ? -1 : 1) * c.clientHeight * 0.9 }); break;
      case 'PageUp': c.scrollBy({ top: -c.clientHeight * 0.9 }); break;
      case 'Home': c.scrollTo({ top: 0 }); break;
      case 'End': c.scrollTo({ top: c.scrollHeight }); break;
      case 'h': case 'H': setTool(state.tool === 'highlight' ? 'select' : 'highlight'); break;
      case 'u': case 'U': setTool(state.tool === 'underline' ? 'select' : 'underline'); break;
      case 'n': case 'N': setTool(state.tool === 'note' ? 'select' : 'note'); break;
      case 'v': case 'V': setTool('select'); break;
      case 'Escape':
        if (state.find.isOpen) state.find.close();
        state.annotations.select(null);
        state.annotations.hideSelectionPopup();
        closeMenus();
        setTool('select');
        break;
      case 'Delete': case 'Backspace':
        if (state.annotations.selectedId !== null) state.annotations.remove(state.annotations.selectedId);
        else return;
        break;
      default: return;
    }
    e.preventDefault();
  });
}

// =================================================================== view state

/** Persist page, in-page offset, zoom and sidebars (debounced). */
const persistView = debounce(() => {
  const v = state.viewer;
  if (!v || !state.attachmentId || !v.pages.length) return;
  const pv = v.pages[v._indexAtY(v.container.scrollTop)];
  const offset = pv ? Math.max(0, (v.container.scrollTop - pv.el.offsetTop) / pv.el.offsetHeight) : 0;
  state.view.page = pv ? pv.index + 1 : 1;
  state.view.offset = Math.round(offset * 1000) / 1000;
  state.view.zoomMode = v.zoomMode;
  saveJSON(`rhReader:${state.attachmentId}`, state.view);
}, 400);

// =================================================================== UI helpers

/**
 * Set the toolbar title and subtitle.
 * @param {string} title
 * @param {string} sub
 */
function setTitle(title, sub) {
  $('doc-title').textContent = title;
  $('doc-title').title = title;
  $('doc-sub').textContent = sub;
  $('doc-sub').hidden = !sub;
}

/**
 * Show the thin top progress bar; null hides it.
 * @param {number|null} fraction
 */
function setProgress(fraction) {
  const bar = $('progress');
  if (fraction === null) { bar.classList.remove('is-on'); return; }
  bar.classList.add('is-on');
  $('progress-bar').style.width = `${Math.round(fraction * 100)}%`;
}

/** Placeholder pages with a shimmer while the document loads. */
function showSkeleton() {
  const pages = $('pages');
  pages.textContent = '';
  const wrap = el('div', 'rh-skeleton');
  for (let i = 0; i < 2; i++) {
    const p = el('div', 'rh-skel-page');
    for (let j = 0; j < 9; j++) {
      const line = el('div', 'rh-skel-line');
      line.style.width = `${[92, 88, 95, 60, 90, 85, 93, 70, 40][j]}%`;
      p.append(line);
    }
    wrap.append(p);
  }
  wrap.append(el('div', 'rh-skel-label', 'Loading document'));
  pages.append(wrap);
}

/** Remove the loading skeleton (pages replace it). */
function clearSkeleton() {
  const sk = document.querySelector('.rh-skeleton');
  if (sk) sk.remove();
}

/**
 * Show a centered state card over the document area.
 * @param {{iconName: string, title: string, text: string, actions: {label: string, href?: string, external?: boolean, primary?: boolean, onClick?: () => void}[]}} opts
 * @returns {HTMLElement} the card element
 */
function showState({ iconName, title, text, actions }) {
  clearSkeleton();
  if (!state.pdf) disableDocumentControls();
  $('pages').textContent = '';
  const host = $('state');
  host.textContent = '';
  host.hidden = false;
  const card = el('div', 'rh-state-card');
  const badge = el('div', 'rh-state-icon');
  badge.innerHTML = icon(iconName, 22);
  card.append(badge, el('h1', 'rh-state-title', title), el('p', 'rh-state-text', text));
  const row = el('div', 'rh-state-actions');
  for (const a of actions) {
    const node = a.href ? el('a', 'rh-cta', a.label) : el('button', 'rh-cta', a.label);
    if (a.primary) node.classList.add('rh-cta-primary');
    if (a.href) { node.href = a.href; if (a.external) { node.target = '_blank'; node.rel = 'noopener'; } }
    if (a.onClick) node.addEventListener('click', a.onClick);
    row.append(node);
  }
  if (actions.length) card.append(row);
  host.append(card);
  return card;
}

/** Hide the state card. */
function hideState() {
  $('state').hidden = true;
  $('state').textContent = '';
}

main();
