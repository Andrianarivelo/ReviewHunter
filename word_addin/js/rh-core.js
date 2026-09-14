/*
 * ReviewHunter Word add-in: adapter-independent citation logic.
 *
 * Everything that decides WHAT the document should contain lives here, so it can be
 * tested in a normal browser against the MockAdapter. The adapters (Word or mock) only
 * know HOW to put content controls into a document.
 *
 * Exposes window.RH = { util, LOCATOR_LABELS, LOCALES, ApiClient, StyleCache,
 *                       DocState, BibliographyHtml, Controller }.
 *
 * Storage format inside the document (Office document settings, or a CustomXMLPart):
 *   rh.citations : { citationID: { items: [ {id, locator?, label?, prefix?, suffix?,
 *                                            "suppress-author"?} ], v: 1 } }
 *   rh.items     : { itemId: CSL-JSON snapshot }   (lets the document render offline)
 *   rh.prefs     : { style, styleTitle, styleFormat, locale, appearance, v: 1 }
 *   rh.trash     : [ { citationID, cluster, at } ]  (recently orphaned clusters, so a
 *                                                     cut/paste or Undo can restore them)
 *
 * Adapter interface (implemented by WordAdapter and MockAdapter):
 *   kind                              'word' | 'mock'
 *   supportsFootnotes                 boolean
 *   loadState() / saveState(s) / clearState()
 *   listCitations()                   -> [{citationID, inNote, ref}] in document order
 *   insertCitation(id, html, {asNote, appearance})
 *   applyCitations([{ref, citationID, html, retag?}], {force, appearance})
 *   removeCitation(id)
 *   updateBibliography(html, {create}) -> 'updated' | 'created' | 'absent'
 *   hasBibliography()                 -> boolean
 *   selectBibliography() / selectCitation(id)
 *   selectionCitationId()             -> citationID | null
 *   convertCitations(toNote)          -> number of citations moved
 *   unlinkAll()                       -> number of controls converted to plain text
 *   onSelectionChanged(callback)
 */
(function (global) {
  'use strict';

  // ================================================================== utilities

  const util = {
    /**
     * Short random citation identifier (8 base-36 chars), e.g. "c4k2m9q1x".
     * Content control tags are limited to 255 chars, so "RH-CITE:" + id stays tiny.
     * @returns {string}
     */
    uid() {
      const rnd = (global.crypto && global.crypto.getRandomValues)
        ? Array.from(global.crypto.getRandomValues(new Uint32Array(2))).map(n => n.toString(36)).join('')
        : Math.random().toString(36).slice(2) + Date.now().toString(36);
      return 'c' + rnd.replace(/[^a-z0-9]/g, '').slice(0, 8).padEnd(8, '0');
    },

    /**
     * Debounce a function: it runs once, `wait` ms after the last call.
     * @param {Function} fn
     * @param {number} wait milliseconds
     * @returns {Function}
     */
    debounce(fn, wait) {
      let t = null;
      return function (...args) {
        clearTimeout(t);
        t = setTimeout(() => fn.apply(this, args), wait);
      };
    },

    /**
     * Escape text for safe insertion into HTML.
     * @param {*} s
     * @returns {string}
     */
    escapeHtml(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    },

    /**
     * Deep clone a JSON-compatible value.
     * @template T
     * @param {T} v
     * @returns {T}
     */
    clone(v) {
      return v == null ? v : JSON.parse(JSON.stringify(v));
    },

    /**
     * Convert HTML to plain text (whitespace normalised). Used to compare what a
     * control shows with what it should show.
     * @param {string} html
     * @returns {string}
     */
    htmlToText(html) {
      const div = document.createElement('div');
      div.innerHTML = html || '';
      return (div.textContent || '').replace(/\s+/g, ' ').trim();
    },

    /**
     * Compact author string from ReviewHunter creators:
     * "Hopfield", "Dayan & Abbott", "Vaswani et al.".
     * @param {Array<{creatorType?:string,lastName?:string,firstName?:string,name?:string}>} creators
     * @returns {string}
     */
    shortAuthors(creators) {
      const list = (creators || []);
      const authors = list.filter(c => !c.creatorType || c.creatorType === 'author');
      const use = (authors.length ? authors : list).map(c => c.lastName || c.name || '').filter(Boolean);
      if (!use.length) return '';
      if (use.length === 1) return use[0];
      if (use.length === 2) return `${use[0]} & ${use[1]}`;
      return `${use[0]} et al.`;
    },

    /**
     * Compact author string from a CSL-JSON item (used for snapshots).
     * @param {object} csl
     * @returns {string}
     */
    cslShortAuthors(csl) {
      const names = (csl && (csl.author || csl.editor)) || [];
      return util.shortAuthors(names.map(n => ({ lastName: n.family || n.literal || '' })));
    },

    /**
     * Year from a CSL-JSON item.
     * @param {object} csl
     * @returns {string}
     */
    cslYear(csl) {
      const dp = csl && csl.issued && csl.issued['date-parts'];
      return dp && dp[0] && dp[0][0] ? String(dp[0][0]) : '';
    },

    /**
     * Remove empty options from a citation item so citeproc receives clean input
     * (a label without a locator, for example, is dropped).
     * @param {object} it
     * @returns {{id:string, locator?:string, label?:string, prefix?:string, suffix?:string, 'suppress-author'?:boolean}}
     */
    cleanCiteItem(it) {
      const out = { id: String(it.id) };
      const locator = (it.locator || '').trim();
      if (locator) {
        out.locator = locator;
        out.label = it.label || 'page';
      }
      if (it.prefix && it.prefix.trim()) out.prefix = it.prefix.replace(/\s+$/, '') + ' ';
      if (it.suffix && it.suffix.trim()) out.suffix = (/^[\s,;.]/.test(it.suffix) ? '' : ' ') + it.suffix.trim();
      if (it['suppress-author']) out['suppress-author'] = true;
      return out;
    },

    /**
     * Wrap a callback-style Office "Async" method in a Promise.
     * @param {Function} fn receives the Office callback
     * @returns {Promise<*>}
     */
    officeAsync(fn) {
      return new Promise((resolve, reject) => {
        fn(result => {
          if (result.status === 'succeeded') resolve(result.value);
          else reject(result.error || new Error('Office call failed'));
        });
      });
    },
  };

  /** CSL locator labels offered in the citation editor (value, display text). */
  const LOCATOR_LABELS = [
    ['page', 'Page'], ['chapter', 'Chapter'], ['section', 'Section'], ['paragraph', 'Paragraph'],
    ['figure', 'Figure'], ['table', 'Table'], ['equation', 'Equation'], ['appendix', 'Appendix'],
    ['volume', 'Volume'], ['issue', 'Issue'], ['part', 'Part'], ['book', 'Book'], ['column', 'Column'],
    ['line', 'Line'], ['note', 'Note'], ['verse', 'Verse'], ['act', 'Act'], ['scene', 'Scene'],
    ['opus', 'Opus'], ['folio', 'Folio'], ['sub-verbo', 'Sub verbo'], ['canon', 'Canon'], ['rule', 'Rule'],
    ['supplement', 'Supplement'], ['timestamp', 'Timestamp'], ['version', 'Version'],
    ['article-locator', 'Article'], ['elocation', 'Location'], ['title-locator', 'Title'],
  ];

  /** Locales bundled with ReviewHunter. */
  const LOCALES = [
    ['en-US', 'English (US)'], ['en-GB', 'English (UK)'], ['fr-FR', 'Francais'], ['de-DE', 'Deutsch'],
    ['es-ES', 'Espanol'], ['it-IT', 'Italiano'], ['pt-BR', 'Portugues (Brasil)'],
  ];

  // ================================================================== server client

  /**
   * Thin client for the ReviewHunter HTTP API with an online/offline flag.
   * All URLs are relative, so the task pane talks to the server that served it.
   */
  class ApiClient {
    /** @param {{base?: string, timeoutMs?: number}} [opts] */
    constructor(opts = {}) {
      this.base = opts.base || '';
      this.timeoutMs = opts.timeoutMs || 8000;
      /** @type {boolean|null} null until the first request completes */
      this.online = null;
      this.forceOffline = false;       // test hook: simulate a stopped server
      this._listeners = [];
    }

    /**
     * Subscribe to online/offline transitions.
     * @param {(online:boolean)=>void} fn
     */
    onStatus(fn) { this._listeners.push(fn); }

    /** @param {boolean} value */
    _setOnline(value) {
      if (this.online === value) return;
      this.online = value;
      this._listeners.forEach(fn => { try { fn(value); } catch (e) { /* listener errors are ignored */ } });
    }

    /**
     * fetch() with timeout; network failures flip the status to offline.
     * @param {string} path relative API path
     * @param {RequestInit} [init]
     * @returns {Promise<Response>}
     */
    async _fetch(path, init = {}) {
      if (this.forceOffline) {
        this._setOnline(false);
        throw new TypeError('ReviewHunter is offline (simulated)');
      }
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
      if (init.signal) init.signal.addEventListener('abort', () => ctrl.abort());
      try {
        const r = await fetch(this.base + path, { cache: 'no-store', ...init, signal: ctrl.signal });
        this._setOnline(true);
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r;
      } catch (e) {
        if (e.name === 'TypeError' || (e.name === 'AbortError' && !(init.signal && init.signal.aborted))) {
          this._setOnline(false);
        }
        throw e;
      } finally {
        clearTimeout(timer);
      }
    }

    /** @returns {Promise<boolean>} true when /api/health answers */
    async ping() {
      try {
        const r = await this._fetch('/api/health');
        return !!(await r.json()).ok;
      } catch (e) {
        return false;
      }
    }

    /**
     * Type-ahead search.
     * @param {string} q
     * @param {number} [limit]
     * @param {AbortSignal} [signal] lets the UI cancel stale searches
     * @returns {Promise<Array<object>>}
     */
    async search(q, limit = 25, signal) {
      const r = await this._fetch(`/api/csl/search?q=${encodeURIComponent(q)}&limit=${limit}`, { signal });
      return r.json();
    }

    /**
     * CSL-JSON for item ids (missing ids are simply absent from the answer).
     * @param {Array<string|number>} ids
     * @returns {Promise<Array<object>>}
     */
    async items(ids) {
      const out = [];
      for (let i = 0; i < ids.length; i += 150) {
        const chunk = ids.slice(i, i + 150);
        const r = await this._fetch(`/api/csl/items?ids=${chunk.map(encodeURIComponent).join(',')}`);
        out.push(...(await r.json()));
      }
      return out;
    }

    /** @returns {Promise<Array<{id,title,format,source}>>} installed styles */
    async styles() { return (await this._fetch('/api/csl/styles')).json(); }

    /** @returns {Promise<object>} application settings (default style/locale) */
    async settings() { return (await this._fetch('/api/settings')).json(); }
  }

  // ================================================================== style cache

  /**
   * Keeps the last used CSL style and locale XML in localStorage so documents can be
   * refreshed while ReviewHunter is not running.
   */
  const StyleCache = {
    PREFIX: 'rh.cache.',

    /** @param {string} key @returns {string|null} */
    _get(key) {
      try { return global.localStorage.getItem(this.PREFIX + key); } catch (e) { return null; }
    },

    /** @param {string} key @param {string} value */
    _set(key, value) {
      try { global.localStorage.setItem(this.PREFIX + key, value); } catch (e) { /* quota or disabled */ }
    },

    /**
     * Make `engine` (an RHCitations instance) use the style: from the server when
     * possible (and cache it), else from the local cache.
     * @param {object} engine RHCitations
     * @param {ApiClient} api
     * @param {string} styleId
     * @param {string} lang
     * @returns {Promise<'server'|'cache'|'memory'>}
     */
    async use(engine, api, styleId, lang) {
      if (engine.styleId === styleId && engine.lang === lang && engine.styleXml) return 'memory';
      if (!api.forceOffline && api.online !== false) {
        try {
          await engine.useStyle(styleId, lang);
          this._set(`style.${styleId}`, engine.styleXml);
          Object.entries(engine.locales).forEach(([l, xml]) => xml && this._set(`locale.${l}`, xml));
          return 'server';
        } catch (e) {
          if (e.name === 'TypeError') api._setOnline(false);
          else if (api.online) throw e;       // server answered with an error (e.g. unknown style)
        }
      }
      const xml = this._get(`style.${styleId}`);
      if (!xml) {
        throw new Error(`The style "${styleId}" has not been used on this computer yet and ReviewHunter is offline. `
          + 'Start ReviewHunter once to download it.');
      }
      const m = xml.match(/default-locale="([^"]+)"/);
      for (const l of new Set([lang, 'en-US', m && m[1]].filter(Boolean))) {
        const loc = this._get(`locale.${l}`);
        if (loc) engine.locales[l] = loc;
      }
      engine.styleXml = xml;
      engine.styleId = styleId;
      engine.lang = lang;
      engine.engine = null;
      return 'cache';
    },
  };

  // ================================================================== document state

  const DocState = {
    VERSION: 1,
    TRASH_MAX: 25,

    /**
     * Normalise whatever was read from the document into a complete state object.
     * @param {object} raw
     * @returns {{citations:Object, items:Object, prefs:Object|null, trash:Array}}
     */
    normalize(raw) {
      raw = raw || {};
      const obj = v => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
      return {
        citations: obj(raw.citations),
        items: obj(raw.items),
        prefs: raw.prefs && typeof raw.prefs === 'object' ? raw.prefs : null,
        trash: Array.isArray(raw.trash) ? raw.trash : [],
      };
    },

    /** @returns {{citations:{}, items:{}, prefs:null, trash:[]}} */
    empty() { return DocState.normalize({}); },
  };

  // ================================================================== bibliography HTML

  const BibliographyHtml = {
    /** Font size assumed when converting CSL em-based offsets to points. */
    BASE_PT: 12,

    /**
     * Convert citeproc-js bibliography output into Word-friendly HTML: one <p> per
     * entry with inline CSS (hanging indent, line spacing, entry spacing). Word's HTML
     * import ignores class-based CSS and inline-block divs, so numbered entries become
     * "label + tab + text" inside a hanging-indent paragraph (Word puts an implicit tab
     * stop at the hanging indent).
     * @param {{entries:string[], params:object}} bib output of RHCitations.processDocument
     * @param {{emptyText?: string}} [opts]
     * @returns {string}
     */
    toWord(bib, opts = {}) {
      const esc = util.escapeHtml;
      if (!bib || !bib.params || !bib.params.bibstart) {
        return `<p style="margin:0"><i>${esc(opts.noBibText || 'This citation style does not define a bibliography.')}</i></p>`;
      }
      const entries = bib.entries || [];
      if (!entries.length) {
        return `<p style="margin:0"><i>${esc(opts.emptyText || 'Bibliography: insert a citation to populate this list.')}</i></p>`;
      }
      const p = bib.params;
      const lineSpacing = Number(p.linespacing) || 1;
      const entrySpacing = p.entryspacing == null ? 1 : Number(p.entryspacing) || 0;
      const align = p['second-field-align'];
      let indentPt = 0;
      if (align) {
        indentPt = Math.max(22, Math.round((Number(p.maxoffset) || 3) * 0.6 * this.BASE_PT + 8));
      } else if (p.hangingindent) {
        indentPt = 36;                                  // 0.5 inch, as Zotero does
      }
      const style = [
        'margin-top:0',
        `margin-bottom:${Math.round(entrySpacing * this.BASE_PT * lineSpacing)}pt`,
        `line-height:${Math.round(lineSpacing * 100)}%`,
        indentPt ? `margin-left:${indentPt}pt` : 'margin-left:0',
        indentPt ? `text-indent:-${indentPt}pt` : 'text-indent:0',
      ].join(';');
      const tab = '<span style="mso-tab-count:1">&nbsp;</span>';
      const tpl = document.createElement('template');
      return entries.map(entry => {
        tpl.innerHTML = entry;
        const left = tpl.content.querySelector('.csl-left-margin');
        const right = tpl.content.querySelector('.csl-right-inline');
        let inner;
        if (left && right) {
          const label = left.innerHTML.trim();
          inner = `<span class="rh-bib-label" style="display:inline-block;text-indent:0;min-width:${indentPt}pt">${label}</span>`
            + `${tab}${right.innerHTML.trim()}`;
        } else {
          const node = tpl.content.querySelector('.csl-entry');
          inner = (node ? node.innerHTML : entry).trim();
        }
        return `<p class="rh-bib-entry" style="${style}">${inner}</p>`;
      }).join('');
    },
  };

  // ================================================================== controller

  /**
   * Orchestrates citations for one document through an adapter.
   * All public methods are serialised (one document operation at a time).
   */
  class Controller {
    /**
     * @param {{adapter: object, api: ApiClient, engine: object, defaults?: {style:string, locale:string}}} deps
     *   engine is an RHCitations instance (window.RHCitations from /static/js/citations.js)
     */
    constructor(deps) {
      this.adapter = deps.adapter;
      this.api = deps.api;
      this.engine = deps.engine;
      this.defaults = Object.assign({ style: 'apa', locale: 'en-US', appearance: 'BoundingBox' }, deps.defaults || {});
      this.state = DocState.empty();
      /** citationIDs in document order from the last refresh */
      this.order = [];
      /** last refresh summary, see _summary() */
      this.summary = null;
      this.warnings = [];
      this._queue = Promise.resolve();
      this._listeners = { change: [], busy: [] };
      this._busy = 0;
    }

    // ---------------------------------------------------------------- plumbing

    /**
     * Subscribe to events: 'change' (summary) and 'busy' (boolean).
     * @param {'change'|'busy'} name
     * @param {Function} fn
     */
    on(name, fn) { (this._listeners[name] || (this._listeners[name] = [])).push(fn); }

    /** @param {string} name @param {*} payload */
    _emit(name, payload) {
      (this._listeners[name] || []).forEach(fn => { try { fn(payload); } catch (e) { console.error(e); } });
    }

    /**
     * Run `fn` after every previously queued operation finished.
     * @template T
     * @param {() => Promise<T>} fn
     * @returns {Promise<T>}
     */
    _serial(fn) {
      const run = this._queue.then(async () => {
        this._busy++;
        if (this._busy === 1) this._emit('busy', true);
        try {
          return await fn();
        } finally {
          this._busy--;
          if (this._busy === 0) this._emit('busy', false);
        }
      });
      this._queue = run.catch(() => {});
      return run;
    }

    /** @returns {{style:string, styleTitle?:string, styleFormat?:string, locale:string, appearance:string}} */
    get prefs() {
      return Object.assign({}, this.defaults, this.state.prefs || {});
    }

    /** Is the current style a note style AND can this host insert footnotes? */
    get useFootnotes() {
      return this.engine.isNoteStyle && this.adapter.supportsFootnotes;
    }

    // ---------------------------------------------------------------- lifecycle

    /**
     * Load the document state and default preferences, then refresh the document.
     * @param {{refresh?: boolean}} [opts]
     * @returns {Promise<object>} summary
     */
    async init(opts = {}) {
      return this._serial(async () => {
        this.state = DocState.normalize(await this.adapter.loadState());
        if (!this.state.prefs) {
          try {
            const s = await this.api.settings();
            if (s.citation_style) this.defaults.style = s.citation_style;
            if (s.citation_locale) this.defaults.locale = s.citation_locale;
          } catch (e) { /* offline: keep built-in defaults */ }
        }
        const hasContent = Object.keys(this.state.citations).length || await this.adapter.hasBibliography();
        if (opts.refresh !== false && hasContent) return this._refresh({ snapshots: true });
        await this._useStyle();
        this.order = (await this.adapter.listCitations()).map(e => e.citationID);
        return this._publishSummary({ citations: this.order.length });
      });
    }

    /** Load the document style into the citeproc wrapper (server or offline cache). */
    async _useStyle() {
      const p = this.prefs;
      this.styleSource = await StyleCache.use(this.engine, this.api, p.style, p.locale);
      return this.styleSource;
    }

    /**
     * Build and emit a summary for the UI.
     * @param {object} extra
     * @returns {object}
     */
    _publishSummary(extra = {}) {
      const ids = new Set();
      Object.values(this.state.citations).forEach(c => (c.items || []).forEach(it => ids.add(String(it.id))));
      this.summary = Object.assign({
        citations: this.order.length,
        sources: ids.size,
        prefs: this.prefs,
        isNote: this.engine.isNoteStyle,
        isNumeric: this.engine.isNumeric,
        footnotes: this.useFootnotes,
        styleSource: this.styleSource,
        online: this.api.online,
        warnings: this.warnings.slice(),
        at: new Date(),
      }, extra);
      this._emit('change', this.summary);
      return this.summary;
    }

    // ---------------------------------------------------------------- citations

    /**
     * Insert a new citation cluster at the cursor, then refresh the whole document.
     * @param {Array<object>} items citation items (id + options)
     * @param {Object<string,object>} [cslById] CSL-JSON for these ids (from search results)
     * @returns {Promise<string>} the new citationID
     */
    async insertCitation(items, cslById = {}) {
      if (!items || !items.length) throw new Error('Add at least one reference to the citation.');
      return this._serial(async () => {
        await this._useStyle();
        const clean = items.map(util.cleanCiteItem);
        this._storeSnapshots(clean, cslById);
        await this._ensureSnapshots(clean.map(i => i.id));
        const id = util.uid();
        this.state.citations[id] = { items: clean, v: DocState.VERSION };
        if (!this.state.prefs) this.state.prefs = this._prefsRecord(this.prefs);
        const asNote = this.useFootnotes;
        this.warnings = this.engine.isNoteStyle && !this.adapter.supportsFootnotes
          ? ['This version of Word cannot insert footnotes from an add-in (WordApi 1.5 needed). The note-style citation was inserted in the text instead.']
          : [];
        // Provisional text; the document-wide refresh below sets the final numbering.
        let html = '[citation]';
        try {
          this.engine.addItems(Object.values(this.state.items));
          const available = this._availableItems(clean);
          if (available.length) html = await this.engine.citation(available, 'html');
        } catch (e) {
          html = '[citation]';
        }
        try {
          await this.adapter.insertCitation(id, html, { asNote, appearance: this.prefs.appearance });
        } catch (e) {
          delete this.state.citations[id];
          throw e;
        }
        await this.adapter.saveState(this.state);
        await this._refresh({ snapshots: false });
        return id;
      });
    }

    /**
     * Replace the items of an existing citation, then refresh.
     * @param {string} citationID
     * @param {Array<object>} items
     * @param {Object<string,object>} [cslById]
     */
    async updateCitation(citationID, items, cslById = {}) {
      if (!items || !items.length) return this.removeCitation(citationID);
      return this._serial(async () => {
        const clean = items.map(util.cleanCiteItem);
        this._storeSnapshots(clean, cslById);
        await this._ensureSnapshots(clean.map(i => i.id));
        this.state.citations[citationID] = { items: clean, v: DocState.VERSION };
        await this.adapter.saveState(this.state);
        return this._refresh({ snapshots: false, force: true });
      });
    }

    /**
     * Delete a citation (its control, or its whole footnote) and refresh.
     * @param {string} citationID
     */
    async removeCitation(citationID) {
      return this._serial(async () => {
        await this.adapter.removeCitation(citationID);
        delete this.state.citations[citationID];
        await this.adapter.saveState(this.state);
        return this._refresh({ snapshots: false });
      });
    }

    /**
     * Cluster under the document selection, if any.
     * @returns {Promise<{citationID:string, cluster:object, csl:Object<string,object>}|null>}
     */
    async selectionCluster() {
      const id = await this.adapter.selectionCitationId();
      if (!id) return null;
      const cluster = this.state.citations[id] || (this.state.trash.find(t => t.citationID === id) || {}).cluster;
      if (!cluster) return null;
      const csl = {};
      cluster.items.forEach(it => { if (this.state.items[it.id]) csl[it.id] = this.state.items[it.id]; });
      return { citationID: id, cluster: util.clone(cluster), csl };
    }

    /**
     * Format a draft cluster as it would appear if inserted at the end of the document
     * (or in place, when editing), so numbering in the preview is realistic.
     * @param {Array<object>} items
     * @param {Object<string,object>} cslById
     * @param {string|null} editingId
     * @returns {Promise<string>} HTML
     */
    async preview(items, cslById = {}, editingId = null) {
      if (!items || !items.length) return '';
      return this._serial(async () => {
        await this._useStyle();
        const clean = items.map(util.cleanCiteItem);
        this.engine.addItems(Object.values(this.state.items));
        this.engine.addItems(Object.values(cslById));
        const draftId = editingId || '__rh_draft__';
        const clusters = [];
        let placed = false;
        for (const id of this.order) {
          if (id === editingId) {
            clusters.push({ citationID: id, items: clean });
            placed = true;
          } else if (this.state.citations[id]) {
            clusters.push({ citationID: id, items: this.state.citations[id].items });
          }
        }
        if (!placed) clusters.push({ citationID: draftId, items: clean });
        const usable = clusters
          .map(c => ({ ...c, items: this._availableItems(c.items, cslById) }))
          .filter(c => c.items.length);
        if (!usable.find(c => c.citationID === draftId)) return '';
        const out = await this.engine.processDocument(usable, 'html');
        return out.citations[draftId] || '';
      });
    }

    // ---------------------------------------------------------------- refresh

    /**
     * Public refresh: re-read every citation in document order and re-render all of them
     * plus the bibliography.
     * @param {{snapshots?: boolean, force?: boolean}} [opts]
     */
    async refresh(opts = {}) {
      return this._serial(() => this._refresh(Object.assign({ snapshots: true, force: true }, opts)));
    }

    /**
     * Refresh implementation (must run inside _serial).
     *
     * 1. list RH-CITE controls in document order;
     * 2. give copies of a pasted control a new id (duplicate tags);
     * 3. restore clusters from the trash when a control reappears (Undo, paste);
     * 4. move clusters without a control to the trash (orphans);
     * 5. refresh CSL snapshots from the server when online;
     * 6. citeproc processDocument (numbering, disambiguation, ibid);
     * 7. write every citation and the bibliography, save the state.
     *
     * @param {{snapshots?: boolean, force?: boolean}} opts
     */
    async _refresh(opts = {}) {
      const state = this.state;
      await this._useStyle();
      const entries = await this.adapter.listCitations();

      // 2 + 3: duplicates and restorations
      const seen = new Set();
      const unknown = [];
      for (const e of entries) {
        let cluster = state.citations[e.citationID];
        if (!cluster) {
          const t = state.trash.find(x => x.citationID === e.citationID);
          if (t) {
            cluster = t.cluster;
            state.citations[e.citationID] = cluster;
            state.trash = state.trash.filter(x => x !== t);
          }
        }
        if (seen.has(e.citationID)) {
          const newId = util.uid();
          if (cluster) state.citations[newId] = util.clone(cluster);
          e.retag = newId;
          e.citationID = newId;
        }
        seen.add(e.citationID);
        if (!state.citations[e.citationID]) unknown.push(e.citationID);
      }

      // 4: orphans go to the trash (bounded) instead of being lost immediately
      const now = Date.now();
      for (const id of Object.keys(state.citations)) {
        if (!seen.has(id)) {
          state.trash.unshift({ citationID: id, cluster: state.citations[id], at: now });
          delete state.citations[id];
        }
      }
      state.trash = state.trash.slice(0, DocState.TRASH_MAX);

      // 5: snapshots
      const ids = new Set();
      entries.forEach(e => (state.citations[e.citationID] ? state.citations[e.citationID].items : [])
        .forEach(it => ids.add(String(it.id))));
      if (opts.snapshots !== false && ids.size && this.api.online !== false && !this.api.forceOffline) {
        try {
          const fresh = await this.api.items([...ids]);
          fresh.forEach(csl => { state.items[String(csl.id)] = csl; });
          this.engine.addItems(fresh);
        } catch (e) { /* offline: snapshots stay as they are */ }
      } else {
        const missing = [...ids].filter(id => !state.items[id]);
        if (missing.length) await this._ensureSnapshots(missing);
      }
      this.engine.addItems(Object.values(state.items));

      // 6: citeproc over the whole document
      const clusters = [];
      for (const e of entries) {
        const c = state.citations[e.citationID];
        const items = c ? this._availableItems(c.items) : [];
        if (items.length) clusters.push({ citationID: e.citationID, items });
      }
      const out = await this.engine.processDocument(clusters, 'html');
      const updates = entries.map(e => ({
        ref: e.ref,
        citationID: e.citationID,
        retag: e.retag,
        html: out.citations[e.citationID]
          || '<i>[ReviewHunter: citation data missing, edit or remove this citation]</i>',
      }));

      // 7: write
      await this.adapter.applyCitations(updates, { force: !!opts.force, appearance: this.prefs.appearance });
      const bibHtml = BibliographyHtml.toWord(out.bibliography);
      const bibStatus = await this.adapter.updateBibliography(bibHtml, { create: false });

      // purge snapshots nobody references any more
      const keep = new Set(ids);
      state.trash.forEach(t => ((t.cluster && t.cluster.items) || []).forEach(it => keep.add(String(it.id))));
      Object.keys(state.items).forEach(id => { if (!keep.has(id)) delete state.items[id]; });
      if (!state.prefs && entries.length) state.prefs = this._prefsRecord(this.prefs);

      await this.adapter.saveState(state);
      this.order = entries.map(e => e.citationID);
      this.lastOutput = { citations: out.citations, bibliography: out.bibliography, bibHtml, updates };
      if (!(this.engine.isNoteStyle && !this.adapter.supportsFootnotes)) {
        this.warnings = this.warnings.filter(w => !/footnotes/.test(w));
      }
      return this._publishSummary({
        citations: entries.length,
        bibliography: bibStatus !== 'absent',
        missing: unknown.length,
      });
    }

    // ---------------------------------------------------------------- bibliography

    /**
     * Insert the bibliography at the cursor, or update the existing one.
     * @returns {Promise<'created'|'updated'>}
     */
    async insertBibliography() {
      return this._serial(async () => {
        const exists = await this.adapter.hasBibliography();
        if (!exists) {
          await this._useStyle();
          await this.adapter.updateBibliography(
            '<p style="margin:0"><i>Bibliography</i></p>', { create: true });
        }
        await this._refresh({ snapshots: true, force: true });
        return exists ? 'updated' : 'created';
      });
    }

    /** Move the document selection to the bibliography. */
    async goToBibliography() {
      return this.adapter.selectBibliography();
    }

    // ---------------------------------------------------------------- preferences

    /**
     * Change document preferences. Changing between note and in-text styles moves
     * citations into or out of footnotes (when the host supports footnotes).
     * @param {{style?:string, styleTitle?:string, styleFormat?:string, locale?:string, appearance?:string}} next
     * @returns {Promise<object>} summary
     */
    async setPrefs(next) {
      return this._serial(async () => {
        const before = this.prefs;
        const wasNote = this.engine.styleXml ? this.engine.isNoteStyle : before.styleFormat === 'note';
        const merged = Object.assign({}, before, next);
        this.state.prefs = this._prefsRecord(merged);
        try {
          await this._useStyle();
        } catch (e) {
          this.state.prefs = this._prefsRecord(before);     // keep the document consistent
          throw e;
        }
        let moved = 0;
        const isNote = this.engine.isNoteStyle;
        if (this.adapter.supportsFootnotes && isNote !== wasNote) {
          moved = await this.adapter.convertCitations(isNote);
        }
        this.warnings = isNote && !this.adapter.supportsFootnotes
          ? ['Note styles need footnotes, which this version of Word does not expose to add-ins (WordApi 1.5). Citations stay in the text.']
          : [];
        await this.adapter.saveState(this.state);
        const summary = await this._refresh({ snapshots: true, force: true });
        summary.moved = moved;
        return summary;
      });
    }

    /**
     * Preferences as stored in the document.
     * @param {object} p
     * @returns {object}
     */
    _prefsRecord(p) {
      return {
        style: p.style, styleTitle: p.styleTitle || '', styleFormat: p.styleFormat || '',
        locale: p.locale, appearance: p.appearance || 'BoundingBox', v: DocState.VERSION,
      };
    }

    // ---------------------------------------------------------------- unlink

    /**
     * Convert every ReviewHunter control into plain text and delete all ReviewHunter
     * data from the document. Irreversible for this document: the UI must confirm first.
     * @returns {Promise<number>} number of controls converted
     */
    async unlink() {
      return this._serial(async () => {
        const n = await this.adapter.unlinkAll();
        await this.adapter.clearState();
        this.state = DocState.empty();
        this.order = [];
        this._publishSummary({ citations: 0, bibliography: false, unlinked: n });
        return n;
      });
    }

    // ---------------------------------------------------------------- snapshots

    /**
     * Save CSL snapshots for the given citation items when provided by the caller.
     * @param {Array<{id:string}>} items
     * @param {Object<string,object>} cslById
     */
    _storeSnapshots(items, cslById) {
      items.forEach(it => {
        const csl = cslById && (cslById[it.id] || cslById[Number(it.id)]);
        if (csl) this.state.items[String(it.id)] = Object.assign({}, csl, { id: String(it.id) });
      });
    }

    /**
     * Fetch snapshots that are still missing (throws a readable error when offline).
     * @param {Array<string>} ids
     */
    async _ensureSnapshots(ids) {
      const missing = ids.map(String).filter(id => !this.state.items[id]);
      if (!missing.length) return;
      try {
        const got = await this.api.items(missing);
        got.forEach(csl => { this.state.items[String(csl.id)] = csl; });
      } catch (e) {
        /* offline: _availableItems() skips them and the citation shows a warning text */
      }
    }

    /**
     * Items whose CSL snapshot is known (citeproc would otherwise try to fetch them).
     * @param {Array<object>} items
     * @param {Object<string,object>} [extra] additional CSL-JSON by id (draft items)
     * @returns {Array<object>}
     */
    _availableItems(items, extra) {
      const known = id => this.state.items[id] || (extra && extra[id]);
      return (items || []).filter(it => known(String(it.id))).map(util.cleanCiteItem);
    }
  }

  global.RH = { util, LOCATOR_LABELS, LOCALES, ApiClient, StyleCache, DocState, BibliographyHtml, Controller };
})(typeof window !== 'undefined' ? window : globalThis);
