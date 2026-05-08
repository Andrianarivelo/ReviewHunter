// ReviewHunter - Alpine.js component. Local-first library plus discovery workspace.

function app() {
  return {
    dark: localStorage.dhDark === 'true' || matchMedia('(prefers-color-scheme: dark)').matches,
    collections: [],
    items: [],
    selectedItem: null,
    virtual: 'discover',
    selectedCollectionId: null,
    selectedAuthor: null,
    selectedSubject: null,
    facets: { authors: [], subjects: [] },
    graph: {
      loading: false,
      nodes: [],
      edges: [],
      selectedNode: null,
      scope: { label: 'All subjects', items: 0 },
      request: {},
      focus: '',
      depth: 1,
      limit: 48,
      viewBox: '0 0 920 620',
    },
    showAuthors: true,
    showSubjects: true,
    authorLimit: 12,
    subjectLimit: 12,
    searchQuery: '',

    searchKind: 'article',
    modalQuery: '',
    modalResults: [],
    modalTotal: 0,
    modalOffset: 0,
    modalRows: 25,
    modalLoading: false,
    modalAttempted: false,
    modalSubmitting: false,
    modalSource: '',
    modalUnavailable: false,
    modalBulkCollectionId: '',
    addAllCap: 500,

    sortBy: 'added',
    loading: false,
    stats: { items: 0, downloaded: 0, starred: 0, library_path: '' },
    toasts: [],
    openAdd: false,
    addInput: '',
    autoDownload: true,
    addCollectionId: '',
    dropping: false,
    dragOver: null,
    ws: null,

    contextMenu: { open: false, x: 0, y: 0, type: '', target: null },
    folderChooser: { open: false, mode: '', target: null, title: '', newName: '' },

    async init() {
      await Promise.all([this.loadStats(), this.loadCollections(), this.loadFacets()]);
      this.connectWS();
      this.$nextTick(() => this.$refs.discoverInput && this.$refs.discoverInput.focus());
      addEventListener('click', () => this.closeContextMenu());
      addEventListener('resize', () => this.closeContextMenu());
      addEventListener('scroll', () => this.closeContextMenu(), true);
      addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          this.selectVirtual('discover');
          this.$nextTick(() => this.$refs.discoverInput && this.$refs.discoverInput.focus());
        }
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') {
          e.preventDefault();
          this.openAdd = true;
        }
        if (e.key === 'Escape') {
          this.closeContextMenu();
        }
      });
    },

    async loadStats() {
      const r = await fetch('/api/stats');
      if (r.ok) this.stats = await r.json();
    },
    async loadCollections() {
      const r = await fetch('/api/collections');
      if (r.ok) this.collections = await r.json();
    },
    async loadFacets() {
      const r = await fetch('/api/facets');
      if (r.ok) this.facets = await r.json();
    },
    async loadItems() {
      if (this.virtual === 'discover' || this.virtual === 'graph') return;
      this.loading = true;
      try {
        const params = new URLSearchParams();
        if (this.virtual === 'collection' && this.selectedCollectionId) params.set('collection_id', this.selectedCollectionId);
        if (this.virtual === 'starred') params.set('starred', 'true');
        if (this.virtual === 'failed') params.set('status', 'failed');
        if (this.virtual === 'author' && this.selectedAuthor) params.set('author', this.selectedAuthor);
        if (this.virtual === 'subject' && this.selectedSubject) params.set('subject', this.selectedSubject);
        const r = await fetch(`/api/items?${params}`);
        if (r.ok) {
          let items = await r.json();
          if (this.virtual === 'recent') items = items.slice(0, 30);
          this.items = items;
          this.sortItems();
        }
      } finally {
        this.loading = false;
      }
    },
    async loadSubjectGraph(extra = {}) {
      this.graph.loading = true;
      try {
        this.graph.request = { ...(this.graph.request || {}), ...extra };
        if (Object.prototype.hasOwnProperty.call(extra, 'focus')) this.graph.focus = extra.focus || '';
        const params = new URLSearchParams({
          depth: this.graph.depth,
          limit: this.graph.limit,
        });
        const req = this.graph.request || {};
        if (req.collection_id) params.set('collection_id', req.collection_id);
        if (req.author) params.set('author', req.author);
        if (req.subject) params.set('subject', req.subject);
        if (this.graph.focus) params.set('focus', this.graph.focus);
        const r = await fetch(`/api/subject-graph?${params}`);
        if (!r.ok) throw new Error('graph failed');
        const data = await r.json();
        this.layoutGraph(data);
      } catch (e) {
        this.toast(e.message || 'Could not load concept map', 'error');
      } finally {
        this.graph.loading = false;
      }
    },
    layoutGraph(data) {
      const width = 920;
      const height = 620;
      const cx = width / 2;
      const cy = height / 2;
      const rawNodes = [...(data.nodes || [])];
      const maxCount = Math.max(1, ...rawNodes.map(n => n.count || 1));
      const focusId = rawNodes.find(n => n.focused)?.id || rawNodes[0]?.id || '';
      const nodes = rawNodes.map(n => ({
        ...n,
        r: 20 + Math.sqrt((n.count || 1) / maxCount) * 26,
        x: cx,
        y: cy,
      }));
      nodes.sort((a, b) => (a.id === focusId ? -1 : b.id === focusId ? 1 : (b.count || 0) - (a.count || 0)));
      nodes.forEach((node, i) => {
        if (i === 0) {
          node.x = cx;
          node.y = cy;
          return;
        }
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
      const edges = (data.edges || [])
        .filter(e => byId.has(e.source) && byId.has(e.target))
        .map(e => {
          const a = byId.get(e.source);
          const b = byId.get(e.target);
          return { ...e, x1: a.x, y1: a.y, x2: b.x, y2: b.y };
        });
      const selectedId = this.graph.selectedNode?.id;
      this.graph.nodes = nodes;
      this.graph.edges = edges;
      this.graph.scope = data.scope || { label: 'All subjects', items: 0 };
      this.graph.selectedNode = nodes.find(n => n.id === selectedId) || nodes.find(n => n.focused) || nodes[0] || null;
    },

    selectVirtual(v) {
      this.virtual = v;
      this.selectedCollectionId = null;
      this.selectedAuthor = null;
      this.selectedSubject = null;
      this.closeContextMenu();
      if (v === 'discover') {
        this.$nextTick(() => this.$refs.discoverInput && this.$refs.discoverInput.focus());
        return;
      }
      if (v === 'graph') {
        this.openGraph();
        return;
      }
      this.loadItems();
    },
    openGraph(scope = {}) {
      this.virtual = 'graph';
      this.selectedCollectionId = null;
      this.selectedAuthor = null;
      this.selectedSubject = null;
      this.searchQuery = '';
      this.selectedItem = null;
      this.closeContextMenu();
      this.graph.request = { ...scope };
      this.graph.focus = scope.focus || scope.subject || '';
      this.loadSubjectGraph();
    },
    openGraphFromCurrent() {
      if (this.virtual === 'collection' && this.selectedCollectionId) {
        const c = this.collections.find(x => x.id === this.selectedCollectionId);
        return this.openGraph({ collection_id: this.selectedCollectionId, label: c?.name || 'Folder' });
      }
      if (this.virtual === 'author' && this.selectedAuthor) {
        return this.openGraph({ author: this.selectedAuthor, label: this.shortAuthor(this.selectedAuthor) });
      }
      if (this.virtual === 'subject' && this.selectedSubject) {
        return this.openGraph({ subject: this.selectedSubject, focus: this.selectedSubject, label: this.selectedSubject });
      }
      return this.openGraph();
    },
    selectCollection(id) {
      this.virtual = 'collection';
      this.selectedCollectionId = id;
      this.selectedAuthor = null;
      this.selectedSubject = null;
      this.loadItems();
    },
    selectAuthor(name) {
      this.virtual = 'author';
      this.selectedAuthor = name;
      this.selectedCollectionId = null;
      this.selectedSubject = null;
      this.searchQuery = '';
      this.loadItems();
    },
    selectSubject(name) {
      this.virtual = 'subject';
      this.selectedSubject = name;
      this.selectedCollectionId = null;
      this.selectedAuthor = null;
      this.searchQuery = '';
      this.loadItems();
    },
    shortAuthor(name) {
      const [fam, giv] = (name || '').split(',').map(s => (s || '').trim());
      if (giv) return `${fam}, ${giv.split(/\s+/).filter(Boolean).map(p => p[0] || '').join('. ')}.`;
      return fam || name;
    },
    async selectItem(item) {
      if (!item || !item.id) return;
      const r = await fetch(`/api/items/${item.id}`);
      if (r.ok) this.selectedItem = await r.json();
    },
    paneTitle() {
      if (this.virtual === 'collection') {
        const c = this.collections.find(x => x.id === this.selectedCollectionId);
        return c ? c.name : 'Folder';
      }
      if (this.virtual === 'author') return `Author: ${this.shortAuthor(this.selectedAuthor || '')}`;
      if (this.virtual === 'subject') return `Subject: ${this.selectedSubject}`;
      return ({ all: 'All Papers', recent: 'Recently Added', starred: 'Starred', failed: 'Needs Attention' })[this.virtual] || '';
    },
    collectionName(id) {
      const c = this.collections.find(x => x.id == id);
      return c ? c.name : '';
    },

    get filteredItems() {
      const q = this.searchQuery.trim().toLowerCase();
      if (!q) return this.items;
      return this.items.filter(it => {
        const hay = [
          it.title, it.journal, it.publisher, it.doi,
          (it.authors || []).join(' '),
          (it.keywords || []).join(' '),
          (it.subjects || []).join(' '),
        ].join(' ').toLowerCase();
        return hay.includes(q);
      });
    },
    sortItems() {
      const by = this.sortBy;
      this.items = [...this.items].sort((a, b) => {
        if (by === 'year') return (b.year || 0) - (a.year || 0);
        if (by === 'title') return (a.title || '').localeCompare(b.title || '');
        return (b.added_at || '').localeCompare(a.added_at || '');
      });
    },
    resultKey(r, i) {
      return r.doi || r.title || i;
    },
    formatAuthors(authors) {
      if (!authors || authors.length === 0) return '-';
      const surnames = authors.map(a => String(a).split(',')[0].trim()).filter(Boolean);
      if (surnames.length === 0) return '-';
      if (surnames.length === 1) return surnames[0];
      if (surnames.length === 2) return `${surnames[0]} & ${surnames[1]}`;
      return `${surnames[0]} et al.`;
    },
    statusLabel(s) {
      return ({
        pending: 'pending',
        metadata_only: 'metadata',
        downloading: 'fetching',
        downloaded: 'on disk',
        failed: 'failed',
        needs_metadata: 'no metadata',
      })[s] || s || '';
    },
    isPreviewable(it) {
      const ext = (it.extension || '').toLowerCase().replace(/^\./, '');
      return !ext || ext === 'pdf';
    },
    statusClass(s) {
      return ({
        downloaded: 'bg-emerald-500',
        downloading: 'bg-accent-500 animate-pulse',
        failed: 'bg-rose-500',
        needs_metadata: 'bg-amber-500',
        metadata_only: 'bg-ink-300',
      })[s] || 'bg-ink-300';
    },
    graphNodeLabel(node) {
      const label = node?.label || '';
      return label.length > 24 ? `${label.slice(0, 22)}...` : label;
    },
    graphEdgeWidth(edge) {
      return Math.min(7, 1.1 + Math.sqrt(edge.weight || 1));
    },
    graphEdgeOpacity(edge) {
      const selected = this.graph.selectedNode;
      if (!selected) return 0.38;
      return edge.source === selected.id || edge.target === selected.id ? 0.74 : 0.12;
    },
    graphNodeClass(node) {
      const selected = this.graph.selectedNode;
      if (selected && selected.id === node.id) return 'graph-node selected';
      if (node.focused) return 'graph-node focused';
      return 'graph-node';
    },
    selectGraphNode(node) {
      this.graph.selectedNode = node;
      this.selectedItem = null;
    },
    async enterGraphNode(node = this.graph.selectedNode) {
      if (!node) return;
      this.graph.focus = node.label;
      await this.loadSubjectGraph({ focus: node.label });
    },
    async resetGraphFocus() {
      const req = this.graph.request || {};
      this.graph.focus = req.subject || '';
      await this.loadSubjectGraph({ focus: this.graph.focus });
    },
    searchGraphNode(node = this.graph.selectedNode) {
      if (!node) return;
      this.selectVirtual('discover');
      this.searchKind = 'article';
      this.modalQuery = node.label;
      this.runSearch(true);
    },
    openGraphNodeSubject(node = this.graph.selectedNode) {
      if (!node) return;
      this.selectSubject(node.label);
    },
    async selectGraphItem(item) {
      if (!item || !item.id) return;
      this.graph.selectedNode = null;
      await this.selectItem(item);
    },

    runTopSearch() {
      const q = this.searchQuery.trim();
      if (!q) {
        this.selectVirtual('discover');
        return;
      }
      this.selectVirtual('discover');
      this.modalQuery = q;
      this.runSearch(true);
    },
    async setSearchKind(k) {
      if (this.searchKind === k) return;
      this.searchKind = k;
      this.modalResults = [];
      this.modalTotal = 0;
      this.modalOffset = 0;
      this.modalAttempted = false;
      this.modalUnavailable = false;
      this.modalSource = '';
      if (this.modalQuery.trim()) await this.runSearch(true);
    },
    modalPlaceholder() {
      return ({
        article: 'Search article title, keywords, or DOI',
        author: 'Search author name',
        book: 'Search book title or topic',
      })[this.searchKind];
    },
    suggestedNewFolder() {
      const q = this.modalQuery.trim();
      if (this.searchKind === 'author' && q) return `+ New folder: "${q}"`;
      return '+ New folder';
    },
    async runSearch(reset = true) {
      const q = this.modalQuery.trim();
      if (!q) return;
      if (reset) {
        this.modalResults = [];
        this.modalTotal = 0;
        this.modalOffset = 0;
      }
      this.modalLoading = true;
      this.modalAttempted = true;
      this.modalUnavailable = false;
      try {
        const params = new URLSearchParams({
          q,
          kind: this.searchKind,
          rows: this.modalRows,
          offset: this.modalOffset,
        });
        const r = await fetch(`/api/search?${params}`);
        if (!r.ok) {
          this.modalUnavailable = true;
          return;
        }
        const data = await r.json();
        const fresh = (data.results || []).map(x => ({ ...x, _selected: false }));
        this.modalResults = [...this.modalResults, ...fresh];
        this.modalTotal = data.total || this.modalResults.length;
        this.modalSource = data.source || (this.searchKind === 'book' ? '' : 'CrossRef');
        this.modalOffset = this.modalResults.length;
        this.modalUnavailable = (this.searchKind === 'book') && data.available === false && this.modalResults.length === 0;
      } finally {
        this.modalLoading = false;
      }
    },
    selectedCount() {
      return this.modalResults.filter(r => r._selected).length;
    },
    allSelected() {
      return this.modalResults.length > 0 && this.modalResults.every(r => r._selected);
    },
    toggleSelectAll(checked) {
      this.modalResults.forEach(r => { r._selected = !!checked; });
    },
    async _ensureBulkCollection() {
      const cid = this.modalBulkCollectionId;
      if (cid !== '__new__') return cid ? Number(cid) : null;
      const def = this.searchKind === 'author' ? this.modalQuery.trim() : '';
      const name = prompt('New folder name:', def);
      if (!name) return null;
      const r = await fetch('/api/collections', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) return null;
      const c = await r.json();
      await this.loadCollections();
      this.modalBulkCollectionId = String(c.id);
      return c.id;
    },
    async addOne(r) {
      const cid = await this._ensureBulkCollection();
      await this._submitItems([r], cid);
    },
    async addSelected() {
      const items = this.modalResults.filter(r => r._selected);
      if (items.length === 0) return;
      const cid = await this._ensureBulkCollection();
      await this._submitItems(items, cid);
    },
    async addAll() {
      while (this.modalResults.length < this.modalTotal && this.modalResults.length < this.addAllCap) {
        await this.runSearch(false);
      }
      const items = this.modalResults.slice(0, this.addAllCap);
      const cid = await this._ensureBulkCollection();
      await this._submitItems(items, cid);
    },
    async _submitItems(items, collectionId) {
      if (!items || items.length === 0) return;
      this.modalSubmitting = true;
      try {
        const body = (this.searchKind === 'book')
          ? {
              publications: items.map(({ _selected, ...rest }) => rest),
              auto_download: this.autoDownload,
              collection_id: collectionId,
            }
          : {
              queries: items.map(r => r.doi || r.title).filter(Boolean),
              auto_download: this.autoDownload,
              collection_id: collectionId,
            };
        const url = (this.searchKind === 'book') ? '/api/books/add' : '/api/add';
        const r = await fetch(url, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error('queue failed');
        this.toast(`Queued ${items.length} item${items.length === 1 ? '' : 's'}`, 'ok');
        items.forEach(r => { r._selected = false; });
      } catch (e) {
        this.toast(e.message || 'Could not queue items', 'error');
      } finally {
        this.modalSubmitting = false;
      }
    },

    addCount() {
      return this.addInput.split(/[\n,]/).map(s => s.trim()).filter(Boolean).length;
    },
    async submitAdd() {
      const queries = this.addInput.split(/[\n,]/).map(s => s.trim()).filter(Boolean);
      if (queries.length === 0) return;
      const body = {
        queries,
        auto_download: this.autoDownload,
        collection_id: this.addCollectionId ? Number(this.addCollectionId) : null,
      };
      this.openAdd = false;
      this.addInput = '';
      this.toast(`Queued ${queries.length} item${queries.length === 1 ? '' : 's'}`, 'info');
      await fetch('/api/add', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    },
    async onDropFile(e) {
      this.dropping = false;
      const files = [...(e.dataTransfer?.files || [])];
      const txts = files.filter(f => /\.(txt|csv|bib)$/i.test(f.name));
      const pdfs = files.filter(f => /\.pdf$/i.test(f.name));
      if (txts.length) {
        const reader = new FileReader();
        reader.onload = () => {
          this.addInput = reader.result;
          this.openAdd = true;
        };
        reader.readAsText(txts[0]);
      }
      if (pdfs.length) {
        this.toast('Use Add to import a folder of PDFs', 'info');
      }
    },

    async toggleStar(it) {
      const next = !it.starred;
      it.starred = next;
      if (this.selectedItem && this.selectedItem.id === it.id) this.selectedItem.starred = next;
      await fetch(`/api/items/${it.id}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ starred: next }),
      });
      this.loadStats();
    },
    async downloadItem(it) {
      if (!it) return;
      it.status = 'downloading';
      if (this.selectedItem && this.selectedItem.id === it.id) this.selectedItem.status = 'downloading';
      await fetch(`/api/items/${it.id}/download`, { method: 'POST' });
    },
    async deleteItem(it) {
      if (!confirm(`Remove "${it.title}" from library?`)) return;
      await fetch(`/api/items/${it.id}?delete_file=true`, { method: 'DELETE' });
      this.items = this.items.filter(x => x.id !== it.id);
      if (this.selectedItem && this.selectedItem.id === it.id) this.selectedItem = null;
      this.loadStats();
      this.loadFacets();
    },
    async openItemFolder(it) {
      if (!it || !it.id) return;
      const r = await fetch(`/api/items/${it.id}/open-folder`, { method: 'POST' });
      if (r.ok) this.toast('Opened folder', 'ok');
      else this.toast('Could not open folder', 'error');
    },
    openItemFile(it) {
      if (!it || !it.file_path) return;
      window.open(`/api/items/${it.id}/file`, '_blank');
    },
    async exportItemFile(it) {
      const dest = prompt('Export file to folder path:');
      if (!dest) return;
      const r = await fetch(`/api/items/${it.id}/export-file`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ destination: dest }),
      });
      if (r.ok) {
        const data = await r.json();
        this.toast(`Exported to ${data.path}`, 'ok');
      } else {
        const data = await r.json().catch(() => ({}));
        this.toast(data.detail || 'Export failed', 'error');
      }
    },

    async newCollection() {
      const name = prompt('Folder name:');
      if (!name) return;
      const r = await fetch('/api/collections', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (r.ok) await this.loadCollections();
    },
    async renameCollectionPrompt(c) {
      const name = prompt('Rename folder:', c.name);
      if (!name || name === c.name) return;
      await fetch(`/api/collections/${c.id}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      await this.loadCollections();
    },
    async deleteCollectionConfirm(c) {
      if (!confirm(`Delete folder "${c.name}"? Papers in it will stay in the library.`)) return;
      await fetch(`/api/collections/${c.id}`, { method: 'DELETE' });
      if (this.selectedCollectionId === c.id) this.selectVirtual('discover');
      await this.loadCollections();
    },
    async assign(itemId, cid) {
      if (!cid) return;
      await fetch(`/api/items/${itemId}/collections/${cid}`, { method: 'POST' });
      const r = await fetch(`/api/items/${itemId}`);
      if (r.ok) this.selectedItem = await r.json();
      this.loadCollections();
    },
    async unassign(itemId, cid) {
      await fetch(`/api/items/${itemId}/collections/${cid}`, { method: 'DELETE' });
      const r = await fetch(`/api/items/${itemId}`);
      if (r.ok) this.selectedItem = await r.json();
      this.loadCollections();
    },
    onDragItem(it, e) {
      e.dataTransfer.setData('text/dh-item', String(it.id));
      e.dataTransfer.effectAllowed = 'move';
    },
    async onDropToCollection(cid, e) {
      this.dragOver = null;
      const id = e.dataTransfer.getData('text/dh-item');
      if (!id) return;
      await fetch(`/api/items/${id}/collections/${cid}`, { method: 'POST' });
      this.toast(`Added to ${this.collectionName(cid)}`, 'ok');
      this.loadCollections();
      if (this.selectedItem && this.selectedItem.id == id) {
        const r = await fetch(`/api/items/${id}`);
        if (r.ok) this.selectedItem = await r.json();
      }
    },

    openFolderChooser(mode, target) {
      const title = mode === 'author'
        ? `Add ${this.shortAuthor(target.name)} papers to folder`
        : mode === 'subject'
          ? `Add ${target.name} papers to folder`
        : mode === 'result'
          ? 'Add result to folder'
          : 'Add item to folder';
      const newName = (mode === 'author' || mode === 'subject') ? target.name : '';
      this.folderChooser = { open: true, mode, target, title, newName };
    },
    closeFolderChooser() {
      this.folderChooser = { open: false, mode: '', target: null, title: '', newName: '' };
    },
    async chooseFolder(cid) {
      const fc = this.folderChooser;
      if (!fc.open || !cid) return;
      if (fc.mode === 'item') {
        await this.assign(fc.target.id, cid);
        this.toast('Added to folder', 'ok');
      } else if (fc.mode === 'author') {
        await this.bulkAssignAuthor(fc.target.name, { collection_id: cid });
      } else if (fc.mode === 'subject') {
        await this.bulkAssignSubject(fc.target.name, { collection_id: cid });
      } else if (fc.mode === 'result') {
        await this._submitItems([fc.target], cid);
      }
      this.closeFolderChooser();
      await Promise.all([this.loadCollections(), this.loadFacets(), this.loadItems()]);
    },
    async createFolderFromChooser() {
      const fc = this.folderChooser;
      const name = (fc.newName || '').trim();
      if (!fc.open || !name) return;
      if (fc.mode === 'item') {
        await this.bulkAssignItems([fc.target.id], { collection_name: name });
        this.toast('Folder created and item added', 'ok');
      } else if (fc.mode === 'author') {
        await this.bulkAssignAuthor(fc.target.name, { collection_name: name });
      } else if (fc.mode === 'subject') {
        await this.bulkAssignSubject(fc.target.name, { collection_name: name });
      } else if (fc.mode === 'result') {
        const r = await fetch('/api/collections', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        if (r.ok) {
          const c = await r.json();
          await this._submitItems([fc.target], c.id);
        }
      }
      this.closeFolderChooser();
      await Promise.all([this.loadCollections(), this.loadFacets(), this.loadItems()]);
    },
    async bulkAssignAuthor(author, options) {
      if (!author) return;
      const r = await fetch('/api/bulk/assign', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ author, ...options }),
      });
      if (r.ok) {
        const data = await r.json();
        this.toast(`Added ${data.count} item${data.count === 1 ? '' : 's'} to folder`, 'ok');
      } else {
        this.toast('Could not add author list to folder', 'error');
      }
    },
    async bulkAssignSubject(subject, options) {
      if (!subject) return;
      const r = await fetch('/api/bulk/assign', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ subject, ...options }),
      });
      if (r.ok) {
        const data = await r.json();
        this.toast(`Added ${data.count} item${data.count === 1 ? '' : 's'} to folder`, 'ok');
      } else {
        this.toast('Could not add subject list to folder', 'error');
      }
    },
    async createFolderForSelectedAuthor() {
      if (!this.selectedAuthor) return;
      const name = prompt('New folder name:', this.selectedAuthor);
      if (!name) return;
      await this.bulkAssignAuthor(this.selectedAuthor, { collection_name: name });
      await Promise.all([this.loadCollections(), this.loadItems()]);
    },
    async createFolderForSelectedSubject() {
      if (!this.selectedSubject) return;
      const name = prompt('New folder name:', this.selectedSubject);
      if (!name) return;
      await this.bulkAssignSubject(this.selectedSubject, { collection_name: name });
      await Promise.all([this.loadCollections(), this.loadItems()]);
    },
    async bulkAssignItems(itemIds, options) {
      const r = await fetch('/api/bulk/assign', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ item_ids: itemIds, ...options }),
      });
      if (!r.ok) this.toast('Could not add items to folder', 'error');
    },
    async exportAuthorPdfs(author) {
      if (!author) return;
      const dest = prompt(`Export downloaded PDFs by ${this.shortAuthor(author)} to folder path:`);
      if (!dest) return;
      const r = await fetch('/api/bulk/export-pdfs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ author, destination: dest }),
      });
      if (r.ok) {
        const data = await r.json();
        this.toast(`Exported ${data.exported} PDF${data.exported === 1 ? '' : 's'} (${data.skipped} skipped)`, 'ok');
      } else {
        this.toast('PDF export failed', 'error');
      }
    },
    exportAuthorList(author) {
      if (!author) return;
      window.open(`/api/export/list?author=${encodeURIComponent(author)}`, '_blank');
    },
    async exportSubjectPdfs(subject) {
      if (!subject) return;
      const dest = prompt(`Export downloaded PDFs for ${subject} to folder path:`);
      if (!dest) return;
      const r = await fetch('/api/bulk/export-pdfs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ subject, destination: dest }),
      });
      if (r.ok) {
        const data = await r.json();
        this.toast(`Exported ${data.exported} PDF${data.exported === 1 ? '' : 's'} (${data.skipped} skipped)`, 'ok');
      } else {
        this.toast('PDF export failed', 'error');
      }
    },
    exportSubjectList(subject) {
      if (!subject) return;
      window.open(`/api/export/list?subject=${encodeURIComponent(subject)}`, '_blank');
    },

    openItemContext(it, e) {
      this.selectItem(it);
      this.openContext('item', it, e);
    },
    openCollectionContext(collection, e) {
      this.openContext('collection', collection, e);
    },
    openAuthorContext(author, e) {
      this.openContext('author', author, e);
    },
    openSubjectContext(subject, e) {
      this.openContext('subject', subject, e);
    },
    openResultContext(result, e) {
      this.openContext('result', result, e);
    },
    openContext(type, target, e) {
      const width = 244;
      const height = ['author', 'subject'].includes(type) ? 300 : type === 'collection' ? 210 : 320;
      const x = Math.min(e.clientX, window.innerWidth - width - 12);
      const y = Math.min(e.clientY, window.innerHeight - height - 12);
      this.contextMenu = { open: true, x: Math.max(8, x), y: Math.max(8, y), type, target };
    },
    closeContextMenu() {
      this.contextMenu.open = false;
    },
    contextMenuStyle() {
      return `left:${this.contextMenu.x}px; top:${this.contextMenu.y}px;`;
    },
    contextItems() {
      const t = this.contextMenu.target || {};
      if (this.contextMenu.type === 'collection') {
        return [
          { icon: 'O', label: 'Open folder', action: 'collection-open' },
          { icon: 'M', label: 'Open concept map', action: 'collection-graph' },
          { separator: true },
          { icon: 'R', label: 'Rename folder', action: 'collection-rename' },
          { icon: '!', label: 'Delete folder', action: 'collection-delete', danger: true },
        ];
      }
      if (this.contextMenu.type === 'author') {
        return [
          { icon: 'O', label: 'Open author list', action: 'author-open' },
          { icon: 'M', label: 'Open concept map', action: 'author-graph' },
          { separator: true },
          { icon: '+', label: 'Add to folder...', action: 'author-folder' },
          { icon: 'N', label: 'Create folder and add', action: 'author-new-folder' },
          { separator: true },
          { icon: 'P', label: 'Export PDFs to...', action: 'author-export-pdfs' },
          { icon: 'L', label: 'Export list', action: 'author-export-list' },
        ];
      }
      if (this.contextMenu.type === 'subject') {
        return [
          { icon: 'O', label: 'Open subject list', action: 'subject-open' },
          { icon: 'M', label: 'Open concept map', action: 'subject-graph' },
          { icon: 'Q', label: 'Search online', action: 'subject-search' },
          { separator: true },
          { icon: '+', label: 'Add to folder...', action: 'subject-folder' },
          { icon: 'N', label: 'Create folder and add', action: 'subject-new-folder' },
          { separator: true },
          { icon: 'P', label: 'Export PDFs to...', action: 'subject-export-pdfs' },
          { icon: 'L', label: 'Export list', action: 'subject-export-list' },
        ];
      }
      if (this.contextMenu.type === 'result') {
        return [
          { icon: '+', label: 'Add to library', action: 'result-add' },
          { icon: 'F', label: 'Add to folder...', action: 'result-folder' },
          { icon: 'N', label: 'Create folder and add', action: 'result-new-folder' },
          { separator: true },
          { icon: 'D', label: 'Copy DOI', action: 'result-copy-doi', disabled: !t.doi },
        ];
      }
      return [
        { icon: 'O', label: 'Open PDF', action: 'item-open-file', disabled: !t.file_path },
        { icon: 'E', label: 'Open folder in Explorer', action: 'item-open-folder' },
        { icon: 'D', label: t.status === 'downloaded' ? 'Downloaded' : 'Download', action: 'item-download', disabled: !t.doi || t.status === 'downloading' },
        { separator: true },
        { icon: '+', label: 'Add to folder...', action: 'item-folder' },
        { icon: 'N', label: 'Create folder and add', action: 'item-new-folder' },
        { icon: 'X', label: 'Export file to...', action: 'item-export-file', disabled: !t.file_path },
        { separator: true },
        { icon: 'S', label: t.starred ? 'Unstar' : 'Star', action: 'item-star' },
        { icon: '!', label: 'Delete from library', action: 'item-delete', danger: true },
      ];
    },
    async runContextAction(action) {
      const target = this.contextMenu.target;
      this.closeContextMenu();
      if (!target || action === undefined) return;

      if (action === 'item-open-file') return this.openItemFile(target);
      if (action === 'item-open-folder') return this.openItemFolder(target);
      if (action === 'item-download') return this.downloadItem(target);
      if (action === 'item-folder') return this.openFolderChooser('item', target);
      if (action === 'item-new-folder') {
        const name = prompt('New folder name:', target.title || '');
        if (name) {
          await this.bulkAssignItems([target.id], { collection_name: name });
          await this.loadCollections();
          this.toast('Folder created and item added', 'ok');
        }
        return;
      }
      if (action === 'item-export-file') return this.exportItemFile(target);
      if (action === 'item-star') return this.toggleStar(target);
      if (action === 'item-delete') return this.deleteItem(target);

      if (action === 'collection-open') return this.selectCollection(target.id);
      if (action === 'collection-graph') return this.openGraph({ collection_id: target.id, label: target.name });
      if (action === 'collection-rename') return this.renameCollectionPrompt(target);
      if (action === 'collection-delete') return this.deleteCollectionConfirm(target);

      if (action === 'author-open') return this.selectAuthor(target.name);
      if (action === 'author-graph') return this.openGraph({ author: target.name, label: this.shortAuthor(target.name) });
      if (action === 'author-folder') return this.openFolderChooser('author', target);
      if (action === 'author-new-folder') {
        const name = prompt('New folder name:', target.name);
        if (name) {
          await this.bulkAssignAuthor(target.name, { collection_name: name });
          await this.loadCollections();
        }
        return;
      }
      if (action === 'author-export-pdfs') return this.exportAuthorPdfs(target.name);
      if (action === 'author-export-list') return this.exportAuthorList(target.name);

      if (action === 'subject-open') return this.selectSubject(target.name);
      if (action === 'subject-graph') return this.openGraph({ subject: target.name, focus: target.name, label: target.name });
      if (action === 'subject-search') {
        this.searchKind = 'article';
        this.modalQuery = target.name;
        this.selectVirtual('discover');
        return this.runSearch(true);
      }
      if (action === 'subject-folder') return this.openFolderChooser('subject', target);
      if (action === 'subject-new-folder') {
        const name = prompt('New folder name:', target.name);
        if (name) {
          await this.bulkAssignSubject(target.name, { collection_name: name });
          await this.loadCollections();
        }
        return;
      }
      if (action === 'subject-export-pdfs') return this.exportSubjectPdfs(target.name);
      if (action === 'subject-export-list') return this.exportSubjectList(target.name);

      if (action === 'result-add') return this.addOne(target);
      if (action === 'result-folder') return this.openFolderChooser('result', target);
      if (action === 'result-new-folder') {
        const name = prompt('New folder name:', this.searchKind === 'author' ? this.modalQuery : '');
        if (name) {
          const r = await fetch('/api/collections', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ name }),
          });
          if (r.ok) {
            const c = await r.json();
            await this._submitItems([target], c.id);
            await this.loadCollections();
          }
        }
        return;
      }
      if (action === 'result-copy-doi' && target.doi) {
        await navigator.clipboard?.writeText(target.doi);
        this.toast('DOI copied', 'ok');
      }
    },

    toast(text, kind = 'info', ttl = 3500) {
      const t = { id: Math.random().toString(36).slice(2), text, kind };
      this.toasts.push(t);
      setTimeout(() => {
        this.toasts = this.toasts.filter(x => x.id !== t.id);
      }, ttl);
    },
    connectWS() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${location.host}/ws/jobs`);
      this.ws = ws;
      ws.onmessage = async (ev) => {
        const m = JSON.parse(ev.data);
        if (m.event === 'item_added') {
          this.toast(`Added: ${m.title}`, 'ok');
          this.loadItems();
          this.loadStats();
          this.loadFacets();
        } else if (m.event === 'item_changed' || m.event === 'done') {
          this.loadItems();
          this.loadStats();
          if (this.selectedItem && this.selectedItem.id === m.id) {
            const r = await fetch(`/api/items/${m.id}`);
            if (r.ok) this.selectedItem = await r.json();
          }
        } else if (m.event === 'source') {
          this.toast(`Source: ${m.source}`, 'info', 2000);
        } else if (m.event === 'source_failed') {
          this.toast(`Trying next source: ${m.source} failed`, 'info', 2500);
        } else if (m.event === 'failed') {
          this.toast(m.error || 'Download failed', 'error');
          this.loadItems();
        } else if (m.event === 'batch_done') {
          this.toast(`Batch finished (${m.total})`, 'ok');
          this.loadCollections();
          this.loadFacets();
          this.loadStats();
        }
      };
      ws.onclose = () => setTimeout(() => this.connectWS(), 2000);
      ws.onerror = () => ws.close();
    },
  };
}
