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

    graph: {
      loading: false, nodes: [], edges: [], selectedNode: null,
      scope: { label: 'All subjects', items: 0 }, request: {}, focus: '', depth: 1, limit: 48,
      viewBox: '0 0 920 620',
    },

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

    // ------------------------------------------------------------ concept map

    openGraph(scope = {}) {
      this.virtual = 'graph';
      this.clearItem();
      this.closeContextMenu();
      this.graph.request = { ...scope };
      this.graph.focus = scope.focus || scope.subject || '';
      this.loadSubjectGraph();
    },

    openGraphFromCurrent() {
      if (this.virtual === 'collection' && this.selectedCollectionId) {
        const c = this.collections.find(x => x.id === this.selectedCollectionId);
        return this.openGraph({ collection_id: this.selectedCollectionId, label: c?.name || 'Collection' });
      }
      if (this.virtual === 'author' && this.selectedAuthor) return this.openGraph({ author: this.selectedAuthor });
      if (this.virtual === 'subject' && this.selectedSubject) {
        return this.openGraph({ subject: this.selectedSubject, focus: this.selectedSubject });
      }
      return this.openGraph();
    },

    async loadSubjectGraph(extra = {}) {
      this.graph.loading = true;
      try {
        this.graph.request = { ...(this.graph.request || {}), ...extra };
        if (Object.prototype.hasOwnProperty.call(extra, 'focus')) this.graph.focus = extra.focus || '';
        const req = this.graph.request || {};
        const data = await api.get('/api/subject-graph', {
          depth: this.graph.depth, limit: this.graph.limit, collection_id: req.collection_id,
          author: req.author, subject: req.subject, focus: this.graph.focus,
        });
        this.layoutGraph(data);
      } catch (e) {
        this.toast(e.message || 'Could not load concept map', 'error');
      } finally {
        this.graph.loading = false;
      }
    },

    /** Radial layout: focus node in the centre, rings of neighbours sorted by frequency. */
    layoutGraph(data) {
      const width = 920, height = 620, cx = width / 2, cy = height / 2;
      const rawNodes = [...(data.nodes || [])];
      const maxCount = Math.max(1, ...rawNodes.map(n => n.count || 1));
      const focusId = rawNodes.find(n => n.focused)?.id || rawNodes[0]?.id || '';
      const nodes = rawNodes.map(n => ({ ...n, r: 20 + Math.sqrt((n.count || 1) / maxCount) * 26, x: cx, y: cy }));
      nodes.sort((a, b) => (a.id === focusId ? -1 : b.id === focusId ? 1 : (b.count || 0) - (a.count || 0)));
      nodes.forEach((node, i) => {
        if (i === 0) return;
        const index = i - 1;
        const ring = Math.floor(index / 14);
        const perRing = ring === 0 ? Math.min(14, nodes.length - 1) : 18;
        const slot = ring === 0 ? index : index - 14 - ((ring - 1) * 18);
        const radius = 150 + ring * 115;
        const angle = (-Math.PI / 2) + (slot / Math.max(1, perRing)) * Math.PI * 2 + ring * 0.22;
        node.x = cx + Math.cos(angle) * radius;
        node.y = cy + Math.sin(angle) * radius;
      });
      const byId = new Map(nodes.map(n => [n.id, n]));
      const edges = (data.edges || []).filter(e => byId.has(e.source) && byId.has(e.target)).map(e => {
        const a = byId.get(e.source), b = byId.get(e.target);
        return { ...e, x1: a.x, y1: a.y, x2: b.x, y2: b.y };
      });
      const selectedId = this.graph.selectedNode?.id;
      this.graph.nodes = nodes;
      this.graph.edges = edges;
      this.graph.scope = data.scope || { label: 'All subjects', items: 0 };
      this.graph.selectedNode = nodes.find(n => n.id === selectedId) || nodes.find(n => n.focused) || nodes[0] || null;
    },

    graphNodeLabel(node) {
      const label = node?.label || '';
      return label.length > 24 ? `${label.slice(0, 22)}...` : label;
    },
    graphEdgesMarkup() {
      const esc = v => String(v ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
      const sel = this.graph.selectedNode;
      return (this.graph.edges || []).map(edge => {
        const width = Math.min(7, 1.1 + Math.sqrt(edge.weight || 1));
        const opacity = !sel ? 0.38 : (edge.source === sel.id || edge.target === sel.id ? 0.74 : 0.12);
        return `<line x1="${edge.x1}" y1="${edge.y1}" x2="${edge.x2}" y2="${edge.y2}" stroke-width="${width}" ` +
          `opacity="${opacity}" class="graph-edge"><title>${esc(edge.source)} - ${esc(edge.target)} (${edge.weight})</title></line>`;
      }).join('');
    },
    graphNodeCardClass(node) {
      const s = this.graph.selectedNode;
      if (s && s.id === node.id) return 'graph-node-card selected';
      return node.focused ? 'graph-node-card focused' : 'graph-node-card';
    },
    graphNodeCardStyle(node) {
      const size = Math.max(74, Math.min(132, (node.r || 28) * 2.35));
      return `left:${((node.x || 0) / 920) * 100}%; top:${((node.y || 0) / 620) * 100}%; width:${size}px; height:${size}px;`;
    },
    selectGraphNode(node) { this.graph.selectedNode = node; this.clearItem(); },
    async enterGraphNode(node = this.graph.selectedNode) {
      if (!node) return;
      this.graph.focus = node.label;
      await this.loadSubjectGraph({ focus: node.label });
    },
    async resetGraphFocus() {
      this.graph.focus = (this.graph.request || {}).subject || '';
      await this.loadSubjectGraph({ focus: this.graph.focus });
    },
    searchGraphNode(node = this.graph.selectedNode) {
      if (!node) return;
      this.selectVirtual('discover');
      this.searchKind = 'article';
      this.modalQuery = node.label;
      this.runSearch(true);
    },
    openGraphNodeSubject(node = this.graph.selectedNode) { if (node) this.selectSubject(node.label); },
    async selectGraphItem(item) {
      if (!item || !item.id) return;
      this.graph.selectedNode = null;
      await this.openItem(item.id);
    },
  };
};
