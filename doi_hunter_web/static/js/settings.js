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
    /** /api/system/instance and /api/system/shortcuts (Settings > Advanced > Application). */
    appInstance: null,
    appShortcuts: null,
    appStopped: false,
    appRestarting: false,
    browserLauncher: null,
    /** /api/system/integrations: installed browsers + Word add-in state (one-click install buttons). */
    integrations: null,
    integrationBusy: '',
    integrationMessage: '',
    integrationFor: '',
    fulltextStats: null,
    /** "Test the sources" panel (Settings > PDF Sources). */
    sourcesTest: { doi: '', running: false, result: null },

    async loadSettings() {
      this.settings = await api.get('/api/settings');
    },
    /** Save a list setting typed as lines or a comma-separated string (mirror lists). */
    async saveListSetting(key, text) {
      const items = String(text || '').split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
      await this.saveSetting(key, items);
    },
    /** Run every PDF resolver for a DOI and show candidates + diagnostics. */
    async testSources() {
      const doi = (this.sourcesTest.doi || '').trim();
      if (!doi) return this.toast('Enter a DOI first', 'error');
      this.sourcesTest.running = true;
      this.sourcesTest.result = null;
      try {
        this.sourcesTest.result = await api.get(`/api/pdf-sources/diagnose?doi=${encodeURIComponent(doi)}`);
      } catch (e) { this.toast(e.message, 'error', 6000); }
      this.sourcesTest.running = false;
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
      await Promise.all([this.loadSettings(), this.loadStyles(), this.loadDrive(), this.loadBackups(), this.loadSystem(),
                         this.loadZotero(), this.loadShared()]);
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
      await Promise.all([this.loadAppInstance(), this.loadBrowserLauncher(), this.loadIntegrations()]);
    },

    /** Installed browsers and the Word add-in state. */
    async loadIntegrations() {
      try { this.integrations = await api.get('/api/system/integrations'); } catch (e) { this.integrations = null; }
    },
    /** Browsers offered as one-click install buttons, installed ones first. */
    integrationBrowsers() {
      const names = { chrome: 'Chrome', edge: 'Edge', brave: 'Brave', firefox: 'Firefox', safari: 'Safari' };
      const found = this.integrations?.browsers || {};
      return Object.keys(names).filter(k => k in found)
        .map(k => ({ key: k, label: names[k], present: !!found[k] }))
        .sort((a, b) => b.present - a.present);
    },
    /**
     * Same action as the "ReviewHunter for Firefox / Word" shortcuts: the server opens the browser, the
     * download or the setup, and every security confirmation is made by the person in that window.
     */
    async installIntegration(name) {
      this.integrationBusy = name;
      this.integrationMessage = '';
      try {
        const r = await api.post(`/api/system/integrations/${name}`, {});
        this.integrationMessage = r.message || '';
        this.integrationFor = name;
        this.toast(r.ok ? 'Follow the window that opened' : (r.message || 'Not available'), r.ok ? 'info' : 'error', 9000);
        if (name === 'word') this.watchWordSetup();
      } catch (e) { this.toast(e.message, 'error', 8000); }
      this.integrationBusy = '';
    },
    /** The Word setup runs in its own process: refresh the status while the person answers Windows. */
    watchWordSetup() {
      const until = Date.now() + 180000;
      const tick = async () => {
        await this.loadSystem();
        const w = this.system?.word_addin;
        if ((w && w.ca_trusted && w.registered) || Date.now() > until) return;
        setTimeout(tick, 3000);
      };
      setTimeout(tick, 3000);
    },

    /** Native-messaging registration that lets the extension start ReviewHunter. */
    async loadBrowserLauncher() {
      try { this.browserLauncher = await api.get('/api/system/browser-launcher'); } catch (e) { this.browserLauncher = null; }
    },
    browserLauncherReady() {
      return !!(this.browserLauncher?.browsers || []).some(b => b.browser === 'chrome' && b.linked);
    },
    async repairBrowserLauncher() {
      try {
        const r = await api.post('/api/system/browser-launcher/repair', {});
        this.toast(r.skipped ? `Not managed here (${r.skipped})` : (r.ok ? 'The browser can start ReviewHunter' : (r.errors || []).join('; ')), r.ok ? 'ok' : 'error', 6000);
        await this.loadBrowserLauncher();
      } catch (e) { this.toast(e.message, 'error', 6000); }
    },

    // ------------------------------------------------------------ application (launcher, shortcuts)

    /** Running instance (version, folder, pid) and the shortcuts that should start it. */
    async loadAppInstance() {
      try {
        const [instance, shortcuts] = await Promise.all([api.get('/api/system/instance'), api.get('/api/system/shortcuts')]);
        this.appInstance = instance;
        this.appShortcuts = shortcuts;
      } catch (e) { this.appInstance = null; this.appShortcuts = null; }
    },
    shortcutLabel(s) {
      const kind = { menu: navigator.platform.startsWith('Mac') ? 'Applications folder' : 'Start menu / app menu', desktop: 'Desktop' }[s.kind] || s.kind;
      if (!s.exists) return `${kind}: none`;
      return `${kind}: ${s.linked ? 'starts this installation' : 'points elsewhere'}`;
    },
    async repairShortcuts() {
      try {
        const r = await api.post('/api/system/shortcuts/repair', {});
        if (r.skipped) this.toast(`Shortcuts are not managed here (${r.skipped})`, 'info', 6000);
        else this.toast(r.ok ? `Shortcuts OK (${r.repaired.length} repaired, ${r.created.length} created)` : (r.errors || []).join('; '), r.ok ? 'ok' : 'error', 6000);
        await this.loadAppInstance();
      } catch (e) { this.toast(e.message, 'error', 6000); }
    },
    async restartApp() {
      try {
        await api.post('/api/system/restart', {});
        this.appRestarting = true;
        this.closeDialog();
        this.toast('Restarting ReviewHunter...', 'info', 8000);
        // The page reconnects by itself (WebSocket); reload once the new server answers.
        const until = Date.now() + 90000;
        const wait = async () => {
          if (Date.now() > until) { this.appRestarting = false; return this.toast('ReviewHunter did not come back; open it from its shortcut', 'error', 10000); }
          try {
            const i = await api.get('/api/system/instance');
            if (i.pid !== this.appInstance?.pid) return location.reload();
          } catch (e) { /* still restarting */ }
          setTimeout(wait, 800);
        };
        setTimeout(wait, 1500);
      } catch (e) { this.toast(e.message, 'error', 8000); }
    },
    async quitApp() {
      if (!confirm('Quit ReviewHunter?\n\nThe Chrome connector and Word add-in stop working until you open it again from its shortcut.')) return;
      try {
        await api.post('/api/system/shutdown', {});
        this.closeDialog();
        this.appStopped = true;
      } catch (e) { this.toast(e.message, 'error', 8000); }
    },
    async openPath(path) {
      try { await api.post('/api/system/open-path', { path }); } catch (e) { this.toast(e.message, 'error'); }
    },
    /** Detailed setup (PowerShell window that explains each step), kept for people who want to read it. */
    async setupWordAddin() {
      try {
        const r = await api.post('/api/system/word-addin/setup');
        this.system = { ...(this.system || {}), word_addin: r };
        this.toast(r.launched ? 'Setup window opened. Read each step and confirm the Windows prompts.' : 'Certificate and manifest prepared.', 'info', 9000);
        if (r.launched) this.watchWordSetup();
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
