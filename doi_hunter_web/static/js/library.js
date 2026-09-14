/*
 * Library part: navigation (virtual views), nested collections tree, saved searches,
 * tag selector, the items table with multi-selection, keyboard handling, drag and drop,
 * context menus, trash, duplicates, standalone notes and Quick Copy.
 */
window.libraryPart = function libraryPart() {
  const LS = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ } },
  };

  return {
    // ------------------------------------------------------------------ state
    virtual: 'all',                 // discover | graph | all | unfiled | starred | recent | failed | duplicates | notes | trash | collection | search | author | subject
    selectedCollectionId: null,
    selectedSearchId: null,
    selectedAuthor: null,
    selectedSubject: null,
    collections: [],
    expanded: LS.get('rh.expanded', {}),
    savedSearches: [],
    facets: { authors: [], subjects: [] },
    showAuthors: false,
    showSubjects: false,
    authorLimit: 12,
    subjectLimit: 12,

    items: [],
    loading: false,
    selectedIds: [],
    anchorId: null,
    selectedItem: null,
    searchQuery: '',
    searchMode: LS.get('rh.searchMode', 'titleCreatorYear'),
    sortBy: LS.get('rh.sortBy', 'added'),
    sortDir: LS.get('rh.sortDir', 'desc'),
    includeSub: LS.get('rh.includeSub', true),
    columns: LS.get('rh.columns', { creator: true, year: true, publication: true, added: true, type: false, key: false }),

    tagFilter: [],
    tagQuery: '',
    allTags: [],
    tagPaneOpen: LS.get('rh.tagPaneOpen', true),
    showAutomaticTags: LS.get('rh.showAutoTags', true),

    duplicateGroups: [],
    duplicatesLoading: false,
    standaloneNotes: [],
    trashedNotes: [],
    stats: { items: 0, downloaded: 0, starred: 0, unfiled: 0, trash: 0, failed: 0, notes: 0 },

    contextMenu: { open: false, x: 0, y: 0, type: '', target: null },
    dragOver: null,
    dropping: false,

    // ------------------------------------------------------------------ loading

    async loadStats() {
      try { this.stats = await api.get('/api/stats'); } catch (e) { /* offline */ }
    },
    async loadCollections() {
      this.collections = await api.get('/api/collections');
    },
    async loadFacets() {
      try { this.facets = await api.get('/api/facets'); } catch (e) { /* optional */ }
    },
    async loadSavedSearches() {
      this.savedSearches = await api.get('/api/saved-searches');
    },
    async loadAllTags() {
      this.allTags = await api.get('/api/tags');
    },
    async refreshSidebar() {
      await Promise.all([this.loadStats(), this.loadCollections(), this.loadSavedSearches(), this.loadAllTags(),
        this.loadFacets()]);
    },

    /** Query parameters for the current view (server does all filtering). */
    viewParams() {
      const p = { sort: this.sortBy, direction: this.sortDir, tag: this.tagFilter };
      if (this.searchQuery.trim()) { p.q = this.searchQuery.trim(); p.mode = this.searchMode; }
      switch (this.virtual) {
        case 'collection': p.collection_id = this.selectedCollectionId; p.include_sub = this.includeSub; break;
        case 'unfiled': p.unfiled = true; break;
        case 'starred': p.starred = true; break;
        case 'failed': p.status = 'failed'; break;
        case 'trash': p.trash = true; p.sort = 'deleted'; break;
        case 'search': p.saved_search_id = this.selectedSearchId; break;
        case 'author': p.author = this.selectedAuthor; break;
        case 'subject': p.subject = this.selectedSubject; break;
        case 'recent': p.sort = 'added'; p.direction = 'desc'; p.limit = 50; break;
        case 'advanced': p.conditions = JSON.stringify(this.advanced.conditions); p.match = this.advanced.match; break;
        default: break;
      }
      if (p.include_sub === true) delete p.include_sub;
      else if (p.include_sub === false) p.include_sub = 'false';
      return p;
    },

    isItemView() {
      return !['discover', 'graph', 'duplicates', 'notes'].includes(this.virtual);
    },

    async loadItems() {
      if (this.virtual === 'duplicates') return this.loadDuplicates();
      if (this.virtual === 'notes') return this.loadStandaloneNotes();
      if (!this.isItemView()) return;
      this.loading = true;
      const token = (this._loadToken = (this._loadToken || 0) + 1);
      try {
        const items = await api.get('/api/items', this.viewParams());
        if (token !== this._loadToken) return;  // a newer request superseded this one
        this.items = items;
        const alive = new Set(items.map(i => i.id));
        this.selectedIds = this.selectedIds.filter(id => alive.has(id));
        if (this.virtual === 'trash') this.trashedNotes = await api.get('/api/trash/notes');
      } catch (e) {
        this.toast(e.message, 'error');
      } finally {
        if (token === this._loadToken) this.loading = false;
      }
    },

    async reloadAll() {
      await Promise.all([this.loadItems(), this.refreshSidebar()]);
      if (this.selectedItem) await this.openItem(this.selectedItem.id, { keepTab: true });
    },

    // ------------------------------------------------------------------ navigation

    selectVirtual(v) {
      this.virtual = v;
      this.selectedCollectionId = null;
      this.selectedSearchId = null;
      this.selectedAuthor = null;
      this.selectedSubject = null;
      this.tagFilter = [];
      this.selectedIds = [];
      this.closeContextMenu();
      if (v === 'discover') {
        this.$nextTick(() => this.$refs.discoverInput && this.$refs.discoverInput.focus());
        return;
      }
      if (v === 'graph') return this.openGraph();
      this.loadItems();
    },
    selectCollection(id) {
      this.selectVirtual('collection');
      this.virtual = 'collection';
      this.selectedCollectionId = id;
      this.loadItems();
    },
    selectSavedSearch(id) {
      this.selectVirtual('search');
      this.virtual = 'search';
      this.selectedSearchId = id;
      this.loadItems();
    },
    selectAuthor(name) {
      this.selectVirtual('author');
      this.virtual = 'author';
      this.selectedAuthor = name;
      this.loadItems();
    },
    selectSubject(name) {
      this.selectVirtual('subject');
      this.virtual = 'subject';
      this.selectedSubject = name;
      this.loadItems();
    },

    paneTitle() {
      switch (this.virtual) {
        case 'collection': return this.collectionPath(this.selectedCollectionId) || 'Collection';
        case 'search': return (this.savedSearches.find(s => s.id === this.selectedSearchId) || {}).name || 'Saved search';
        case 'author': return `Author: ${this.shortAuthor(this.selectedAuthor || '')}`;
        case 'subject': return `Subject: ${this.selectedSubject}`;
        case 'advanced': return 'Advanced search results';
        default: return ({ all: 'My Library', unfiled: 'Unfiled Items', starred: 'Starred', recent: 'Recently Added',
          failed: 'Needs Attention', trash: 'Trash', duplicates: 'Duplicate Items', notes: 'Notes' })[this.virtual] || '';
      }
    },

    // ------------------------------------------------------------------ collections tree

    /** Flattened, ordered tree rows honouring expanded/collapsed state. */
    get collectionRows() {
      const byParent = new Map();
      for (const c of this.collections) {
        const key = c.parent_id || 0;
        if (!byParent.has(key)) byParent.set(key, []);
        byParent.get(key).push(c);
      }
      for (const list of byParent.values()) list.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
      const rows = [];
      const walk = (parent, depth) => {
        for (const c of byParent.get(parent) || []) {
          const hasChildren = byParent.has(c.id);
          rows.push({ ...c, depth, hasChildren });
          if (hasChildren && this.expanded[c.id]) walk(c.id, depth + 1);
        }
      };
      walk(0, 0);
      return rows;
    },
    toggleExpand(id) {
      this.expanded = { ...this.expanded, [id]: !this.expanded[id] };
      LS.set('rh.expanded', this.expanded);
    },
    collectionName(id) {
      const c = this.collections.find(x => x.id == id);
      return c ? c.name : '';
    },
    collectionPath(id) {
      const parts = [];
      let c = this.collections.find(x => x.id == id);
      let guard = 0;
      while (c && guard++ < 50) {
        parts.unshift(c.name);
        c = this.collections.find(x => x.id === c.parent_id);
      }
      return parts.join(' / ');
    },
    async newCollection(parentId = null) {
      const name = prompt(parentId ? `New subcollection in "${this.collectionName(parentId)}":` : 'New collection name:');
      if (!name || !name.trim()) return;
      const c = await api.post('/api/collections', { name: name.trim(), parent_id: parentId });
      if (parentId) this.expanded = { ...this.expanded, [parentId]: true };
      await this.loadCollections();
      this.toast(`Created "${c.name}"`, 'ok');
    },
    async renameCollection(c) {
      const name = prompt('Rename collection:', c.name);
      if (!name || name === c.name) return;
      await api.patch(`/api/collections/${c.id}`, { name });
      await this.loadCollections();
    },
    async setCollectionColor(c, color) {
      await api.patch(`/api/collections/${c.id}/v2`, { color });
      await this.loadCollections();
    },
    async deleteCollection(c) {
      if (!confirm(`Delete collection "${c.name}" and its subcollections?\nItems stay in My Library.`)) return;
      await api.del(`/api/collections/${c.id}`);
      if (this.selectedCollectionId === c.id) this.selectVirtual('all');
      await this.refreshSidebar();
    },
    async moveCollection(id, parentId) {
      try {
        await api.patch(`/api/collections/${id}/v2`, parentId ? { parent_id: parentId } : { move_to_root: true });
        if (parentId) this.expanded = { ...this.expanded, [parentId]: true };
        await this.loadCollections();
      } catch (e) { this.toast(e.message, 'error'); }
    },
    async removeSelectedFromCollection() {
      if (this.virtual !== 'collection' || !this.selectedIds.length) return;
      for (const id of this.selectedIds) await api.del(`/api/items/${id}/collections/${this.selectedCollectionId}`);
      this.toast(`Removed ${this.selectedIds.length} from collection`, 'ok');
      await this.reloadAll();
    },

    // ------------------------------------------------------------------ tag selector

    /** Tags shown in the selector: tags on items in the current view, colored tags first. */
    get viewTags() {
      const counts = new Map();
      for (const it of this.items) for (const t of it.tags || []) {
        const cur = counts.get(t.name) || { ...t, count: 0 };
        cur.count += 1;
        counts.set(t.name, cur);
      }
      for (const t of this.allTags) if (t.color && !counts.has(t.name)) counts.set(t.name, { ...t, count: 0 });
      const q = this.tagQuery.trim().toLowerCase();
      return [...counts.values()]
        .filter(t => (this.showAutomaticTags || t.type !== 1) && (!q || t.name.toLowerCase().includes(q)))
        .sort((a, b) => (b.color ? 1 : 0) - (a.color ? 1 : 0) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
    },
    toggleTagFilter(name) {
      this.tagFilter = this.tagFilter.includes(name) ? this.tagFilter.filter(t => t !== name) : [...this.tagFilter, name];
      this.loadItems();
    },
    clearTagFilter() { this.tagFilter = []; this.loadItems(); },
    async renameTag(tag) {
      const name = prompt('Rename tag (renaming onto an existing tag merges them):', tag.name);
      if (!name || name === tag.name) return;
      const full = this.allTags.find(t => t.name === tag.name) || tag;
      await api.patch(`/api/tags/${full.id}`, { name });
      this.tagFilter = this.tagFilter.map(t => (t === tag.name ? name : t));
      await this.reloadAll();
    },
    async setTagColor(tag, color) {
      const full = this.allTags.find(t => t.name === tag.name) || tag;
      await api.patch(`/api/tags/${full.id}`, color ? { color } : { clear_color: true });
      await this.reloadAll();
    },
    async deleteTag(tag) {
      if (!confirm(`Delete tag "${tag.name}" from all items?`)) return;
      const full = this.allTags.find(t => t.name === tag.name) || tag;
      await api.del(`/api/tags/${full.id}`);
      this.tagFilter = this.tagFilter.filter(t => t !== tag.name);
      await this.reloadAll();
    },
    async deleteAutomaticTags() {
      if (!confirm('Delete all automatic tags (imported keywords) from the library?')) return;
      const r = await api.post('/api/tags/delete-automatic');
      this.toast(`Deleted ${r.deleted} automatic tags`, 'ok');
      await this.reloadAll();
    },
    /** Drop items on a tag: assign the tag to all dragged items. */
    async dropOnTag(tag, e) {
      const ids = this.draggedIds(e);
      if (!ids.length) return;
      await api.post('/api/tags/bulk', { item_ids: ids, add: [tag.name] });
      this.toast(`Tagged ${ids.length} item${ids.length === 1 ? '' : 's'} "${tag.name}"`, 'ok');
      await this.reloadAll();
    },

    // ------------------------------------------------------------------ saved searches

    async deleteSavedSearch(s) {
      if (!confirm(`Delete saved search "${s.name}"?`)) return;
      await api.del(`/api/saved-searches/${s.id}`);
      if (this.selectedSearchId === s.id) this.selectVirtual('all');
      await this.loadSavedSearches();
    },

    // ------------------------------------------------------------------ table & selection

    setSort(key) {
      if (this.sortBy === key) this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      else { this.sortBy = key; this.sortDir = ['title', 'creator', 'publication', 'type'].includes(key) ? 'asc' : 'desc'; }
      LS.set('rh.sortBy', this.sortBy);
      LS.set('rh.sortDir', this.sortDir);
      this.loadItems();
    },
    sortIndicator(key) { return this.sortBy === key ? (this.sortDir === 'asc' ? '▲' : '▼') : ''; },
    toggleColumn(key) {
      this.columns = { ...this.columns, [key]: !this.columns[key] };
      LS.set('rh.columns', this.columns);
    },
    gridTemplate() {
      const cols = ['28px', 'minmax(220px, 3fr)'];
      if (this.columns.creator) cols.push('minmax(110px, 1.2fr)');
      if (this.columns.year) cols.push('58px');
      if (this.columns.publication) cols.push('minmax(110px, 1.2fr)');
      if (this.columns.type) cols.push('120px');
      if (this.columns.key) cols.push('130px');
      if (this.columns.added) cols.push('92px');
      cols.push('58px');
      return `grid-template-columns: ${cols.join(' ')};`;
    },
    isSelected(id) { return this.selectedIds.includes(id); },

    /** Click with Ctrl (toggle) / Shift (range) / plain (single) selection semantics. */
    async clickItem(it, e) {
      const ids = this.items.map(i => i.id);
      if (e && e.shiftKey && this.anchorId != null) {
        const a = ids.indexOf(this.anchorId), b = ids.indexOf(it.id);
        const [lo, hi] = a < b ? [a, b] : [b, a];
        this.selectedIds = ids.slice(lo, hi + 1);
      } else if (e && (e.ctrlKey || e.metaKey)) {
        this.selectedIds = this.isSelected(it.id) ? this.selectedIds.filter(x => x !== it.id) : [...this.selectedIds, it.id];
        this.anchorId = it.id;
      } else {
        this.selectedIds = [it.id];
        this.anchorId = it.id;
      }
      if (this.selectedIds.length === 1) await this.openItem(this.selectedIds[0], { keepTab: true });
      else if (this.selectedIds.length === 0) this.clearItem();
    },
    selectAllItems() {
      this.selectedIds = this.items.map(i => i.id);
    },
    async moveSelection(delta, extend) {
      if (!this.items.length) return;
      const ids = this.items.map(i => i.id);
      const current = this.selectedIds.length ? ids.indexOf(this.selectedIds[this.selectedIds.length - 1]) : -1;
      const next = Math.max(0, Math.min(ids.length - 1, current + delta));
      if (extend) {
        if (!this.selectedIds.includes(ids[next])) this.selectedIds = [...this.selectedIds, ids[next]];
      } else {
        this.selectedIds = [ids[next]];
        this.anchorId = ids[next];
        await this.openItem(ids[next], { keepTab: true });
      }
      this.$nextTick(() => {
        const row = document.querySelector(`[data-item-row="${ids[next]}"]`);
        if (row) row.scrollIntoView({ block: 'nearest' });
      });
    },

    /** Keyboard shortcuts active while the item list has focus. */
    onListKey(e) {
      if (e.target.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); this.moveSelection(1, e.shiftKey); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); this.moveSelection(-1, e.shiftKey); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); this.selectAllItems(); }
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        if (this.virtual === 'trash') this.deleteSelectedPermanently();
        else if (this.virtual === 'collection' && !e.shiftKey) this.removeSelectedFromCollection();
        else this.trashSelected();
      } else if (e.key === 'Enter' && this.selectedItem) {
        e.preventDefault();
        this.openBestAttachment(this.selectedItem);
      }
    },

    // ------------------------------------------------------------------ item actions

    selectedItemsList() {
      const set = new Set(this.selectedIds);
      return this.items.filter(i => set.has(i.id));
    },
    async toggleStar(it) {
      const next = !it.starred;
      it.starred = next ? 1 : 0;
      if (this.selectedItem && this.selectedItem.id === it.id) this.selectedItem.starred = it.starred;
      await api.patch(`/api/items/${it.id}`, { starred: next });
      this.loadStats();
    },
    async trashSelected() {
      const ids = [...this.selectedIds];
      if (!ids.length) return;
      await api.post('/api/items/trash', { item_ids: ids });
      this.toast(`Moved ${ids.length} item${ids.length === 1 ? '' : 's'} to the Trash`, 'ok');
      this.selectedIds = [];
      this.clearItem();
      await this.reloadAll();
    },
    async restoreSelected() {
      const ids = [...this.selectedIds];
      await api.post('/api/items/restore', { item_ids: ids });
      this.toast(`Restored ${ids.length} item${ids.length === 1 ? '' : 's'}`, 'ok');
      this.selectedIds = [];
      await this.reloadAll();
    },
    async deleteSelectedPermanently() {
      const ids = [...this.selectedIds];
      if (!ids.length || !confirm(`Permanently delete ${ids.length} item(s)? Their files in the ReviewHunter papers folder are deleted too. This cannot be undone.`)) return;
      await api.post('/api/items/delete-permanently', { item_ids: ids });
      this.selectedIds = [];
      this.clearItem();
      await this.reloadAll();
    },
    async emptyTrash() {
      if (!confirm('Empty the Trash? All items and notes in it are permanently deleted.')) return;
      const r = await api.post('/api/trash/empty');
      this.toast(`Deleted ${r.deleted} item(s) permanently`, 'ok');
      await this.reloadAll();
    },
    async restoreNote(n) { await api.post('/api/items/restore', { note_ids: [n.id] }); await this.reloadAll(); },
    async deleteNoteForever(n) {
      if (!confirm('Delete this note permanently?')) return;
      await api.del(`/api/notes/${n.id}`, { permanent: true });
      await this.reloadAll();
    },
    async downloadItem(it) {
      await api.post(`/api/items/${it.id}/download`);
      it.status = 'downloading';
      this.toast('Looking for an available PDF...', 'info');
    },
    async retrieveMetadata(it) {
      this.toast('Retrieving metadata from PDF...', 'info');
      try {
        const r = await api.post(`/api/items/${it.id}/retrieve-metadata`);
        if (r.ok) this.toast(`Metadata found (${r.source})`, 'ok', 5000);
        else if (r.reason === 'duplicate') this.toast(`This PDF matches item #${r.duplicate_of} already in the library`, 'error', 7000);
        else this.toast(`No metadata found: ${r.reason}`, 'error', 6000);
        await this.reloadAll();
      } catch (e) { this.toast(e.message, 'error'); }
    },
    async duplicateItem(it) {
      const copy = await api.post(`/api/items/${it.id}/duplicate`);
      await this.reloadAll();
      await this.openItem(copy.id);
    },
    async assignSelectedToCollection(cid, ids = this.selectedIds) {
      for (const id of ids) await api.post(`/api/items/${id}/collections/${cid}`);
      this.toast(`Added ${ids.length} item${ids.length === 1 ? '' : 's'} to "${this.collectionName(cid)}"`, 'ok');
      await this.reloadAll();
    },

    // ------------------------------------------------------------------ duplicates & notes views

    async loadDuplicates() {
      this.duplicatesLoading = true;
      try {
        this.duplicateGroups = (await api.get('/api/duplicates')).map(g => ({ ...g, master: g.master_id }));
      } finally { this.duplicatesLoading = false; }
    },
    async mergeGroup(g) {
      const others = g.items.map(i => i.id).filter(id => id !== Number(g.master));
      const r = await api.post('/api/duplicates/merge', { master_id: Number(g.master), other_ids: others });
      this.toast(`Merged ${r.merged.length} duplicate(s)`, 'ok');
      await this.loadDuplicates();
      this.refreshSidebar();
    },
    async loadStandaloneNotes() {
      this.standaloneNotes = await api.get('/api/notes', { standalone: true });
    },
    async newStandaloneNote() {
      const note = await api.post('/api/notes', { item_id: null, html: '<h1>New note</h1><p></p>' });
      if (this.virtual !== 'notes') this.selectVirtual('notes');
      await this.loadStandaloneNotes();
      this.openNoteEditor(note);
    },

    // ------------------------------------------------------------------ drag & drop

    onDragItem(it, e) {
      const ids = this.isSelected(it.id) ? [...this.selectedIds] : [it.id];
      e.dataTransfer.setData('application/x-rh-items', JSON.stringify(ids));
      e.dataTransfer.effectAllowed = 'copyMove';
      const ghost = document.createElement('div');
      ghost.className = 'drag-ghost';
      ghost.textContent = ids.length === 1 ? (it.title || 'Item') : `${ids.length} items`;
      document.body.appendChild(ghost);
      e.dataTransfer.setDragImage(ghost, 12, 12);
      setTimeout(() => ghost.remove(), 0);
    },
    onDragCollection(c, e) {
      e.dataTransfer.setData('application/x-rh-collection', String(c.id));
      e.dataTransfer.effectAllowed = 'move';
    },
    draggedIds(e) {
      try { return JSON.parse(e.dataTransfer.getData('application/x-rh-items') || '[]'); } catch (err) { return []; }
    },
    async onDropCollection(target, e) {
      this.dragOver = null;
      const colId = e.dataTransfer.getData('application/x-rh-collection');
      if (colId) {
        if (Number(colId) !== (target ? target.id : null)) await this.moveCollection(Number(colId), target ? target.id : null);
        return;
      }
      const ids = this.draggedIds(e);
      if (ids.length && target) return this.assignSelectedToCollection(target.id, ids);
      const files = [...(e.dataTransfer.files || [])];
      if (files.length) return this.importDroppedFiles(files, target ? target.id : null);
    },
    /** Files dropped on the items list: PDFs become items, .bib/.ris/.json are imported. */
    async onDropFiles(e) {
      this.dropping = false;
      if (e.dataTransfer.types.includes('application/x-rh-items')) return;
      const files = [...(e.dataTransfer.files || [])];
      if (!files.length) return;
      await this.importDroppedFiles(files, this.virtual === 'collection' ? this.selectedCollectionId : null);
    },
    async importDroppedFiles(files, collectionId) {
      const pdfs = files.filter(f => /\.pdf$/i.test(f.name));
      const biblio = files.filter(f => /\.(bib|bibtex|ris|json|txt)$/i.test(f.name));
      if (pdfs.length) {
        const r = await api.upload('/api/import/pdfs', pdfs.map(f => ['files', f]), { collection_id: collectionId });
        this.toast(`Imported ${r.imported} PDF(s). Retrieving metadata...`, 'ok', 5000);
      }
      for (const f of biblio) {
        const r = await api.upload('/api/import/file', [['file', f]], { collection_id: collectionId, new_collection: !collectionId });
        this.toast(r.format === 'identifiers' ? `Queued ${r.queued} identifier(s)` : `Imported ${r.created} new, ${r.existing} existing from ${f.name}`, 'ok', 5000);
      }
      await this.reloadAll();
    },

    // ------------------------------------------------------------------ context menus

    openContext(type, target, e) {
      const width = 260, height = 420;
      const x = Math.min(e.clientX, window.innerWidth - width - 12);
      const y = Math.min(e.clientY, window.innerHeight - height - 12);
      this.contextMenu = { open: true, x: Math.max(8, x), y: Math.max(8, y), type, target };
    },
    async openItemContext(it, e) {
      if (!this.isSelected(it.id)) await this.clickItem(it, null);
      this.openContext('item', it, e);
    },
    closeContextMenu() { this.contextMenu.open = false; },
    contextMenuStyle() { return `left:${this.contextMenu.x}px; top:${this.contextMenu.y}px;`; },

    contextItems() {
      const t = this.contextMenu.target || {};
      const sep = { separator: true };
      switch (this.contextMenu.type) {
        case 'collection': return [
          { label: 'New Subcollection...', action: () => this.newCollection(t.id) },
          { label: 'Rename Collection...', action: () => this.renameCollection(t) },
          { label: 'Concept Map', action: () => this.openGraph({ collection_id: t.id, label: t.name }) },
          sep,
          { label: 'Export Collection...', action: () => this.openExport({ collection_id: t.id, label: t.name }) },
          { label: 'Create Bibliography from Collection...', action: () => this.openBibliography({ collection_id: t.id, label: t.name }) },
          { label: 'Generate Report from Collection', action: () => window.open(`/api/report?collection_id=${t.id}`, '_blank') },
          sep,
          { label: 'Move to Top Level', action: () => this.moveCollection(t.id, null), disabled: !t.parent_id },
          { label: 'Delete Collection...', action: () => this.deleteCollection(t), danger: true },
        ];
        case 'tag': return [
          { label: 'Rename Tag...', action: () => this.renameTag(t) },
          { label: 'Assign Color...', action: () => this.openTagColor(t) },
          { label: 'Remove Color', action: () => this.setTagColor(t, null), disabled: !t.color },
          sep,
          { label: 'Delete Tag...', action: () => this.deleteTag(t), danger: true },
        ];
        case 'search': return [
          { label: 'Edit Saved Search...', action: () => this.openAdvanced(t) },
          { label: 'Export Results...', action: () => this.openExport({ saved_search_id: t.id, label: t.name }) },
          sep,
          { label: 'Delete Saved Search', action: () => this.deleteSavedSearch(t), danger: true },
        ];
        case 'author': return [
          { label: 'Show Items', action: () => this.selectAuthor(t.name) },
          { label: 'Concept Map', action: () => this.openGraph({ author: t.name }) },
          { label: 'Search Catalogue Online', action: () => { this.selectVirtual('discover'); this.searchKind = 'author'; this.modalQuery = t.name; this.runSearch(true); } },
        ];
        case 'subject': return [
          { label: 'Show Items', action: () => this.selectSubject(t.name) },
          { label: 'Concept Map', action: () => this.openGraph({ subject: t.name, focus: t.name }) },
          { label: 'Search Online', action: () => { this.selectVirtual('discover'); this.searchKind = 'article'; this.modalQuery = t.name; this.runSearch(true); } },
        ];
        case 'result': return [
          { label: 'Add to Library', action: () => this.addOne(t) },
          { label: 'Copy DOI', action: () => navigator.clipboard.writeText(t.doi), disabled: !t.doi },
          { label: 'Open DOI Page', action: () => window.open(`https://doi.org/${t.doi}`, '_blank'), disabled: !t.doi },
        ];
        case 'item': {
          const many = this.selectedIds.length > 1;
          if (this.virtual === 'trash') return [
            { label: many ? `Restore ${this.selectedIds.length} Items` : 'Restore to Library', action: () => this.restoreSelected() },
            { label: 'Delete Permanently...', action: () => this.deleteSelectedPermanently(), danger: true },
          ];
          const pdf = (t.attachment_count || 0) > 0;
          return [
            { label: 'View PDF', action: () => this.openBestAttachment(t), disabled: many || !pdf },
            { label: 'Show File', action: () => this.showBestAttachment(t), disabled: many || !pdf },
            sep,
            { label: 'Add Note', action: () => this.addChildNote(t), disabled: many },
            { label: 'Attach File...', action: () => this.pickAttachment(t), disabled: many },
            { label: 'Attach Link to URI...', action: () => this.addLink(t), disabled: many },
            sep,
            { label: 'Find Available PDF', action: () => this.selectedItemsList().forEach(i => this.downloadItem(i)) },
            { label: 'Retrieve Metadata for PDF', action: () => this.retrieveMetadata(t), disabled: many || !pdf },
            { label: 'Rename File from Parent Metadata', action: async () => { const r = await api.post(`/api/items/${t.id}/rename-files`); this.toast(`Renamed ${r.renamed} file(s)`, 'ok'); this.reloadAll(); }, disabled: many || !pdf },
            sep,
            { label: 'Add to Collection...', action: () => this.openFolderChooser() },
            { label: 'Remove from Collection', action: () => this.removeSelectedFromCollection(), disabled: this.virtual !== 'collection' },
            { label: 'Add Tags...', action: () => this.openBulkTags() },
            sep,
            { label: 'Copy Bibliography', hint: 'Ctrl+Shift+C', action: () => this.quickCopy('bibliography') },
            { label: 'Copy Citation', hint: 'Ctrl+Shift+A', action: () => this.quickCopy('citation') },
            { label: 'Create Bibliography from Items...', action: () => this.openBibliography({ ids: this.selectedIds }) },
            { label: 'Export Items...', action: () => this.openExport({ ids: this.selectedIds }) },
            { label: 'Generate Report from Items', action: () => window.open(`/api/report?ids=${this.selectedIds.join(',')}`, '_blank') },
            sep,
            { label: 'Duplicate Item', action: () => this.duplicateItem(t), disabled: many },
            { label: t.starred ? 'Unstar' : 'Star', action: () => this.toggleStar(t), disabled: many },
            { label: many ? `Move ${this.selectedIds.length} Items to Trash` : 'Move Item to Trash...', action: () => this.trashSelected(), danger: true },
          ];
        }
        default: return [];
      }
    },
    async runContext(entry) {
      this.closeContextMenu();
      if (entry && !entry.disabled && entry.action) {
        try { await entry.action(); } catch (e) { this.toast(e.message, 'error'); }
      }
    },

    // ------------------------------------------------------------------ quick copy

    /** Zotero Quick Copy: formatted bibliography or citation for the selected items. */
    async quickCopy(kind = 'bibliography') {
      const ids = this.selectedIds.length ? [...this.selectedIds] : (this.selectedItem ? [this.selectedItem.id] : []);
      if (!ids.length) return this.toast('Select items first', 'error');
      try {
        const cite = await this.citationEngine();
        let html;
        if (kind === 'citation') html = await cite.citation(ids.map(id => ({ id })), 'html');
        else html = (await cite.bibliography(ids, 'html')).text;
        await RHCitations.copyRich(html);
        this.toast(`${kind === 'citation' ? 'Citation' : 'Bibliography'} copied (${this.styleTitle(this.settings.citation_style)})`, 'ok');
      } catch (e) {
        this.toast(`Quick Copy failed: ${e.message}`, 'error');
      }
    },

    // ------------------------------------------------------------------ formatting helpers

    shortAuthor(name) {
      const [fam, giv] = (name || '').split(',').map(s => (s || '').trim());
      if (giv) return `${fam}, ${giv.split(/\s+/).filter(Boolean).map(p => p[0] || '').join('. ')}.`;
      return fam || name;
    },
    formatCreators(it) {
      const cs = (it.creators && it.creators.length) ? it.creators : (it.authors || []).map(a => ({ lastName: String(a).split(',')[0] }));
      const main = cs.filter(c => !c.creatorType || ['author', 'programmer', 'inventor', 'presenter', 'director'].includes(c.creatorType));
      const list = (main.length ? main : cs).map(c => c.name || c.lastName || c.firstName).filter(Boolean);
      if (!list.length) return '';
      if (list.length === 1) return list[0];
      if (list.length === 2) return `${list[0]} and ${list[1]}`;
      return `${list[0]} et al.`;
    },
    formatAuthors(authors) {
      if (!authors || !authors.length) return '-';
      const s = authors.map(a => String(a).split(',')[0].trim()).filter(Boolean);
      return s.length === 1 ? s[0] : s.length === 2 ? `${s[0]} & ${s[1]}` : `${s[0]} et al.`;
    },
    formatDate(iso) {
      if (!iso) return '';
      const d = new Date(iso.replace(' ', 'T') + (iso.includes('Z') || iso.includes('+') ? '' : 'Z'));
      return isNaN(d) ? iso.slice(0, 10) : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    },
    typeIcon(type) {
      return ({ journalArticle: 'article', book: 'book', bookSection: 'chapter', conferencePaper: 'conference',
        preprint: 'preprint', thesis: 'thesis', report: 'report', webpage: 'web', blogPost: 'web', dataset: 'data',
        computerProgram: 'code', patent: 'patent', presentation: 'slides', videoRecording: 'video',
        magazineArticle: 'article', newspaperArticle: 'news', encyclopediaArticle: 'book', manuscript: 'doc',
        letter: 'doc', document: 'doc' })[type] || 'doc';
    },
    statusLabel(s) {
      return ({ pending: 'pending', metadata_only: '', downloading: 'fetching', downloaded: '', failed: 'no PDF',
        needs_metadata: 'no metadata' })[s] ?? s ?? '';
    },
  };
};
