/*
 * MockAdapter: a browser-only stand-in for Word used in dev/test mode.
 *
 * The "document" is a contenteditable <div>. Content controls are <span class="rh-cc">
 * (citations) and <div class="rh-cc rh-bibl"> (bibliography) carrying the same tag
 * strings Word would store ("RH-CITE:<id>", "RH-BIBL"). Footnotes are simulated with a
 * <sup class="mock-fnref"> marker in the text and an <li> in a footnote list.
 * Document settings are kept as JSON strings, exactly like Office serialises them.
 */
(function (global) {
  'use strict';

  const CITE_PREFIX = 'RH-CITE:';
  const BIBL_TAG = 'RH-BIBL';

  class MockAdapter {
    /**
     * @param {{root: HTMLElement, footnotesRoot?: HTMLElement, footnotes?: boolean}} opts
     *   root          contenteditable element acting as the document body
     *   footnotesRoot element that receives the footnote list (created when missing)
     *   footnotes     simulate WordApi 1.5 footnote support (default true)
     */
    constructor(opts) {
      this.kind = 'mock';
      this.root = opts.root;
      this.supportsFootnotes = opts.footnotes !== false;
      this.footnotesRoot = opts.footnotesRoot || (() => {
        const div = document.createElement('div');
        this.root.after(div);
        return div;
      })();
      this.footnotesRoot.classList.add('mock-footnotes');
      if (!this.footnotesRoot.querySelector('ol')) this.footnotesRoot.appendChild(document.createElement('ol'));
      this.settings = {};                 // name -> JSON string (serialised like Office)
      this.savedRange = null;             // last caret position inside the document
      this.selectedControl = null;        // control clicked by the user
      this._selectionListeners = [];
      this._bindSelection();
    }

    // ---------------------------------------------------------------- selection

    /** Track the caret and clicks on controls, like Word's selection. */
    _bindSelection() {
      const capture = () => {
        const sel = document.getSelection();
        if (!sel || !sel.rangeCount) return;
        const node = sel.anchorNode;
        if (!node || !(this.root.contains(node) || this.footnotesRoot.contains(node))) return;
        this.savedRange = sel.getRangeAt(0).cloneRange();
        const el = node.nodeType === 1 ? node : node.parentElement;
        this._select(el && el.closest('.rh-cc'));
      };
      // selectionchange is asynchronous and not always delivered (e.g. synthetic input),
      // so the caret is also captured on pointer/keyboard events and when focus leaves.
      document.addEventListener('selectionchange', capture);
      ['mouseup', 'keyup', 'focusout'].forEach(t => this.root.addEventListener(t, capture));
      const onClick = ev => {
        const cc = ev.target.closest && ev.target.closest('.rh-cc');
        if (cc) this._select(cc);
      };
      this.root.addEventListener('click', onClick);
      this.footnotesRoot.addEventListener('click', onClick);
    }

    /** @param {HTMLElement|null} el */
    _select(el) {
      if (this.selectedControl === el) return;
      if (this.selectedControl) this.selectedControl.classList.remove('is-selected');
      this.selectedControl = el || null;
      if (el) el.classList.add('is-selected');
      this._selectionListeners.forEach(fn => fn());
    }

    /** @param {Function} fn called when the selection changes */
    onSelectionChanged(fn) { this._selectionListeners.push(fn); }

    /**
     * Test helper: put the caret right after the first occurrence of `text`.
     * @param {string} text
     * @returns {boolean} found
     */
    placeCaretAfter(text) {
      const walker = document.createTreeWalker(this.root, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walker.nextNode())) {
        if (n.parentElement.closest('.rh-cc')) continue;
        const i = n.data.indexOf(text);
        if (i >= 0) {
          const r = document.createRange();
          r.setStart(n, i + text.length);
          r.collapse(true);
          this.savedRange = r;
          this._select(null);
          return true;
        }
      }
      return false;
    }

    /** Test helper: caret at the end of the document. */
    placeCaretAtEnd() {
      const last = this.root.lastElementChild || this.root;
      const r = document.createRange();
      r.selectNodeContents(last);
      r.collapse(false);
      this.savedRange = r;
      this._select(null);
    }

    // ---------------------------------------------------------------- state

    /** @returns {Promise<object>} raw state read from "document settings" */
    async loadState() {
      const get = k => (this.settings[k] ? JSON.parse(this.settings[k]) : undefined);
      return { citations: get('rh.citations'), items: get('rh.items'), prefs: get('rh.prefs'), trash: get('rh.trash') };
    }

    /** @param {object} state */
    async saveState(state) {
      this.settings['rh.citations'] = JSON.stringify(state.citations || {});
      this.settings['rh.items'] = JSON.stringify(state.items || {});
      if (state.prefs) this.settings['rh.prefs'] = JSON.stringify(state.prefs);
      this.settings['rh.trash'] = JSON.stringify(state.trash || []);
    }

    async clearState() {
      ['rh.citations', 'rh.items', 'rh.prefs', 'rh.trash'].forEach(k => delete this.settings[k]);
    }

    // ---------------------------------------------------------------- helpers

    /** @returns {HTMLElement[]} citation controls, body first then footnotes are merged by marker order */
    _citationControls() {
      const out = [];
      // Walk the body in document order; a footnote marker stands for its footnote's controls.
      const walker = document.createTreeWalker(this.root, NodeFilter.SHOW_ELEMENT);
      let el;
      while ((el = walker.nextNode())) {
        if (el.classList.contains('rh-cc') && (el.dataset.tag || '').startsWith(CITE_PREFIX)) {
          out.push({ el, inNote: false });
        } else if (el.classList.contains('mock-fnref')) {
          const li = this._footnoteFor(el);
          if (li) li.querySelectorAll('.rh-cc').forEach(cc => {
            if ((cc.dataset.tag || '').startsWith(CITE_PREFIX)) out.push({ el: cc, inNote: true, li, ref: el });
          });
        }
      }
      return out;
    }

    /** @param {HTMLElement} marker @returns {HTMLElement|null} */
    _footnoteFor(marker) {
      return this.footnotesRoot.querySelector(`li[data-fn="${marker.dataset.fn}"]`);
    }

    /** Renumber footnote markers and reorder the footnote list like Word does. */
    _renumber() {
      const ol = this.footnotesRoot.querySelector('ol');
      const markers = [...this.root.querySelectorAll('.mock-fnref')];
      markers.forEach((m, i) => {
        m.textContent = String(i + 1);
        const li = this._footnoteFor(m);
        if (li) ol.appendChild(li);
      });
      // footnotes whose marker was deleted by the user disappear too
      ol.querySelectorAll('li').forEach(li => {
        if (!this.root.querySelector(`.mock-fnref[data-fn="${li.dataset.fn}"]`)) li.remove();
      });
    }

    /**
     * Create a control element.
     * @param {string} tag
     * @param {string} html
     * @param {string} appearance
     * @param {'span'|'div'} [name]
     * @returns {HTMLElement}
     */
    _makeControl(tag, html, appearance, name = 'span') {
      const el = document.createElement(name);
      el.className = 'rh-cc' + (tag === BIBL_TAG ? ' rh-bibl' : '');
      el.dataset.tag = tag;
      el.dataset.appearance = appearance || 'BoundingBox';
      el.title = tag === BIBL_TAG ? 'ReviewHunter bibliography' : 'ReviewHunter citation';
      el.contentEditable = 'false';
      el.innerHTML = html;
      return el;
    }

    /** @returns {Range} insertion point (outside any control) */
    _insertionRange() {
      const inBody = this.savedRange && this.savedRange.startContainer.isConnected
        && this.root.contains(this.savedRange.startContainer);
      if (!inBody) this.placeCaretAtEnd();
      const r = this.savedRange.cloneRange();
      r.collapse(false);
      const el = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement;
      const cc = el && el.closest('.rh-cc');
      if (cc) {                       // never nest controls: insert after the selected one
        r.setStartAfter(cc);
        r.collapse(true);
      }
      return r;
    }

    /** @param {string} id @returns {HTMLElement|null} */
    _find(id) {
      return this.root.querySelector(`.rh-cc[data-tag="${CITE_PREFIX}${id}"]`)
        || this.footnotesRoot.querySelector(`.rh-cc[data-tag="${CITE_PREFIX}${id}"]`);
    }

    // ---------------------------------------------------------------- citations

    /** @returns {Promise<Array<{citationID, inNote, ref}>>} */
    async listCitations() {
      this._renumber();
      return this._citationControls().map((c, i) => ({
        citationID: c.el.dataset.tag.slice(CITE_PREFIX.length), inNote: c.inNote, ref: { index: i },
      }));
    }

    /**
     * @param {string} id
     * @param {string} html
     * @param {{asNote?: boolean, appearance?: string}} opts
     */
    async insertCitation(id, html, opts = {}) {
      const range = this._insertionRange();
      const cc = this._makeControl(CITE_PREFIX + id, html, opts.appearance);
      if (opts.asNote && this.supportsFootnotes) {
        const fn = 'fn' + Math.random().toString(36).slice(2, 9);
        const sup = document.createElement('sup');
        sup.className = 'mock-fnref';
        sup.dataset.fn = fn;
        sup.contentEditable = 'false';
        range.insertNode(sup);
        const li = document.createElement('li');
        li.dataset.fn = fn;
        li.appendChild(cc);
        this.footnotesRoot.querySelector('ol').appendChild(li);
        this._placeAfter(sup);
      } else {
        range.insertNode(cc);
        this._placeAfter(cc);
      }
      this._renumber();
    }

    /** Move the caret after a node (like Word after an insertion). @param {Node} node */
    _placeAfter(node) {
      const r = document.createRange();
      r.setStartAfter(node);
      r.collapse(true);
      this.savedRange = r;
      this._select(null);
    }

    /**
     * Write rendered citations (same order as listCitations()).
     * @param {Array<{ref:{index:number}, html:string, retag?:string}>} updates
     * @param {{force?: boolean, appearance?: string}} opts
     */
    async applyCitations(updates, opts = {}) {
      const controls = this._citationControls();
      this.lastApplied = 0;
      for (const u of updates) {
        const c = controls[u.ref.index];
        if (!c) continue;
        if (u.retag) c.el.dataset.tag = CITE_PREFIX + u.retag;
        if (opts.appearance) c.el.dataset.appearance = opts.appearance;
        if (!opts.force && c.el.innerHTML === u.html) continue;
        c.el.innerHTML = u.html;
        this.lastApplied++;
      }
    }

    /** @param {string} id */
    async removeCitation(id) {
      const cc = this._find(id);
      if (!cc) return;
      const li = cc.closest('li[data-fn]');
      if (li && li.textContent.replace(cc.textContent, '').replace(/[.\s]/g, '') === '') {
        const marker = this.root.querySelector(`.mock-fnref[data-fn="${li.dataset.fn}"]`);
        if (marker) marker.remove();
        li.remove();
      } else {
        cc.remove();
      }
      if (this.selectedControl === cc) this._select(null);
      this._renumber();
    }

    /** @returns {Promise<string|null>} */
    async selectionCitationId() {
      const cc = this.selectedControl;
      if (!cc || !cc.isConnected) return null;
      const tag = cc.dataset.tag || '';
      return tag.startsWith(CITE_PREFIX) ? tag.slice(CITE_PREFIX.length) : null;
    }

    /** @param {string} id */
    async selectCitation(id) {
      const cc = this._find(id);
      if (cc) {
        cc.scrollIntoView({ block: 'center', behavior: 'smooth' });
        this._select(cc);
      }
      return !!cc;
    }

    // ---------------------------------------------------------------- bibliography

    async hasBibliography() {
      return !!this.root.querySelector(`.rh-cc[data-tag="${BIBL_TAG}"]`);
    }

    /**
     * @param {string} html
     * @param {{create?: boolean}} opts
     * @returns {Promise<'updated'|'created'|'absent'>}
     */
    async updateBibliography(html, opts = {}) {
      const existing = this.root.querySelector(`.rh-cc[data-tag="${BIBL_TAG}"]`);
      if (existing) {
        existing.innerHTML = html;
        return 'updated';
      }
      if (!opts.create) return 'absent';
      const range = this._insertionRange();
      const el = this._makeControl(BIBL_TAG, html, 'BoundingBox', 'div');
      // Blocks go after the paragraph that holds the caret.
      let block = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
      while (block && block.parentElement !== this.root) block = block.parentElement;
      if (block && block !== this.root) block.after(el);
      else this.root.appendChild(el);
      return 'created';
    }

    async selectBibliography() {
      const el = this.root.querySelector(`.rh-cc[data-tag="${BIBL_TAG}"]`);
      if (el) {
        el.scrollIntoView({ block: 'start', behavior: 'smooth' });
        this._select(el);
      }
      return !!el;
    }

    // ---------------------------------------------------------------- conversions

    /**
     * Move citations into footnotes (toNote) or back into the text.
     * @param {boolean} toNote
     * @returns {Promise<number>}
     */
    async convertCitations(toNote) {
      let moved = 0;
      for (const c of this._citationControls()) {
        if (toNote && !c.inNote) {
          const r = document.createRange();
          r.setStartAfter(c.el);
          r.collapse(true);
          this.savedRange = r;
          const html = c.el.innerHTML;
          const id = c.el.dataset.tag.slice(CITE_PREFIX.length);
          const appearance = c.el.dataset.appearance;
          // remove a single space typed before an in-text citation
          const prev = c.el.previousSibling;
          c.el.remove();
          if (prev && prev.nodeType === 3) prev.data = prev.data.replace(/ $/, '');
          await this.insertCitation(id, html, { asNote: true, appearance });
          moved++;
        } else if (!toNote && c.inNote) {
          c.ref.before(document.createTextNode(' '));
          c.ref.before(c.el);
          c.li.remove();
          c.ref.remove();
          moved++;
        }
      }
      this._renumber();
      return moved;
    }

    /** @returns {Promise<number>} controls converted to plain text */
    async unlinkAll() {
      let n = 0;
      [...this.root.querySelectorAll('.rh-cc'), ...this.footnotesRoot.querySelectorAll('.rh-cc')].forEach(el => {
        const frag = document.createDocumentFragment();
        while (el.firstChild) frag.appendChild(el.firstChild);
        el.replaceWith(frag);
        n++;
      });
      // footnote markers become plain superscript numbers
      this.root.querySelectorAll('.mock-fnref').forEach(m => m.classList.add('is-unlinked'));
      return n;
    }
  }

  global.RHMockAdapter = MockAdapter;
})(typeof window !== 'undefined' ? window : globalThis);
