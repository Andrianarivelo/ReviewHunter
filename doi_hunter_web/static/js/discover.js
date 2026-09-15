/*
 * Discover (online search: CrossRef articles, author catalogues, books) and the
 * subject Concept Map. Ported from ReviewHunter v1 with the same behaviour.
 */
window.discoverPart = function discoverPart() {
  return {
    searchKind: 'article',
    modalQuery: '',
    modalResults: [],
    modalTotal: 0,
    modalOffset: 0,
    modalRows: 25,
    modalSort: 'pertinence',
    modalLoading: false,
    modalAttempted: false,
    modalSubmitting: false,
    modalSource: '',
    modalUnavailable: false,
    modalBulkCollectionId: '',
    addAllCap: 500,
    autoDownload: true,

    // ------------------------------------------------------------ discover search

    /** Placeholder text for the discover input depending on the active tab. */
    modalPlaceholder() {
      return ({
        article: 'Search article title, keywords, or DOI',
        author: 'Search author name',
        book: 'Search book title or topic',
      })[this.searchKind];
    },

    /** Switch between Articles / Authors / Books and re-run the query. */
    async setSearchKind(k) {
      if (this.searchKind === k) return;
      this.searchKind = k;
      this.modalSort = k === 'author' ? 'newest' : 'pertinence';
      Object.assign(this, { modalResults: [], modalTotal: 0, modalOffset: 0, modalAttempted: false,
        modalUnavailable: false, modalSource: '' });
      if (this.modalQuery.trim()) await this.runSearch(true);
    },

    /** Run (or page) the online search. */
    async runSearch(reset = true) {
      const q = this.modalQuery.trim();
      if (!q) return;
      if (reset) Object.assign(this, { modalResults: [], modalTotal: 0, modalOffset: 0 });
      this.modalLoading = true;
      this.modalAttempted = true;
      this.modalUnavailable = false;
      try {
        const data = await api.get('/api/search', {
          q, kind: this.searchKind, rows: this.modalRows, offset: this.modalOffset, sort: this.modalSort,
        });
        const fresh = (data.results || []).map(x => ({ ...x, _selected: false }));
        this.modalResults = [...this.modalResults, ...fresh];
        this.modalTotal = data.total || this.modalResults.length;
        this.modalSource = data.source || (this.searchKind === 'book' ? '' : 'CrossRef');
        this.modalOffset = this.modalResults.length;
        this.modalUnavailable = this.searchKind === 'book' && data.available === false && !this.modalResults.length;
      } catch (e) {
        this.modalUnavailable = true;
        this.toast(e.message, 'error');
      } finally {
        this.modalLoading = false;
      }
    },

    async changeModalSort() {
      if (this.modalAttempted && this.modalQuery.trim()) await this.runSearch(true);
    },
    selectedCount() { return this.modalResults.filter(r => r._selected).length; },
    allSelected() { return this.modalResults.length > 0 && this.modalResults.every(r => r._selected); },
    toggleSelectAll(checked) { this.modalResults.forEach(r => { r._selected = !!checked; }); },
    resultKey(r, i) { return r.doi || r.title || i; },
    suggestedNewFolder() {
      const q = this.modalQuery.trim();
      return this.searchKind === 'author' && q ? `+ New collection: "${q}"` : '+ New collection';
    },

    /** Resolve the "add to" collection selector, creating a collection when requested. */
    async _ensureBulkCollection() {
      const cid = this.modalBulkCollectionId;
      if (cid !== '__new__') return cid ? Number(cid) : null;
      const name = prompt('New collection name:', this.searchKind === 'author' ? this.modalQuery.trim() : '');
      if (!name) return null;
      const c = await api.post('/api/collections', { name });
      await this.loadCollections();
      this.modalBulkCollectionId = String(c.id);
      return c.id;
    },
    async addOne(r) { await this._submitItems([r], await this._ensureBulkCollection()); },
    async addSelected() {
      const items = this.modalResults.filter(r => r._selected);
      if (items.length) await this._submitItems(items, await this._ensureBulkCollection());
    },
    async addAll() {
      while (this.modalResults.length < this.modalTotal && this.modalResults.length < this.addAllCap) {
        await this.runSearch(false);
      }
      await this._submitItems(this.modalResults.slice(0, this.addAllCap), await this._ensureBulkCollection());
    },

    /** Queue discover results for import (background job, progress arrives on the WebSocket). */
    async _submitItems(items, collectionId) {
      if (!items || !items.length) return;
      this.modalSubmitting = true;
      try {
        if (this.searchKind === 'book') {
          await api.post('/api/books/add', {
            publications: items.map(({ _selected, ...rest }) => rest),
            auto_download: this.autoDownload, collection_id: collectionId,
          });
        } else {
          await api.post('/api/add', {
            queries: items.map(r => r.doi || r.title).filter(Boolean),
            auto_download: this.autoDownload, collection_id: collectionId,
          });
        }
        this.toast(`Queued ${items.length} item${items.length === 1 ? '' : 's'}`, 'ok');
        items.forEach(r => { r._selected = false; });
      } catch (e) {
        this.toast(e.message || 'Could not queue items', 'error');
      } finally {
        this.modalSubmitting = false;
      }
    },

  };
};
