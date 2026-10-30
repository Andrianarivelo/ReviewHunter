/*
 * Item pane: Info (type-aware field editor + creators), Tags, Notes (rich-text editor),
 * Attachments (files, links, reader), Related items, and a live formatted-citation preview.
 * Every edit is saved immediately with PATCH so there is no "Save" button to forget.
 */
window.detailPart = function detailPart() {
  return {
    itemTypes: [],
    itemPane: false,          // gates the item pane template; cleared BEFORE selectedItem becomes null
    typeById: {},
    creatorLabels: {},
    detailTab: 'info',
    citationPreview: '',
    tagInput: '',
    relatedQuery: '',
    relatedResults: [],
    note: { open: false, id: null, itemId: null, html: '', saving: false, savedAt: '' },
    attachPreview: true,

    async loadItemTypes() {
      const data = await api.get('/api/item-types');
      this.itemTypes = data.types;
      this.typeById = Object.fromEntries(data.types.map(t => [t.id, t]));
      this.creatorLabels = data.creator_labels;
    },

    /** Load one item into the right pane. */
    async openItem(id, { keepTab = false } = {}) {
      if (!id) return;
      try {
        const item = await api.get(`/api/items/${id}`);
        if (!item.creators || !item.creators.length) item.creators = [];
        this.selectedItem = item;
        this.itemPane = true;
        if (!keepTab) this.detailTab = 'info';
        if (this.note.open && this.note.itemId !== id) this.closeNoteEditor();
        this.updateCitationPreview();
      } catch (e) {
        this.clearItem();
      }
    },

    /**
     * Close the item pane safely: Alpine would re-evaluate the pane's bindings against a null item
     * before x-if removes the template, so hide the template first and drop the item on the next tick.
     */
    clearItem() {
      this.itemPane = false;
      this.$nextTick(() => { if (!this.itemPane) this.selectedItem = null; });
    },

    fieldsFor(item) {
      const t = this.typeById[item?.item_type];
      return t ? t.fields.filter(f => f.id !== 'title' && f.id !== 'abstractNote') : [];
    },
    /**
     * Fields shown in the Info tab: everything that has a value, plus the few that are worth filling in
     * (publication, date, DOI). The other empty fields (an article type has ~20) sit behind
     * "Show N empty fields"; the choice is remembered in this browser.
     */
    showEmptyFields: JSON.parse(localStorage.getItem('rh.showEmptyFields') || 'false'),
    essentialFields: ['publicationTitle', 'date', 'DOI'],
    hasFieldValue(item, fid) {
      const v = item?.field_values?.[fid];
      return v !== undefined && v !== null && String(v).trim() !== '';
    },
    visibleFields(item) {
      const all = this.fieldsFor(item);
      if (this.showEmptyFields) return all;
      return all.filter(f => this.hasFieldValue(item, f.id) || this.essentialFields.includes(f.id));
    },
    hiddenFieldCount(item) {
      return this.fieldsFor(item).length - this.visibleFields(item).length;
    },
    toggleEmptyFields() {
      this.showEmptyFields = !this.showEmptyFields;
      localStorage.setItem('rh.showEmptyFields', JSON.stringify(this.showEmptyFields));
    },
    creatorTypesFor(item) {
      const t = this.typeById[item?.item_type];
      return t ? t.creators : [{ id: 'author', label: 'Author' }];
    },
    isLongField(fid) { return ['extra'].includes(fid); },

    /** Persist one field value. */
    async saveField(fid, value) {
      const it = this.selectedItem;
      if (!it) return;
      try {
        const updated = await api.patch(`/api/items/${it.id}`, { field_values: { [fid]: value } });
        this.selectedItem = { ...updated, creators: updated.creators || [] };
        this.patchListRow(updated);
        if (['date', 'title', 'publicationTitle', 'volume', 'pages', 'issue', 'publisher'].includes(fid)) this.updateCitationPreview();
      } catch (e) {
        this.toast(e.message, 'error', 6000);
        await this.openItem(it.id, { keepTab: true });
      }
    },
    async saveTitle(value) { return this.saveField('title', value); },
    async changeItemType(type) {
      const it = this.selectedItem;
      const updated = await api.patch(`/api/items/${it.id}`, { item_type: type });
      this.selectedItem = { ...updated, creators: updated.creators || [] };
      this.patchListRow(updated);
      this.updateCitationPreview();
    },
    /** Keep the table row in sync after an edit without reloading the whole list. */
    patchListRow(updated) {
      const idx = this.items.findIndex(i => i.id === updated.id);
      if (idx >= 0) this.items.splice(idx, 1, { ...this.items[idx], ...updated });
    },

    // ------------------------------------------------------------ creators

    addCreator(index = null) {
      const it = this.selectedItem;
      const c = { creatorType: this.creatorTypesFor(it)[0].id, lastName: '', firstName: '' };
      if (index === null) it.creators.push(c);
      else it.creators.splice(index + 1, 0, c);
      this.$nextTick(() => {
        const inputs = document.querySelectorAll('[data-creator-last]');
        const target = inputs[index === null ? inputs.length - 1 : index + 1];
        if (target) target.focus();
      });
    },
    removeCreator(i) {
      this.selectedItem.creators.splice(i, 1);
      this.saveCreators();
    },
    moveCreator(i, delta) {
      const cs = this.selectedItem.creators;
      const j = i + delta;
      if (j < 0 || j >= cs.length) return;
      [cs[i], cs[j]] = [cs[j], cs[i]];
      this.saveCreators();
    },
    /** Switch a creator between two-field (last, first) and single-field (organisation) mode. */
    toggleCreatorMode(c) {
      if (c.name !== undefined && c.name !== null) {
        const parts = String(c.name).trim().split(/\s+/);
        c.lastName = parts.pop() || '';
        c.firstName = parts.join(' ');
        delete c.name;
      } else {
        c.name = [c.firstName, c.lastName].filter(Boolean).join(' ');
        delete c.lastName;
        delete c.firstName;
      }
      this.saveCreators();
    },
    async saveCreators() {
      const it = this.selectedItem;
      const creators = it.creators.filter(c => (c.name || c.lastName || c.firstName || '').trim());
      const updated = await api.patch(`/api/items/${it.id}`, { creators });
      this.selectedItem = { ...updated, creators: updated.creators || [] };
      this.patchListRow(updated);
      this.updateCitationPreview();
    },

    // ------------------------------------------------------------ citation preview

    async updateCitationPreview() {
      const it = this.selectedItem;
      if (!it || !this.settings.citation_style) return;
      try {
        const cite = await this.citationEngine();
        cite.items[String(it.id)] = null;  // force refetch of the edited item
        delete cite.items[String(it.id)];
        const bib = await cite.bibliography([it.id], 'html');
        if (this.selectedItem && this.selectedItem.id === it.id) this.citationPreview = bib.entries.join('') || '';
      } catch (e) {
        this.citationPreview = '';
      }
    },

    // ------------------------------------------------------------ tags

    async addTagToItem() {
      const names = this.tagInput.split(/[;,]/).map(s => s.trim()).filter(Boolean);
      if (!names.length || !this.selectedItem) return;
      let updated = null;
      for (const name of names) updated = await api.post(`/api/items/${this.selectedItem.id}/tags`, { name });
      this.tagInput = '';
      this.selectedItem = { ...updated, creators: updated.creators || [] };
      this.patchListRow(updated);
      this.loadAllTags();
    },
    async removeTagFromItem(name) {
      const updated = await api.del(`/api/items/${this.selectedItem.id}/tags/${encodeURIComponent(name)}`);
      this.selectedItem = { ...updated, creators: updated.creators || [] };
      this.patchListRow(updated);
      this.loadAllTags();
    },
    tagSuggestions() {
      const q = this.tagInput.trim().toLowerCase();
      if (!q) return [];
      const have = new Set((this.selectedItem?.tags || []).map(t => t.name.toLowerCase()));
      return this.allTags.filter(t => t.name.toLowerCase().includes(q) && !have.has(t.name.toLowerCase())).slice(0, 8);
    },
    tagStyle(t) {
      return t.color ? `background:${t.color}22;border-color:${t.color}88;color:inherit` : '';
    },

    // ------------------------------------------------------------ notes

    async addChildNote(it = this.selectedItem) {
      if (!it) return;
      const note = await api.post('/api/notes', { item_id: it.id, html: '<p></p>' });
      if (!this.selectedItem || this.selectedItem.id !== it.id) await this.openItem(it.id);
      this.openNoteEditor(note);
    },
    openNoteEditor(note) {
      this.note = { open: true, id: note.id, itemId: note.item_id, html: note.html || '<p></p>', saving: false, savedAt: '' };
      this.$nextTick(() => {
        const el = document.getElementById('note-editor');
        if (el) {
          el.innerHTML = this.note.html;
          el.focus();
        }
      });
    },
    async closeNoteEditor() {
      if (this.note.open) await this.flushNote();
      this.note = { open: false, id: null, itemId: null, html: '', saving: false, savedAt: '' };
      if (this.selectedItem) await this.openItem(this.selectedItem.id, { keepTab: true });
      if (this.virtual === 'notes') this.loadStandaloneNotes();
    },
    /** Debounced autosave triggered by input events in the contenteditable editor. */
    onNoteInput() {
      clearTimeout(this._noteTimer);
      this._noteTimer = setTimeout(() => this.flushNote(), 700);
    },
    async flushNote() {
      clearTimeout(this._noteTimer);
      const el = document.getElementById('note-editor');
      if (!el || !this.note.id) return;
      const html = el.innerHTML;
      if (html === this.note.html) return;
      this.note.saving = true;
      try {
        const saved = await api.patch(`/api/notes/${this.note.id}`, { item_id: this.note.itemId, html });
        this.note.html = html;
        this.note.savedAt = new Date().toLocaleTimeString();
        const sn = this.standaloneNotes.find(n => n.id === saved.id);
        if (sn) Object.assign(sn, saved);
      } catch (e) {
        this.toast(`Note not saved: ${e.message}`, 'error');
      } finally {
        this.note.saving = false;
      }
    },
    /** Rich-text toolbar command (execCommand is still the most reliable option for contenteditable). */
    noteCmd(cmd, value = null) {
      const el = document.getElementById('note-editor');
      if (!el) return;
      el.focus();
      if (cmd === 'link') {
        const url = prompt('Link URL (https://...):');
        if (!url || !/^https?:\/\//i.test(url)) return;
        document.execCommand('createLink', false, url);
      } else if (cmd === 'formatBlock') {
        document.execCommand('formatBlock', false, value);
      } else if (cmd === 'hiliteColor') {
        document.execCommand('hiliteColor', false, value);
      } else {
        document.execCommand(cmd, false, value);
      }
      this.onNoteInput();
    },
    async deleteNote(n) {
      if (!confirm('Move this note to the Trash?')) return;
      await api.del(`/api/notes/${n.id}`);
      if (this.note.id === n.id) this.note = { open: false, id: null, itemId: null, html: '', saving: false, savedAt: '' };
      if (this.selectedItem) await this.openItem(this.selectedItem.id, { keepTab: true });
      if (this.virtual === 'notes') this.loadStandaloneNotes();
      this.loadStats();
    },

    // ------------------------------------------------------------ attachments

    pdfAttachment(it) {
      return (it?.attachments || []).find(a => a.mime === 'application/pdf' && a.exists)
        || (it?.attachments || []).find(a => a.kind !== 'link' && a.exists);
    },
    /** Open the best attachment: PDFs in the built-in reader, others with the system app. */
    async openBestAttachment(it) {
      let full = it;
      if (!full.attachments) full = await api.get(`/api/items/${it.id}`);
      const att = this.pdfAttachment(full);
      if (!att) return this.toast('No file attached. Try "Find Available PDF" or the browser connector.', 'error', 5000);
      this.openAttachment(att);
    },
    async showBestAttachment(it) {
      const full = it.attachments ? it : await api.get(`/api/items/${it.id}`);
      const att = this.pdfAttachment(full);
      if (att) await api.post(`/api/attachments/${att.id}/show`);
    },
    openAttachment(att) {
      if (att.kind === 'link') return window.open(att.url, '_blank', 'noopener');
      if (att.mime === 'application/pdf') return window.open(`/reader?attachment=${att.id}`, '_blank');
      if (att.mime === 'text/html') return window.open(`/api/attachments/${att.id}/file`, '_blank', 'noopener');
      return api.post(`/api/attachments/${att.id}/open`).catch(e => this.toast(e.message, 'error'));
    },
    pickAttachment(it = this.selectedItem) {
      this._attachTarget = it;
      const input = document.getElementById('attach-file-input');
      if (input) { input.value = ''; input.click(); }
    },
    async onAttachFiles(files, it = this._attachTarget || this.selectedItem) {
      if (!it || !files.length) return;
      for (const f of files) {
        await api.upload(`/api/items/${it.id}/attachments`, [['file', f]]);
      }
      this.toast(`Attached ${files.length} file(s)`, 'ok');
      await this.openItem(it.id, { keepTab: true });
      this.loadItems();
    },
    async addLink(it = this.selectedItem) {
      const url = prompt('Link URL (https://...):', it.url || '');
      if (!url) return;
      try {
        await api.post(`/api/items/${it.id}/links`, { url, title: null });
        await this.openItem(it.id, { keepTab: true });
      } catch (e) { this.toast(e.message, 'error'); }
    },
    async renameAttachment(att) {
      const title = prompt('Attachment title:', att.title || '');
      if (!title) return;
      await api.patch(`/api/attachments/${att.id}`, { title });
      await this.openItem(this.selectedItem.id, { keepTab: true });
    },
    async deleteAttachment(att) {
      if (!confirm(`Delete attachment "${att.title}"? The file is removed if it is stored in the ReviewHunter papers folder.`)) return;
      await api.del(`/api/attachments/${att.id}`);
      await this.openItem(this.selectedItem.id, { keepTab: true });
      this.loadItems();
    },
    formatSize(bytes) {
      if (!bytes) return '';
      const u = ['B', 'KB', 'MB', 'GB'];
      let i = 0, n = bytes;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
    },

    // ------------------------------------------------------------ related

    async searchRelated() {
      const q = this.relatedQuery.trim();
      if (!q) { this.relatedResults = []; return; }
      const rows = await api.get('/api/items', { q, mode: 'titleCreatorYear', limit: 10 });
      const exclude = new Set([this.selectedItem.id, ...(this.selectedItem.related || []).map(r => r.id)]);
      this.relatedResults = rows.filter(r => !exclude.has(r.id));
    },
    async addRelated(other) {
      await api.post(`/api/items/${this.selectedItem.id}/related/${other.id}`);
      this.relatedQuery = '';
      this.relatedResults = [];
      await this.openItem(this.selectedItem.id, { keepTab: true });
    },
    async removeRelated(other) {
      await api.del(`/api/items/${this.selectedItem.id}/related/${other.id}`);
      await this.openItem(this.selectedItem.id, { keepTab: true });
    },
    async regenerateKey() {
      const updated = await api.post(`/api/items/${this.selectedItem.id}/citation-key`);
      this.selectedItem = { ...updated, creators: updated.creators || [] };
    },
    copyText(text, label = 'Copied') {
      navigator.clipboard.writeText(text).then(() => this.toast(label, 'ok'));
    },
  };
};
