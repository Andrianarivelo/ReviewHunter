/*
 * Settings > Zotero (smart connector) and Settings > Shared Libraries (collaboration over a synced
 * Google Drive folder). Mixed into the root Alpine component like the other parts.
 *
 * Server contract: doi_hunter_web/sync_api.py. Long operations report over the event WebSocket:
 * zotero_progress / zotero_done / zotero_failed and library_changed / shared_synced.
 */
window.collabPart = function collabPart() {
  return {
    /** GET /api/zotero/status payload (install, data dir, libraries, settings, last sync). */
    zconn: null,
    zconnBusy: false,
    /** Live progress of a running Zotero sync (from zotero_progress events). */
    zconnProgress: null,
    /** GET /api/shared payload (member card, libraries, Drive roots). */
    shared: null,
    sharedBusy: '',
    sharedDiscovered: [],
    /** "Create shared library" form; collectionId set when sharing an existing collection. */
    shareForm: { name: '', parent: '', collectionId: null, collectionName: '' },
    joinPath: '',

    // ------------------------------------------------------------ Zotero connector

    async loadZotero() {
      try { this.zconn = await api.get('/api/zotero/status'); }
      catch (e) { this.zconn = null; this.toast(e.message, 'error'); }
    },
    /** One-line state shown next to the status dot. */
    zoteroSummary() {
      const z = this.zconn;
      if (!z) return { label: 'Checking Zotero...', tone: 'idle' };
      if (!z.found) return { label: 'Zotero data folder not found', tone: 'warn' };
      const version = z.install.version ? `Zotero ${z.install.version}` : 'Zotero';
      return { label: `${version} ${z.running ? 'is running' : 'is closed'} - data in ${z.data_dir}`, tone: z.error ? 'warn' : 'on' };
    },
    async saveZoteroConfig(patch) {
      try { this.zconn = await api.post('/api/zotero/config', patch); }
      catch (e) { this.toast(e.message, 'error', 6000); }
    },
    toggleZoteroLibrary(lib, enabled) {
      this.saveZoteroConfig({ libraries: { [lib.key]: { enabled } } });
    },
    setZoteroTarget(lib, value) {
      this.saveZoteroConfig({ libraries: { [lib.key]: { target_collection_id: value === '' ? null : Number(value) } } });
    },
    async syncZoteroNow() {
      this.zconnBusy = true;
      this.zconnProgress = null;
      try {
        const r = await api.post('/api/zotero/sync', {});
        if (!r.queued) { this.zconnBusy = false; this.toast(r.reason, 'info'); }
      } catch (e) { this.zconnBusy = false; this.toast(e.message, 'error', 6000); }
    },
    /** Called from the WebSocket handler (app.js). */
    onZoteroEvent(m) {
      if (m.event === 'zotero_progress') { this.zconnBusy = true; this.zconnProgress = m; return; }
      this.zconnBusy = false;
      this.zconnProgress = null;
      if (m.event === 'zotero_failed') { this.toast(m.error, 'error', 8000); return; }
      const parts = [`${m.items} new`, `${m.updated || 0} updated`];
      if (m.trashed) parts.push(`${m.trashed} moved to trash`);
      if (m.files) parts.push(`${m.files} files`);
      this.toast(`Zotero sync: ${parts.join(', ')}`, 'ok', 7000);
      if (this.dialog === 'settings' && this.settingsTab === 'zotero') this.loadZotero();
    },
    /** Human summary of the last sync report. */
    zoteroLastLine() {
      const r = this.zconn && this.zconn.last_sync;
      if (!r || !r.finished_at) return 'Never synchronized.';
      const conflicts = (r.conflicts_kept_local || 0) + (r.conflicts_zotero_won || 0);
      return `Last sync ${r.finished_at.replace('T', ' ')}: ${r.items} new, ${r.updated} updated, ${r.unchanged} unchanged`
        + `${r.trashed ? `, ${r.trashed} to trash` : ''}${conflicts ? `, ${conflicts} conflicts resolved` : ''}`
        + ` (${r.seconds}s)${r.warning_count ? `, ${r.warning_count} warnings` : ''}.`;
    },

    // ------------------------------------------------------------ Shared libraries

    async loadShared() {
      try {
        this.shared = await api.get('/api/shared');
        if (!this.shareForm.parent) this.shareForm.parent = this.shared.default_parent || '';
      } catch (e) { this.shared = null; this.toast(e.message, 'error'); }
    },
    async saveMember(patch) {
      try { this.shared.member = await api.post('/api/shared/member', patch); }
      catch (e) { this.toast(e.message, 'error'); }
    },
    async discoverShared() {
      this.sharedBusy = 'discover';
      try {
        this.sharedDiscovered = await api.get('/api/shared/discover', { root: this.joinPath.trim() || undefined });
        if (!this.sharedDiscovered.length) this.toast('No shared library found in your Drive folders yet', 'info', 5000);
      } catch (e) { this.toast(e.message, 'error'); }
      this.sharedBusy = '';
    },
    /** Entry point from a collection's context menu. */
    async startShareCollection(c) {
      this.shareForm = { name: c.name, parent: this.shareForm.parent, collectionId: c.id, collectionName: c.name };
      await this.openSettings('shared');
    },
    async createShared() {
      const f = this.shareForm;
      if (!f.name.trim() || !f.parent.trim()) return this.toast('Enter a name and a folder', 'error');
      this.sharedBusy = 'create';
      try {
        const r = await api.post('/api/shared/create', { name: f.name.trim(), parent_dir: f.parent.trim(), collection_id: f.collectionId });
        this.toast(`Shared library created: ${r.published} records published, ${r.files_uploaded} files`, 'ok', 7000);
        this.shareForm = { name: '', parent: f.parent, collectionId: null, collectionName: '' };
        await Promise.all([this.loadShared(), this.reloadAll()]);
      } catch (e) { this.toast(e.message, 'error', 8000); }
      this.sharedBusy = '';
    },
    async joinShared(path) {
      this.sharedBusy = 'join';
      try {
        const r = await api.post('/api/shared/join', { path });
        this.toast(`Joined: ${r.items_added} papers, ${r.files_downloaded} files${r.files_pending ? ` (${r.files_pending} files still syncing)` : ''}`, 'ok', 7000);
        this.sharedDiscovered = this.sharedDiscovered.filter(d => d.path !== path);
        this.joinPath = '';
        await Promise.all([this.loadShared(), this.reloadAll()]);
      } catch (e) { this.toast(e.message, 'error', 8000); }
      this.sharedBusy = '';
    },
    async syncShared(lib) {
      this.sharedBusy = lib.library_id;
      try {
        const r = await api.post(`/api/shared/${lib.library_id}/sync`, {});
        this.toast(`${lib.name}: ${r.published} sent, ${r.items_added} new, ${r.items_updated} updated${r.files_pending ? `, ${r.files_pending} files still syncing` : ''}`, 'ok', 6000);
        await this.loadShared();
      } catch (e) { this.toast(e.message, 'error', 8000); }
      this.sharedBusy = '';
    },
    async syncSharedByCollection(collectionId) {
      if (!this.shared) await this.loadShared();
      const lib = (this.shared?.libraries || []).find(l => l.collection_id === collectionId);
      if (lib) await this.syncShared(lib);
    },
    async setSharedAuto(lib, value) {
      await api.post(`/api/shared/${lib.library_id}/auto`, { auto_sync: value });
      lib.auto_sync = value ? 1 : 0;
    },
    async openSharedFolder(lib) {
      try { await api.post(`/api/shared/${lib.library_id}/open`, {}); }
      catch (e) { this.toast(e.message, 'error'); }
    },
    async leaveShared(lib) {
      if (!confirm(`Stop syncing "${lib.name}"?\n\nYour copies of its papers stay in your library as a normal collection. Other members are not affected.`)) return;
      await api.post(`/api/shared/${lib.library_id}/leave`, {});
      await Promise.all([this.loadShared(), this.reloadAll()]);
    },
    /** "Alice (2 min ago)" style member line. */
    memberLine(m) {
      const when = m.last_sync_at ? this.timeAgo(m.last_sync_at) : 'never';
      return `${m.name}${m.email ? ` <${m.email}>` : ''}${m.left_at ? ' (left)' : ''} - seen ${when}`;
    },
    timeAgo(iso) {
      const s = (Date.now() - new Date(iso).getTime()) / 1000;
      if (!isFinite(s)) return iso;
      if (s < 90) return 'just now';
      if (s < 5400) return `${Math.round(s / 60)} min ago`;
      if (s < 129600) return `${Math.round(s / 3600)} h ago`;
      return `${Math.round(s / 86400)} days ago`;
    },
    sharedStatus(lib) {
      if (!lib.available) return { tone: 'warn', label: 'Folder unavailable (is Google Drive running?)' };
      const r = lib.last_result;
      if (r && r.ok === false) return { tone: 'warn', label: r.error || 'Last sync failed' };
      const role = lib.role === 'viewer' ? 'View only' : 'Can edit';
      return { tone: 'on', label: `${role} - ${lib.items} papers - synced ${lib.last_sync_at ? this.timeAgo(lib.last_sync_at) : 'never'}` };
    },
  };
};
