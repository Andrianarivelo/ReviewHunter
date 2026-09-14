/**
 * viewer.js - Continuous-scroll PDF page view with lazy rendering.
 *
 * Design
 * ------
 * Every page gets a placeholder <div class="rh-page"> sized exactly like the
 * rendered page, so the scrollbar and page offsets are correct from the start.
 * A geometric visibility test (binary search of page offsets against the
 * scroll window extended by one viewport above and below) decides which pages
 * are "near"; only those get a canvas and a text layer. Pages that leave the
 * zone release their canvas memory. This replaces an IntersectionObserver on
 * purpose: IO callbacks are asynchronous (they lag behind a zoom relayout) and
 * are suspended in hidden documents, whereas the geometric test is
 * synchronous, deterministic and O(log n) per scroll event.
 *
 * Units and scales
 * ----------------
 *   zoom      : user zoom, 1 = 100 %.
 *   cssScale  : CSS pixels per PDF point = zoom * 96 / 72 (a PDF point is
 *               1/72 inch, a CSS pixel is 1/96 inch), same convention as pdf.js.
 *   outputScale: device pixels per CSS pixel (devicePixelRatio), applied to the
 *               canvas backing store only, so pages are crisp on HiDPI screens.
 *
 * The text layer from pdf.js 3.11 positions spans in percent of the page and
 * sizes fonts with calc(var(--scale-factor) * N px), so it follows zoom by
 * simply updating --scale-factor on the page element (no re-layout needed).
 * Overlays (annotations, find matches) are also positioned in percent of the
 * page, which makes them zoom invariant as well.
 */

import { clamp, el } from './util.js';

/** CSS pixels per PDF point. */
export const CSS_UNITS = 96 / 72;
/** Maximum canvas backing store area (browser limits are around 16.7 M px). */
const MAX_CANVAS_PIXELS = 16777216;
/** Vertical gap between pages and outer padding, CSS px (keep in sync with CSS). */
const PAGE_GAP = 12;
const H_PADDING = 24;
/** pdf.js "auto" zoom never exceeds this for portrait pages. */
const MAX_AUTO_ZOOM = 1.25;
export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 5;
/** Pages whose proxies we load eagerly to get exact sizes. */
const EAGER_SIZE_LIMIT = 400;

/**
 * @typedef {Object} PageView
 * @property {number} index        0-based page index
 * @property {HTMLDivElement} el   page container
 * @property {HTMLDivElement} textLayerEl
 * @property {HTMLDivElement} hlLayer    highlight/underline overlays (under the text layer)
 * @property {HTMLDivElement} findLayer  find-match overlays
 * @property {HTMLDivElement} noteLayer  sticky notes (above the text layer)
 * @property {any} proxy           PDFPageProxy once loaded
 * @property {any} vp1             viewport at scale 1 (PDF points, page rotation applied)
 * @property {HTMLCanvasElement|null} canvas
 * @property {number} renderedScale cssScale of the current canvas, 0 if none
 * @property {any} renderTask
 * @property {boolean} textReady   text layer rendered
 * @property {HTMLElement[]} textDivs spans created by pdf.js (parallel to textStrs)
 * @property {string[]} textStrs   strings of the text items with a `str`
 * @property {boolean} near        inside the lazy-render zone
 */

export class PdfViewer extends EventTarget {
  /**
   * @param {HTMLElement} container - scrollable element
   * @param {HTMLElement} pagesEl   - inner element receiving page divs
   */
  constructor(container, pagesEl) {
    super();
    this.container = container;
    this.pagesEl = pagesEl;
    /** @type {PageView[]} */
    this.pages = [];
    this.pdf = null;
    this.zoomMode = 'auto';
    this.zoom = 1;
    this.currentIndex = 0;
    this.textContentCache = new Map();
    this._queue = new Set();
    this._active = 0;
    /** @type {Set<number>} indices of pages currently in the render zone */
    this._near = new Set();
    this.container.addEventListener('scroll', () => this._onScroll(), { passive: true });
    this._resizeObserver = new ResizeObserver(() => this._onResize());
    this._resizeObserver.observe(this.container);
    this._lastWidth = 0;
    this._lastHeight = 0;
  }

  /** @returns {number} CSS pixels per PDF point at the current zoom. */
  get cssScale() {
    return this.zoom * CSS_UNITS;
  }

  /**
   * Attach a loaded PDFDocumentProxy and build page placeholders.
   * @param {any} pdf - PDFDocumentProxy
   * @param {{zoomMode?: string|number, page?: number, offset?: number}} [initial]
   */
  async setDocument(pdf, initial = {}) {
    this.pdf = pdf;
    const first = await pdf.getPage(1);
    const vp1 = first.getViewport({ scale: 1 });
    this.pagesEl.textContent = '';
    this.pages = [];
    for (let i = 0; i < pdf.numPages; i++) {
      const pv = this._createPageView(i, vp1);
      if (i === 0) pv.proxy = first;
      this.pages.push(pv);
      this.pagesEl.append(pv.el);
    }
    this._near = new Set();
    this._applyZoomMode(initial.zoomMode ?? 'auto', null);
    this.dispatchEvent(new CustomEvent('pagesetup', { detail: this.pages[0] }));
    if (initial.page) this.scrollToPage(initial.page - 1, { fraction: initial.offset || 0 });
    this._updateCurrentPage(true);
    if (pdf.numPages <= EAGER_SIZE_LIMIT) this._loadAllSizes();
  }

  /**
   * Build the DOM for one page placeholder.
   * @param {number} index
   * @param {any} vp1 - provisional scale-1 viewport (from page 1)
   * @returns {PageView}
   */
  _createPageView(index, vp1) {
    const root = el('div', 'rh-page is-empty');
    root.dataset.page = String(index + 1);
    const hlLayer = el('div', 'rh-hl-layer');
    const findLayer = el('div', 'rh-find-layer');
    const textLayerEl = el('div', 'textLayer');
    const noteLayer = el('div', 'rh-note-layer');
    const label = el('div', 'rh-page-label', String(index + 1));
    root.append(label, hlLayer, findLayer, textLayerEl, noteLayer);
    return {
      index, el: root, textLayerEl, hlLayer, findLayer, noteLayer,
      proxy: null, vp1, exactSize: false, canvas: null, renderedScale: 0, renderTask: null,
      textTask: null, textReady: false, textDivs: [], textStrs: [], near: false,
    };
  }

  /**
   * Fetch all page proxies in the background so placeholders get exact sizes
   * (mixed page sizes are common in supplements). Layout is corrected once at
   * the end while keeping the reading position.
   */
  async _loadAllSizes() {
    const pdf = this.pdf;
    let changed = false;
    for (const pv of this.pages) {
      if (this.pdf !== pdf) return;
      if (!pv.exactSize) changed = (await this._ensureProxy(pv, false)) || changed;
    }
    if (changed) this._relayout(null);
  }

  /**
   * Load a page proxy and its true scale-1 viewport.
   * @param {PageView} pv
   * @param {boolean} [relayout=true] - fix the layout immediately if the size changed
   * @returns {Promise<boolean>} true if the page size differed from the placeholder
   */
  async _ensureProxy(pv, relayout = true) {
    if (!pv.proxy) pv.proxy = await this.pdf.getPage(pv.index + 1);
    if (pv.exactSize) return false;
    const vp1 = pv.proxy.getViewport({ scale: 1 });
    const changed = Math.abs(vp1.width - pv.vp1.width) > 0.5 || Math.abs(vp1.height - pv.vp1.height) > 0.5;
    pv.vp1 = vp1;
    pv.exactSize = true;
    if (changed && relayout) this._relayout(null);
    this.dispatchEvent(new CustomEvent('pagesetup', { detail: pv }));
    return changed;
  }

  /**
   * Public: make sure a page's proxy and exact size are loaded.
   * @param {number} index - 0-based page index
   * @returns {Promise<PageView>}
   */
  async ensurePage(index) {
    const pv = this.pages[index];
    await this._ensureProxy(pv);
    return pv;
  }

  // ------------------------------------------------------------------ zoom

  /**
   * Resolve a zoom mode into a numeric zoom for the current container size.
   * @param {string|number} mode - 'auto' | 'page-fit' | 'page-width' | number
   * @returns {number}
   */
  _resolveZoom(mode) {
    if (typeof mode === 'number') return clamp(mode, MIN_ZOOM, MAX_ZOOM);
    const pv = this.pages[this.currentIndex] || this.pages[0];
    if (!pv) return 1;
    const availW = Math.max(100, this.container.clientWidth - 2 * H_PADDING);
    const availH = Math.max(100, this.container.clientHeight - 2 * PAGE_GAP);
    const widthZoom = availW / (pv.vp1.width * CSS_UNITS);
    const heightZoom = availH / (pv.vp1.height * CSS_UNITS);
    let z;
    if (mode === 'page-width') z = widthZoom;
    else if (mode === 'page-fit') z = Math.min(widthZoom, heightZoom);
    else {
      const landscape = pv.vp1.width > pv.vp1.height;
      z = landscape ? Math.min(heightZoom, widthZoom) : Math.min(MAX_AUTO_ZOOM, widthZoom);
    }
    return clamp(Math.floor(z * 1000) / 1000, MIN_ZOOM, MAX_ZOOM);
  }

  /**
   * Set zoom (mode string or number) keeping the point under `anchor` fixed.
   * @param {string|number} mode
   * @param {{x: number, y: number}|null} [anchor] - client coordinates; defaults to viewer top-center
   */
  setZoom(mode, anchor = null) {
    this._applyZoomMode(mode, anchor);
  }

  /**
   * Multiply the current zoom by a step factor.
   * @param {number} factor - e.g. 1.1 to zoom in
   * @param {{x: number, y: number}|null} [anchor]
   */
  zoomBy(factor, anchor = null) {
    const steps = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];
    let next;
    if (factor > 1) next = steps.find((s) => s > this.zoom + 1e-3) ?? MAX_ZOOM;
    else next = [...steps].reverse().find((s) => s < this.zoom - 1e-3) ?? MIN_ZOOM;
    this.setZoom(next, anchor);
  }

  /**
   * Internal zoom application with reading-position preservation.
   * @param {string|number} mode
   * @param {{x: number, y: number}|null} anchor
   */
  _applyZoomMode(mode, anchor) {
    this.zoomMode = mode;
    const zoom = this._resolveZoom(mode);
    const changed = Math.abs(zoom - this.zoom) > 1e-4 || !this._laidOut;
    this.zoom = zoom;
    if (changed) this._relayout(anchor);
    this.dispatchEvent(new CustomEvent('scalechange', { detail: { zoom, mode } }));
  }

  /**
   * Capture a position (page + fractional offsets) under a client point.
   * @param {{x: number, y: number}|null} anchor
   * @returns {{index: number, fy: number, fx: number, dy: number, dx: number}|null}
   */
  _captureAnchor(anchor) {
    if (!this._laidOut || !this.pages.length) return null;
    const rect = this.container.getBoundingClientRect();
    const dy = anchor ? anchor.y - rect.top : 0;
    const dx = anchor ? anchor.x - rect.left : this.container.clientWidth / 2;
    const y = this.container.scrollTop + dy;
    const x = this.container.scrollLeft + dx;
    const index = this._indexAtY(y);
    const pe = this.pages[index].el;
    return {
      index,
      fy: (y - pe.offsetTop) / pe.offsetHeight,
      fx: (x - pe.offsetLeft) / pe.offsetWidth,
      dy, dx,
    };
  }

  /**
   * Resize every placeholder for the current zoom, keeping the anchor stable.
   * Existing canvases are stretched by CSS until the re-render lands, which
   * avoids white flashes during zoom.
   * @param {{x: number, y: number}|null} anchor
   */
  _relayout(anchor) {
    const saved = this._captureAnchor(anchor);
    const s = this.cssScale;
    for (const pv of this.pages) {
      pv.el.style.width = `${Math.floor(pv.vp1.width * s)}px`;
      pv.el.style.height = `${Math.floor(pv.vp1.height * s)}px`;
      pv.el.style.setProperty('--scale-factor', String(s));
    }
    this._laidOut = true;
    if (saved) {
      const pe = this.pages[saved.index].el;
      this.container.scrollTop = pe.offsetTop + saved.fy * pe.offsetHeight - saved.dy;
      if (anchor) this.container.scrollLeft = pe.offsetLeft + saved.fx * pe.offsetWidth - saved.dx;
    }
    this._onScroll();
  }

  /** Re-resolve fit modes when the container is resized (sidebars, window). */
  _onResize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (w === this._lastWidth && h === this._lastHeight) return;
    this._lastWidth = w;
    this._lastHeight = h;
    if (this.pdf && typeof this.zoomMode === 'string') this._applyZoomMode(this.zoomMode, null);
  }

  // ------------------------------------------------------------ navigation

  /**
   * Binary search the page containing document y (container scroll coordinates).
   * @param {number} y
   * @returns {number} page index
   */
  _indexAtY(y) {
    let lo = 0;
    let hi = this.pages.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.pages[mid].el.offsetTop - PAGE_GAP / 2 <= y) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  /**
   * Scroll so a page (optionally a fraction down the page) is at the top.
   * @param {number} index - 0-based
   * @param {{fraction?: number, offsetPx?: number, smooth?: boolean}} [opts]
   */
  scrollToPage(index, opts = {}) {
    const pv = this.pages[clamp(index, 0, this.pages.length - 1)];
    if (!pv) return;
    const top = pv.el.offsetTop + (opts.fraction || 0) * pv.el.offsetHeight + (opts.offsetPx || 0) - (opts.fraction || opts.offsetPx ? 0 : PAGE_GAP);
    this.container.scrollTo({ top: Math.max(0, top), behavior: opts.smooth ? 'smooth' : 'auto' });
    if (!opts.smooth) this._onScroll(); // update page state synchronously
  }

  /**
   * Scroll to a PDF user-space point on a page (used by outline links).
   * @param {number} index - 0-based page index
   * @param {number|null} x - PDF x (points) or null
   * @param {number|null} y - PDF y (points, origin bottom-left) or null
   */
  async scrollToPdfPoint(index, x, y) {
    const pv = this.pages[index];
    if (!pv) return;
    await this._ensureProxy(pv);
    if (y === null || y === undefined) { this.scrollToPage(index); return; }
    const [vx, vy] = pv.vp1.convertToViewportPoint(x || 0, y);
    const top = pv.el.offsetTop + vy * this.cssScale - PAGE_GAP;
    this.container.scrollTo({ top: Math.max(0, top) });
    this._onScroll();
    if (x !== null && x !== undefined) {
      const left = pv.el.offsetLeft + vx * this.cssScale - H_PADDING;
      if (left > this.container.clientWidth / 2) this.container.scrollLeft = left;
    }
  }

  /**
   * Navigate to a pdf.js destination (named string or explicit array).
   * Explicit destinations look like [pageRef, {name: 'XYZ'}, left, top, zoom].
   * @param {string|Array} dest
   */
  async goToDestination(dest) {
    if (!this.pdf || !dest) return;
    const explicit = typeof dest === 'string' ? await this.pdf.getDestination(dest) : dest;
    if (!Array.isArray(explicit)) return;
    const ref = explicit[0];
    let index;
    if (ref && typeof ref === 'object') index = await this.pdf.getPageIndex(ref);
    else if (Number.isInteger(ref)) index = ref;
    else return;
    const kind = explicit[1] && explicit[1].name;
    let x = null;
    let y = null;
    if (kind === 'XYZ') { x = explicit[2]; y = explicit[3]; }
    else if (kind === 'FitH' || kind === 'FitBH') { y = explicit[2]; }
    else if (kind === 'FitR') { x = explicit[2]; y = explicit[5]; }
    await this.scrollToPdfPoint(index, x, y);
  }

  /** Public: resync render zone and current page after a programmatic scroll. */
  update() {
    this._onScroll();
  }

  /** Scroll handler: cheap (two binary searches), so no frame throttling needed. */
  _onScroll() {
    this._updateNear();
    this._updateCurrentPage(false);
  }

  /**
   * Determine the "current" page: the page under a line one third down the
   * viewport, or the last page when scrolled to the very bottom.
   * @param {boolean} force - emit pagechange even if unchanged
   */
  _updateCurrentPage(force) {
    if (!this.pages.length) return;
    const c = this.container;
    let index;
    if (c.scrollTop + c.clientHeight >= c.scrollHeight - 2 && c.scrollTop > 0) index = this.pages.length - 1;
    else index = this._indexAtY(c.scrollTop + c.clientHeight / 3);
    if (index !== this.currentIndex || force) {
      this.currentIndex = index;
      this.dispatchEvent(new CustomEvent('pagechange', { detail: { index } }));
    }
    this.dispatchEvent(new CustomEvent('scrolled'));
  }

  // ------------------------------------------------------------- rendering

  /**
   * Recompute the render zone [scrollTop - H, scrollTop + 2H] (H = viewport
   * height), render pages entering it and release pages leaving it.
   */
  _updateNear() {
    if (!this.pages.length || !this._laidOut) return;
    const c = this.container;
    const h = Math.max(c.clientHeight, 200);
    const first = this._indexAtY(Math.max(0, c.scrollTop - h));
    const last = this._indexAtY(c.scrollTop + 2 * h);
    for (const i of [...this._near]) {
      if (i < first || i > last) {
        this._near.delete(i);
        this.pages[i].near = false;
        this._release(this.pages[i]);
      }
    }
    for (let i = first; i <= last; i++) {
      const pv = this.pages[i];
      if (!pv.near) { pv.near = true; this._near.add(i); }
      this._enqueue(pv);
    }
  }

  /**
   * Add a page to the render queue if it needs (re-)rendering.
   * @param {PageView} pv
   */
  _enqueue(pv) {
    if (pv.renderedScale === this.cssScale && pv.textReady) return;
    this._queue.add(pv);
    this._pump();
  }

  /**
   * Render queued pages, two at a time, nearest to the current page first.
   */
  _pump() {
    while (this._active < 2 && this._queue.size) {
      let best = null;
      for (const pv of this._queue) {
        if (!best || Math.abs(pv.index - this.currentIndex) < Math.abs(best.index - this.currentIndex)) best = pv;
      }
      this._queue.delete(best);
      if (!best.near) continue;
      this._active++;
      this._renderPage(best)
        .catch((err) => {
          const benign = err && (err.name === 'RenderingCancelledException' || err.name === 'AbortException');
          if (!benign) console.warn('Page render failed', err);
        })
        .finally(() => { this._active--; this._pump(); });
    }
  }

  /**
   * Render one page: canvas at the current scale and, once, its text layer.
   * @param {PageView} pv
   */
  async _renderPage(pv) {
    await this._ensureProxy(pv);
    const scale = this.cssScale;
    if (pv.renderedScale !== scale) {
      const viewport = pv.proxy.getViewport({ scale });
      // Device pixel ratio, reduced if the backing store would be too large.
      let outputScale = window.devicePixelRatio || 1;
      const area = viewport.width * viewport.height * outputScale * outputScale;
      if (area > MAX_CANVAS_PIXELS) outputScale = Math.sqrt(MAX_CANVAS_PIXELS / (viewport.width * viewport.height));
      const canvas = document.createElement('canvas');
      canvas.className = 'rh-canvas';
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      const ctx = canvas.getContext('2d', { alpha: false });
      if (pv.renderTask) pv.renderTask.cancel();
      pv.renderTask = pv.proxy.render({
        canvasContext: ctx,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
      });
      try {
        await pv.renderTask.promise;
      } finally {
        pv.renderTask = null;
      }
      // The zoom may have changed or the page may have left the zone meanwhile.
      if (!pv.near) return;
      if (pv.canvas) pv.canvas.replaceWith(canvas);
      else pv.el.prepend(canvas);
      pv.canvas = canvas;
      pv.renderedScale = scale;
      pv.el.classList.remove('is-empty');
      this.dispatchEvent(new CustomEvent('pagerendered', { detail: pv }));
      if (scale !== this.cssScale) this._queue.add(pv);
    }
    if (!pv.textReady && pv.near) await this._renderTextLayer(pv);
  }

  /**
   * Render the selectable text layer (pdf.js renderTextLayer, v3.11 API).
   * textDivs[i] holds the text of textStrs[i]; both skip marked-content items,
   * which lets find-in-document map string offsets back to DOM text nodes.
   * @param {PageView} pv
   */
  async _renderTextLayer(pv) {
    if (pv.textBusy) return;
    pv.textBusy = true;
    try {
      await this._renderTextLayerInner(pv);
    } finally {
      pv.textBusy = false;
    }
  }

  /**
   * Body of _renderTextLayer (guarded against concurrent calls by textBusy).
   * @param {PageView} pv
   */
  async _renderTextLayerInner(pv) {
    const textContent = await this.getTextContent(pv.index);
    if (!pv.near || pv.textReady) return;
    pv.textLayerEl.textContent = '';
    pv.textDivs = [];
    pv.textStrs = [];
    const viewport = pv.proxy.getViewport({ scale: this.cssScale });
    pv.textTask = pdfjsLib.renderTextLayer({
      textContentSource: textContent,
      container: pv.textLayerEl,
      viewport,
      textDivs: pv.textDivs,
      textContentItemsStr: pv.textStrs,
    });
    await pv.textTask.promise;
    pv.textTask = null;
    // endOfContent keeps Chrome selections from jumping when dragging over gaps.
    const eoc = el('div', 'endOfContent');
    pv.textLayerEl.append(eoc);
    pv.textReady = true;
    this.dispatchEvent(new CustomEvent('textlayerrendered', { detail: pv }));
  }

  /**
   * Release a far-away page's canvas and text layer to bound memory usage.
   * @param {PageView} pv
   */
  _release(pv) {
    this._queue.delete(pv);
    if (pv.renderTask) { pv.renderTask.cancel(); pv.renderTask = null; }
    if (pv.textTask) { pv.textTask.cancel(); pv.textTask = null; }
    if (pv.canvas) {
      // Shrinking the backing store frees GPU memory immediately in Chrome.
      pv.canvas.width = 0;
      pv.canvas.height = 0;
      pv.canvas.remove();
      pv.canvas = null;
    }
    pv.renderedScale = 0;
    if (pv.textReady) {
      pv.textLayerEl.textContent = '';
      pv.textDivs = [];
      pv.textStrs = [];
      pv.textReady = false;
    }
    pv.findLayer.textContent = '';
    pv.el.classList.add('is-empty');
  }

  /**
   * Cached page text content (shared by the text layer and find).
   * @param {number} index - 0-based page index
   * @returns {Promise<any>} pdf.js TextContent
   */
  getTextContent(index) {
    if (!this.textContentCache.has(index)) {
      const p = this.pdf.getPage(index + 1).then((page) => page.getTextContent());
      this.textContentCache.set(index, p);
      p.catch(() => this.textContentCache.delete(index));
    }
    return this.textContentCache.get(index);
  }

  /** @returns {number} number of pages with a live canvas (for diagnostics/tests). */
  renderedCount() {
    return this.pages.filter((pv) => pv.canvas).length;
  }
}

/**
 * Lazily rendered page thumbnails in the left sidebar.
 */
export class ThumbnailView {
  /**
   * @param {HTMLElement} panel - scrollable sidebar panel
   * @param {PdfViewer} viewer
   */
  constructor(panel, viewer) {
    this.panel = panel;
    this.viewer = viewer;
    this.items = [];
    this.queue = [];
    this.busy = false;
    this.width = 118;
  }

  /** Build thumbnail placeholders for the viewer's document. */
  build() {
    this.panel.textContent = '';
    this.items = [];
    this.panel.addEventListener('scroll', () => this.refresh(), { passive: true });
    for (const pv of this.viewer.pages) {
      const btn = el('button', 'rh-thumb');
      btn.dataset.index = String(pv.index);
      btn.title = `Page ${pv.index + 1}`;
      const frame = el('div', 'rh-thumb-frame');
      frame.style.aspectRatio = `${pv.vp1.width} / ${pv.vp1.height}`;
      const label = el('span', 'rh-thumb-label', String(pv.index + 1));
      btn.append(frame, label);
      btn.addEventListener('click', () => this.viewer.scrollToPage(pv.index));
      this.panel.append(btn);
      const item = { pv, btn, frame, done: false, queued: false };
      this.items.push(item);
    }
    this.viewer.addEventListener('pagesetup', (e) => {
      const item = this.items[e.detail.index];
      if (item) item.frame.style.aspectRatio = `${e.detail.vp1.width} / ${e.detail.vp1.height}`;
    });
    this.refresh();
  }

  /**
   * Queue thumbnails inside the panel's visible window (plus 300 px margin).
   * Uses the same geometric test as the page view; a collapsed or hidden
   * panel (clientHeight 0) renders nothing.
   */
  refresh() {
    const h = this.panel.clientHeight;
    if (!h || !this.items.length || this.panel.hidden || this.panel.closest('.is-collapsed')) return;
    const top = this.panel.scrollTop - 300;
    const bottom = this.panel.scrollTop + h + 300;
    const base = this.items[0].btn.offsetTop;
    for (const item of this.items) {
      const y = item.btn.offsetTop - base;
      if (y > bottom) break;
      if (y + item.btn.offsetHeight < top || item.done || item.queued) continue;
      item.queued = true;
      this.queue.push(item);
    }
    this._pump();
  }

  /** Render queued thumbnails one at a time (low priority, after page renders). */
  async _pump() {
    if (this.busy) return;
    this.busy = true;
    while (this.queue.length) {
      const item = this.queue.shift();
      try {
        const page = item.pv.proxy || await this.viewer.pdf.getPage(item.pv.index + 1);
        const base = page.getViewport({ scale: 1 });
        const dpr = window.devicePixelRatio || 1;
        const vp = page.getViewport({ scale: (this.width / base.width) * dpr });
        const canvas = document.createElement('canvas');
        canvas.width = Math.floor(vp.width);
        canvas.height = Math.floor(vp.height);
        await page.render({ canvasContext: canvas.getContext('2d', { alpha: false }), viewport: vp }).promise;
        item.frame.append(canvas);
        item.done = true;
      } catch (err) {
        item.queued = false;
      }
      // Yield so thumbnails never starve the main page renders.
      await new Promise((r) => setTimeout(r, 16));
    }
    this.busy = false;
  }

  /**
   * Mark the current page thumbnail and keep it visible.
   * @param {number} index
   */
  setCurrent(index) {
    this.items.forEach((it) => it.btn.classList.toggle('is-current', it.pv.index === index));
    const cur = this.items[index];
    if (cur && this.panel.offsetParent) {
      const pr = this.panel.getBoundingClientRect();
      const br = cur.btn.getBoundingClientRect();
      if (br.top < pr.top || br.bottom > pr.bottom) cur.btn.scrollIntoView({ block: 'nearest' });
    }
  }
}

/**
 * Build the nested document outline (bookmarks) in the sidebar.
 * @param {any} pdf - PDFDocumentProxy
 * @param {HTMLElement} panel
 * @param {PdfViewer} viewer
 * @returns {Promise<number>} number of top-level entries
 */
export async function buildOutline(pdf, panel, viewer) {
  panel.textContent = '';
  let outline = null;
  try { outline = await pdf.getOutline(); } catch (e) { outline = null; }
  if (!outline || !outline.length) {
    panel.append(el('div', 'rh-empty-hint', 'This document has no outline.'));
    return 0;
  }
  /**
   * Recursively render outline nodes.
   * @param {any[]} nodes
   * @param {HTMLElement} parent
   * @param {number} depth
   */
  const renderLevel = (nodes, parent, depth) => {
    const list = el('ul', 'rh-outline-list');
    for (const node of nodes) {
      const li = el('li', 'rh-outline-node');
      const row = el('div', 'rh-outline-row');
      row.style.paddingLeft = `${6 + depth * 12}px`;
      const hasKids = node.items && node.items.length;
      const toggle = el('button', 'rh-outline-toggle');
      toggle.setAttribute('aria-label', 'Expand');
      if (!hasKids) toggle.classList.add('is-leaf');
      const link = el('button', 'rh-outline-link', node.title || 'Untitled');
      link.title = node.title || '';
      if (node.bold) link.style.fontWeight = '600';
      if (node.italic) link.style.fontStyle = 'italic';
      link.addEventListener('click', () => {
        if (node.dest) viewer.goToDestination(node.dest);
        else if (node.url) window.open(node.url, '_blank', 'noopener');
      });
      row.append(toggle, link);
      li.append(row);
      if (hasKids) {
        const child = renderLevel(node.items, li, depth + 1);
        const collapsed = depth >= 1;
        li.classList.toggle('is-collapsed', collapsed);
        toggle.addEventListener('click', () => li.classList.toggle('is-collapsed'));
        li.append(child);
      }
      list.append(li);
    }
    return list;
  };
  panel.append(renderLevel(outline, panel, 0));
  return outline.length;
}
