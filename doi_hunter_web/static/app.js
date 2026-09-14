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

      /** Boot: load reference data, first view, WebSocket, global shortcuts. */
      async init() {
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
      },

      toast(text, kind = 'info', ttl = 3500) {
        const t = { id: Math.random().toString(36).slice(2), text, kind };
        this.toasts.push(t);
        if (this.toasts.length > 5) this.toasts.shift();
        setTimeout(() => { this.toasts = this.toasts.filter(x => x.id !== t.id); }, ttl);
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
        ws.onopen = () => { if (!this.online) { this.online = true; this.reloadAll(); } };
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
            case 'zotero_progress':
              this.zotero.running = true;
              this.zotero.progress = m;
              break;
            case 'zotero_done':
              this.zotero.running = false;
              this.zotero.progress = null;
              this.zotero.report = m;
              this.toast(`Zotero import finished: ${m.items} items, ${m.files} files, ${m.notes} notes`, 'ok', 8000);
              scheduleRefresh();
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
    settingsPart(),
    discoverPart(),
  );
}
