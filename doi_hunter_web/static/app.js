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
      hoverNode: null,
      scope: { label: 'All subjects', items: 0 },
      request: {},
      focus: '',
      depth: 1,
      limit: 60,
      anchorYear: new Date().getFullYear(),
      communities: 0,
      width: 2400,
      height: 1600,
      zoom: 1,
      panX: 0,
      panY: 0,
      minWeight: 1,
      maxWeight: 1,
      neighborOnly: false,
      search: '',
      drag: null,
      pan: null,
      animFrame: null,
      tick: 0,
    },
    showAuthors: true,
    showSubjects: true,
    authorLimit: 12,
    subjectLimit: 12,
    searchQuery: '',

    searchKind: 'article',
    modalQuery: '',
    modalResults: [],
    selectedResult: null,
    modalTotal: 0,
    modalOffset: 0,
    modalRows: 25,
    modalSort: 'pertinence',
    modalExact: false,
    modalTitleContains: false,
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
    storage: {
      loading: false,
      library_root: '',
      papers_dir: '',
      default_google_drive: '',
      google_drive_candidates: [],
      using_google_drive_for_papers: false,
    },
    googleDrive: {
      loading: false,
      available: false,
      connected: false,
      client_configured: false,
      token_exists: false,
      client_config_path: '',
      project_id: '',
      project_number: '',
      drive_api_url: '',
      folder_id: '',
      folder_name: '',
      folder_path: '',
      auto_upload: false,
      user: {},
      quota: {},
      error: '',
      error_detail: '',
      client_type: '',
      oauth_redirect_uri: '',
      oauth_redirect_ready: false,
      oauth_message: '',
      redirect_uris: [],
      scopes: [],
      folders: [],
    },
    toasts: [],
    openAdd: false,
    openSettings: false,
    settings: {
      downloadPath: '',
      exportRoot: '',
      migrateExisting: true,
      picking: '',
      googleClientSecretPath: '',
      driveFolderId: '',
      driveFolderPath: 'ReviewHunter',
      driveFolderSearch: '',
      driveAutoUpload: false,
    },
    addInput: '',
    autoDownload: true,
    addCollectionId: '',
    dropping: false,
    dragOver: null,
    ws: null,

    contextMenu: { open: false, x: 0, y: 0, type: '', target: null },
    folderChooser: { open: false, mode: '', target: null, title: '', newName: '' },

    async init() {
      this._initGraphHandlers();
      await Promise.all([this.loadStats(), this.loadCollections(), this.loadFacets(), this.loadStorage(), this.loadGoogleDriveStatus()]);
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
    async loadStorage() {
      const r = await fetch('/api/storage');
      if (r.ok) {
        this.storage = { ...this.storage, ...(await r.json()) };
        if (!this.settings.downloadPath) this.settings.downloadPath = this.drivePapersSuggestion();
        if (!this.settings.exportRoot) this.settings.exportRoot = this.driveExportSuggestion();
      }
    },
    async loadGoogleDriveStatus() {
      const r = await fetch('/api/google-drive/status');
      if (!r.ok) return;
      const data = await r.json();
      this.googleDrive = { ...this.googleDrive, ...data };
      this.settings.googleClientSecretPath = data.client_config_path || this.settings.googleClientSecretPath || '';
      this.settings.driveFolderId = data.folder_id || '';
      this.settings.driveFolderPath = data.folder_path || data.folder_name || this.settings.driveFolderPath || 'ReviewHunter';
      this.settings.driveAutoUpload = !!data.auto_upload;
    },
    async loadCollections() {
      const r = await fetch('/api/collections');
      if (r.ok) this.collections = await r.json();
    },
    async loadFacets() {
      const r = await fetch('/api/facets');
      if (r.ok) this.facets = await r.json();
    },
    joinPath(base, ...parts) {
      const root = String(base || '').trim().replace(/[\\/]+$/, '');
      if (!root) return parts.map(p => String(p || '').replace(/^[\\/]+|[\\/]+$/g, '')).filter(Boolean).join('/');
      const sep = root.includes('\\') ? '\\' : '/';
      const tail = parts
        .map(p => String(p || '').replace(/^[\\/]+|[\\/]+$/g, ''))
        .filter(Boolean);
      return [root, ...tail].join(sep);
    },
    drivePapersSuggestion(root = '') {
      const base = root || this.storage.default_google_drive || '';
      if (base) return this.joinPath(base, 'ReviewHunter', 'papers');
      return this.storage.papers_dir || this.stats.papers_path || '';
    },
    driveExportSuggestion(root = '') {
      return root || this.storage.default_google_drive || this.storage.library_root || this.stats.library_path || '';
    },
    openSettingsDialog() {
      this.closeContextMenu();
      this.settings.downloadPath = this.drivePapersSuggestion();
      this.settings.exportRoot = this.driveExportSuggestion();
      this.settings.migrateExisting = true;
      this.openSettings = true;
      this.loadGoogleDriveStatus();
    },
    closeSettingsDialog() {
      this.openSettings = false;
      this.settings.picking = '';
    },
    selectDriveCandidate(path) {
      if (!path) return;
      this.settings.downloadPath = this.drivePapersSuggestion(path);
      this.settings.exportRoot = path;
    },
    async pickStorageFolder(target = 'download') {
      const isExport = target === 'export';
      const initial = isExport
        ? (this.settings.exportRoot || this.storage.default_google_drive || this.storage.library_root || '')
        : (this.settings.downloadPath || this.storage.default_google_drive || this.storage.papers_dir || '');
      this.settings.picking = target;
      try {
        const params = new URLSearchParams({
          title: isExport ? 'Select export folder' : 'Select download folder',
          initial,
        });
        const r = await fetch(`/api/storage/pick-folder?${params}`);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Folder picker unavailable');
        if (data.path) {
          if (isExport) this.settings.exportRoot = data.path;
          else this.settings.downloadPath = data.path;
        }
      } catch (e) {
        this.toast(e.message || 'Folder picker unavailable', 'error', 6000);
      } finally {
        this.settings.picking = '';
      }
    },
    async pickGoogleClientSecret() {
      this.settings.picking = 'google-client';
      try {
        const params = new URLSearchParams({ initial: this.settings.googleClientSecretPath || '' });
        const r = await fetch(`/api/google-drive/pick-client-secret?${params}`);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'File picker unavailable');
        if (data.path) this.settings.googleClientSecretPath = data.path;
      } catch (e) {
        this.toast(e.message || 'File picker unavailable', 'error', 6000);
      } finally {
        this.settings.picking = '';
      }
    },
    async connectGoogleDriveApi() {
      this.googleDrive.loading = true;
      try {
        const r = await fetch('/api/google-drive/connect', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ client_secret_path: this.settings.googleClientSecretPath || null }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Google Drive connection failed');
        this.googleDrive = { ...this.googleDrive, ...data };
        this.settings.driveFolderId = data.folder_id || '';
        this.settings.driveFolderPath = data.folder_path || data.folder_name || 'ReviewHunter';
        this.settings.driveAutoUpload = !!data.auto_upload;
        if (!data.connected) {
          const msg = data.error || (data.token_exists ? 'OAuth is authorized, but Google Drive is not ready.' : 'Google Drive is not connected.');
          this.toast(msg, 'error', 10000);
          return;
        }
        this.toast('Google Drive connected', 'ok', 5000);
      } catch (e) {
        this.toast(e.message || 'Google Drive connection failed', 'error', 8000);
        await this.loadGoogleDriveStatus();
      } finally {
        this.googleDrive.loading = false;
      }
    },
    async disconnectGoogleDriveApi() {
      if (!confirm('Disconnect Google Drive from ReviewHunter?')) return;
      this.googleDrive.loading = true;
      try {
        const r = await fetch('/api/google-drive/disconnect', { method: 'POST' });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Could not disconnect Google Drive');
        this.googleDrive = { ...this.googleDrive, ...data, folders: [] };
        this.toast('Google Drive disconnected', 'ok');
      } catch (e) {
        this.toast(e.message || 'Could not disconnect Google Drive', 'error');
      } finally {
        this.googleDrive.loading = false;
      }
    },
    async searchGoogleDriveFolders() {
      if (!this.googleDrive.connected) {
        this.toast('Connect Google Drive first', 'error');
        return;
      }
      this.googleDrive.loading = true;
      try {
        const params = new URLSearchParams({ q: this.settings.driveFolderSearch || '', limit: 25 });
        const r = await fetch(`/api/google-drive/folders?${params}`);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Drive folder search failed');
        this.googleDrive.folders = data.folders || [];
      } catch (e) {
        this.toast(e.message || 'Drive folder search failed', 'error');
      } finally {
        this.googleDrive.loading = false;
      }
    },
    selectGoogleDriveFolder(folder) {
      this.settings.driveFolderId = folder.id || '';
      this.settings.driveFolderPath = folder.name || '';
    },
    async saveGoogleDriveFolder() {
      if (!this.googleDrive.connected) {
        this.toast('Connect Google Drive first', 'error');
        return;
      }
      this.googleDrive.loading = true;
      try {
        const r = await fetch('/api/google-drive/folder', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            folder_id: this.settings.driveFolderId || null,
            folder_path: this.settings.driveFolderId ? null : (this.settings.driveFolderPath || 'ReviewHunter'),
            auto_upload: this.settings.driveAutoUpload,
          }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Could not save Drive folder');
        this.googleDrive = { ...this.googleDrive, ...data };
        this.settings.driveFolderId = data.folder_id || this.settings.driveFolderId;
        this.settings.driveFolderPath = data.folder_path || data.folder_name || this.settings.driveFolderPath;
        this.toast('Drive folder saved', 'ok');
      } catch (e) {
        this.toast(e.message || 'Could not save Drive folder', 'error', 7000);
      } finally {
        this.googleDrive.loading = false;
      }
    },
    async uploadDownloadedToDriveApi() {
      if (!this.googleDrive.connected) {
        this.toast('Connect Google Drive first', 'error');
        return;
      }
      this.googleDrive.loading = true;
      try {
        const r = await fetch('/api/google-drive/upload-downloaded', { method: 'POST' });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Drive upload failed');
        this.toast(`Uploaded ${data.uploaded} file${data.uploaded === 1 ? '' : 's'} to Drive`, data.failed ? 'info' : 'ok', 6500);
        await Promise.all([this.loadItems(), this.loadGoogleDriveStatus()]);
      } catch (e) {
        this.toast(e.message || 'Drive upload failed', 'error', 7000);
      } finally {
        this.googleDrive.loading = false;
      }
    },
    async exportLibraryToDriveApi() {
      if (!this.googleDrive.connected) {
        this.toast('Connect Google Drive first', 'error');
        return;
      }
      this.googleDrive.loading = true;
      try {
        const r = await fetch('/api/google-drive/export-library', { method: 'POST' });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Drive export failed');
        this.toast(`Exported backup to Drive (${data.upload?.files || 0} files)`, 'ok', 6500);
      } catch (e) {
        this.toast(e.message || 'Drive export failed', 'error', 7000);
      } finally {
        this.googleDrive.loading = false;
      }
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
    // Visual size of a node card in world units. Width scales with item count;
    // the height stays roughly constant because labels wrap to two lines max.
    _nodeCardWidth(count) {
      return Math.max(120, Math.min(220, 110 + Math.sqrt(Math.max(1, count)) * 16));
    },
    _nodeCardHeight(_count) { return 56; },
    _nodeBoundingRadius(node) {
      const w = node.w || this._nodeCardWidth(node.count);
      const h = node.h || this._nodeCardHeight(node.count);
      // Diagonal-half + padding gives a comfortable repulsion radius.
      return Math.sqrt((w / 2) ** 2 + (h / 2) ** 2) + 8;
    },

    layoutGraph(data) {
      const rawNodes = [...(data.nodes || [])];
      if (rawNodes.length === 0) {
        this.graph.nodes = [];
        this.graph.edges = [];
        this.graph.scope = data.scope || { label: 'All subjects', items: 0 };
        this.graph.selectedNode = null;
        return;
      }
      // Scale the world by node count so the same density target holds whether
      // we render 30 nodes or 90.
      const N0 = rawNodes.length;
      const factor = Math.max(1, Math.sqrt(N0 / 32));
      this.graph.width = Math.round(2200 * factor);
      this.graph.height = Math.round(1500 * factor);
      const W = this.graph.width;
      const H = this.graph.height;
      const cx = W / 2;
      const cy = H / 2;
      const focusId = rawNodes.find(n => n.focused)?.id || rawNodes[0]?.id || '';

      // Sunflower seed within an inscribed disc — never starts outside the
      // canvas, never collinear, gives the simulation a clean initial state.
      const golden = Math.PI * (3 - Math.sqrt(5));
      const seedRadius = Math.min(W, H) * 0.42;
      const N = rawNodes.length;
      const nodes = rawNodes.map((n, i) => {
        const w = this._nodeCardWidth(n.count);
        const h = this._nodeCardHeight(n.count);
        const node = { ...n, w, h, r: 0, vx: 0, vy: 0, pinned: false };
        node.r = this._nodeBoundingRadius(node);
        if (n.id === focusId) {
          node.x = cx; node.y = cy;
        } else {
          const t = i + 1;
          const frac = Math.sqrt(t / N);
          const radius = frac * seedRadius;
          const angle = t * golden;
          node.x = cx + Math.cos(angle) * radius;
          node.y = cy + Math.sin(angle) * radius;
        }
        return node;
      });

      const byId = new Map(nodes.map(n => [n.id, n]));
      const edgesRaw = (data.edges || []).filter(e => byId.has(e.source) && byId.has(e.target));
      const maxWeight = Math.max(1, ...edgesRaw.map(e => e.weight || 1));
      const edges = edgesRaw.map(e => ({
        ...e,
        sourceNode: byId.get(e.source),
        targetNode: byId.get(e.target),
      }));

      this.runForceLayout(nodes, edges, focusId);
      // Alternate fit-and-collide so neither step undoes the other; the
      // collision pass has its own corner-unstick fallback for stubborn cases.
      for (let i = 0; i < 8; i++) {
        this._fitInsideWorld(nodes);
        this._resolveCollisions(nodes, focusId);
      }
      this._fitInsideWorld(nodes);

      const selectedId = this.graph.selectedNode?.id;
      this.graph.nodes = nodes;
      this.graph.edges = edges;
      this.graph.maxWeight = maxWeight;
      this.graph.minWeight = 1;
      this.graph.anchorYear = data.anchor_year || this.graph.anchorYear;
      this.graph.communities = data.communities || 0;
      this.graph.scope = data.scope || { label: 'All subjects', items: 0 };
      this.graph.selectedNode =
        nodes.find(n => n.id === selectedId) || nodes.find(n => n.focused) || nodes[0] || null;
      this.graph.tick++;
      this._fitGraphToView();
    },

    // Fruchterman-Reingold force layout with weight-scaled attraction. We use
    // a SOFT margin push instead of a hard clamp, so nodes drifting outside
    // are nudged inward instead of snapping to a boundary line and piling up.
    runForceLayout(nodes, edges, focusId) {
      const W = this.graph.width;
      const H = this.graph.height;
      const cx = W / 2;
      const cy = H / 2;
      const N = nodes.length;
      if (N === 0) return;
      const area = W * H;
      const k = Math.sqrt(area / N) * 0.7;
      const k2 = k * k;
      const iterations = 420;
      let temperature = Math.min(W, H) / 8;
      const cool = temperature / iterations;
      const maxWeight = Math.max(1, ...edges.map(e => e.weight || 1));

      for (let step = 0; step < iterations; step++) {
        // Repulsion — boosted at very short ranges so cards refuse to coincide.
        for (let i = 0; i < N; i++) {
          const u = nodes[i];
          let fx = 0, fy = 0;
          for (let j = 0; j < N; j++) {
            if (i === j) continue;
            const v = nodes[j];
            let dx = u.x - v.x;
            let dy = u.y - v.y;
            let d2 = dx * dx + dy * dy;
            if (d2 < 0.01) {
              dx = (Math.random() - 0.5) * 2;
              dy = (Math.random() - 0.5) * 2;
              d2 = dx * dx + dy * dy + 0.01;
            }
            const dist = Math.sqrt(d2);
            let f = k2 / dist;
            // Hard short-range push when the bounding circles would overlap.
            const minDist = u.r + v.r;
            if (dist < minDist) f += (minDist - dist) * 6;
            fx += (dx / dist) * f;
            fy += (dy / dist) * f;
          }
          u._fx = fx;
          u._fy = fy;
        }

        // Attraction along edges, scaled by weight so heavier links pull harder.
        for (const e of edges) {
          const a = e.sourceNode;
          const b = e.targetNode;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const dist = Math.max(0.1, Math.sqrt(dx * dx + dy * dy));
          const w = (e.weight || 1) / maxWeight;
          const f = (dist * dist) / k * (0.5 + 0.9 * w);
          const ux = (dx / dist) * f;
          const uy = (dy / dist) * f;
          a._fx -= ux;
          a._fy -= uy;
          b._fx += ux;
          b._fy += uy;
        }

        for (let i = 0; i < N; i++) {
          const u = nodes[i];
          if (u.id === focusId) { u.x = cx; u.y = cy; continue; }
          // Centripetal pull keeps disconnected components from drifting.
          u._fx += (cx - u.x) * 0.05;
          u._fy += (cy - u.y) * 0.05;
          // Strong, quadratic boundary force prevents nodes from piling up
          // against the walls when many small components repel each other.
          const margin = u.r + 40;
          if (u.x < margin) {
            const d = margin - u.x;
            u._fx += d * 1.6 + d * d * 0.012;
          }
          if (u.x > W - margin) {
            const d = u.x - (W - margin);
            u._fx -= d * 1.6 + d * d * 0.012;
          }
          if (u.y < margin) {
            const d = margin - u.y;
            u._fy += d * 1.6 + d * d * 0.012;
          }
          if (u.y > H - margin) {
            const d = u.y - (H - margin);
            u._fy -= d * 1.6 + d * d * 0.012;
          }
          const mag = Math.sqrt(u._fx * u._fx + u._fy * u._fy);
          if (mag > 0) {
            const limit = Math.min(mag, temperature);
            u.x += (u._fx / mag) * limit;
            u.y += (u._fy / mag) * limit;
          }
        }
        temperature -= cool;
      }
      for (const n of nodes) { delete n._fx; delete n._fy; }
    },

    // Bounding-box collision resolution. Iterates until no card overlaps any
    // other; pushes pairs apart along the shorter overlap axis. This is the
    // safety net that guarantees readability.
    _resolveCollisions(nodes, focusId) {
      const N = nodes.length;
      const W = this.graph.width;
      const H = this.graph.height;
      const gap = 8;
      // A node is "stuck" on a wall if it's at the fit-clamp boundary; pushing
      // it further only triggers fit-vs-collide oscillation, so we skip it.
      const stuckLeft = (n) => n.x <= n.w / 2 + 12.5;
      const stuckRight = (n) => n.x >= W - n.w / 2 - 12.5;
      const stuckTop = (n) => n.y <= n.h / 2 + 12.5;
      const stuckBot = (n) => n.y >= H - n.h / 2 - 12.5;
      for (let pass = 0; pass < 80; pass++) {
        let moved = false;
        for (let i = 0; i < N; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < N; j++) {
            const b = nodes[j];
            const aw = a.w / 2 + gap / 2;
            const ah = a.h / 2 + gap / 2;
            const bw = b.w / 2 + gap / 2;
            const bh = b.h / 2 + gap / 2;
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const overlapX = (aw + bw) - Math.abs(dx);
            const overlapY = (ah + bh) - Math.abs(dy);
            if (overlapX <= 0 || overlapY <= 0) continue;
            const aFocus = a.id === focusId;
            const bFocus = b.id === focusId;
            if (overlapX < overlapY) {
              const sign = dx >= 0 ? 1 : -1;
              // sign>0 means b is right of a; pushing a left, b right.
              const aFixed = aFocus || (sign > 0 ? stuckLeft(a) : stuckRight(a));
              const bFixed = bFocus || (sign > 0 ? stuckRight(b) : stuckLeft(b));
              if (aFixed && !bFixed) {
                b.x = a.x + sign * (aw + bw);
              } else if (bFixed && !aFixed) {
                a.x = b.x - sign * (aw + bw);
              } else if (!aFixed && !bFixed) {
                const push = overlapX / 2;
                a.x -= sign * push;
                b.x += sign * push;
              } else {
                // Both fixed on X — split along Y, otherwise nudge the
                // non-focused node toward the world centre to break the lock.
                const sy = dy >= 0 ? 1 : -1;
                const aFixedY = aFocus || (sy > 0 ? stuckTop(a) : stuckBot(a));
                const bFixedY = bFocus || (sy > 0 ? stuckBot(b) : stuckTop(b));
                if (!aFixedY || !bFixedY) {
                  if (!aFixedY) a.y -= sy * (overlapY / 2);
                  if (!bFixedY) b.y += sy * (overlapY / 2);
                } else {
                  this._unstickToward(a.id === focusId ? b : a, W, H, focusId);
                }
              }
            } else {
              const sign = dy >= 0 ? 1 : -1;
              const aFixed = aFocus || (sign > 0 ? stuckTop(a) : stuckBot(a));
              const bFixed = bFocus || (sign > 0 ? stuckBot(b) : stuckTop(b));
              if (aFixed && !bFixed) {
                b.y = a.y + sign * (ah + bh);
              } else if (bFixed && !aFixed) {
                a.y = b.y - sign * (ah + bh);
              } else if (!aFixed && !bFixed) {
                const push = overlapY / 2;
                a.y -= sign * push;
                b.y += sign * push;
              } else {
                const sx = dx >= 0 ? 1 : -1;
                const aFixedX = aFocus || (sx > 0 ? stuckLeft(a) : stuckRight(a));
                const bFixedX = bFocus || (sx > 0 ? stuckRight(b) : stuckLeft(b));
                if (!aFixedX || !bFixedX) {
                  if (!aFixedX) a.x -= sx * (overlapX / 2);
                  if (!bFixedX) b.x += sx * (overlapX / 2);
                } else {
                  this._unstickToward(a.id === focusId ? b : a, W, H, focusId);
                }
              }
            }
            moved = true;
          }
        }
        if (!moved) break;
      }
    },

    _unstickToward(node, W, H, focusId) {
      if (!node || node.id === focusId) return;
      const tx = W / 2 - node.x;
      const ty = H / 2 - node.y;
      const norm = Math.sqrt(tx * tx + ty * ty) || 1;
      const step = 12;
      node.x += (tx / norm) * step;
      node.y += (ty / norm) * step;
    },

    _fitInsideWorld(nodes) {
      const W = this.graph.width;
      const H = this.graph.height;
      for (const n of nodes) {
        const mx = n.w / 2 + 12;
        const my = n.h / 2 + 12;
        n.x = Math.max(mx, Math.min(W - mx, n.x));
        n.y = Math.max(my, Math.min(H - my, n.y));
      }
    },

    // Live, low-iteration simulation while a node is being dragged. We re-run
    // the bounding-box collision pass each frame so nothing overlaps the
    // dragged node either.
    relaxOnce() {
      const nodes = this.graph.nodes;
      const edges = this.graph.edges;
      const N = nodes.length;
      if (N === 0) return;
      const W = this.graph.width;
      const H = this.graph.height;
      const cx = W / 2;
      const cy = H / 2;
      const k = Math.sqrt((W * H) / N) * 0.7;
      const k2 = k * k;
      const maxWeight = Math.max(1, ...edges.map(e => e.weight || 1));
      const focusId = nodes.find(n => n.focused)?.id || '';
      for (let i = 0; i < N; i++) {
        const u = nodes[i];
        if (u.pinned) continue;
        let fx = 0, fy = 0;
        for (let j = 0; j < N; j++) {
          if (i === j) continue;
          const v = nodes[j];
          let dx = u.x - v.x;
          let dy = u.y - v.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = 0.5; dy = 0.5; d2 = 0.5; }
          const dist = Math.sqrt(d2);
          let f = k2 / dist;
          const minDist = u.r + v.r;
          if (dist < minDist) f += (minDist - dist) * 6;
          fx += (dx / dist) * f;
          fy += (dy / dist) * f;
        }
        u._fx = fx;
        u._fy = fy;
      }
      for (const e of edges) {
        const a = e.sourceNode, b = e.targetNode;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.max(0.1, Math.sqrt(dx * dx + dy * dy));
        const w = (e.weight || 1) / maxWeight;
        const f = (dist * dist) / k * (0.5 + 0.9 * w);
        const ux = (dx / dist) * f, uy = (dy / dist) * f;
        if (!a.pinned) { a._fx -= ux; a._fy -= uy; }
        if (!b.pinned) { b._fx += ux; b._fy += uy; }
      }
      for (const u of nodes) {
        if (u.pinned) continue;
        u._fx += (cx - u.x) * 0.05;
        u._fy += (cy - u.y) * 0.05;
        const margin = u.r + 40;
        if (u.x < margin) { const d = margin - u.x; u._fx += d * 1.6 + d * d * 0.012; }
        if (u.x > W - margin) { const d = u.x - (W - margin); u._fx -= d * 1.6 + d * d * 0.012; }
        if (u.y < margin) { const d = margin - u.y; u._fy += d * 1.6 + d * d * 0.012; }
        if (u.y > H - margin) { const d = u.y - (H - margin); u._fy -= d * 1.6 + d * d * 0.012; }
        const mag = Math.sqrt(u._fx * u._fx + u._fy * u._fy);
        if (mag > 0) {
          const limit = Math.min(mag, 24);
          u.x += (u._fx / mag) * limit;
          u.y += (u._fy / mag) * limit;
        }
        delete u._fx;
        delete u._fy;
      }
      // Single collision pass keeps the dragged node from being eaten visually.
      this._resolveCollisions(nodes, focusId);
      this._fitInsideWorld(nodes);
      this.graph.tick++;
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
      this.selectedResult = null;
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
      this.selectedResult = null;
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
      this.selectedResult = null;
      this.loadItems();
    },
    selectAuthor(name) {
      this.virtual = 'author';
      this.selectedAuthor = name;
      this.selectedCollectionId = null;
      this.selectedSubject = null;
      this.selectedResult = null;
      this.searchQuery = '';
      this.loadItems();
    },
    selectSubject(name) {
      this.virtual = 'subject';
      this.selectedSubject = name;
      this.selectedCollectionId = null;
      this.selectedAuthor = null;
      this.selectedResult = null;
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
      this.selectedResult = null;
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
    resultWebsiteUrl(r) {
      if (!r) return '';
      return r.landing_url || r.url || (r.doi ? `https://doi.org/${r.doi}` : '') || r.pdf_url || '';
    },
    selectResult(r) {
      this.selectedResult = r;
      this.selectedItem = null;
      this.graph.selectedNode = null;
    },
    isSelectedResult(r) {
      if (!this.selectedResult || !r) return false;
      return this.resultKey(this.selectedResult, 0) === this.resultKey(r, 0);
    },
    formatAuthors(authors) {
      if (!authors || authors.length === 0) return '-';
      const surnames = authors.map(a => String(a).split(',')[0].trim()).filter(Boolean);
      if (surnames.length === 0) return '-';
      if (surnames.length === 1) return surnames[0];
      if (surnames.length === 2) return `${surnames[0]} & ${surnames[1]}`;
      return `${surnames[0]} et al.`;
    },
    formatBytes(n) {
      n = Number(n || 0);
      if (!n) return '';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let i = 0;
      while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
      return `${n >= 10 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
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
    // ----- Concept map: visual helpers -------------------------------------
    graphCommunityHue(community) {
      // 10 visually distinct hues spaced around the wheel; higher-index
      // communities cycle but stay visually separable thanks to saturation.
      const hues = [202, 268, 14, 158, 332, 42, 188, 92, 248, 320];
      return hues[(community || 0) % hues.length];
    },
    graphCommunityColor(community, alpha = 1) {
      const h = this.graphCommunityHue(community);
      return `hsla(${h}, 62%, 56%, ${alpha})`;
    },
    graphFilteredEdges() {
      const min = this.graph.minWeight || 1;
      return (this.graph.edges || []).filter(e => (e.weight || 1) >= min);
    },
    graphIsHighlighted(node) {
      const sel = this.graph.selectedNode;
      const hov = this.graph.hoverNode;
      if (this.graph.search) {
        const q = this.graph.search.toLowerCase();
        if ((node.label || '').toLowerCase().includes(q)) return true;
      }
      if (!sel && !hov) return !this.graph.search;
      const target = hov || sel;
      if (target && node.id === target.id) return true;
      return (this.graph.edges || []).some(e =>
        (e.source === target.id && e.target === node.id) ||
        (e.target === target.id && e.source === node.id),
      );
    },
    graphIsDimmed(node) {
      if (!this.graph.selectedNode && !this.graph.hoverNode && !this.graph.search) return false;
      return !this.graphIsHighlighted(node);
    },
    graphEdgePath(edge) {
      const a = edge.sourceNode;
      const b = edge.targetNode;
      if (!a || !b) return '';
      // Quadratic curve with a perpendicular offset proportional to length so
      // densely-connected pairs read as two distinct strokes.
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const offset = Math.min(80, dist * 0.12);
      const mx = (a.x + b.x) / 2 + (-dy / dist) * offset;
      const my = (a.y + b.y) / 2 + (dx / dist) * offset;
      return `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`;
    },
    graphEdgeWidth(edge) {
      const max = this.graph.maxWeight || 1;
      const w = (edge.weight || 1) / max;
      return 1.2 + Math.sqrt(w) * 5.4;
    },
    graphEdgeOpacity(edge) {
      const sel = this.graph.selectedNode;
      const hov = this.graph.hoverNode;
      const target = hov || sel;
      if (!target) return 0.32;
      const touches = edge.source === target.id || edge.target === target.id;
      return touches ? 0.85 : 0.07;
    },
    graphEdgeStroke(edge) {
      // Older shared work fades cool, newer trends warm.
      const anchor = this.graph.anchorYear || new Date().getFullYear();
      const recent = edge.recent;
      if (!recent) return 'hsla(220, 12%, 60%, 1)';
      const age = Math.max(0, anchor - recent);
      const heat = Math.max(0, Math.min(1, 1 - age / 12));
      const hue = 220 - heat * 200; // 220 (cool blue) to ~20 (warm orange)
      return `hsla(${hue}, ${50 + heat * 30}%, ${60 - heat * 8}%, 1)`;
    },
    graphEdgesMarkup() {
      const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      })[ch]);
      const sel = this.graph.selectedNode;
      const hov = this.graph.hoverNode;
      const target = hov || sel;
      const edges = this.graphFilteredEdges();
      // Render highlighted edges last so they paint on top.
      const ordered = edges.slice().sort((x, y) => {
        const xt = target && (x.source === target.id || x.target === target.id) ? 1 : 0;
        const yt = target && (y.source === target.id || y.target === target.id) ? 1 : 0;
        return xt - yt;
      });
      return ordered.map(edge => {
        const path = this.graphEdgePath(edge);
        const stroke = this.graphEdgeStroke(edge);
        const width = this.graphEdgeWidth(edge);
        const opacity = this.graphEdgeOpacity(edge);
        const recent = edge.recent ? ` · most recent ${edge.recent}` : '';
        return (
          `<path d="${path}" stroke="${stroke}" stroke-width="${width}" ` +
          `opacity="${opacity}" fill="none" stroke-linecap="round" class="graph-edge">` +
          `<title>${esc(edge.source)} ↔ ${esc(edge.target)} · ${edge.weight} shared item${edge.weight === 1 ? '' : 's'}${recent}</title>` +
          `</path>`
        );
      }).join('');
    },
    graphNodeCardClass(node) {
      const sel = this.graph.selectedNode;
      const classes = ['graph-node-card'];
      if (sel && sel.id === node.id) classes.push('selected');
      else if (node.focused) classes.push('focused');
      if (this.graphIsDimmed(node)) classes.push('dimmed');
      if (this.graphIsHighlighted(node) && (sel || this.graph.hoverNode)) classes.push('highlighted');
      return classes.join(' ');
    },
    graphNodeCardStyle(node) {
      const size = node.w || this._nodeCardWidth(node.count);
      const left = (node.x / this.graph.width) * 100;
      const top = (node.y / this.graph.height) * 100;
      const accent = this.graphCommunityColor(node.community, 0.95);
      const tint = this.graphCommunityColor(node.community, 0.14);
      return `left:${left}%; top:${top}%; width:${size}px; --node-accent:${accent}; --node-tint:${tint};`;
    },
    graphMomentumLabel(node) {
      const m = node.momentum || 0;
      if (m >= 0.66) return 'Trending';
      if (m >= 0.33) return 'Active';
      if (m > 0) return 'Steady';
      return 'Archive';
    },
    graphYearLabel(node) {
      if (!node.year_min && !node.year_max) return '';
      if (node.year_min === node.year_max) return String(node.year_max);
      return `${node.year_min}–${node.year_max}`;
    },
    graphTopAuthor(node) {
      const a = (node.top_authors || [])[0];
      if (!a) return '';
      return this.shortAuthor(a);
    },
    graphTopAuthors(node) {
      return (node.top_authors || []).map(a => this.shortAuthor(a));
    },
    graphTransform() {
      const z = this.graph.zoom;
      // translate(-50%,-50%) centers the world; pan/scale layered on top.
      return `translate(-50%, -50%) translate(${this.graph.panX}px, ${this.graph.panY}px) scale(${z})`;
    },
    graphResetView() {
      this._fitGraphToView();
    },
    _fitGraphToView() {
      this.$nextTick(() => {
        const wrap = document.querySelector('.graph-stage-wrapper');
        if (!wrap) return;
        const rect = wrap.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const W = this.graph.width;
        const H = this.graph.height;
        // Fit the bounding box of the actual nodes, not the full world. With
        // tight packing this puts the cluster in the visible area instead of
        // leaving a sea of empty space around it.
        const nodes = this.graph.nodes || [];
        let minX = W, minY = H, maxX = 0, maxY = 0;
        if (nodes.length === 0) { minX = 0; minY = 0; maxX = W; maxY = H; }
        else {
          for (const n of nodes) {
            const hw = (n.w || 140) / 2;
            const hh = (n.h || 56) / 2;
            if (n.x - hw < minX) minX = n.x - hw;
            if (n.y - hh < minY) minY = n.y - hh;
            if (n.x + hw > maxX) maxX = n.x + hw;
            if (n.y + hh > maxY) maxY = n.y + hh;
          }
        }
        const bbW = Math.max(1, maxX - minX);
        const bbH = Math.max(1, maxY - minY);
        const padding = 60;
        const zx = (rect.width - padding * 2) / bbW;
        const zy = (rect.height - padding * 2) / bbH;
        const zoom = Math.max(0.35, Math.min(1.4, Math.min(zx, zy)));
        // Pan so the bbox centre lands at the wrapper centre.
        const bbCx = (minX + maxX) / 2;
        const bbCy = (minY + maxY) / 2;
        this.graph.zoom = zoom;
        this.graph.panX = (W / 2 - bbCx) * zoom;
        this.graph.panY = (H / 2 - bbCy) * zoom;
      });
    },
    graphZoomBy(factor, screenX = null, screenY = null, wrapperEl = null) {
      const prev = this.graph.zoom;
      const next = Math.max(0.35, Math.min(3.0, prev * factor));
      if (next === prev) return;
      if (screenX != null && screenY != null && wrapperEl) {
        // Anchor zoom around cursor: keep the world point under the cursor fixed.
        // Stage transform: translate(-50%,-50%) translate(pan) scale(zoom)
        // Wrapper-local position of world point (wx, wy):
        //   px = wrapperCenterX + panX + (wx - W/2) * zoom
        // Solve for wx: wx = (px - wrapperCenterX - panX) / zoom + W/2
        const rect = wrapperEl.getBoundingClientRect();
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const px = screenX - rect.left;
        const py = screenY - rect.top;
        const wx = (px - cx - this.graph.panX) / prev + this.graph.width / 2;
        const wy = (py - cy - this.graph.panY) / prev + this.graph.height / 2;
        this.graph.panX = (px - cx) - (wx - this.graph.width / 2) * next;
        this.graph.panY = (py - cy) - (wy - this.graph.height / 2) * next;
      }
      this.graph.zoom = next;
    },
    graphRelayout() {
      if (!this.graph.nodes.length) return;
      const focusId = this.graph.nodes.find(n => n.focused)?.id || this.graph.nodes[0].id;
      const golden = Math.PI * (3 - Math.sqrt(5));
      const W = this.graph.width;
      const H = this.graph.height;
      const cx = W / 2;
      const cy = H / 2;
      const seedRadius = Math.min(W, H) * 0.42;
      const N = this.graph.nodes.length;
      this.graph.nodes.forEach((n, i) => {
        n.pinned = false;
        if (n.id === focusId) { n.x = cx; n.y = cy; return; }
        const t = i + 1;
        const frac = Math.sqrt(t / N);
        const radius = frac * seedRadius;
        const angle = t * golden;
        n.x = cx + Math.cos(angle) * radius;
        n.y = cy + Math.sin(angle) * radius;
      });
      this.runForceLayout(this.graph.nodes, this.graph.edges, focusId);
      for (let i = 0; i < 8; i++) {
        this._fitInsideWorld(this.graph.nodes);
        this._resolveCollisions(this.graph.nodes, focusId);
      }
      this._fitInsideWorld(this.graph.nodes);
      this.graph.tick++;
    },

    // ----- Concept map: pointer interactions -------------------------------
    // The .graph-stage element is transformed; its getBoundingClientRect() therefore
    // already reflects pan + zoom, so we use it as the source of truth for
    // converting screen coordinates to world coordinates.
    _stageRectFor(target) {
      const wrapper = target.closest ? target.closest('.graph-stage-wrapper') : null;
      const stage = wrapper ? wrapper.querySelector('.graph-stage') : null;
      return stage ? stage.getBoundingClientRect() : null;
    },
    _screenToWorld(stageRect, clientX, clientY) {
      const sx = this.graph.width / stageRect.width;
      const sy = this.graph.height / stageRect.height;
      return {
        x: (clientX - stageRect.left) * sx,
        y: (clientY - stageRect.top) * sy,
      };
    },
    onGraphWheel(ev) {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      this.graphZoomBy(factor, ev.clientX, ev.clientY, ev.currentTarget);
    },
    onGraphPanStart(ev) {
      // Only handle drags that begin on the stage background, not on a node.
      if (ev.target.closest && ev.target.closest('.graph-node-card')) return;
      this.graph.pan = {
        startX: ev.clientX,
        startY: ev.clientY,
        origPanX: this.graph.panX,
        origPanY: this.graph.panY,
      };
      ev.currentTarget.classList.add('panning');
    },
    onGraphPanMove(ev) {
      if (!this.graph.pan) return;
      this.graph.panX = this.graph.pan.origPanX + (ev.clientX - this.graph.pan.startX);
      this.graph.panY = this.graph.pan.origPanY + (ev.clientY - this.graph.pan.startY);
    },
    onGraphPanEnd(ev) {
      this.graph.pan = null;
      if (ev && ev.currentTarget && ev.currentTarget.classList) {
        ev.currentTarget.classList.remove('panning');
      }
    },
    onNodeDragStart(node, ev) {
      ev.stopPropagation();
      ev.preventDefault();
      const rect = this._stageRectFor(ev.currentTarget);
      if (!rect) return;
      const w = this._screenToWorld(rect, ev.clientX, ev.clientY);
      this.graph.drag = {
        node,
        offsetX: node.x - w.x,
        offsetY: node.y - w.y,
        getRect: () => {
          const wrap = document.querySelector('.graph-stage-wrapper');
          return wrap ? wrap.querySelector('.graph-stage').getBoundingClientRect() : rect;
        },
        moved: false,
        downX: ev.clientX,
        downY: ev.clientY,
      };
      node.pinned = true;
      window.addEventListener('mousemove', this._onNodeDragMove);
      window.addEventListener('mouseup', this._onNodeDragEnd, { once: true });
    },
    _initGraphHandlers() {
      this._onNodeDragMove = (ev) => {
        const drag = this.graph.drag;
        if (!drag) return;
        // Suppress micro-movements so a clean click doesn't pin the node.
        if (!drag.moved && Math.abs(ev.clientX - drag.downX) + Math.abs(ev.clientY - drag.downY) < 4) {
          return;
        }
        drag.moved = true;
        const rect = drag.getRect();
        const w = this._screenToWorld(rect, ev.clientX, ev.clientY);
        drag.node.x = w.x + drag.offsetX;
        drag.node.y = w.y + drag.offsetY;
        this.relaxOnce();
      };
      this._onNodeDragEnd = () => {
        const drag = this.graph.drag;
        window.removeEventListener('mousemove', this._onNodeDragMove);
        if (drag && !drag.moved) {
          // No drag occurred — release the pin so layout can flow naturally.
          drag.node.pinned = false;
        }
        this.graph.drag = null;
      };
    },
    selectGraphNode(node) {
      this.graph.selectedNode = node;
      this.selectedItem = null;
    },
    onNodeHover(node) {
      this.graph.hoverNode = node;
    },
    onNodeHoverEnd() {
      this.graph.hoverNode = null;
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
      this.modalSort = 'pertinence';
      this.modalExact = false;
      this.modalTitleContains = false;
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

    looksLikeUrl(text) {
      return /^https?:\/\//i.test(String(text || '').trim());
    },
    looksLikeDoi(text) {
      let value = String(text || '').trim();
      try { value = decodeURIComponent(value); } catch (_e) {}
      return /\b10\.\d{4,9}\/[-._;()/:A-Z0-9]+/i.test(value)
        || /^doi:\s*10\.\d{4,9}\//i.test(value)
        || /^https?:\/\/(dx\.)?doi\.org\/10\.\d{4,9}\//i.test(value);
    },
    topQueryLooksAddable() {
      const q = this.searchQuery.trim();
      return this.looksLikeDoi(q) || this.looksLikeUrl(q);
    },
    async addTopQuery() {
      const q = this.searchQuery.trim();
      if (!q) return;
      try {
        const r = await fetch('/api/add', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            queries: [q],
            auto_download: this.autoDownload,
            collection_id: null,
          }),
        });
        if (!r.ok) throw new Error('Could not add paper');
        this.searchQuery = '';
        this.selectVirtual('recent');
        this.toast('Queued paper from search bar', 'ok');
      } catch (e) {
        this.toast(e.message || 'Could not add paper', 'error');
      }
    },
    async runTopSearch() {
      const q = this.searchQuery.trim();
      if (!q) {
        this.selectVirtual('discover');
        return;
      }
      if (this.topQueryLooksAddable()) {
        await this.addTopQuery();
        return;
      }
      this.selectVirtual('discover');
      this.modalQuery = q;
      this.runSearch(true);
    },
    async setSearchKind(k) {
      if (this.searchKind === k) return;
      this.searchKind = k;
      this.modalSort = k === 'author' ? 'newest' : 'pertinence';
      if (k === 'author') {
        this.modalExact = false;
        this.modalTitleContains = false;
      }
      this.modalResults = [];
      this.selectedResult = null;
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
        this.selectedResult = null;
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
          sort: this.modalSort,
          exact: this.modalExact,
          title_contains: this.modalTitleContains,
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
    async changeModalSort() {
      if (this.modalAttempted && this.modalQuery.trim()) await this.runSearch(true);
    },
    async changeModalExact() {
      if (this.searchKind === 'author') {
        this.modalExact = false;
        return;
      }
      if (this.modalAttempted && this.modalQuery.trim()) await this.runSearch(true);
    },
    async changeModalTitleContains() {
      if (this.searchKind === 'author') {
        this.modalTitleContains = false;
        return;
      }
      if (this.modalAttempted && this.modalQuery.trim()) await this.runSearch(true);
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
    async addResultAndSelect(r) {
      const before = new Set(this.items.map(x => x.id));
      await this._submitItems([r], null);
      await this.loadItems();
      const match = this.items.find(x =>
        !before.has(x.id)
        || (r.doi && x.doi === r.doi)
        || (!r.doi && x.title === r.title)
      );
      if (match) await this.selectItem(match);
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
    openDriveLink(it) {
      if (!it || !it.drive_web_link) return;
      window.open(it.drive_web_link, '_blank');
    },
    async uploadItemToDrive(it) {
      if (!it || !it.id) return;
      if (!this.googleDrive.connected) {
        this.toast('Connect Google Drive first', 'error');
        return;
      }
      const r = await fetch(`/api/google-drive/items/${it.id}/upload`, { method: 'POST' });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        this.toast('Uploaded to Google Drive', 'ok');
        if (this.selectedItem && this.selectedItem.id === it.id) this.selectedItem = data.item || this.selectedItem;
        await this.loadItems();
      } else {
        this.toast(data.detail || 'Drive upload failed', 'error');
      }
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
    async downloadCollection(c) {
      if (!c || !c.id) return;
      const url = `/api/collections/${c.id}/zip`;
      try {
        const r = await fetch(url, { method: 'HEAD' });
        if (!r.ok) {
          const data = await fetch(url).then(x => x.json()).catch(() => ({}));
          throw new Error(data.detail || 'No downloaded PDFs found in this folder');
        }
        window.location.href = url;
      } catch (e) {
        this.toast(e.message || 'Could not download folder ZIP', 'error');
      }
    },
    async addCollectionToDrive(c) {
      if (!c || !c.id) return;
      const r = await fetch(`/api/collections/${c.id}/google-drive`, { method: 'POST' });
      if (r.ok) {
        this.toast(`Queued Google Drive upload for ${c.name}`, 'ok');
      } else {
        const data = await r.json().catch(() => ({}));
        this.toast(data.detail || 'Could not add folder to Google Drive', 'error');
      }
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
    async exportLibraryToDrive(customDestination = '') {
      this.storage.loading = true;
      try {
        const r = await fetch('/api/storage/export-google-drive', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ destination: customDestination || null }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Google Drive export failed');
        this.toast(`Exported ${data.items} records and ${data.files} files`, 'ok', 5000);
        await this.loadStorage();
      } catch (e) {
        this.toast(e.message || 'Google Drive export failed', 'error', 6000);
      } finally {
        this.storage.loading = false;
      }
    },
    async exportLibraryToCustomFolder() {
      this.openSettingsDialog();
    },
    async useGoogleDriveForDownloads(customDestination = '', migrateExisting = true) {
      this.storage.loading = true;
      try {
        const r = await fetch('/api/storage/use-google-drive', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ destination: customDestination || null, migrate_existing: !!migrateExisting }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Could not use Google Drive for downloads');
        this.toast(`Download folder set (${data.migrated} existing files copied)`, 'ok', 5500);
        await Promise.all([this.loadStorage(), this.loadStats(), this.loadItems()]);
        return true;
      } catch (e) {
        this.toast(e.message || 'Could not use Google Drive for downloads', 'error', 6500);
        return false;
      } finally {
        this.storage.loading = false;
      }
    },
    async setCustomDownloadFolder() {
      this.openSettingsDialog();
    },
    async saveStorageSettings() {
      const dest = (this.settings.downloadPath || '').trim();
      if (!dest) {
        this.toast('Choose a download folder', 'error');
        return;
      }
      const ok = await this.useGoogleDriveForDownloads(dest, this.settings.migrateExisting);
      if (ok) this.openSettings = false;
    },
    async exportLibraryFromSettings() {
      const dest = (this.settings.exportRoot || this.settings.downloadPath || '').trim();
      if (!dest) {
        this.toast('Choose an export folder', 'error');
        return;
      }
      await this.exportLibraryToDrive(dest);
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
      const height = ['author', 'subject'].includes(type) ? 300 : type === 'collection' ? 290 : 320;
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
          { icon: 'Z', label: 'Download ZIP', action: 'collection-download', disabled: !t.count },
          { icon: 'G', label: 'Add to Google Drive', action: 'collection-drive', disabled: !t.count },
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
          { icon: 'O', label: 'Open website', action: 'result-open-website', disabled: !this.resultWebsiteUrl(t) },
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
        { icon: 'G', label: t.drive_web_link ? 'Open in Google Drive' : 'Upload to Google Drive', action: t.drive_web_link ? 'item-open-drive' : 'item-upload-drive', disabled: !t.file_path },
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
      if (action === 'item-open-drive') return this.openDriveLink(target);
      if (action === 'item-upload-drive') return this.uploadItemToDrive(target);
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
      if (action === 'collection-download') return this.downloadCollection(target);
      if (action === 'collection-drive') return this.addCollectionToDrive(target);
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
        this.modalSort = 'pertinence';
        this.modalExact = false;
        this.modalTitleContains = false;
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
      if (action === 'result-open-website') {
        const url = this.resultWebsiteUrl(target);
        if (url) window.open(url, '_blank');
        return;
      }
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
        } else if (m.event === 'drive_uploading') {
          this.toast('Uploading to Google Drive', 'info', 2500);
        } else if (m.event === 'drive_uploaded') {
          this.toast('Uploaded to Google Drive', 'ok', 3500);
          this.loadItems();
          if (this.selectedItem && this.selectedItem.id === m.id) {
            const r = await fetch(`/api/items/${m.id}`);
            if (r.ok) this.selectedItem = await r.json();
          }
        } else if (m.event === 'drive_failed') {
          this.toast(m.error || 'Google Drive upload failed', 'error', 6000);
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
