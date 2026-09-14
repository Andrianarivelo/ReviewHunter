/**
 * find.js - Find in document.
 *
 * Text is extracted once per page with getTextContent() (cached and shared
 * with the text layer). For every page we concatenate the item strings into
 * one searchable string and remember where each item starts, so a match
 * [start, end) can be mapped back to (item index, char offset) pairs.
 * Because pdf.js creates exactly one span per string item (textDivs[i] holds
 * textStrs[i]), those pairs address DOM text nodes directly; a DOM Range over
 * them yields client rects that we draw as percent-positioned boxes in the
 * page's find layer. Matching is case-insensitive and whitespace-tolerant
 * (runs of whitespace in the query match any whitespace, including line ends).
 */

import { debounce, el } from './util.js';

export class FindController {
  /**
   * @param {{viewer: import('./viewer.js').PdfViewer, bar: HTMLElement, input: HTMLInputElement,
   *          countEl: HTMLElement, prevBtn: HTMLElement, nextBtn: HTMLElement, closeBtn: HTMLElement,
   *          onToggle?: (open: boolean) => void}} opts
   */
  constructor(opts) {
    Object.assign(this, opts);
    /** @type {{text: string, starts: number[]}[]} per-page searchable text */
    this.pageText = [];
    this.extracting = null;
    /** @type {{page: number, start: number, end: number}[]} */
    this.matches = [];
    this.current = -1;
    this.query = '';
    this._search = debounce(() => this.search(this.input.value, true), 180);

    this.input.addEventListener('input', () => this._search());
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); this._search.flush(); this.step(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') { e.preventDefault(); this.close(); }
    });
    this.prevBtn.addEventListener('click', () => this.step(-1));
    this.nextBtn.addEventListener('click', () => this.step(1));
    this.closeBtn.addEventListener('click', () => this.close());
    this.viewer.addEventListener('textlayerrendered', (e) => this.paintPage(e.detail.index));
  }

  /** @returns {boolean} whether the find bar is open */
  get isOpen() {
    return !this.bar.hidden;
  }

  /** Open the bar and focus the input (selecting any existing query). */
  open() {
    this.bar.hidden = false;
    const sel = String(window.getSelection() || '').trim();
    if (sel && sel.length < 80 && !sel.includes('\n')) { this.input.value = sel; this.search(sel, true); }
    this.input.focus();
    this.input.select();
    if (this.onToggle) this.onToggle(true);
  }

  /** Close the bar and clear match highlights. */
  close() {
    this.bar.hidden = true;
    this.matches = [];
    this.current = -1;
    this.query = '';
    this.viewer.pages.forEach((pv) => { pv.findLayer.textContent = ''; });
    this.countEl.textContent = '';
    this.viewer.container.focus({ preventScroll: true });
    if (this.onToggle) this.onToggle(false);
  }

  /**
   * Extract and cache searchable text for all pages (once).
   * @returns {Promise<void>}
   */
  _extractAll() {
    if (this.extracting) return this.extracting;
    this.extracting = (async () => {
      const n = this.viewer.pages.length;
      for (let i = 0; i < n; i++) {
        if (!this.pageText[i]) {
          const tc = await this.viewer.getTextContent(i);
          let text = '';
          const starts = [];
          for (const item of tc.items) {
            if (item.str === undefined) continue; // marked content: no span
            starts.push(text.length);
            text += item.str;
            if (item.hasEOL) text += ' ';
          }
          this.pageText[i] = { text: text.toLowerCase(), starts };
        }
        if (i % 10 === 9) this.countEl.textContent = `Indexing ${i + 1}/${n}`;
      }
    })();
    return this.extracting;
  }

  /**
   * Run a search and optionally jump to the first match at or after the current page.
   * @param {string} raw - query
   * @param {boolean} jump
   */
  async search(raw, jump) {
    const q = raw.trim().toLowerCase();
    this.query = q;
    this.viewer.pages.forEach((pv) => { pv.findLayer.textContent = ''; });
    this.matches = [];
    this.current = -1;
    if (!q) { this.countEl.textContent = ''; return; }
    await this._extractAll();
    if (q !== this.query) return; // superseded while indexing
    // Escape regex metacharacters, then let whitespace match any whitespace.
    const pattern = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
    const re = new RegExp(pattern, 'g');
    this.pageText.forEach((pt, page) => {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(pt.text))) {
        this.matches.push({ page, start: m.index, end: m.index + m[0].length });
        if (m[0].length === 0) re.lastIndex++;
      }
    });
    if (this.matches.length && jump) {
      const from = this.viewer.currentIndex;
      const idx = this.matches.findIndex((mm) => mm.page >= from);
      this.current = idx >= 0 ? idx : 0;
    }
    this._updateCount();
    this.viewer.pages.forEach((pv) => { if (pv.textReady) this.paintPage(pv.index); });
    if (this.current >= 0) this._reveal();
  }

  /**
   * Move to the next or previous match (wrapping around).
   * @param {number} dir - +1 or -1
   */
  step(dir) {
    if (!this.matches.length) return;
    const prev = this.matches[this.current];
    this.current = (this.current + dir + this.matches.length) % this.matches.length;
    this._updateCount();
    if (prev) this.paintPage(prev.page);
    this.paintPage(this.matches[this.current].page);
    this._reveal();
  }

  /** Refresh the "n of m" label. */
  _updateCount() {
    if (!this.query) { this.countEl.textContent = ''; return; }
    this.countEl.textContent = this.matches.length ? `${this.current + 1} of ${this.matches.length}` : 'No matches';
    this.bar.classList.toggle('is-miss', !this.matches.length);
  }

  /**
   * Map a string offset on a page to a DOM (text node, offset) position.
   * @param {any} pv - PageView
   * @param {number} offset - offset in the page string
   * @param {boolean} isEnd - true for range ends (prefer the end of the previous node)
   * @returns {{node: Text, offset: number}|null}
   */
  _locate(pv, offset, isEnd) {
    const starts = this.pageText[pv.index].starts;
    // Binary search the last item starting at or before offset.
    let lo = 0;
    let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] < offset || (!isEnd && starts[mid] === offset)) lo = mid;
      else hi = mid - 1;
    }
    for (let i = lo; i >= 0 && i < pv.textDivs.length; i--) {
      const tn = pv.textDivs[i].firstChild;
      if (!tn) continue;
      return { node: tn, offset: Math.max(0, Math.min(tn.length, offset - starts[i])) };
    }
    return null;
  }

  /**
   * Draw match boxes for a page whose text layer is rendered.
   * @param {number} index - page index
   */
  paintPage(index) {
    const pv = this.viewer.pages[index];
    if (!pv) return;
    pv.findLayer.textContent = '';
    if (!this.query || !pv.textReady || !this.pageText[index]) return;
    if (pv.textDivs.length !== this.pageText[index].starts.length) return; // safety: mapping mismatch
    const pageRect = pv.el.getBoundingClientRect();
    this.matches.forEach((m, i) => {
      if (m.page !== index) return;
      const a = this._locate(pv, m.start, false);
      const b = this._locate(pv, m.end, true);
      if (!a || !b) return;
      const range = document.createRange();
      try {
        range.setStart(a.node, a.offset);
        range.setEnd(b.node, b.offset);
      } catch (e) {
        return;
      }
      for (const r of range.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        const box = el('div', i === this.current ? 'rh-find-hit is-current' : 'rh-find-hit');
        box.style.left = `${((r.left - pageRect.left) / pageRect.width) * 100}%`;
        box.style.top = `${((r.top - pageRect.top) / pageRect.height) * 100}%`;
        box.style.width = `${(r.width / pageRect.width) * 100}%`;
        box.style.height = `${(r.height / pageRect.height) * 100}%`;
        pv.findLayer.append(box);
      }
    });
  }

  /** Scroll the current match into view (rendering its page first if needed). */
  async _reveal() {
    const m = this.matches[this.current];
    if (!m) return;
    const v = this.viewer;
    const pv = v.pages[m.page];
    const c = v.container;
    const tryScroll = () => {
      const hit = pv.findLayer.querySelector('.rh-find-hit.is-current');
      if (!hit) return false;
      const hr = hit.getBoundingClientRect();
      const cr = c.getBoundingClientRect();
      if (hr.top < cr.top + 40 || hr.bottom > cr.bottom - 40) {
        c.scrollTop += hr.top - cr.top - c.clientHeight * 0.35;
      }
      if (hr.left < cr.left || hr.right > cr.right) c.scrollLeft += hr.left - cr.left - c.clientWidth / 2;
      v.update();
      return true;
    };
    if (tryScroll()) return;
    // Page not rendered yet: bring it on screen, then wait for its text layer.
    v.scrollToPage(m.page);
    const onText = (e) => {
      if (e.detail.index !== m.page) return;
      v.removeEventListener('textlayerrendered', onText);
      requestAnimationFrame(() => tryScroll());
    };
    v.addEventListener('textlayerrendered', onText);
    setTimeout(() => v.removeEventListener('textlayerrendered', onText), 5000);
  }
}
