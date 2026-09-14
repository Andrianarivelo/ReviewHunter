/*
 * Settings: General, Citations (styles manager), Sync & Backup (Google Drive wizard for the
 * desktop folder mode and the API mode, automatic backups, restore), Browser connector,
 * Word add-in, Advanced (paths, full-text index).
 */
window.settingsPart = function settingsPart() {
  return {
    settings: {},
    settingsTab: 'general',
    styles: [],
    styleUrl: '',
    drive: null,
    driveBusy: false,
    driveWizard: { mode: '', step: 1, root: '', papersInDrive: true },
    backups: [],
    remoteBackups: [],
    system: null,
    fulltextStats: null,

    async loadSettings() {
      this.settings = await api.get('/api/settings');
    },
    async saveSetting(key, value) {
      this.settings = await api.patch('/api/settings', { [key]: value });
      if (key === 'citation_style' || key === 'citation_locale') {
        this._citeEngine = null;
        this.updateCitationPreview();
      }
    },

    /** Shared citeproc wrapper configured with the default style (used by Quick Copy and previews). */
    async citationEngine() {
      const style = this.settings.citation_style || 'apa';
      const locale = this.settings.citation_locale || 'en-US';
      if (!this._citeEngine) this._citeEngine = new RHCitations();
      await this._citeEngine.useStyle(style, locale);
      return this._citeEngine;
    },

    async openSettings(tab = 'general') {
      this.settingsTab = tab;
      this.openDialog('settings');
      await Promise.all([this.loadSettings(), this.loadStyles(), this.loadDrive(), this.loadBackups(), this.loadSystem()]);
      this.fulltextStats = await api.get('/api/fulltext/stats').catch(() => null);
    },

    // ------------------------------------------------------------ citation styles

    async loadStyles() {
      this.styles = await api.get('/api/csl/styles');
    },
    styleTitle(id) {
      return (this.styles.find(s => s.id === id) || {}).title || id;
    },
    async installStyle() {
      if (!this.styleUrl.trim()) return;
      try {
        const s = await api.post('/api/csl/styles/install', { url: this.styleUrl.trim() });
        this.styleUrl = '';
        await this.loadStyles();
        this.toast(`Installed "${s.title}"`, 'ok');
      } catch (e) { this.toast(e.message, 'error', 7000); }
    },
    async uploadStyle(file) {
      if (!file) return;
      try {
        const s = await api.upload('/api/csl/styles/upload', [['file', file]]);
        await this.loadStyles();
        this.toast(`Installed "${s.title}"`, 'ok');
      } catch (e) { this.toast(e.message, 'error'); }
    },
    async removeStyle(s) {
      if (!confirm(`Remove style "${s.title}"?`)) return;
      await api.del(`/api/csl/styles/${s.id}`);
      await this.loadStyles();
    },

    // ------------------------------------------------------------ Google Drive

    async loadDrive() {
      try {
        this.drive = await api.get('/api/drive/status');
        if (!this.driveWizard.root && this.drive.desktop.candidates.length) this.driveWizard.root = this.drive.desktop.candidates[0];
      } catch (e) { this.drive = null; }
    },
    driveSummary() {
      const d = this.drive;
      if (!d) return { label: 'Unknown', tone: 'muted' };
      if (d.mode === 'desktop') return { label: 'Google Drive folder', tone: 'ok' };
      if (d.mode === 'api' && d.api.connected) return { label: 'Google Drive API', tone: 'ok' };
      if (d.mode === 'api') return { label: 'Drive API disconnected', tone: 'warn' };
      return { label: 'Local only', tone: 'muted' };
    },
    async installDriveDesktop() {
      const r = await api.post('/api/drive/desktop/install');
      this.toast(r.method === 'winget'
        ? 'Installer opened in a console window. Follow its prompts, sign in to Google Drive, then click Re-detect.'
        : 'Download page opened in your browser.', 'info', 9000);
    },
    async enableDriveDesktop() {
      this.driveBusy = true;
      try {
        const r = await api.post('/api/drive/desktop/enable', { root: this.driveWizard.root, papers_in_drive: this.driveWizard.papersInDrive });
        const moved = r.papers ? ` ${r.papers.migrated} file(s) copied to Drive.` : '';
        this.toast(`Google Drive folder enabled. First backup created.${moved}`, 'ok', 7000);
        await Promise.all([this.loadDrive(), this.loadBackups(), this.loadSettings()]);
        this.driveWizard.mode = '';
      } catch (e) { this.toast(e.message, 'error', 7000); }
      finally { this.driveBusy = false; }
    },
    async disableDrive() {
      if (!confirm('Stop using Google Drive? Files already in Drive stay there; new backups go to the local folder.')) return;
      this.drive = await api.post('/api/drive/disable');
      await this.loadSettings();
    },
    async uploadClientSecret(file) {
      if (!file) return;
      try {
        await api.upload('/api/drive/api/client-secret', [['file', file]]);
        this.toast('OAuth client saved. Now click "Connect Google account".', 'ok', 6000);
        this.driveWizard.step = 3;
        await this.loadDrive();
      } catch (e) { this.toast(e.message, 'error', 9000); }
    },
    async connectDriveApi() {
      try {
        await api.post('/api/drive/api/connect');
        this.toast('A Google sign-in page opened in your browser. Approve access, then come back here.', 'info', 9000);
        const started = Date.now();
        const poll = async () => {
          await this.loadDrive();
          if (this.drive?.api.connected) {
            this.toast('Google Drive connected', 'ok');
            this.driveWizard.mode = '';
            return this.loadSettings();
          }
          if (this.drive?.api.oauth.error) return this.toast(`Connection failed: ${this.drive.api.oauth.error}`, 'error', 9000);
          if (Date.now() - started < 300000) setTimeout(poll, 2500);
        };
        setTimeout(poll, 2500);
      } catch (e) { this.toast(e.message, 'error'); }
    },
    async disconnectDriveApi() {
      if (!confirm('Disconnect Google Drive API? Files in Drive are kept.')) return;
      await api.post('/api/drive/api/disconnect');
      await Promise.all([this.loadDrive(), this.loadSettings()]);
    },
    async syncDriveApi() {
      this.driveBusy = true;
      try {
        const r = await api.post('/api/drive/api/sync');
        this.toast(`Drive sync done: ${r.uploaded} uploaded, ${r.unchanged} unchanged, backup ${r.backup}`, 'ok', 7000);
        await this.loadDrive();
      } catch (e) { this.toast(e.message, 'error', 8000); }
      finally { this.driveBusy = false; }
    },
    async pullDriveFiles() {
      this.driveBusy = true;
      try {
        const r = await api.post('/api/drive/api/pull-files');
        this.toast(`Downloaded ${r.downloaded} missing file(s) from Drive`, 'ok');
      } catch (e) { this.toast(e.message, 'error'); }
      finally { this.driveBusy = false; }
    },
    async loadRemoteBackups() {
      try { this.remoteBackups = await api.get('/api/drive/api/backups'); } catch (e) { this.toast(e.message, 'error'); }
    },

    // ------------------------------------------------------------ backups

    async loadBackups() {
      try { this.backups = await api.get('/api/backups'); } catch (e) { this.backups = []; }
    },
    async backupNow() {
      this.driveBusy = true;
      try {
        const r = await api.post('/api/backups');
        this.toast(`Backup created (${r.items} items)`, 'ok');
        await Promise.all([this.loadBackups(), this.loadSettings(), this.loadDrive()]);
      } catch (e) { this.toast(e.message, 'error'); }
      finally { this.driveBusy = false; }
    },
    async restoreBackup(b, fromDrive = false) {
      const label = fromDrive ? b.name : `${b.name} (${b.items ?? '?'} items)`;
      if (!confirm(`Restore ${label}?\n\nYour current library is backed up first. The restore is applied when ReviewHunter restarts.`)) return;
      try {
        const r = await api.post('/api/backups/restore', fromDrive ? { drive_file_id: b.id } : { path: b.path });
        alert(`Backup verified (${r.staged_items} items) and staged.\n\nSafety copy of the current library:\n${r.safety_backup}\n\nClose ReviewHunter (its console window) and start it again from the desktop shortcut to finish restoring.`);
      } catch (e) { this.toast(e.message, 'error', 8000); }
    },

    // ------------------------------------------------------------ system, connector, Word

    async loadSystem() {
      try { this.system = await api.get('/api/system/info'); } catch (e) { this.system = null; }
    },
    async openPath(path) {
      try { await api.post('/api/system/open-path', { path }); } catch (e) { this.toast(e.message, 'error'); }
    },
    async setupWordAddin() {
      try {
        const r = await api.post('/api/system/word-addin/setup');
        this.system = { ...(this.system || {}), word_addin: r };
        this.toast(r.launched ? 'Setup window opened. Read each step and confirm the Windows prompts.' : 'Certificate and manifest prepared.', 'info', 9000);
      } catch (e) { this.toast(e.message, 'error'); }
    },
    async reindexFulltext(force) {
      this.toast('Indexing attachment text...', 'info');
      const r = await api.post(`/api/fulltext/reindex${force ? '?force=true' : ''}`);
      this.toast(`Indexed ${r.attachments} attachment(s)`, 'ok');
      this.fulltextStats = await api.get('/api/fulltext/stats');
    },
  };
};
