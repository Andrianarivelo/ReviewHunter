/*
 * Modal dialogs: Add by Identifier, New Item, Import, Export, Create Bibliography,
 * Advanced Search / Saved Search editor, Add to Collection, Bulk tags, Tag colour.
 */
window.dialogsPart = function dialogsPart() {
  const TAG_COLORS = ['#ff6666', '#ff8c19', '#999999', '#5fb236', '#009980', '#2ea8e5', '#576dd9', '#a28ae5', '#a6507b'];

  return {
    dialog: '',                       // name of the open dialog ('' = none)
    tagColors: TAG_COLORS,

    identify: { text: '', collectionId: '', tags: '', autoDownload: true, busy: false },
    newItemMenu: false,
    importState: { file: null, text: '', format: '', collectionId: '', newCollection: true, busy: false, result: null },
    exportState: { format: 'bibtex', scope: {}, label: '' },
    bib: { scope: {}, label: '', style: '', locale: 'en-US', mode: 'bibliography', html: '', text: '', loading: false, error: '', styleQuery: '' },
    advanced: { id: null, name: '', match: 'all', conditions: [{ field: 'title', op: 'contains', value: '' }] },
    folderChooser: { query: '', newName: '' },
    bulkTags: { add: '', remove: '' },
    tagColorTarget: null,
    confirmState: { title: '', body: '', action: null },

    openDialog(name) { this.dialog = name; this.closeContextMenu(); },
    closeDialog() { this.dialog = ''; },

    // ------------------------------------------------------------ add by identifier

    openIdentify() {
      this.identify = { text: '', collectionId: this.virtual === 'collection' ? String(this.selectedCollectionId) : '',
        tags: '', autoDownload: true, busy: false };
      this.openDialog('identify');
      this.$nextTick(() => document.getElementById('identify-input')?.focus());
    },
    identifyCount() {
      return this.identify.text.split(/\n+/).map(s => s.trim()).filter(Boolean).length;
    },
    async submitIdentify() {
      if (!this.identify.text.trim()) return;
      this.identify.busy = true;
      try {
        const r = await api.post('/api/items/identify', {
          text: this.identify.text,
          collection_id: this.identify.collectionId ? Number(this.identify.collectionId) : null,
          tags: this.identify.tags.split(/[;,]/).map(s => s.trim()).filter(Boolean),
          auto_download: this.identify.autoDownload,
        });
        this.toast(`Resolving ${r.queued} identifier(s)...`, 'info', 4000);
        this.closeDialog();
      } catch (e) {
        this.toast(e.message, 'error');
      } finally {
        this.identify.busy = false;
      }
    },

    // ------------------------------------------------------------ new item

    async createItem(type) {
      this.newItemMenu = false;
      const body = { item_type: type, title: 'Untitled', field_values: {} };
      if (this.virtual === 'collection') body.collections = [this.selectedCollectionId];
      const item = await api.post('/api/items', body);
      await this.loadItems();
      this.selectedIds = [item.id];
      await this.openItem(item.id);
      this.$nextTick(() => {
        const t = document.getElementById('item-title-input');
        if (t) { t.focus(); t.select(); }
      });
    },

    // ------------------------------------------------------------ import

    openImport() {
      this.importState = { file: null, text: '', format: '', collectionId: '', newCollection: true, busy: false, result: null };
      this.openDialog('import');
    },
    onImportFile(file) {
      if (!file) return;
      this.importState.file = file;
      const reader = new FileReader();
      reader.onload = () => { this.importState.text = String(reader.result || '').slice(0, 20000); };
      reader.readAsText(file);
    },
    importPreviewLabel() {
      const t = (this.importState.text || '').trimStart();
      const name = (this.importState.file?.name || '').toLowerCase();
      if (/\.pdf$/.test(name)) return 'PDF';
      if (/\.(bib|bibtex)$/.test(name) || /@\w+\s*\{/.test(t)) return 'BibTeX / BibLaTeX';
      if (/\.ris$/.test(name) || /^TY {2}-/m.test(t)) return 'RIS';
      if (/\.json$/.test(name) || t.startsWith('[') || t.startsWith('{')) return 'CSL-JSON';
      return t ? 'Identifiers or titles (one per line)' : '';
    },
    async submitImport() {
      const s = this.importState;
      s.busy = true;
      try {
        const cid = s.collectionId ? Number(s.collectionId) : null;
        if (s.file && /\.pdf$/i.test(s.file.name)) {
          const r = await api.upload('/api/import/pdfs', [['files', s.file]], { collection_id: cid });
          s.result = { created: r.imported, existing: 0, total: r.imported, format: 'PDF' };
        } else if (s.file) {
          s.result = await api.upload('/api/import/file', [['file', s.file]], { collection_id: cid, new_collection: s.newCollection && !cid });
        } else if (s.text.trim()) {
          s.result = await api.post('/api/import/text', { text: s.text, collection_id: cid,
            create_collection: s.newCollection && !cid ? 'Clipboard import' : null });
        }
        const r = s.result || {};
        this.toast(r.format === 'identifiers' ? `Queued ${r.queued} identifier(s)` : `Imported ${r.created || 0} new item(s), ${r.existing || 0} already in library`, 'ok', 5000);
        await this.reloadAll();
        if (r.collection_id) this.expanded = { ...this.expanded };
      } catch (e) {
        this.toast(e.message, 'error', 7000);
      } finally {
        s.busy = false;
      }
    },
    async importFromClipboard() {
      try {
        const text = await navigator.clipboard.readText();
        if (!text.trim()) return this.toast('Clipboard is empty', 'error');
        this.openImport();
        this.importState.text = text;
      } catch (e) {
        this.toast('Clipboard access was denied by the browser', 'error');
      }
    },

    // ------------------------------------------------------------ export

    openExport(scope = {}) {
      const label = scope.label || (scope.ids ? `${scope.ids.length} item(s)` : 'My Library');
      this.exportState = { format: localStorage.getItem('rh.exportFormat') || 'bibtex', scope, label };
      this.openDialog('export');
    },
    exportUrl(download = true) {
      const s = this.exportState.scope || {};
      return '/api/export' + api.qs({ format: this.exportState.format, ids: s.ids ? s.ids.join(',') : null,
        collection_id: s.collection_id, saved_search_id: s.saved_search_id, tag: s.tag, download: download ? null : 'false' });
    },
    doExport() {
      try { localStorage.setItem('rh.exportFormat', this.exportState.format); } catch (e) { /* ignore */ }
      const a = document.createElement('a');
      a.href = this.exportUrl(true);
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
      this.closeDialog();
    },
    async copyExport() {
      const text = await api.get(this.exportUrl(false).replace('/api/export', '/api/export'));
      await navigator.clipboard.writeText(typeof text === 'string' ? text : JSON.stringify(text, null, 2));
      this.toast('Export copied to clipboard', 'ok');
    },

    // ------------------------------------------------------------ bibliography

    async openBibliography(scope = {}) {
      this.bib = { scope, label: scope.label || (scope.ids ? `${scope.ids.length} item(s)` : 'My Library'),
        style: this.settings.citation_style || 'apa', locale: this.settings.citation_locale || 'en-US',
        mode: 'bibliography', html: '', text: '', loading: false, error: '', styleQuery: '' };
      this.openDialog('bibliography');
      if (!this.styles.length) await this.loadStyles();
      await this.renderBibliography();
    },
    async bibIds() {
      const s = this.bib.scope;
      if (s.ids) return s.ids;
      const rows = await api.get('/api/items', { collection_id: s.collection_id, saved_search_id: s.saved_search_id, limit: 5000 });
      return rows.map(r => r.id);
    },
    async renderBibliography() {
      this.bib.loading = true;
      this.bib.error = '';
      try {
        const ids = await this.bibIds();
        const cite = new RHCitations();
        await cite.useStyle(this.bib.style, this.bib.locale);
        if (this.bib.mode === 'citations') {
          const parts = [];
          for (const id of ids) parts.push(`<p>${await cite.citation([{ id }], 'html')}</p>`);
          this.bib.html = parts.join('');
        } else {
          this.bib.html = (await cite.bibliography(ids, 'html')).text || '<p><i>This style has no bibliography.</i></p>';
        }
      } catch (e) {
        this.bib.error = e.message;
        this.bib.html = '';
      } finally {
        this.bib.loading = false;
      }
    },
    filteredStyles() {
      const q = this.bib.styleQuery.trim().toLowerCase();
      return this.styles.filter(s => !q || s.title.toLowerCase().includes(q) || s.id.includes(q));
    },
    async copyBibliography(asText = false) {
      if (asText) await navigator.clipboard.writeText(RHCitations.htmlToText(this.bib.html));
      else await RHCitations.copyRich(this.bib.html);
      this.toast('Copied to clipboard', 'ok');
    },
    downloadBibliographyHtml() {
      const doc = `<!doctype html><meta charset="utf-8"><title>Bibliography</title><body style="font:12pt/1.5 'Times New Roman',serif;max-width:48rem;margin:2rem auto">${this.bib.html}</body>`;
      const url = URL.createObjectURL(new Blob([doc], { type: 'text/html' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `bibliography-${this.bib.style}.html`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    },

    // ------------------------------------------------------------ advanced search

    searchFields: [
      { id: 'title', label: 'Title' }, { id: 'creator', label: 'Creator' }, { id: 'year', label: 'Year' },
      { id: 'publicationTitle', label: 'Publication' }, { id: 'publisher', label: 'Publisher' },
      { id: 'abstract', label: 'Abstract' }, { id: 'tag', label: 'Tag' }, { id: 'collection', label: 'Collection' },
      { id: 'note', label: 'Note' }, { id: 'fulltext', label: 'Attachment content' }, { id: 'itemType', label: 'Item type' },
      { id: 'doi', label: 'DOI' }, { id: 'citationKey', label: 'Citation key' }, { id: 'extra', label: 'Extra' },
      { id: 'language', label: 'Language' }, { id: 'dateAdded', label: 'Date added' },
      { id: 'hasAttachment', label: 'Has file attachment' }, { id: 'starred', label: 'Starred' },
    ],
    opsFor(field) {
      if (field === 'year') return [['is', 'is'], ['isLessThan', 'is before'], ['isGreaterThan', 'is after'], ['contains', 'contains']];
      if (field === 'dateAdded') return [['isAfter', 'is after'], ['isBefore', 'is before'], ['is', 'is']];
      if (field === 'hasAttachment' || field === 'starred') return [['is', 'is']];
      if (field === 'collection' || field === 'itemType') return [['is', 'is'], ['isNot', 'is not']];
      if (field === 'fulltext' || field === 'note') return [['contains', 'contains'], ['doesNotContain', 'does not contain']];
      return [['contains', 'contains'], ['doesNotContain', 'does not contain'], ['is', 'is'], ['isNot', 'is not'], ['beginsWith', 'begins with']];
    },
    openAdvanced(saved = null) {
      this.advanced = saved
        ? { id: saved.id, name: saved.name, match: saved.match, conditions: JSON.parse(JSON.stringify(saved.conditions || [])) }
        : { id: null, name: '', match: 'all', conditions: [{ field: 'title', op: 'contains', value: '' }] };
      if (!this.advanced.conditions.length) this.advanced.conditions.push({ field: 'title', op: 'contains', value: '' });
      this.openDialog('advanced');
    },
    addCondition(i) { this.advanced.conditions.splice(i + 1, 0, { field: 'title', op: 'contains', value: '' }); },
    removeCondition(i) {
      this.advanced.conditions.splice(i, 1);
      if (!this.advanced.conditions.length) this.addCondition(-1);
    },
    runAdvanced() {
      this.closeDialog();
      this.selectVirtual('all');
      this.virtual = 'advanced';
      this.loadItems();
    },
    async saveAdvanced() {
      const name = this.advanced.name || prompt('Name of the saved search:', 'Untitled search');
      if (!name) return;
      const body = { name, match: this.advanced.match, conditions: this.advanced.conditions };
      const saved = this.advanced.id
        ? await api.put(`/api/saved-searches/${this.advanced.id}`, body)
        : await api.post('/api/saved-searches', body);
      await this.loadSavedSearches();
      this.closeDialog();
      this.selectSavedSearch(saved.id);
    },

    // ------------------------------------------------------------ collection chooser, bulk tags, tag colour

    openFolderChooser() {
      this.folderChooser = { query: '', newName: '' };
      this.openDialog('folder');
    },
    chooserRows() {
      const q = this.folderChooser.query.trim().toLowerCase();
      return this.collections
        .map(c => ({ ...c, path: this.collectionPath(c.id) }))
        .filter(c => !q || c.path.toLowerCase().includes(q))
        .sort((a, b) => a.path.localeCompare(b.path));
    },
    async chooseFolder(cid) {
      this.closeDialog();
      await this.assignSelectedToCollection(cid);
    },
    async createFolderFromChooser() {
      const name = this.folderChooser.newName.trim();
      if (!name) return;
      const c = await api.post('/api/collections', { name });
      await this.loadCollections();
      await this.chooseFolder(c.id);
    },
    openBulkTags() {
      this.bulkTags = { add: '', remove: '' };
      this.openDialog('bulkTags');
    },
    async submitBulkTags() {
      const split = s => s.split(/[;,]/).map(x => x.trim()).filter(Boolean);
      await api.post('/api/tags/bulk', { item_ids: this.selectedIds, add: split(this.bulkTags.add), remove: split(this.bulkTags.remove) });
      this.closeDialog();
      this.toast('Tags updated', 'ok');
      await this.reloadAll();
    },
    openTagColor(tag) {
      this.tagColorTarget = tag;
      this.openDialog('tagColor');
    },
  };
};
