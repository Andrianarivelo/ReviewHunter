/*
 * WordAdapter: implements the ReviewHunter document adapter with Office.js (Word API).
 *
 * Citations are rich-text content controls tagged "RH-CITE:<citationID>"; the
 * bibliography is one rich-text content control tagged "RH-BIBL". Cluster data lives in
 * Office document settings (or a CustomXMLPart with namespace urn:reviewhunter:citations
 * when settings are unavailable).
 *
 * Requirement sets: WordApi 1.3 (required by the manifest) for getRange/
 * parentContentControlOrNullObject/compareLocationWith; WordApi 1.5 (checked at run time)
 * for footnotes (Range.insertFootnote, Body.footnotes, NoteItem.reference).
 */
(function (global) {
  'use strict';

  const CITE_PREFIX = 'RH-CITE:';
  const BIBL_TAG = 'RH-BIBL';
  const XML_NS = 'urn:reviewhunter:citations';
  const KEYS = ['rh.citations', 'rh.items', 'rh.prefs', 'rh.trash'];
  const PLACEHOLDER = '[citation]';

  class WordAdapter {
    constructor() {
      this.kind = 'word';
      const req = Office.context.requirements;
      this.supportsFootnotes = !!(req && req.isSetSupported('WordApi', '1.5'));
      this.settings = Office.context.document && Office.context.document.settings;
      this.storage = this.settings ? 'settings' : 'customxml';
    }

    // ---------------------------------------------------------------- state

    /** @returns {Promise<object>} raw state (citations, items, prefs, trash) */
    async loadState() {
      if (this.settings) {
        return {
          citations: this.settings.get('rh.citations'),
          items: this.settings.get('rh.items'),
          prefs: this.settings.get('rh.prefs'),
          trash: this.settings.get('rh.trash'),
        };
      }
      const part = await this._xmlPart();
      if (!part) return {};
      const xml = await RH.util.officeAsync(cb => part.getXmlAsync(cb));
      const doc = new DOMParser().parseFromString(xml, 'application/xml');
      const node = doc.getElementsByTagNameNS(XML_NS, 'state')[0];
      try {
        return node ? JSON.parse(node.textContent) : {};
      } catch (e) {
        return {};
      }
    }

    /** @param {object} state */
    async saveState(state) {
      if (this.settings) {
        this.settings.set('rh.citations', state.citations || {});
        this.settings.set('rh.items', state.items || {});
        if (state.prefs) this.settings.set('rh.prefs', state.prefs);
        this.settings.set('rh.trash', state.trash || []);
        await RH.util.officeAsync(cb => this.settings.saveAsync(cb));
        return;
      }
      const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const xml = `<rh:reviewhunter xmlns:rh="${XML_NS}"><rh:state>${esc(JSON.stringify(state))}</rh:state></rh:reviewhunter>`;
      const old = await this._xmlPart();
      if (old) await RH.util.officeAsync(cb => old.deleteAsync(cb));
      await RH.util.officeAsync(cb => Office.context.document.customXmlParts.addAsync(xml, cb));
    }

    async clearState() {
      if (this.settings) {
        KEYS.forEach(k => this.settings.remove(k));
        await RH.util.officeAsync(cb => this.settings.saveAsync(cb));
        return;
      }
      const old = await this._xmlPart();
      if (old) await RH.util.officeAsync(cb => old.deleteAsync(cb));
    }

    /** @returns {Promise<Office.CustomXmlPart|null>} */
    async _xmlPart() {
      const parts = await RH.util.officeAsync(cb => Office.context.document.customXmlParts.getByNamespaceAsync(XML_NS, cb));
      return parts && parts.length ? parts[0] : null;
    }

    // ---------------------------------------------------------------- enumeration

    /**
     * Load all ReviewHunter citation controls in document order.
     * Body controls come from body.contentControls; footnote controls from each
     * footnote body, merged by comparing footnote reference marks with body controls.
     * @param {Word.RequestContext} ctx
     * @returns {Promise<Array<{cc: Word.ContentControl, inNote: boolean, fn?: Word.NoteItem}>>}
     */
    async _collect(ctx) {
      const body = ctx.document.body;
      const ccs = body.contentControls;
      ccs.load('items/id,items/tag,items/text,items/appearance,items/font/name,items/font/size');
      let notes = null;
      if (this.supportsFootnotes) {
        notes = body.footnotes;
        notes.load('items');
      }
      await ctx.sync();

      const noteEntries = [];
      if (notes && notes.items.length) {
        const perNote = notes.items.map(fn => {
          const c = fn.body.contentControls;
          c.load('items/id,items/tag,items/text,items/appearance,items/font/name,items/font/size');
          return { fn, c };
        });
        await ctx.sync();
        perNote.forEach(({ fn, c }) => c.items
          .filter(x => (x.tag || '').startsWith(CITE_PREFIX))
          .forEach(cc => noteEntries.push({ cc, fn, inNote: true })));
      }
      const noteIds = new Set(noteEntries.map(e => e.cc.id));
      const bodyEntries = ccs.items
        .filter(x => (x.tag || '').startsWith(CITE_PREFIX) && !noteIds.has(x.id))
        .map(cc => ({ cc, inNote: false }));
      if (!noteEntries.length || !bodyEntries.length) return bodyEntries.length ? bodyEntries : noteEntries;

      // Mixed document: position of each footnote = number of body citations before it.
      const cmp = noteEntries.map(n => bodyEntries.map(b => n.fn.reference.compareLocationWith(b.cc.getRange('Whole'))));
      await ctx.sync();
      const merged = [];
      noteEntries.forEach((n, i) => {
        n.pos = cmp[i].filter(r => r.value === 'After' || r.value === 'AdjacentAfter').length;
      });
      let ni = 0;
      bodyEntries.forEach((b, bi) => {
        while (ni < noteEntries.length && noteEntries[ni].pos <= bi) merged.push(noteEntries[ni++]);
        merged.push(b);
      });
      while (ni < noteEntries.length) merged.push(noteEntries[ni++]);
      return merged;
    }

    /** @returns {Promise<Array<{citationID, inNote, ref}>>} */
    async listCitations() {
      return Word.run(async ctx => {
        const all = await this._collect(ctx);
        return all.map(e => ({
          citationID: e.cc.tag.slice(CITE_PREFIX.length),
          inNote: e.inNote,
          ref: { ccId: e.cc.id },
        }));
      });
    }

    // ---------------------------------------------------------------- citations

    /**
     * Configure a freshly created control.
     * @param {Word.ContentControl} cc
     * @param {string} tag
     * @param {string} title
     * @param {string} appearance 'BoundingBox' | 'Hidden' | 'Tags'
     */
    _setup(cc, tag, title, appearance) {
      cc.tag = tag;
      cc.title = title;
      cc.appearance = appearance || 'BoundingBox';
      cc.cannotDelete = false;
      cc.cannotEdit = false;
    }

    /**
     * Insert a citation control at the end of the current selection (the selected text
     * is kept), or inside a new footnote.
     * @param {string} id
     * @param {string} html
     * @param {{asNote?: boolean, appearance?: string}} opts
     */
    async insertCitation(id, html, opts = {}) {
      return Word.run(async ctx => {
        const sel = ctx.document.getSelection();
        const inside = sel.parentContentControlOrNullObject;
        inside.load('tag,isNullObject');
        await ctx.sync();
        // Never nest a citation inside another ReviewHunter control.
        const anchor = (!inside.isNullObject && (inside.tag || '').startsWith('RH-'))
          ? inside.getRange('After')
          : sel.getRange('End');
        let textRange;
        let after;
        if (opts.asNote && this.supportsFootnotes) {
          const fn = anchor.insertFootnote('');
          textRange = fn.body.insertText(PLACEHOLDER, 'End');
          after = fn.reference.getRange('After');
        } else {
          textRange = anchor.insertText(PLACEHOLDER, 'Replace');
        }
        const cc = textRange.insertContentControl();
        this._setup(cc, CITE_PREFIX + id, 'ReviewHunter citation', opts.appearance);
        cc.load('font/name,font/size');
        await ctx.sync();
        const name = cc.font.name;
        const size = cc.font.size;
        cc.insertHtml(`<span>${html}</span>`, 'Replace');
        if (name) cc.font.name = name;           // Word's HTML import may reset the font
        if (size) cc.font.size = size;
        (after || cc.getRange('After')).select('Start');
        await ctx.sync();
      });
    }

    /**
     * Write rendered citations. Controls whose text already matches are skipped unless
     * `force` (style changes can alter formatting without changing text).
     * @param {Array<{ref:{ccId:number}, html:string, retag?:string}>} updates
     * @param {{force?: boolean, appearance?: string}} opts
     */
    async applyCitations(updates, opts = {}) {
      if (!updates.length) return;
      return Word.run(async ctx => {
        const all = await this._collect(ctx);
        const byId = new Map(all.map(e => [e.cc.id, e.cc]));
        for (const u of updates) {
          const cc = byId.get(u.ref.ccId);
          if (!cc) continue;
          if (u.retag) cc.tag = CITE_PREFIX + u.retag;
          if (opts.appearance && cc.appearance !== opts.appearance) cc.appearance = opts.appearance;
          if (!opts.force && (cc.text || '').replace(/\s+/g, ' ').trim() === RH.util.htmlToText(u.html)) continue;
          const name = cc.font.name;
          const size = cc.font.size;
          cc.insertHtml(`<span>${u.html}</span>`, 'Replace');
          if (name) cc.font.name = name;
          if (size) cc.font.size = size;
        }
        await ctx.sync();
      });
    }

    /** @param {string} id delete the control (and its footnote when it holds nothing else) */
    async removeCitation(id) {
      return Word.run(async ctx => {
        const all = await this._collect(ctx);
        const hit = all.find(e => e.cc.tag === CITE_PREFIX + id);
        if (!hit) return;
        if (hit.inNote) {
          const r = hit.fn.body.getRange('Whole');
          r.load('text');
          await ctx.sync();
          const rest = r.text.replace(hit.cc.text, '').replace(/[.\s]/g, '');
          if (!rest) {
            hit.fn.delete();
            await ctx.sync();
            return;
          }
        }
        hit.cc.delete(false);
        await ctx.sync();
      });
    }

    /** @returns {Promise<string|null>} citationID of the control around the selection */
    async selectionCitationId() {
      return Word.run(async ctx => {
        const cc = ctx.document.getSelection().parentContentControlOrNullObject;
        cc.load('tag,isNullObject');
        await ctx.sync();
        if (cc.isNullObject || !(cc.tag || '').startsWith(CITE_PREFIX)) return null;
        return cc.tag.slice(CITE_PREFIX.length);
      });
    }

    /** @param {string} id */
    async selectCitation(id) {
      return Word.run(async ctx => {
        const found = ctx.document.contentControls.getByTag(CITE_PREFIX + id);
        found.load('items');
        await ctx.sync();
        if (!found.items.length) return false;
        found.items[0].select('Select');
        await ctx.sync();
        return true;
      });
    }

    // ---------------------------------------------------------------- bibliography

    async hasBibliography() {
      return Word.run(async ctx => {
        const found = ctx.document.body.contentControls.getByTag(BIBL_TAG);
        found.load('items');
        await ctx.sync();
        return found.items.length > 0;
      });
    }

    /**
     * Update the RH-BIBL control, or create it at the cursor paragraph when asked.
     * @param {string} html
     * @param {{create?: boolean}} opts
     * @returns {Promise<'updated'|'created'|'absent'>}
     */
    async updateBibliography(html, opts = {}) {
      return Word.run(async ctx => {
        const found = ctx.document.body.contentControls.getByTag(BIBL_TAG);
        found.load('items');
        await ctx.sync();
        if (found.items.length) {
          found.items[0].insertHtml(html, 'Replace');
          await ctx.sync();
          return 'updated';
        }
        if (!opts.create) return 'absent';
        const para = ctx.document.getSelection().paragraphs.getFirst();
        para.load('text');
        await ctx.sync();
        const target = para.text.trim() === '' ? para : para.insertParagraph('', 'After');
        const cc = target.insertContentControl();
        this._setup(cc, BIBL_TAG, 'ReviewHunter bibliography', 'BoundingBox');
        cc.insertHtml(html, 'Replace');
        await ctx.sync();
        return 'created';
      });
    }

    async selectBibliography() {
      return Word.run(async ctx => {
        const found = ctx.document.body.contentControls.getByTag(BIBL_TAG);
        found.load('items');
        await ctx.sync();
        if (!found.items.length) return false;
        found.items[0].select('Start');
        await ctx.sync();
        return true;
      });
    }

    // ---------------------------------------------------------------- conversions

    /**
     * Move in-text citations into new footnotes (toNote) or footnote citations back
     * into the text at the reference mark. Needs WordApi 1.5.
     * @param {boolean} toNote
     * @returns {Promise<number>} moved citations
     */
    async convertCitations(toNote) {
      if (!this.supportsFootnotes) return 0;
      return Word.run(async ctx => {
        const all = await this._collect(ctx);
        let moved = 0;
        if (toNote) {
          for (const e of all.filter(x => !x.inNote)) {
            const tag = e.cc.tag;
            const appearance = e.cc.appearance;
            const fn = e.cc.getRange('After').insertFootnote('');
            const r = fn.body.insertText(e.cc.text || PLACEHOLDER, 'End');
            const cc = r.insertContentControl();
            this._setup(cc, tag, 'ReviewHunter citation', appearance);
            e.cc.delete(false);
            moved++;
          }
        } else {
          const notes = all.filter(x => x.inNote);
          const texts = notes.map(n => {
            const r = n.fn.body.getRange('Whole');
            r.load('text');
            return r;
          });
          await ctx.sync();
          notes.forEach((n, i) => {
            const spaced = n.fn.reference.insertText(' ', 'Before');
            const r = spaced.insertText(n.cc.text || PLACEHOLDER, 'After');
            const cc = r.insertContentControl();
            this._setup(cc, n.cc.tag, 'ReviewHunter citation', n.cc.appearance);
            const rest = texts[i].text.replace(n.cc.text, '').replace(/[.\s]/g, '');
            if (!rest) n.fn.delete();
            else n.cc.delete(false);
            moved++;
          });
        }
        await ctx.sync();
        return moved;
      });
    }

    /** @returns {Promise<number>} controls converted to plain text */
    async unlinkAll() {
      return Word.run(async ctx => {
        const all = await this._collect(ctx);
        const bib = ctx.document.body.contentControls.getByTag(BIBL_TAG);
        bib.load('items');
        await ctx.sync();
        all.forEach(e => e.cc.delete(true));      // keepContent = true
        bib.items.forEach(cc => cc.delete(true));
        await ctx.sync();
        return all.length + bib.items.length;
      });
    }

    /** @param {Function} fn selection change callback */
    onSelectionChanged(fn) {
      Office.context.document.addHandlerAsync(Office.EventType.DocumentSelectionChanged, () => fn());
    }
  }

  global.RHWordAdapter = WordAdapter;
})(typeof window !== 'undefined' ? window : globalThis);
