/* Bulk publication import: captured destination, resumable progress and per-entry evidence. */
window.bulkImportPart = function bulkImportPart() {
  const STORAGE_KEY = 'rh.bulkImportJob';
  const TERMINAL = new Set(['completed', 'cancelled', 'failed']);
  const fresh = collectionId => ({ mode: 'text', text: '', files: [], folderFiles: [], folderPath: '', recursive: true,
    collectionId: collectionId ? String(collectionId) : '', newName: '', autoSearch: true, autoDownload: true,
    submitting: false, job: null, error: '', notice: '', resultFilter: 'all' });
  let pollTimer = null, polling = false;

  return {
    importState: fresh(null),

    /** Reopening a running import preserves its original destination and progress. */
    openImport(collectionId) {
      if (!this.importState.job && !this.importState.text && !this.importState.files.length && !this.importState.folderPath) {
        const destination = collectionId === undefined ? (this.virtual === 'collection' ? this.selectedCollectionId : null) : collectionId;
        this.importState = fresh(destination);
      }
      // An explicit collection action overrides a draft, while accepted jobs stay fixed.
      if (collectionId !== undefined && !this.importState.job && !this.importState.submitting) {
        this.importState.collectionId = collectionId ? String(collectionId) : '';
      }
      this.openDialog('import');
      if (this.bulkImportActive()) this.pollBulkImport();
    },

    /** Clear a finished report only when the user explicitly starts another import. */
    newBulkImport(collectionId) {
      if (this.bulkImportActive() || this.importState.submitting) return;
      clearTimeout(pollTimer);
      try { localStorage.removeItem(STORAGE_KEY); } catch (_) { /* Storage is optional. */ }
      this.importState = fresh(collectionId === undefined ? (this.virtual === 'collection' ? this.selectedCollectionId : null) : collectionId);
    },

    /** Restore only a server job ID, never document content or private folder paths. */
    async restoreBulkImport() {
      let id;
      try { id = localStorage.getItem(STORAGE_KEY); } catch (_) { return; }
      if (!id) return;
      try {
        this.importState.job = await api.get('/api/bulk-import/' + encodeURIComponent(id));
        if (this.bulkImportActive()) this.pollBulkImport();
      } catch (_) {
        // An expired job or server restart must not prevent normal library startup.
        try { localStorage.removeItem(STORAGE_KEY); } catch (_) { /* Storage is optional. */ }
      }
    },

    bulkImportActive() { return !!this.importState.job && !TERMINAL.has(this.importState.job.status); },
    bulkImportLocked() { return this.importState.submitting || this.bulkImportActive(); },
    bulkImportCanStart() {
      const state = this.importState;
      if (this.bulkImportLocked() || state.job || state.collectionId === '__new__' && !state.newName.trim()) return false;
      return state.mode === 'text' ? !!state.text.trim() : state.mode === 'folder' ? !!state.folderPath.trim() || !!state.files.length : !!state.files.length;
    },

    /** Folder mode cannot inherit a CSV or Word file selection from another source. */
    setImportMode(mode) {
      if (this.bulkImportLocked()) return;
      if (mode === 'folder' && this.importState.mode !== 'folder') {
        this.importState.files = [];
        this.importState.folderFiles = [];
        this.importState.notice = '';
      }
      this.importState.mode = mode;
      this.importState.error = '';
    },

    /** Selected files remain File objects; binary PDFs and Word files are never decoded as text. */
    onImportFiles(files, folder = false) {
      if (this.bulkImportLocked()) return;
      const incoming = Array.from(files || []);
      this.importState.folderFiles = folder ? incoming : [];
      const allowed = folder ? /\.pdf$/i : /\.(pdf|csv|tsv|docx|bib|bibtex|ris|json|txt)$/i;
      const accepted = incoming.filter(file => allowed.test(file.name) && (!folder || this.importState.recursive || (file.webkitRelativePath || file.name).split('/').length <= 2));
      const rejected = incoming.length - accepted.length;
      this.importState.files = accepted;
      this.importState.mode = folder ? 'folder' : 'files';
      this.importState.folderPath = '';
      this.importState.error = '';
      this.importState.notice = rejected ? `${rejected} unsupported files or excluded subfolder files were not selected. Word files must be saved as .docx.` : '';
      if (!accepted.length) this.importState.error = folder ? 'No PDF files were selected.' : 'Choose PDF, CSV, Word (.docx), text, BibTeX, RIS or CSL-JSON files.';
    },
    /** Reapply recursion to the original folder selection, without losing omitted files. */
    refreshImportFolder() {
      if (this.importState.folderFiles.length) this.onImportFiles(this.importState.folderFiles, true);
    },
    onImportFile(file) { if (file) this.onImportFiles([file]); },
    removeImportFile(index) {
      if (this.bulkImportLocked()) return;
      const [removed] = this.importState.files.splice(index, 1);
      this.importState.folderFiles = this.importState.folderFiles.filter(file => file !== removed);
    },

    async importFromClipboard() {
      if (this.bulkImportLocked()) return;
      try {
        this.importState.text = await navigator.clipboard.readText();
        this.importState.mode = 'text';
        if (!this.importState.text.trim()) this.importState.error = 'Clipboard is empty.';
      } catch (_) { this.importState.error = 'Paste into the text box with Ctrl+V; browser clipboard access is unavailable.'; }
    },

    /** The entire request uses one destination snapshot, independent of later navigation. */
    async submitImport() {
      if (!this.bulkImportCanStart()) return;
      const state = this.importState;
      state.submitting = true; state.error = '';
      try {
        const fields = {
          text: state.mode === 'text' ? state.text : '',
          folder_path: state.mode === 'folder' ? state.folderPath.trim() : '', recursive: state.recursive,
          collection_id: state.collectionId && state.collectionId !== '__new__' ? Number(state.collectionId) : null,
          new_collection_name: state.collectionId === '__new__' ? state.newName.trim() : '',
          auto_search: state.autoSearch, auto_download: state.autoDownload,
        };
        const files = state.mode === 'text' || fields.folder_path ? [] : state.files.map(file => ['files', file]);
        state.job = await api.upload('/api/bulk-import', files, fields);
        try { localStorage.setItem(STORAGE_KEY, state.job.job_id); } catch (_) { /* Polling works without persistence. */ }
        await this.pollBulkImport();
      } catch (error) { state.error = error.message || 'The import could not be started.'; }
      finally { state.submitting = false; }
    },

    /** Polling survives a closed dialog and tolerates transient connection failures. */
    async pollBulkImport() {
      const state = this.importState, id = state.job?.job_id;
      if (!id || polling) return;
      clearTimeout(pollTimer); polling = true;
      try {
        const job = await api.get('/api/bulk-import/' + encodeURIComponent(id));
        if (state !== this.importState || state.job?.job_id !== id) return;
        state.job = job; state.error = '';
        if (TERMINAL.has(job.status)) await this.reloadAll();
      } catch (error) { state.error = 'Progress temporarily unavailable. ' + error.message; }
      finally {
        polling = false;
        if (state === this.importState && this.bulkImportActive()) pollTimer = setTimeout(() => this.pollBulkImport(), state.error ? 4000 : 1000);
      }
    },
    async cancelBulkImport() {
      const job = this.importState.job;
      if (!this.bulkImportActive() || job.status === 'cancelling') return;
      try {
        this.importState.job = await api.post('/api/bulk-import/' + encodeURIComponent(job.job_id) + '/cancel');
        this.pollBulkImport();
      } catch (error) { this.importState.error = error.message; }
    },

    bulkImportProgress() { const job = this.importState.job; return job ? Math.round(100 * (job.completed || 0) / Math.max(1, job.total || 0)) : 0; },
    bulkImportDestination() {
      const state = this.importState;
      if (state.job) return state.job.collection?.name || 'My Library';
      if (state.collectionId === '__new__') return state.newName.trim() || 'New collection';
      return state.collectionId ? this.collectionPath(Number(state.collectionId)) : 'My Library';
    },
    bulkImportCounts() {
      const rows = this.importState.job?.records || [];
      return { added: rows.filter(row => row.created).length, matched: rows.filter(row => row.matched).length,
        pdf: rows.filter(row => row.pdf).length, review: rows.filter(row => this.bulkImportNeedsReview(row)).length };
    },
    /** A saved citation can still need attention for missing metadata or a failed PDF. */
    bulkImportNeedsReview(row) {
      return !!row.error || ['failed', 'needs_review', 'not_found'].includes(row.status)
        || ['failed', 'not_found'].includes(row.pdf_status) || row.metadata_status === 'partial' || !!row.warnings?.length;
    },
    bulkImportRows() {
      const rows = this.importState.job?.records || [];
      return this.importState.resultFilter === 'review' ? rows.filter(row => this.bulkImportNeedsReview(row)) : rows;
    },
    bulkImportPdfStatus(status) {
      return ({ not_requested: 'PDF lookup not requested', not_found: 'PDF not found', failed: 'PDF download failed',
        downloaded: 'PDF downloaded', attached: 'PDF attached', existing: 'PDF available' })[status] || '';
    },
    bulkImportStatus(status) {
      return ({ queued: 'Queued', running: 'Identifying and importing', cancelling: 'Stopping after this entry', completed: 'Import complete',
        cancelled: 'Cancelled', created: 'Added', matched: 'Already in library', pdf: 'PDF attached', needs_review: 'Review needed', not_found: 'Not found', failed: 'Failed' })[status] || status;
    },
    async openBulkImportItem(id) { this.closeDialog(); await this.openItem(id); },

    /** Export a durable text report without exposing the app's internal job storage. */
    exportBulkImportReport() {
      const job = this.importState.job;
      if (!job) return;
      const cell = value => '"' + String(value ?? '').replace(/"/g, '""').replace(/^([=+@-])/, "'$1") + '"';
      const rows = [['source', 'query', 'status', 'item_id', 'pdf', 'error', 'warnings'],
        ...(job.records || []).map(row => [row.source, row.query, row.status, row.item_id, !!row.pdf, row.error, (row.warnings || []).join('; ')])];
      const url = URL.createObjectURL(new Blob(['\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = 'ReviewHunter-import-report.csv'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  };
};
