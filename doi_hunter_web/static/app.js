// ReviewHunter web app root component (Alpine.js).
//
// The component is assembled from focused parts (see /static/js/*.js):
//   libraryPart   navigation, collections, tags, items table, selection, context menus
//   detailPart    item pane: fields, creators, tags, notes, attachments, related
//   dialogsPart   add by identifier, import, export, bibliography, advanced search
//   settingsPart  preferences, citation styles, Google Drive, backups, integrations
//   discoverPart  online discovery and the concept map (from v1)

function app() {
  return mixParts(
    {
      dark: (() => {
        try { return localStorage.dhDark === 'true' || (localStorage.dhDark === undefined && matchMedia('(prefers-color-scheme: dark)').matches); }
        catch (e) { return false; }
      })(),
      toasts: [],
      ws: null,
      online: true,
      batch: { active: false, index: 0, total: 0, query: '' },
      impactRefresh: { active: false, index: 0, total: 0, journal: '' },
      /** Chrome connector detected on this page (its content script answers window messages). */
      connector: { present: false, version: '' },
      /** Live hand-off state per item id: {state, message}. */
      handoffs: {},

      /** Boot: load reference data, first view, WebSocket, global shortcuts. */
      async init() {
        this.listenToConnector();
        try {
          await Promise.all([this.loadItemTypes(), this.loadSettings(), this.refreshSidebar()]);
          this.loadStyles().catch(() => null);
          await this.loadItems();
          this.online = true;
        } catch (e) {
          this.online = false;
          this.toast('ReviewHunter server is not reachable', 'error', 8000);
        }
        this.connectWS();
        this.restoreBulkImport();
        addEventListener('click', () => { this.closeContextMenu(); });
        addEventListener('resize', () => this.closeContextMenu());
        addEventListener('keydown', (e) => this.onGlobalKey(e));
        addEventListener('dragover', (e) => e.preventDefault());
        addEventListener('drop', (e) => e.preventDefault());
      },

      /** Application-wide keyboard shortcuts (Zotero-compatible where possible). */
      onGlobalKey(e) {
        const mod = e.ctrlKey || e.metaKey;
        const typing = e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"]');
        if (e.key === 'Escape') {
          if (this.contextMenu.open) return this.closeContextMenu();
          if (this.note.open) return this.closeNoteEditor();
          if (this.dialog) return this.closeDialog();
        }
        if (mod && e.shiftKey && e.key.toLowerCase() === 'c' && !typing) { e.preventDefault(); return this.quickCopy('bibliography'); }
        if (mod && e.shiftKey && e.key.toLowerCase() === 'a' && !typing) { e.preventDefault(); return this.quickCopy('citation'); }
        if (mod && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          if (!this.isItemView()) this.selectVirtual('all');
          return this.$nextTick(() => this.$refs.search && this.$refs.search.focus());
        }
        if (mod && e.shiftKey && e.key.toLowerCase() === 'n') { e.preventDefault(); return this.newStandaloneNote(); }
        if (mod && e.key.toLowerCase() === 'n' && !e.shiftKey) { e.preventDefault(); return this.openIdentify(); }
        if (mod && e.key === ',') { e.preventDefault(); return this.openSettings(); }
      },

      toggleDark() {
        this.dark = !this.dark;
        try { localStorage.dhDark = this.dark; } catch (e) { /* ignore */ }
        if (this.virtual === 'graph') this.$nextTick(() => this.renderInsight());
      },

      toast(text, kind = 'info', ttl = 3500) {
        const t = { id: Math.random().toString(36).slice(2), text, kind };
        this.toasts.push(t);
        if (this.toasts.length > 5) this.toasts.shift();
        setTimeout(() => { this.toasts = this.toasts.filter(x => x.id !== t.id); }, ttl);
      },

      /**
       * Bridge to the Chrome connector. The extension's content script answers {type:'ping'} with
       * {type:'hello', version} and relays {type:'handoff'} to its service worker, which fetches the
       * PDF with the browser session (see browser_extension/src/bridge/app_bridge.js).
       */
      listenToConnector() {
        addEventListener('message', (ev) => {
          if (ev.source !== window || !ev.data || ev.data.source !== 'reviewhunter-connector') return;
          const m = ev.data;
          if (m.type === 'hello') {
            const wasPresent = this.connector.present;
            this.connector = { present: true, version: m.version || '' };
            if (!wasPresent) this.flushPendingHandoffs();
          } else if (m.type === 'handoff-ack') {
            if (m.accepted) this.toast('Chrome is fetching the PDF with your browser session...', 'info', 4000);
            else this.toast(`Chrome connector declined: ${m.error || 'hand-off disabled in the extension options'}`, 'error', 7000);
          }
        });
        // The content script may have run before this listener existed: it also leaves a DOM marker.
        const marker = document.documentElement.getAttribute('data-reviewhunter-connector');
        if (marker) this.connector = { present: true, version: marker === 'unknown' ? '' : marker };
        postMessage({ source: 'reviewhunter-app', type: 'ping' }, location.origin);
      },
      /** Hand a failed download to the Chrome extension (queued until the connector answers). */
      sendHandoff(rec) {
        if (!rec || !rec.item_id) return;
        this.handoffs[rec.item_id] = { state: 'pending', message: `${(rec.plan || []).length} source(s) to try in Chrome` };
        if (this.connector.present) {
          postMessage({ source: 'reviewhunter-app', type: 'handoff', handoff: rec }, location.origin);
          return;
        }
        this._pendingHandoffs = [...(this._pendingHandoffs || []).filter(r => r.item_id !== rec.item_id), rec];
        postMessage({ source: 'reviewhunter-app', type: 'ping' }, location.origin);
        this.toast('Server could not fetch the PDF. Install or enable the ReviewHunter Chrome extension: it retries with your browser session (Settings > Browser Connector).', 'error', 9000);
      },
      flushPendingHandoffs() {
        for (const rec of this._pendingHandoffs || []) postMessage({ source: 'reviewhunter-app', type: 'handoff', handoff: rec }, location.origin);
        this._pendingHandoffs = [];
      },

      /** Live job events (downloads, imports, connector saves) from the server. */
      connectWS() {
        // Exactly one live socket: a second one would duplicate every toast and refresh.
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const ws = new WebSocket(`${proto}://${location.host}/ws/jobs`);
        this.ws = ws;
        let refreshTimer = null;
        const scheduleRefresh = () => {
          clearTimeout(refreshTimer);
          refreshTimer = setTimeout(() => this.reloadAll(), 400);
        };
        ws.onopen = () => {
          this.appStopped = false;   // reopened from its shortcut after Settings > Quit
          if (!this.online) { this.online = true; this.reloadAll(); }
        };
        ws.onmessage = async (ev) => {
          const m = JSON.parse(ev.data);
          switch (m.event) {
            case 'item_added':
              this.toast(`${m.source === 'connector' ? 'Saved from browser' : 'Added'}: ${m.title}`, 'ok');
              scheduleRefresh();
              break;
            case 'item_changed': case 'done':
              scheduleRefresh();
              break;
            case 'source':
              this.toast(`Trying ${m.source}`, 'info', 1800);
              break;
            case 'failed':
              this.toast(m.error || 'Operation failed', 'error', 6000);
              scheduleRefresh();
              break;
            case 'browser_handoff':
              this.sendHandoff(m);
              break;
            case 'handoff_progress':
              this.handoffs[m.id] = { state: m.state, message: m.message };
              this.toast(`Chrome: ${m.message}`, 'info', 2500);
              break;
            case 'handoff_done':
              this.handoffs[m.id] = { state: 'done', message: m.message };
              this.toast(`Chrome: ${m.message}${m.title ? ` (${m.title})` : ''}`, 'ok', 6000);
              scheduleRefresh();
              break;
            case 'handoff_failed':
              this.handoffs[m.id] = { state: 'failed', message: m.message };
              this.toast(`Chrome: ${m.message}`, 'error', 8000);
              scheduleRefresh();
              break;
            case 'zotero_progress':
              this.zotero.running = true;
              this.zotero.progress = m;
              this.onZoteroEvent(m);
              break;
            case 'zotero_done':
              this.zotero.running = false;
              this.zotero.progress = null;
              this.zotero.report = m;
              this.onZoteroEvent(m);
              scheduleRefresh();
              break;
            case 'zotero_failed':
              this.zotero.running = false;
              this.onZoteroEvent(m);
              break;
            case 'library_changed':
              scheduleRefresh();
              if (this.dialog === 'settings' && this.settingsTab === 'shared') this.loadShared();
              break;
            case 'shared_synced':
              if (this.dialog === 'settings' && this.settingsTab === 'shared') this.loadShared();
              break;
            case 'batch_start':
              this.batch = { active: true, index: 0, total: m.total, query: '' };
              break;
            case 'batch_progress':
              this.batch = { active: true, index: m.index, total: m.total, query: m.query };
              break;
            case 'batch_done':
              this.batch = { active: false, index: 0, total: 0, query: '' };
              this.toast(`Finished (${m.total})`, 'ok');
              scheduleRefresh();
              break;
            case 'items_bulk_changed':
              scheduleRefresh();
              break;
            case 'impact_factor_progress':
              this.impactRefresh = { active: true, index: m.index, total: m.total, journal: m.journal };
              break;
            case 'impact_factor_done':
              this.impactRefresh = { active: false, index: 0, total: 0, journal: '' };
              this.toast(`Impact factors: ${m.matched} of ${m.journals} journal(s) matched, ${m.items_updated} item(s) updated`, 'ok', 7000);
              scheduleRefresh();
              break;
            default: break;
          }
        };
        ws.onclose = () => {
          if (this.ws !== ws) return;   // an older socket closing must not spawn another reconnect loop
          this.online = false;
          setTimeout(() => this.connectWS(), 2500);
        };
        ws.onerror = () => ws.close();
      },
    },
    libraryPart(),
    detailPart(),
    dialogsPart(),
    bulkImportPart(),
    settingsPart(),
    discoverPart(),
    insightsPart(),
    collabPart(),
  );
}
