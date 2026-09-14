/*
 * ReviewHunter citation engine wrapper (shared by the web app and the Word add-in).
 *
 * Wraps citeproc-js (window.CSL, the engine Zotero uses) behind a small async API:
 *
 *   const rc = new RHCitations({ baseUrl: '' });          // '' = same origin
 *   await rc.useStyle('apa', 'en-US');                     // loads style + locales
 *   const html = await rc.bibliography([1, 2], 'html');    // formatted bibliography
 *   const txt  = await rc.citation([{id: 1, locator: '12', label: 'page'}], 'text');
 *   // Document-wide processing (numbering / disambiguation across citations):
 *   const out  = await rc.processDocument(clusters, 'html', uncitedIds);
 *
 * clusters = [{ citationID: 'c1', items: [{id, locator, label, prefix, suffix,
 *                                           'suppress-author', 'author-only'}] }, ...]
 *
 * Requirements: citeproc.js loaded before this file; API endpoints
 *   GET /api/csl/styles/{id}, GET /api/csl/locales/{lang}, GET /api/csl/items?ids=...
 */
(function (global) {
  'use strict';

  class RHCitations {
    /** @param {{baseUrl?: string}} opts */
    constructor(opts = {}) {
      this.baseUrl = opts.baseUrl || '';
      this.styleId = null;
      this.lang = 'en-US';
      this.styleXml = null;
      this.locales = {};       // lang -> XML string (loaded asynchronously, served synchronously to citeproc)
      this.items = {};         // id (string) -> CSL-JSON
      this.engine = null;
      this.format = 'html';
    }

    // ------------------------------------------------------------------ loading

    async _text(url) {
      const r = await fetch(this.baseUrl + url, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
      return r.text();
    }

    async _loadLocale(lang) {
      if (this.locales[lang]) return;
      this.locales[lang] = await this._text(`/api/csl/locales/${encodeURIComponent(lang)}`);
    }

    /** Load a style (by id) and its locale; rebuilds the engine. */
    async useStyle(styleId, lang = 'en-US') {
      if (this.styleId === styleId && this.lang === lang && this.engine) return;
      this.styleXml = await this._text(`/api/csl/styles/${encodeURIComponent(styleId)}`);
      // A style can force its own default-locale (e.g. a French journal style).
      const m = this.styleXml.match(/default-locale="([^"]+)"/);
      const wanted = [lang, 'en-US'];
      if (m) wanted.push(m[1]);
      await Promise.all([...new Set(wanted)].map(l => this._loadLocale(l).catch(() => null)));
      this.styleId = styleId;
      this.lang = lang;
      this.engine = null;
    }

    /** Fetch CSL-JSON for ids not cached yet (or all when refresh=true). */
    async loadItems(ids, refresh = false) {
      const missing = ids.map(String).filter(id => refresh || !this.items[id]);
      if (missing.length) {
        const chunks = [];
        for (let i = 0; i < missing.length; i += 200) chunks.push(missing.slice(i, i + 200));
        for (const chunk of chunks) {
          const r = await fetch(`${this.baseUrl}/api/csl/items?ids=${chunk.join(',')}`);
          if (!r.ok) throw new Error(`items: HTTP ${r.status}`);
          for (const it of await r.json()) this.items[String(it.id)] = it;
        }
      }
      return ids.map(String).filter(id => this.items[id]);
    }

    /** Register CSL-JSON objects directly (e.g. search results that already carry csl). */
    addItems(cslItems) {
      for (const it of cslItems || []) this.items[String(it.id)] = it;
    }

    _newEngine(format) {
      if (!global.CSL) throw new Error('citeproc-js (CSL) is not loaded');
      if (!this.styleXml) throw new Error('call useStyle() first');
      const self = this;
      const sys = {
        retrieveLocale(lang) {
          return self.locales[lang] || self.locales[lang.split('-')[0]] || self.locales['en-US'];
        },
        retrieveItem(id) {
          return self.items[String(id)];
        },
      };
      const engine = new global.CSL.Engine(sys, this.styleXml, this.lang, false);
      engine.setOutputFormat(format);
      this.engine = engine;
      this.format = format;
      return engine;
    }

    /** Is the current style a note style (footnotes) or in-text? */
    get isNoteStyle() {
      return /class="note"/.test(this.styleXml || '');
    }

    get isNumeric() {
      return /citation-format="numeric"/.test(this.styleXml || '');
    }

    // ------------------------------------------------------------------ formatting

    /** Bibliography for ids, in citeproc order. Returns {html|text, entries, params}. */
    async bibliography(ids, format = 'html') {
      const valid = await this.loadItems(ids);
      const engine = this._newEngine(format);
      engine.updateItems(valid);
      const out = engine.makeBibliography();
      if (!out) return { text: '', entries: [], params: {} };  // style has no bibliography
      const [params, entries] = out;
      const body = format === 'html'
        ? `${params.bibstart}${entries.join('')}${params.bibend}`
        : entries.join(format === 'rtf' ? '\\par\n' : '\n');
      return { text: body, entries, params };
    }

    /** One stand-alone citation cluster (no document context). */
    async citation(citationItems, format = 'html') {
      await this.loadItems(citationItems.map(c => c.id));
      const engine = this._newEngine(format);
      engine.updateItems(citationItems.map(c => String(c.id)));
      return engine.makeCitationCluster(citationItems.map(c => ({ ...c, id: String(c.id) })));
    }

    /**
     * Whole-document processing: consistent numbering, "ibid.", disambiguation.
     * @returns {{citations: Object<string,string>, bibliography: {text, entries, params}}}
     */
    async processDocument(clusters, format = 'html', uncitedIds = []) {
      const allIds = new Set(uncitedIds.map(String));
      clusters.forEach(c => c.items.forEach(it => allIds.add(String(it.id))));
      await this.loadItems([...allIds]);
      const engine = this._newEngine(format);
      const inputs = clusters.map((c, index) => ([
        {
          citationID: c.citationID,
          citationItems: c.items.filter(it => this.items[String(it.id)]).map(it => ({ ...it, id: String(it.id) })),
          properties: { noteIndex: this.isNoteStyle ? index + 1 : 0 },
        },
        [], [],
      ]));
      const citations = {};
      const pre = [];
      for (const [citation] of inputs) {
        if (!citation.citationItems.length) {
          citations[citation.citationID] = '[missing item]';
          continue;
        }
        const [, updates] = engine.processCitationCluster(citation, pre, []);
        for (const [, text, citationID] of updates) citations[citationID] = text;
        pre.push([citation.citationID, citation.properties.noteIndex]);
      }
      if (uncitedIds.length) engine.updateUncitedItems(uncitedIds.map(String));
      const bib = engine.makeBibliography();
      let bibliography = { text: '', entries: [], params: {} };
      if (bib) {
        const [params, entries] = bib;
        bibliography = {
          params, entries,
          text: format === 'html' ? `${params.bibstart}${entries.join('')}${params.bibend}` : entries.join('\n'),
        };
      }
      return { citations, bibliography };
    }

    /** HTML -> plain text helper (for clipboard fallbacks). */
    static htmlToText(html) {
      const div = document.createElement('div');
      div.innerHTML = html;
      return (div.innerText || div.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
    }

    /** Copy rich HTML + plain text to the clipboard (Quick Copy). */
    static async copyRich(html, text) {
      text = text || RHCitations.htmlToText(html);
      if (navigator.clipboard && global.ClipboardItem) {
        try {
          await navigator.clipboard.write([new global.ClipboardItem({
            'text/html': new Blob([html], { type: 'text/html' }),
            'text/plain': new Blob([text], { type: 'text/plain' }),
          })]);
          return true;
        } catch (e) { /* fall through */ }
      }
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
        return true;
      }
      return false;
    }
  }

  global.RHCitations = RHCitations;
})(typeof window !== 'undefined' ? window : globalThis);
