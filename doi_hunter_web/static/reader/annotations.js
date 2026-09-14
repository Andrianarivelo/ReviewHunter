/**
 * annotations.js - Highlights, underlines and sticky notes.
 *
 * Coordinate systems (all conversions go through the page's scale-1 viewport)
 * -------------------------------------------------------------------------
 *   client px   : what getClientRects() returns (viewport of the browser window).
 *   page px     : client px minus the page element's top-left corner.
 *   vp1 units   : page px * (vp1.width / pageElement.width). This is the pdf.js
 *                 viewport at scale 1: origin top-left, y down, page rotation
 *                 applied. Dividing by the element width (not by cssScale)
 *                 absorbs the Math.floor() rounding of page sizes.
 *   PDF points  : vp1.convertToPdfPoint(x, y) inverts the viewport transform
 *                 (flip y, remove rotation, add the view box origin), giving
 *                 unrotated PDF user space with origin bottom-left, y up.
 *
 * Stored:  position.rects = [[x1, y1, x2, y2], ...] in PDF points, normalized
 *          so x1 < x2 and y1 < y2.
 * Display: vp1.convertToViewportRectangle(rect) gives vp1 units, which we turn
 *          into percentages of the page size, so overlays follow zoom for free.
 */

import { COLORS, api, toast, debounce, el, icon, copyText, normalizeText } from './util.js';

/** Sticky note size in PDF points (Zotero uses 22 pt). */
const NOTE_SIZE = 22;

export class AnnotationController extends EventTarget {
  /**
   * @param {{attachmentId: number, viewer: import('./viewer.js').PdfViewer, listEl: HTMLElement,
   *          totalEl: HTMLElement, filtersEl: HTMLElement, searchEl: HTMLElement,
   *          getTool: () => string, getColor: () => string, setColor: (hex: string) => void}} opts
   */
  constructor(opts) {
    super();
    Object.assign(this, opts);
    /** @type {any[]} */
    this.items = [];
    this.selectedId = null;
    this.colorFilter = new Set();
    this.query = '';
    this.popover = null;
    this.selPopup = null;
    this._pendingPatches = new Map();
    this._bindViewer();
    this._bindSidebar();
  }

  // ================================================================ data

  /** Load annotations from the server and render everything. */
  async load() {
    this.items = await api(`/api/attachments/${this.attachmentId}/annotations`);
    this.items.forEach((a) => this._normalize(a));
    this._sort();
    this.renderAllOverlays();
    this.renderList();
  }

  /**
   * Make sure an annotation from the server has a parsed position object.
   * @param {any} a
   */
  _normalize(a) {
    if (typeof a.position === 'string') {
      try { a.position = JSON.parse(a.position); } catch (e) { a.position = {}; }
    }
    if (!a.position || !Array.isArray(a.position.rects)) a.position = { rects: [] };
  }

  /** Sort annotations in reading order (page, then sort_index). */
  _sort() {
    this.items.sort((a, b) => (a.page - b.page) || String(a.sort_index || '').localeCompare(String(b.sort_index || '')) || (a.id - b.id));
  }

  /**
   * Find an annotation by id.
   * @param {number} id
   * @returns {any|undefined}
   */
  get(id) {
    return this.items.find((a) => a.id === id);
  }

  /**
   * Create an annotation on the server and show it.
   * @param {{type: string, color: string, page: number, text?: string, comment?: string, rects: number[][], sortIndex: string}} data
   * @returns {Promise<any|null>} created annotation
   */
  async create(data) {
    try {
      const created = await api(`/api/attachments/${this.attachmentId}/annotations`, {
        method: 'POST',
        body: {
          type: data.type,
          color: data.color,
          page: data.page,
          text: data.text || null,
          comment: data.comment || null,
          position: { rects: data.rects },
          sort_index: data.sortIndex,
        },
      });
      this._normalize(created);
      this.items.push(created);
      this._sort();
      this.renderPageOverlays(created.page - 1);
      this.renderList();
      return created;
    } catch (err) {
      toast(`Could not save annotation: ${err.message}`, 'error');
      return null;
    }
  }

  /**
   * Update fields locally at once and persist them (comments are debounced).
   * @param {number} id
   * @param {Record<string, any>} fields
   * @param {{debounce?: boolean, source?: string}} [opts]
   */
  update(id, fields, opts = {}) {
    const a = this.get(id);
    if (!a) return;
    Object.assign(a, fields);
    if ('color' in fields || 'type' in fields || 'position' in fields) {
      this.renderPageOverlays(a.page - 1);
      this.renderList();
      if (this.popover && this.popover.annId === id) this._refreshPopover();
    }
    if ('comment' in fields) this._syncCommentViews(a, opts.source);
    const pending = { ...(this._pendingPatches.get(id) || {}), ...fields };
    this._pendingPatches.set(id, pending);
    if (opts.debounce) this._flushPatchDebounced(id);
    else this._flushPatch(id);
  }

  /**
   * Send the accumulated PATCH for an annotation.
   * @param {number} id
   */
  async _flushPatch(id) {
    const body = this._pendingPatches.get(id);
    if (!body) return;
    this._pendingPatches.delete(id);
    try {
      await api(`/api/annotations/${id}`, { method: 'PATCH', body });
    } catch (err) {
      toast(`Could not update annotation: ${err.message}`, 'error');
    }
  }

  /**
   * Debounced PATCH per annotation id (shared debounce keyed by id).
   * @param {number} id
   */
  _flushPatchDebounced(id) {
    if (!this._debouncers) this._debouncers = new Map();
    if (!this._debouncers.has(id)) this._debouncers.set(id, debounce(() => this._flushPatch(id), 600));
    this._debouncers.get(id)();
  }

  /** Flush every pending debounced PATCH (before unload or closing the popover). */
  flushAll() {
    if (this._debouncers) this._debouncers.forEach((d) => d.flush());
  }

  /**
   * Delete an annotation.
   * @param {number} id
   */
  async remove(id) {
    const a = this.get(id);
    if (!a) return;
    this._pendingPatches.delete(id);
    try {
      await api(`/api/annotations/${id}`, { method: 'DELETE' });
    } catch (err) {
      toast(`Could not delete annotation: ${err.message}`, 'error');
      return;
    }
    this.items = this.items.filter((x) => x.id !== id);
    if (this.selectedId === id) this.select(null);
    this.renderPageOverlays(a.page - 1);
    this.renderList();
  }

  // ======================================================= overlays

  /** Wire viewer events: overlay rendering, clicks, selection handling. */
  _bindViewer() {
    const v = this.viewer;
    v.addEventListener('pagesetup', (e) => this.renderPageOverlays(e.detail.index));
    v.addEventListener('scalechange', () => { this._positionPopover(); this.hideSelectionPopup(); });

    const c = v.container;
    let down = null;
    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      down = { x: e.clientX, y: e.clientY, t: Date.now() };
      // Clicks inside the popovers must not reset the selection state.
      if (e.target.closest('.rh-popover, .rh-sel-popup')) return;
      this.hideSelectionPopup();
    });
    c.addEventListener('pointerup', (e) => {
      if (e.button !== 0 || !down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4;
      down = null;
      if (e.target.closest('.rh-popover, .rh-sel-popup')) return;
      // Let the browser finalize the selection before reading it.
      setTimeout(() => this._onPointerUp(e, moved), 0);
    });
    c.addEventListener('pointermove', (e) => {
      if (e.buttons) return;
      const pageEl = e.target.closest('.rh-page');
      if (!pageEl) return;
      const hit = this.hitTest(Number(pageEl.dataset.page) - 1, e.clientX, e.clientY);
      pageEl.classList.toggle('is-over-annotation', Boolean(hit));
    });
  }

  /**
   * Handle the end of a click or drag on the document.
   * @param {PointerEvent} e
   * @param {boolean} moved - true when the pointer travelled (a drag)
   */
  _onPointerUp(e, moved) {
    const tool = this.getTool();
    const pageEl = e.target.closest && e.target.closest('.rh-page');
    const sel = window.getSelection();
    const hasSelection = sel && sel.rangeCount && !sel.isCollapsed && String(sel).trim();

    if (hasSelection) {
      const pieces = this.collectSelection();
      if (!pieces.length) return;
      if (tool === 'highlight' || tool === 'underline') {
        this.createFromPieces(pieces, tool, this.getColor());
        sel.removeAllRanges();
      } else if (tool === 'select') {
        this.showSelectionPopup(pieces);
      }
      return;
    }
    if (moved || !pageEl) return;
    const index = Number(pageEl.dataset.page) - 1;
    if (e.target.closest('.rh-sticky')) return; // handled by the sticky itself
    const hit = this.hitTest(index, e.clientX, e.clientY);
    if (hit) {
      this.select(hit.id, { openPopover: true });
      return;
    }
    if (tool === 'note') {
      this.createNoteAt(index, e.clientX, e.clientY);
      return;
    }
    this.select(null);
  }

  /** Render overlays on every page whose exact size is known. */
  renderAllOverlays() {
    this.viewer.pages.forEach((pv) => this.renderPageOverlays(pv.index));
  }

  /**
   * Convert stored PDF rects to percentage boxes of a page.
   * @param {any} pv - PageView
   * @param {number[][]} rects - PDF point rects
   * @returns {{left: number, top: number, width: number, height: number}[]} percent boxes
   */
  pdfRectsToPercent(pv, rects) {
    const vp = pv.vp1;
    return rects.map((r) => {
      // convertToViewportRectangle maps both corners through the viewport
      // transform; corners may swap under rotation, hence min/max.
      const [a, b, c, d] = vp.convertToViewportRectangle(r);
      const x1 = Math.min(a, c);
      const x2 = Math.max(a, c);
      const y1 = Math.min(b, d);
      const y2 = Math.max(b, d);
      return {
        left: (x1 / vp.width) * 100,
        top: (y1 / vp.height) * 100,
        width: ((x2 - x1) / vp.width) * 100,
        height: ((y2 - y1) / vp.height) * 100,
      };
    });
  }

  /**
   * (Re)draw highlight, underline and note overlays of one page.
   * @param {number} index - 0-based page index
   */
  renderPageOverlays(index) {
    const pv = this.viewer.pages[index];
    if (!pv || !pv.exactSize) return;
    pv.hlLayer.textContent = '';
    pv.noteLayer.textContent = '';
    for (const a of this.items) {
      if (a.page !== index + 1) continue;
      const boxes = this.pdfRectsToPercent(pv, a.position.rects);
      const selected = a.id === this.selectedId;
      if (a.type === 'note') {
        const b = boxes[0];
        if (!b) continue;
        const note = el('button', 'rh-sticky');
        note.dataset.annId = String(a.id);
        note.style.cssText = `left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%;--ann-color:${a.color}`;
        note.title = a.comment ? a.comment.slice(0, 120) : 'Note';
        note.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h18v12l-6 6H3z" fill="var(--ann-color)"/><path d="M15 21v-6h6" fill="rgba(0,0,0,.18)"/><path d="M7 8h10M7 12h6" stroke="rgba(0,0,0,.45)" stroke-width="1.6" stroke-linecap="round"/></svg>';
        note.classList.toggle('is-selected', selected);
        note.addEventListener('click', (ev) => { ev.stopPropagation(); this.select(a.id, { openPopover: true }); });
        pv.noteLayer.append(note);
        continue;
      }
      for (const b of boxes) {
        const box = el('div', a.type === 'underline' ? 'rh-ul' : 'rh-hl');
        box.dataset.annId = String(a.id);
        box.style.cssText = `left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%;--ann-color:${a.color}`;
        box.classList.toggle('is-selected', selected);
        pv.hlLayer.append(box);
      }
    }
    if (this.popover && this.popover.page === index) this._positionPopover();
  }

  /**
   * Find the top-most annotation under a client point on a page.
   * @param {number} index - page index
   * @param {number} clientX
   * @param {number} clientY
   * @returns {any|null} annotation
   */
  hitTest(index, clientX, clientY) {
    const pv = this.viewer.pages[index];
    if (!pv || !pv.exactSize) return null;
    const r = pv.el.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * 100;
    const py = ((clientY - r.top) / r.height) * 100;
    // A small tolerance (in percent of the page) makes thin underlines clickable.
    const tolX = (3 / r.width) * 100;
    const tolY = (3 / r.height) * 100;
    for (let i = this.items.length - 1; i >= 0; i--) {
      const a = this.items[i];
      if (a.page !== index + 1 || a.type === 'note') continue;
      for (const b of this.pdfRectsToPercent(pv, a.position.rects)) {
        if (px >= b.left - tolX && px <= b.left + b.width + tolX && py >= b.top - tolY && py <= b.top + b.height + tolY) return a;
      }
    }
    return null;
  }

  /**
   * Select an annotation (or clear with null), sync overlays and the list.
   * @param {number|null} id
   * @param {{openPopover?: boolean, scroll?: boolean}} [opts]
   */
  select(id, opts = {}) {
    const prev = this.selectedId;
    this.selectedId = id;
    document.querySelectorAll('.rh-hl.is-selected, .rh-ul.is-selected, .rh-sticky.is-selected')
      .forEach((n) => n.classList.remove('is-selected'));
    if (id !== null) {
      document.querySelectorAll(`[data-ann-id="${id}"]`).forEach((n) => {
        if (!n.closest('.rh-ann-list')) n.classList.add('is-selected');
      });
    }
    this.listEl.querySelectorAll('.rh-ann-card').forEach((card) => {
      card.classList.toggle('is-selected', Number(card.dataset.annId) === id);
    });
    if (id === null) this.closePopover();
    else if (opts.openPopover) this.openPopover(id);
    if (id !== null && opts.scroll) {
      const card = this.listEl.querySelector(`.rh-ann-card[data-ann-id="${id}"]`);
      if (card) card.scrollIntoView({ block: 'nearest' });
    }
    if (prev !== id) this.dispatchEvent(new CustomEvent('selectionchange', { detail: { id } }));
  }

  /**
   * Scroll the document to an annotation and flash its overlay.
   * @param {number} id
   */
  async scrollTo(id) {
    const a = this.get(id);
    if (!a) return;
    const pv = await this.viewer.ensurePage(a.page - 1);
    const boxes = this.pdfRectsToPercent(pv, a.position.rects);
    const topPct = boxes.length ? Math.min(...boxes.map((b) => b.top)) : 0;
    const c = this.viewer.container;
    const y = pv.el.offsetTop + (topPct / 100) * pv.el.offsetHeight;
    c.scrollTo({ top: Math.max(0, y - c.clientHeight * 0.3) });
    this.viewer.update();
    this.select(id);
    requestAnimationFrame(() => {
      document.querySelectorAll(`.rh-page [data-ann-id="${id}"]`).forEach((n) => {
        n.classList.remove('is-flash');
        void n.offsetWidth; // restart the CSS animation
        n.classList.add('is-flash');
      });
    });
  }

  // ======================================================= selection -> rects

  /**
   * Split the current DOM selection per page into merged page rects + text.
   * Only text nodes of pdf.js text spans are measured, so element boxes such
   * as the endOfContent helper never leak into the geometry.
   * @returns {{pv: any, rects: {x1: number, y1: number, x2: number, y2: number}[], text: string}[]}
   */
  collectSelection() {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount || sel.isCollapsed) return [];
    const range = sel.getRangeAt(0);
    const out = [];
    for (const pv of this.viewer.pages) {
      if (!pv.textReady || !range.intersectsNode(pv.textLayerEl)) continue;
      const pageRect = pv.el.getBoundingClientRect();
      const raw = [];
      let text = '';
      for (const span of pv.textDivs) {
        const tn = span.firstChild;
        if (!tn || tn.nodeType !== Node.TEXT_NODE || !range.intersectsNode(tn)) continue;
        // comparePoint < 0: point lies before the range start, so the start is
        // inside this node. > 0: point lies after the range end.
        const start = range.comparePoint(tn, 0) < 0 ? (range.startContainer === tn ? range.startOffset : tn.length) : 0;
        const end = range.comparePoint(tn, tn.length) > 0 ? (range.endContainer === tn ? range.endOffset : 0) : tn.length;
        if (end <= start) continue;
        const sub = document.createRange();
        sub.setStart(tn, start);
        sub.setEnd(tn, end);
        for (const r of sub.getClientRects()) {
          if (r.width < 0.5 || r.height < 0.5) continue;
          // Clip to the page box and move to page-local CSS px.
          const x1 = Math.max(r.left, pageRect.left) - pageRect.left;
          const y1 = Math.max(r.top, pageRect.top) - pageRect.top;
          const x2 = Math.min(r.right, pageRect.right) - pageRect.left;
          const y2 = Math.min(r.bottom, pageRect.bottom) - pageRect.top;
          if (x2 - x1 > 0.5 && y2 - y1 > 0.5) raw.push({ x1, y1, x2, y2 });
        }
        text += tn.data.slice(start, end);
        // pdf.js inserts <br> after items that end a line: keep a word break.
        // A trailing "letter-" at a line end is marked with   so a soft
        // hyphen ("princi- ples") can be joined below.
        if (end === tn.length && span.nextSibling && span.nextSibling.nodeName === 'BR') text += /[A-Za-z]-$/.test(text) ? ' ' : ' ';
      }
      const rects = mergeLineRects(raw);
      // Join soft hyphenation only when the next line continues in lowercase
      // (keeps compound names such as "Xiao-Jing" intact).
      text = text.replace(/- \s*(?=[a-z])/g, '').replace(/ /g, ' ');
      if (rects.length) out.push({ pv, rects, text: normalizeText(text) });
    }
    return out;
  }

  /**
   * Convert page-local CSS px rects to PDF point rects for storage.
   * @param {any} pv - PageView
   * @param {{x1: number, y1: number, x2: number, y2: number}[]} rects
   * @returns {number[][]}
   */
  pageRectsToPdf(pv, rects) {
    const k = pv.vp1.width / pv.el.getBoundingClientRect().width; // CSS px -> vp1 units
    const round = (n) => Math.round(n * 100) / 100;
    return rects.map((r) => {
      const [ax, ay] = pv.vp1.convertToPdfPoint(r.x1 * k, r.y1 * k);
      const [bx, by] = pv.vp1.convertToPdfPoint(r.x2 * k, r.y2 * k);
      return [round(Math.min(ax, bx)), round(Math.min(ay, by)), round(Math.max(ax, bx)), round(Math.max(ay, by))];
    });
  }

  /**
   * Build the reading-order sort key "PPPPP|YYYYYY|XXXXX" from the first rect
   * (page index, top offset and left offset in vp1 units, i.e. points from
   * the top-left of the displayed page).
   * @param {any} pv
   * @param {number[][]} pdfRects
   * @returns {string}
   */
  sortIndexFor(pv, pdfRects) {
    let top = Infinity;
    let left = Infinity;
    for (const r of pdfRects) {
      const [a, b, c, d] = pv.vp1.convertToViewportRectangle(r);
      const t = Math.min(b, d);
      const l = Math.min(a, c);
      if (t < top - 1 || (Math.abs(t - top) <= 1 && l < left)) { top = t; left = l; }
    }
    const pad = (n, w) => String(Math.max(0, Math.round(Number.isFinite(n) ? n : 0))).padStart(w, '0');
    return `${pad(pv.index, 5)}|${pad(top, 6)}|${pad(left, 5)}`;
  }

  /**
   * Create one annotation per page from collected selection pieces.
   * @param {ReturnType<AnnotationController['collectSelection']>} pieces
   * @param {'highlight'|'underline'} type
   * @param {string} color
   * @returns {Promise<any[]>} created annotations
   */
  async createFromPieces(pieces, type, color) {
    const created = [];
    for (const piece of pieces) {
      const rects = this.pageRectsToPdf(piece.pv, piece.rects);
      const ann = await this.create({
        type, color, page: piece.pv.index + 1, text: piece.text, rects,
        sortIndex: this.sortIndexFor(piece.pv, rects),
      });
      if (ann) created.push(ann);
    }
    if (created.length) this.select(created[created.length - 1].id, { scroll: true });
    return created;
  }

  /**
   * Place a sticky note centered on a click point and open its editor.
   * @param {number} index - page index
   * @param {number} clientX
   * @param {number} clientY
   */
  async createNoteAt(index, clientX, clientY) {
    const pv = await this.viewer.ensurePage(index);
    const r = pv.el.getBoundingClientRect();
    const k = pv.vp1.width / r.width;
    const [px, py] = pv.vp1.convertToPdfPoint((clientX - r.left) * k, (clientY - r.top) * k);
    const h = NOTE_SIZE / 2;
    const rects = [[px - h, py - h, px + h, py + h].map((n) => Math.round(n * 100) / 100)];
    const ann = await this.create({
      type: 'note', color: this.getColor(), page: index + 1, text: '', rects,
      sortIndex: this.sortIndexFor(pv, rects),
    });
    if (ann) {
      this.select(ann.id, { openPopover: true, scroll: true });
      this.dispatchEvent(new CustomEvent('notecreated'));
    }
  }

  // ======================================================= selection popup

  /**
   * Show the floating "annotate selection" popup next to the selection end.
   * @param {ReturnType<AnnotationController['collectSelection']>} pieces
   */
  showSelectionPopup(pieces) {
    this.hideSelectionPopup();
    const last = pieces[pieces.length - 1];
    const lastRect = last.rects[last.rects.length - 1];
    const firstRect = pieces[0].rects[0];
    const popup = el('div', 'rh-sel-popup');
    popup.addEventListener('pointerdown', (e) => e.preventDefault()); // keep the selection
    const dots = el('div', 'rh-dots');
    for (const c of COLORS) {
      const dot = el('button', 'rh-dot');
      dot.style.setProperty('--dot', c.hex);
      dot.title = `Highlight ${c.name}`;
      dot.addEventListener('click', () => this._annotateSelection('highlight', c.hex));
      dots.append(dot);
    }
    const actions = el('div', 'rh-sel-actions');
    const mk = (name, label, fn) => {
      const b = el('button', 'rh-btn rh-btn-sm rh-btn-text');
      b.innerHTML = `${icon(name, 14)}<span>${label}</span>`;
      b.addEventListener('click', fn);
      return b;
    };
    actions.append(
      mk('highlighter', 'Highlight', () => this._annotateSelection('highlight', this.getColor())),
      mk('underline', 'Underline', () => this._annotateSelection('underline', this.getColor())),
      mk('copy', 'Copy', async () => {
        const text = normalizeText(String(window.getSelection()));
        if (await copyText(text)) toast('Copied selection', 'success', 1600);
        this.hideSelectionPopup();
      }),
    );
    popup.append(dots, el('span', 'rh-sep'), actions);
    last.pv.el.append(popup);
    // Place below the last line; flip above the first line if it would leave the page.
    const pageW = last.pv.el.clientWidth;
    const pageH = last.pv.el.clientHeight;
    const w = popup.offsetWidth;
    const hPop = popup.offsetHeight;
    let left = (lastRect.x1 + lastRect.x2) / 2 - w / 2;
    left = this._clampX(last.pv, left, w, pageW);
    let top = lastRect.y2 + 8;
    if (top + hPop > pageH - 4 && last.pv === pieces[0].pv) top = Math.max(4, firstRect.y1 - hPop - 8);
    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;
    this.selPopup = popup;
  }

  /**
   * Clamp a popup's left edge (page px) so it stays inside both the page and
   * the part of the page currently visible in the horizontally scrolled viewer.
   * @param {any} pv - PageView
   * @param {number} x - desired left, page px
   * @param {number} w - popup width
   * @param {number} pageW - page width
   * @returns {number}
   */
  _clampX(pv, x, w, pageW) {
    const pr = pv.el.getBoundingClientRect();
    const cr = this.viewer.container.getBoundingClientRect();
    const minX = Math.max(4, cr.left - pr.left + 8);
    const maxX = Math.min(pageW, cr.right - pr.left - 14) - w - 4;
    return Math.max(minX, Math.min(maxX, x));
  }

  /** Hide the selection popup if visible. */
  hideSelectionPopup() {
    if (this.selPopup) { this.selPopup.remove(); this.selPopup = null; }
  }

  /**
   * Annotate the live selection from the popup.
   * @param {'highlight'|'underline'} type
   * @param {string} color
   */
  async _annotateSelection(type, color) {
    const pieces = this.collectSelection();
    this.hideSelectionPopup();
    if (!pieces.length) return;
    if (color !== this.getColor()) this.setColor(color);
    window.getSelection().removeAllRanges();
    await this.createFromPieces(pieces, type, color);
  }

  // ======================================================= popover editor

  /**
   * Open the annotation editor popover anchored under the annotation.
   * @param {number} id
   */
  openPopover(id) {
    const a = this.get(id);
    if (!a) return;
    this.closePopover();
    const pv = this.viewer.pages[a.page - 1];
    const pop = el('div', 'rh-popover');
    pop.setAttribute('role', 'dialog');
    pop.addEventListener('pointerdown', (e) => e.stopPropagation());

    const head = el('div', 'rh-pop-head');
    const dots = el('div', 'rh-dots');
    for (const c of COLORS) {
      const dot = el('button', 'rh-dot');
      dot.style.setProperty('--dot', c.hex);
      dot.title = c.name;
      dot.dataset.color = c.hex;
      dot.addEventListener('click', () => this.update(id, { color: c.hex }));
      dots.append(dot);
    }
    const grow = el('span', 'rh-grow');
    const copyBtn = el('button', 'rh-btn rh-btn-sm');
    copyBtn.title = 'Copy text';
    copyBtn.innerHTML = icon('copy', 14);
    copyBtn.addEventListener('click', async () => {
      const cur = this.get(id);
      const md = cur.text ? `> ${cur.text} (p. ${cur.page})` : `${cur.comment || ''} (p. ${cur.page})`;
      if (await copyText(md)) toast('Copied', 'success', 1400);
    });
    const delBtn = el('button', 'rh-btn rh-btn-sm rh-btn-danger');
    delBtn.title = 'Delete annotation (Delete)';
    delBtn.innerHTML = icon('trash', 14);
    delBtn.addEventListener('click', () => this.remove(id));
    head.append(dots, grow, copyBtn, delBtn);

    const ta = el('textarea', 'rh-pop-comment');
    ta.placeholder = a.type === 'note' ? 'Write a note' : 'Add comment';
    ta.value = a.comment || '';
    ta.rows = 3;
    ta.addEventListener('input', () => this.update(id, { comment: ta.value }, { debounce: true, source: 'popover' }));
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); this.select(null); this.viewer.container.focus(); }
    });
    pop.append(head, ta);
    pv.el.append(pop);
    this.popover = { el: pop, annId: id, page: pv.index, textarea: ta };
    this._refreshPopover();
    this._positionPopover();
    if (a.type === 'note' || !a.text) setTimeout(() => ta.focus(), 0);
  }

  /** Update the popover's active color dot. */
  _refreshPopover() {
    if (!this.popover) return;
    const a = this.get(this.popover.annId);
    if (!a) return;
    this.popover.el.querySelectorAll('.rh-dot').forEach((d) => d.classList.toggle('is-active', d.dataset.color === a.color));
  }

  /** Position the popover under the annotation's bounding box (page px). */
  _positionPopover() {
    if (!this.popover) return;
    const a = this.get(this.popover.annId);
    const pv = this.viewer.pages[this.popover.page];
    if (!a || !pv) return;
    const boxes = this.pdfRectsToPercent(pv, a.position.rects);
    if (!boxes.length) return;
    const W = pv.el.clientWidth;
    const H = pv.el.clientHeight;
    const left = Math.min(...boxes.map((b) => b.left)) / 100 * W;
    const right = Math.max(...boxes.map((b) => b.left + b.width)) / 100 * W;
    const top = Math.min(...boxes.map((b) => b.top)) / 100 * H;
    const bottom = Math.max(...boxes.map((b) => b.top + b.height)) / 100 * H;
    const pop = this.popover.el;
    const w = pop.offsetWidth;
    const h = pop.offsetHeight;
    let x = (left + right) / 2 - w / 2;
    x = this._clampX(pv, x, w, W);
    let y = bottom + 8;
    if (y + h > H - 4) y = Math.max(4, top - h - 8);
    pop.style.left = `${x}px`;
    pop.style.top = `${y}px`;
  }

  /** Close the popover, flushing a pending comment save. */
  closePopover() {
    if (!this.popover) return;
    this.flushAll();
    this.popover.el.remove();
    this.popover = null;
  }

  /**
   * Mirror a comment change into whichever editor did not produce it.
   * @param {any} a - annotation
   * @param {string} [source] - 'popover' | 'list'
   */
  _syncCommentViews(a, source) {
    if (source !== 'popover' && this.popover && this.popover.annId === a.id) this.popover.textarea.value = a.comment || '';
    if (source !== 'list') {
      const ta = this.listEl.querySelector(`.rh-ann-card[data-ann-id="${a.id}"] textarea`);
      if (ta && document.activeElement !== ta) { ta.value = a.comment || ''; autosize(ta); }
    }
    const sticky = document.querySelector(`.rh-sticky[data-ann-id="${a.id}"]`);
    if (sticky) sticky.title = a.comment ? a.comment.slice(0, 120) : 'Note';
  }

  // ======================================================= sidebar list

  /** Wire search and header controls of the annotations sidebar. */
  _bindSidebar() {
    this.searchEl.addEventListener('input', debounce(() => {
      this.query = this.searchEl.value.trim().toLowerCase();
      this.renderList();
    }, 120));
  }

  /**
   * Annotations passing the color filter and text query.
   * @returns {any[]}
   */
  filtered() {
    return this.items.filter((a) => {
      if (this.colorFilter.size && !this.colorFilter.has(a.color)) return false;
      if (this.query) {
        const hay = `${a.text || ''} ${a.comment || ''}`.toLowerCase();
        if (!hay.includes(this.query)) return false;
      }
      return true;
    });
  }

  /** Rebuild the color filter chips from the colors in use. */
  _renderFilters() {
    const used = [...new Set(this.items.map((a) => a.color))];
    for (const c of [...this.colorFilter]) if (!used.includes(c)) this.colorFilter.delete(c);
    this.filtersEl.textContent = '';
    this.filtersEl.hidden = used.length < 2 && !this.colorFilter.size;
    const order = COLORS.map((c) => c.hex);
    used.sort((a, b) => order.indexOf(a) - order.indexOf(b));
    for (const hex of used) {
      const chip = el('button', 'rh-dot rh-dot-sm');
      chip.style.setProperty('--dot', hex);
      chip.title = `Show only ${(COLORS.find((c) => c.hex === hex) || { name: hex }).name}`;
      chip.classList.toggle('is-active', this.colorFilter.has(hex));
      chip.addEventListener('click', () => {
        if (this.colorFilter.has(hex)) this.colorFilter.delete(hex);
        else this.colorFilter.add(hex);
        this.renderList();
      });
      this.filtersEl.append(chip);
    }
  }

  /** Rebuild the annotation cards grouped by page. */
  renderList() {
    this._renderFilters();
    this.totalEl.textContent = String(this.items.length);
    const list = this.filtered();
    const scroll = this.listEl.scrollTop;
    this.listEl.textContent = '';
    if (!this.items.length) {
      const empty = el('div', 'rh-empty-hint');
      empty.innerHTML = 'No annotations yet.<br>Select text to highlight, or press <kbd>N</kbd> to add a note.';
      this.listEl.append(empty);
      return;
    }
    if (!list.length) {
      this.listEl.append(el('div', 'rh-empty-hint', 'No annotations match the filter.'));
      return;
    }
    let page = -1;
    for (const a of list) {
      if (a.page !== page) {
        page = a.page;
        this.listEl.append(el('div', 'rh-ann-page', `Page ${page}`));
      }
      this.listEl.append(this._card(a));
    }
    this.listEl.scrollTop = scroll;
  }

  /**
   * Build one annotation card.
   * @param {any} a
   * @returns {HTMLElement}
   */
  _card(a) {
    const card = el('div', 'rh-ann-card');
    card.dataset.annId = String(a.id);
    card.style.setProperty('--ann-color', a.color);
    card.classList.toggle('is-selected', a.id === this.selectedId);
    card.tabIndex = 0;

    const head = el('div', 'rh-card-head');
    const kind = el('span', 'rh-card-kind');
    kind.innerHTML = icon(a.type === 'note' ? 'note' : a.type === 'underline' ? 'underline' : 'highlighter', 13);
    kind.append(document.createTextNode(a.type === 'note' ? 'Note' : a.type === 'underline' ? 'Underline' : 'Highlight'));
    const grow = el('span', 'rh-grow');
    const del = el('button', 'rh-btn rh-btn-xs rh-card-del');
    del.title = 'Delete';
    del.innerHTML = icon('trash', 13);
    del.addEventListener('click', (e) => { e.stopPropagation(); this.remove(a.id); });
    head.append(kind, grow, del);
    card.append(head);

    if (a.text) card.append(el('blockquote', 'rh-card-text', a.text));
    const ta = el('textarea', 'rh-card-comment');
    ta.placeholder = a.type === 'note' ? 'Write a note' : 'Add comment';
    ta.value = a.comment || '';
    ta.rows = 1;
    ta.addEventListener('input', () => {
      autosize(ta);
      this.update(a.id, { comment: ta.value }, { debounce: true, source: 'list' });
    });
    ta.addEventListener('click', (e) => e.stopPropagation());
    ta.addEventListener('focus', () => this.select(a.id));
    ta.addEventListener('blur', () => this.flushAll());
    card.append(ta);
    requestAnimationFrame(() => autosize(ta));

    card.addEventListener('click', () => this.scrollTo(a.id));
    card.addEventListener('keydown', (e) => {
      if (e.target !== card) return;
      if (e.key === 'Enter') this.scrollTo(a.id);
      if (e.key === 'Delete' || e.key === 'Backspace') this.remove(a.id);
    });
    return card;
  }

  /**
   * Markdown export: "> text (p. N)" followed by the comment paragraph.
   * @returns {string}
   */
  toMarkdown() {
    return this.filtered().map((a) => {
      if (a.text) return `> ${a.text} (p. ${a.page})` + (a.comment ? `\n\n${a.comment}` : '');
      return `${a.comment || ''} (p. ${a.page})`;
    }).join('\n\n');
  }
}

/**
 * Merge per-glyph-run rects into one rect per visual line segment.
 * Two rects are on the same line when their vertical overlap exceeds half of
 * the smaller height; they merge when the horizontal gap is below 0.8 line
 * heights (inter-word gaps) so columns separated by a gutter stay apart.
 * @param {{x1: number, y1: number, x2: number, y2: number}[]} rects
 * @returns {{x1: number, y1: number, x2: number, y2: number}[]}
 */
export function mergeLineRects(rects) {
  const sorted = rects.slice().sort((a, b) => ((a.y1 + a.y2) / 2 - (b.y1 + b.y2) / 2) || (a.x1 - b.x1));
  const lines = [];
  for (const r of sorted) {
    let merged = false;
    for (const L of lines) {
      const overlap = Math.min(L.y2, r.y2) - Math.max(L.y1, r.y1);
      const minH = Math.min(L.y2 - L.y1, r.y2 - r.y1);
      if (overlap < minH * 0.5) continue;
      const gap = Math.max(r.x1 - L.x2, L.x1 - r.x2);
      if (gap > Math.max(L.y2 - L.y1, r.y2 - r.y1) * 0.8) continue;
      L.x1 = Math.min(L.x1, r.x1); L.x2 = Math.max(L.x2, r.x2);
      L.y1 = Math.min(L.y1, r.y1); L.y2 = Math.max(L.y2, r.y2);
      merged = true;
      break;
    }
    if (!merged) lines.push({ ...r });
  }
  return lines;
}

/**
 * Grow a textarea to fit its content (bounded by CSS max-height).
 * @param {HTMLTextAreaElement} ta
 */
function autosize(ta) {
  ta.style.height = 'auto';
  ta.style.height = `${ta.scrollHeight + 2}px`;
}
