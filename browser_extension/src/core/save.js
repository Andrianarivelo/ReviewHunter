/**
 * Save orchestration (runs in the service worker so that it survives the popup
 * closing). Reports progress as a checklist of steps.
 *
 * @module core/save
 */

import * as api from "../api.js";
import { acquirePdf, shortUrl } from "./pdf.js";
import { acquireFromShadowLibraries, openForUser } from "./shadow.js";
import { snapshotHtml } from "./detect.js";
import { toPayload } from "../extract/index.js";
import { addPending, removePending, getSettings } from "../settings.js";
import { fileNameFromUrl } from "../shared/identifiers.js";

/** Item types for which an HTML snapshot makes sense. */
const WEB_TYPES = new Set(["webpage", "blogPost", "newspaperArticle", "magazineArticle", "encyclopediaArticle", "forumPost"]);

/**
 * @typedef {object} Step
 * @property {string} id
 * @property {string} label
 * @property {"pending"|"running"|"done"|"error"|"skipped"|"waiting"} state  waiting: needs the user (human check)
 * @property {string} [detail]
 */

/**
 * @typedef {object} SaveRequest
 * @property {?number} tabId          Tab the detection came from.
 * @property {object} detection       Detection result (kind, items, url).
 * @property {number[]} [selected]    Indexes of items to save (multiple pages).
 * @property {object} [overrides]     Field overrides for a single item (e.g. edited title).
 * @property {?number} collectionId
 * @property {string[]} tags
 * @property {string} [note]
 * @property {boolean} attachPdf
 * @property {boolean} enrich
 * @property {boolean} [snapshot]
 */

/**
 * Small progress tracker producing immutable snapshots for the UI.
 */
class Progress {
  /**
   * @param {(steps: Step[]) => void} emit  Called on every change.
   */
  constructor(emit) {
    this.steps = [];
    this.emit = emit || (() => {});
    /** True when a PDF will be attached later, after the user completes a human check. */
    this.watching = false;
  }

  /**
   * Add or update a step and notify.
   *
   * @param {string} id  Step id.
   * @param {Partial<Step>} patch  Fields to set.
   * @returns {void}
   */
  set(id, patch) {
    let step = this.steps.find((s) => s.id === id);
    if (!step) {
      step = { id, label: id, state: "pending", detail: "" };
      this.steps.push(step);
    }
    Object.assign(step, patch);
    this.emit(this.steps.map((s) => ({ ...s })));
  }
}

/**
 * Human readable byte size.
 *
 * @param {number} n  Bytes.
 * @returns {string} "1.2 MB" style.
 */
function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Download a PDF in the browser and attach it to an item, updating a "pdf" step.
 *
 * Order: page / publisher candidates (user's session), then, when the item has a DOI and the
 * option is on, Sci-Hub / SciDB fetched from the browser. If those only show a human check,
 * the page is opened for the user and the item is remembered with `watch: true`; background.js
 * attaches the PDF as soon as the check is completed (never solved automatically).
 *
 * @param {Progress} progress  Tracker (progress.watching is set when waiting for the user).
 * @param {string} stepId      Step id to update.
 * @param {{id: number, title: string}} saved  Saved item.
 * @param {string[]} candidates  PDF URLs.
 * @param {{tabId?: ?number, allowBackgroundTab: boolean, doi?: string, pageUrl?: string, rememberFailure?: boolean,
 *   shadow?: boolean}} opts
 * @returns {Promise<boolean>} True when attached.
 */
async function attachPdfTo(progress, stepId, saved, candidates, opts) {
  const onStep = (text) => progress.set(stepId, { detail: text });
  let res = { ok: false, attempts: [] };
  if (candidates.length) {
    progress.set(stepId, { state: "running", label: "Looking for the PDF", detail: shortUrl(candidates[0]) });
    res = await acquirePdf(candidates, { tabId: opts.tabId, allowBackgroundTab: opts.allowBackgroundTab, onStep });
  }
  let blocked = null;
  if (!res.ok && opts.shadow && opts.doi) {
    progress.set(stepId, { state: "running", label: candidates.length ? "Publisher PDF not reachable: trying Sci-Hub / SciDB" : "Trying Sci-Hub / SciDB" });
    const sh = await acquireFromShadowLibraries(opts.doi, { onStep });
    if (sh.ok) res = sh;
    else {
      res = { ok: false, attempts: [...res.attempts, ...sh.attempts] };
      blocked = sh.blocked;
    }
  }
  if (!res.ok) {
    if (!candidates.length && !(opts.shadow && opts.doi)) {
      progress.set(stepId, { state: "skipped", label: "No PDF link found on this page" });
      return false;
    }
    const last = res.attempts[res.attempts.length - 1];
    if (blocked) {
      // The user completes the check; background.js sees the article page and attaches the PDF.
      await addPending({ itemId: saved.id, title: saved.title, doi: opts.doi || "", urls: [...candidates, blocked.url], pageUrl: opts.pageUrl || "", watch: true });
      const tabId = await openForUser(blocked.url, opts.tabId);
      progress.watching = true;
      progress.set(stepId, {
        state: "waiting",
        label: `${blocked.source.replace(/\s*\(.*\)$/, "")} asks you to confirm you are human`,
        detail: tabId !== null
          ? `Opened ${shortUrl(blocked.url)} in a new tab. Complete the check there: the PDF will be attached automatically and you will get a notification.`
          : `Open ${blocked.url} in a tab and complete the check: the PDF will then be attached automatically.`,
      });
      return false;
    }
    if (opts.rememberFailure !== false) {
      await addPending({ itemId: saved.id, title: saved.title, doi: opts.doi || "", urls: candidates, pageUrl: opts.pageUrl || "" });
    }
    const shadowTried = res.attempts.filter((a) => /Sci-Hub|SciDB/.test(a.via));
    const shadowUnreachable = shadowTried.length && shadowTried.every((a) => /network error|timed out/.test(a.error));
    progress.set(stepId, {
      state: "error",
      label: "Couldn't fetch the PDF automatically",
      detail: shadowUnreachable
        ? "Sci-Hub and SciDB could not be reached from the extension (web filter, DNS block or untrusted certificate). If you open the article page yourself and it loads, the PDF is attached automatically."
        : `${last ? `${last.via}: ${last.error}. ` : ""}Open the PDF in a tab and click the ReviewHunter button there to attach it.`,
    });
    return false;
  }
  progress.set(stepId, { state: "done", label: `PDF found via ${res.via}`, detail: `${formatBytes(res.bytes.length)} from ${shortUrl(res.finalUrl)}` });
  const upId = `${stepId}-upload`;
  progress.set(upId, { state: "running", label: "Attaching PDF" });
  try {
    const previousId = saved.id;
    const uploaded = await api.uploadAttachment(saved.id, res.bytes, { url: res.finalUrl, title: "Full Text PDF", contentType: "application/pdf" });
    saved.id = uploaded.item_id || saved.id;
    await removePending(previousId);
    if (saved.id !== previousId) await removePending(saved.id);
    progress.set(upId, { state: "done", label: "PDF attached" });
    return true;
  } catch (e) {
    progress.set(upId, { state: "error", label: "PDF upload failed", detail: e.message });
    return false;
  }
}

/**
 * Save what the popup (or a context menu) asked for.
 *
 * @param {SaveRequest} req  Request.
 * @param {(steps: Step[]) => void} onProgress  Progress callback.
 * @returns {Promise<{ok: boolean, itemIds: number[], message: string}>} Summary.
 */
export async function runSave(req, onProgress) {
  const progress = new Progress(onProgress);
  const settings = await getSettings();
  const det = req.detection || {};
  const common = { collection_id: req.collectionId || null, tags: req.tags || [], note: req.note || undefined };
  try {
    if (det.kind === "pdf") return await savePdfTab(req, progress, common);
    if (det.kind === "multiple") return await saveMultiple(req, progress, common);
    if (det.kind !== "single" || !det.items || !det.items.length) {
      return { ok: false, itemIds: [], message: "Nothing to save on this page." };
    }

    const item = { ...det.items[0], ...(req.overrides || {}) };
    progress.set("meta", { state: "running", label: "Saving metadata" });
    const payload = toPayload(item);
    delete payload.pdf_url;
    const { saved } = await api.saveItems({ items: [payload], ...common, enrich: !!req.enrich, pdf_url: item.pdf_url || undefined });
    const s = saved[0];
    progress.set("meta", {
      state: "done",
      label: s.existing ? "Already in library: updated folder, tags and note" : "Metadata saved",
      detail: s.title,
    });

    if (req.attachPdf) {
      if (!s.needs_pdf) {
        progress.set("pdf", { state: "skipped", label: "A file is already attached" });
      } else {
        const candidates = [...new Set([...(item._pdfCandidates || []), s.pdf_url].filter(Boolean))];
        await attachPdfTo(progress, "pdf", s, candidates, {
          tabId: req.tabId,
          allowBackgroundTab: settings.useBackgroundTab,
          doi: item.doi,
          pageUrl: det.url,
          shadow: !!settings.useShadowLibraries,
        });
      }
    }

    if (req.snapshot && WEB_TYPES.has(item.item_type) && req.tabId !== null && req.tabId !== undefined) {
      progress.set("snap", { state: "running", label: "Saving page snapshot" });
      const html = await snapshotHtml(req.tabId);
      if (html) {
        try {
          await api.uploadAttachment(s.id, new Blob([html], { type: "text/html" }), { kind: "snapshot", title: "Snapshot", url: det.url, contentType: "text/html" });
          progress.set("snap", { state: "done", label: "Snapshot saved", detail: formatBytes(html.length) });
        } catch (e) {
          progress.set("snap", { state: "error", label: "Snapshot failed", detail: e.message });
        }
      } else {
        progress.set("snap", { state: "error", label: "Snapshot failed", detail: "the page could not be read" });
      }
    }
    return { ok: true, itemIds: [s.id], message: s.existing ? "Updated existing item" : "Saved", watching: progress.watching };
  } catch (e) {
    progress.set("fatal", { state: "error", label: "Saving failed", detail: e.message });
    return { ok: false, itemIds: [], message: e.message };
  }
}

/**
 * Download a PDF and let the server identify its contents before choosing the
 * reference. URL metadata is a hint, never a reason to create a provisional item.
 *
 * @param {SaveRequest} req  Request.
 * @param {Progress} progress  Tracker.
 * @param {object} common  collection / tags / note.
 * @returns {Promise<{ok: boolean, itemIds: number[], message: string}>}
 */
async function savePdfTab(req, progress, common) {
  const det = req.detection;
  const item = { ...(det.items || [])[0], ...(req.overrides || {}) };
  const settings = await getSettings();
  progress.set("pdf", { state: "running", label: "Downloading this PDF" });
  const candidates = [...new Set([det.url, ...(item._pdfCandidates || []), item.pdf_url].filter(Boolean))];
  const res = await acquirePdf(candidates, { tabId: req.tabId, allowBackgroundTab: !!settings.useBackgroundTab, onStep: (t) => progress.set("pdf", { detail: t }) });
  if (!res.ok) {
    const last = res.attempts[res.attempts.length - 1];
    const fallback = "Reopen the PDF from the article page to refresh its link, or save it with the PDF viewer's download button and import that file into ReviewHunter.";
    progress.set("pdf", { state: "error", label: "Couldn't read this PDF", detail: `${last ? `${last.via}: ${last.error}. ` : ""}${fallback}` });
    return { ok: false, itemIds: [], message: fallback };
  }
  progress.set("pdf", { state: "done", label: `PDF downloaded via ${res.via}`, detail: formatBytes(res.bytes.length) });

  progress.set("up", { state: "running", label: "Reading PDF metadata and matching its reference" });
  const metadata = toPayload(item);
  delete metadata.pdf_url;
  const out = await api.importPdf(new Blob([res.bytes], { type: "application/pdf" }), fileNameFromUrl(res.finalUrl || det.url), common.collection_id, {
    metadata, sourceUrl: res.finalUrl || det.url, tags: common.tags, note: common.note,
  });
  if (!out.imported || !out.item_ids?.length) throw new Error("the server did not confirm the PDF import");
  for (const id of out.item_ids) await removePending(id);
  const result = out.results?.[0];
  const message = result?.metadata_status === "partial" ? "PDF saved; some metadata could not be verified" : "PDF saved";
  progress.set("up", { state: "done", label: result?.metadata_status === "partial" ? message : "PDF attached to its reference", detail: result?.title || "" });
  return { ok: true, itemIds: out.item_ids, message };
}

/**
 * Save the selected entries of a multi-item page.
 * Entries with metadata go through /connector/save (one request), identifier-only
 * entries through /connector/identifiers; PDFs are then fetched without opening tabs.
 *
 * @param {SaveRequest} req  Request.
 * @param {Progress} progress  Tracker.
 * @param {object} common  collection / tags / note.
 * @returns {Promise<{ok: boolean, itemIds: number[], message: string}>}
 */
async function saveMultiple(req, progress, common) {
  const det = req.detection;
  const chosen = (req.selected || []).map((i) => det.items[i]).filter(Boolean);
  if (!chosen.length) return { ok: false, itemIds: [], message: "Nothing selected" };
  const withMeta = chosen.filter((i) => !i._idOnly);
  const idOnly = chosen.filter((i) => i._idOnly);
  /** @type {Array<{saved: object, item: ?object}>} */
  const results = [];
  let failed = [];

  if (withMeta.length) {
    progress.set("meta", { state: "running", label: `Saving ${withMeta.length} item${withMeta.length > 1 ? "s" : ""}` });
    const { saved } = await api.saveItems({ items: withMeta.map((i) => { const p = toPayload(i); delete p.pdf_url; return p; }), ...common, enrich: !!req.enrich });
    saved.forEach((s, idx) => results.push({ saved: s, item: withMeta[idx] }));
    progress.set("meta", { state: "done", label: `${saved.length} item${saved.length > 1 ? "s" : ""} saved`, detail: `${saved.filter((s) => s.existing).length} already in library` });
  }
  if (idOnly.length) {
    progress.set("ids", { state: "running", label: `Resolving ${idOnly.length} identifier${idOnly.length > 1 ? "s" : ""}` });
    const text = idOnly.map((i) => i.doi || (i.arxiv_id ? `arXiv:${i.arxiv_id}` : "") || (i.pmid ? `PMID: ${i.pmid}` : "")).filter(Boolean).join("\n");
    const out = await api.saveIdentifiers({ text, collection_id: common.collection_id, tags: common.tags });
    out.saved.forEach((s) => results.push({ saved: s, item: null }));
    failed = out.failed || [];
    progress.set("ids", {
      state: failed.length && !out.saved.length ? "error" : "done",
      label: `${out.saved.length} resolved`,
      detail: failed.length ? `Not found: ${failed.slice(0, 5).join(", ")}${failed.length > 5 ? "..." : ""}` : "",
    });
  }

  if (req.attachPdf) {
    const todo = results.filter((r) => r.saved.needs_pdf && ((r.item && r.item._pdfCandidates && r.item._pdfCandidates.length) || r.saved.pdf_url)).slice(0, 25);
    let attached = 0;
    for (const [n, r] of todo.entries()) {
      const candidates = [...new Set([...((r.item && r.item._pdfCandidates) || []), r.saved.pdf_url].filter(Boolean))];
      progress.set("pdfs", { state: "running", label: `PDFs ${n + 1}/${todo.length}`, detail: r.saved.title });
      const res = await acquirePdf(candidates, { tabId: req.tabId, allowBackgroundTab: false });
      if (res.ok) {
        try {
          const uploaded = await api.uploadAttachment(r.saved.id, res.bytes, { url: res.finalUrl, title: "Full Text PDF" });
          r.saved.id = uploaded.item_id || r.saved.id;
          attached += 1;
        } catch (_) {
          /* counted as not attached */
        }
      }
    }
    progress.set("pdfs", todo.length
      ? { state: attached ? "done" : "error", label: `${attached} of ${todo.length} PDF${todo.length > 1 ? "s" : ""} attached`, detail: attached < todo.length ? "Open the missing ones in a tab to attach them" : "" }
      : { state: "skipped", label: "No PDF links for these items" });
  }
  return { ok: results.length > 0, itemIds: results.map((r) => r.saved.id), message: `${results.length} saved` };
}

/**
 * Attach the PDF shown in a tab to an item saved earlier without its PDF.
 *
 * @param {{tabId: number, url: string, itemId: number, title: string}} req  Request.
 * @param {(steps: Step[]) => void} onProgress  Progress callback.
 * @returns {Promise<{ok: boolean, itemIds: number[], message: string}>}
 */
export async function runAttachPending(req, onProgress) {
  const progress = new Progress(onProgress);
  try {
    const saved = { id: req.itemId, title: req.title };
    const ok = await attachPdfTo(progress, "pdf", saved, [req.url], {
      tabId: req.tabId,
      allowBackgroundTab: false,
      rememberFailure: false,
    });
    return { ok, itemIds: [saved.id], message: ok ? "PDF attached" : "PDF not attached" };
  } catch (e) {
    progress.set("fatal", { state: "error", label: "Attaching failed", detail: e.message });
    return { ok: false, itemIds: [], message: e.message };
  }
}
