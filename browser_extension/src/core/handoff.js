/**
 * Browser hand-off: fetch a PDF the ReviewHunter server could not get, with the user's session.
 *
 * The server builds a *plan* (ordered steps) when its own download fails: open-access copies
 * behind a JavaScript check (Europe PMC, PubMed Central), publisher PDFs that need the
 * institutional session, Sci-Hub proxies whose PDF host wants the article page as Referer,
 * SciDB pages behind DDoS-Guard. Each step is {url, source, kind: "pdf"|"page", referer, wall, note}.
 *
 * Strategies, per step:
 *   - "pdf" without referer: fetch from the service worker; if the answer is a JavaScript wall,
 *     open the URL in a background tab (the check clears alone) and read the PDF from there;
 *   - "pdf" with referer, and "page": open the referer / page in a background tab, wait until
 *     it is no longer a wall, then fetch the PDF *from the page context* (same cookies and
 *     Referer as a click in that page); a page is parsed for its viewer object / PDF link;
 *   - an interactive wall (ALTCHA, CAPTCHA) is never solved: the page is opened for the user, the
 *     item is remembered as pending and background.js attaches the PDF once the article appears.
 * Bytes are accepted only when they start with %PDF-; the upload is verified with the server.
 *
 * Everything that touches chrome.* is injected through `deps`, so the harness (test/handoff_test.js)
 * can run the orchestration in a plain page.
 *
 * @module core/handoff
 */

import * as api from "../api.js";
import { fetchInWorker, fetchInTab, shortUrl } from "./pdf.js";
import { openForUser } from "./shadow.js";
import { addPending } from "../settings.js";
import { classifyWall, findPdfLinkInHtml } from "../shared/identifiers.js";

/** How long a JavaScript wall may take to clear in a background tab (ms). */
export const WALL_WAIT_MS = 40000;
/** Polling interval while a background tab loads (ms). */
const POLL_MS = 900;
/** Give up on one step after this delay (ms). */
const STEP_TIMEOUT_MS = 75000;

/**
 * Injected into a tab: describe the loaded document (self-contained, serialised by chrome.scripting).
 *
 * @returns {{contentType: string, href: string, html: string}}
 */
function pageProbe() {
  const html = document.contentType && document.contentType.includes("html") ? document.documentElement.outerHTML : "";
  return { contentType: document.contentType || "", href: location.href, html: html.length < 400000 ? html : html.slice(0, 400000) };
}

/** Default dependencies (real Chrome APIs). */
export function defaultDeps() {
  return {
    fetchInWorker,
    fetchInTab,
    createTab: (url) => chrome.tabs.create({ url, active: false }),
    getTab: (id) => chrome.tabs.get(id),
    updateTab: (id, url) => chrome.tabs.update(id, { url }).catch(() => {}),
    removeTab: (id) => chrome.tabs.remove(id).catch(() => {}),
    probe: async (tabId) => {
      try {
        const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: pageProbe });
        return result || null;
      } catch (_) {
        return null;
      }
    },
    openForUser,
    addPending,
    upload: (itemId, bytes, meta) => api.uploadAttachment(itemId, bytes, meta),
    itemStatus: (itemId) => api.itemStatus(itemId),
    report: (itemId, state, message, step) => api.handoffStatus(itemId, { state, message, step }).catch(() => null),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  };
}

/**
 * Open `pageUrl` in a background tab, wait for walls to clear, then get the PDF from the page context.
 *
 * @param {string} pageUrl   Page to load (article page, or the PDF URL itself when no referer is needed).
 * @param {string} pdfUrl    PDF to fetch from that page ("" = find the viewer / link in the page).
 * @param {object} deps      Injected dependencies (see defaultDeps).
 * @param {(text: string) => void} onStep  Progress callback.
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, error?: string, wall?: string, wallUrl?: string}>}
 */
export async function fetchFromPage(pageUrl, pdfUrl, deps, onStep = () => {}) {
  let tab;
  try {
    tab = await deps.createTab(pageUrl);
  } catch (e) {
    return { ok: false, error: `cannot open a tab (${(e && e.message) || e})` };
  }
  const deadline = Date.now() + STEP_TIMEOUT_MS;
  let wallSince = 0;
  let lastError = "the page never showed the article";
  let unprobable = 0;
  try {
    while (Date.now() < deadline) {
      await deps.sleep(POLL_MS);
      let t;
      try {
        t = await deps.getTab(tab.id);
      } catch (_) {
        return { ok: false, error: "the tab was closed" };
      }
      if (!t || t.status !== "complete" || !t.url || t.url === "about:blank") continue;
      const probe = await deps.probe(tab.id);
      const href = (probe && probe.href) || t.url;
      if (!probe || probe.contentType === "application/pdf") {
        // Chrome's PDF viewer (uninjectable) or a PDF document: read it from the page, then from the worker.
        const inTab = probe ? await deps.fetchInTab(tab.id, href) : { ok: false };
        if (inTab.ok) return inTab;
        const w = await deps.fetchInWorker(href);
        if (w.ok) return w;
        lastError = w.error || inTab.error || lastError;
        unprobable += 1;
        if (probe || unprobable >= 4) return { ok: false, error: lastError };
        continue;
      }
      const wall = classifyWall(probe.html);
      if (wall) {
        if (wall.kind === "interactive") return { ok: false, error: wall.label, wall: "interactive", wallUrl: href };
        if (wall.kind === "blocked") return { ok: false, error: wall.label, wall: "blocked", wallUrl: href };
        wallSince = wallSince || Date.now();
        if (Date.now() - wallSince > WALL_WAIT_MS) return { ok: false, error: `${wall.label} did not clear`, wall: "auto", wallUrl: href };
        onStep(`${wall.label} on ${shortUrl(href)}, waiting`);
        continue;
      }
      // Real page: fetch the wanted PDF (or the one the page embeds) with the page's cookies and Referer.
      const target = pdfUrl || findPdfLinkInHtml(probe.html, href, { minScore: 18 });
      if (!target) return { ok: false, error: "article not available there" };
      onStep(`Downloading ${shortUrl(target)} from ${shortUrl(href)}`);
      const r = await deps.fetchInTab(tab.id, target);
      if (r.ok) return r;
      if (r.html) {
        const w2 = classifyWall(r.html);
        if (w2 && w2.kind === "auto") {
          // The PDF host itself runs a check: let the tab visit it, the loop will read the viewer.
          lastError = w2.label;
          wallSince = wallSince || Date.now();
          await deps.updateTab?.(tab.id, target);
          if (!deps.updateTab) return { ok: false, error: `${w2.label} on the PDF host`, wall: "auto", wallUrl: target };
          continue;
        }
        const link = findPdfLinkInHtml(r.html, r.finalUrl || target, { minScore: 18 });
        if (link && link !== target) {
          const r2 = await deps.fetchInTab(tab.id, link);
          if (r2.ok) return r2;
          return { ok: false, error: r2.error || r.error };
        }
      }
      return { ok: false, error: r.error || lastError };
    }
    return { ok: false, error: lastError };
  } finally {
    await deps.removeTab(tab.id);
  }
}

/**
 * Run one plan step.
 *
 * @param {{url: string, source: string, kind: string, referer?: string, wall?: string, note?: string}} step  Step.
 * @param {object} deps  Dependencies.
 * @param {(text: string) => void} onStep  Progress callback.
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, error?: string, wall?: string, wallUrl?: string}>}
 */
export async function runStep(step, deps, onStep) {
  if (step.kind === "pdf" && !step.referer) {
    onStep(`Downloading ${shortUrl(step.url)} with your browser session`);
    const w = await deps.fetchInWorker(step.url);
    if (w.ok) return w;
    const wall = w.html ? classifyWall(w.html) : null;
    if (wall && wall.kind === "interactive") return { ok: false, error: wall.label, wall: "interactive", wallUrl: w.finalUrl || step.url };
    if (wall && wall.kind === "blocked") return { ok: false, error: wall.label, wall: "blocked", wallUrl: w.finalUrl || step.url };
    if (w.html && !wall) {
      const link = findPdfLinkInHtml(w.html, w.finalUrl || step.url, { minScore: 18 });
      if (link) {
        const w2 = await deps.fetchInWorker(link);
        if (w2.ok) return w2;
      }
    }
    // JavaScript check, login redirect or viewer page: let a background tab do the visit.
    onStep(`Opening ${shortUrl(step.url)} in a background tab`);
    return fetchFromPage(step.url, "", deps, onStep);
  }
  if (step.kind === "pdf") {
    onStep(`Opening ${shortUrl(step.referer)} to download ${shortUrl(step.url)}`);
    return fetchFromPage(step.referer, step.url, deps, onStep);
  }
  onStep(`Trying ${step.source}`);
  return fetchFromPage(step.url, "", deps, onStep);
}

/**
 * Run a whole hand-off: try every step, attach the first PDF found, report to the server.
 *
 * @param {{item_id: number, doi: string, title: string, plan: Array<object>}} rec  Hand-off record.
 * @param {{deps?: object, onStep?: (text: string) => void}} [opts]
 * @returns {Promise<{ok: boolean, waiting?: boolean, via?: string, error?: string, attempts: Array<{source: string, error: string}>}>}
 */
export async function runHandoff(rec, opts = {}) {
  const deps = { ...defaultDeps(), ...(opts.deps || {}) };
  const onStep = opts.onStep || (() => {});
  const itemId = rec.item_id;
  const attempts = [];
  let blocked = null;
  const status = await deps.itemStatus(itemId).catch(() => null);
  if (status && (status.has_pdf || status.in_trash)) {
    await deps.report(itemId, "done", status.has_pdf ? "already has its PDF" : "item is in the trash", "");
    return { ok: true, via: "already attached", attempts };
  }
  for (const step of rec.plan || []) {
    const say = (text) => {
      onStep(text);
      deps.report(itemId, "running", text, step.source);
    };
    let r;
    try {
      r = await runStep(step, deps, say);
    } catch (e) {
      r = { ok: false, error: (e && e.message) || String(e) };
    }
    if (r.ok) {
      say(`Attaching the PDF from ${step.source}`);
      let canonicalId = itemId;
      try {
        const uploaded = await deps.upload(itemId, r.bytes, { url: r.finalUrl || step.url, title: "Full Text PDF", contentType: "application/pdf" });
        canonicalId = uploaded?.item_id || itemId;
      } catch (e) {
        attempts.push({ source: step.source, error: `upload failed: ${(e && e.message) || e}` });
        continue;
      }
      const after = await deps.itemStatus(canonicalId).catch(() => null);
      if (after && after.has_pdf) {
        await deps.report(itemId, "done", `PDF attached from ${step.source}`, step.source);
        return { ok: true, via: step.source, attempts, item_id: canonicalId };
      }
      attempts.push({ source: step.source, error: "the server did not confirm the attachment" });
      continue;
    }
    attempts.push({ source: step.source, error: r.error || "failed" });
    if (r.wall === "interactive" && !blocked) blocked = { url: r.wallUrl || step.url, source: step.source };
  }
  if (blocked) {
    const tabId = await deps.openForUser(blocked.url, null);
    await deps.addPending({ itemId, doi: rec.doi, title: rec.title, url: blocked.url, at: Date.now() });
    const msg = `${blocked.source} asks you to confirm you are human: the page is open in Chrome, the PDF will be attached once the article appears`;
    await deps.report(itemId, "waiting", msg, blocked.source);
    return { ok: false, waiting: true, tabId, attempts };
  }
  const last = attempts[attempts.length - 1];
  const msg = attempts.length
    ? `no source worked in Chrome (${attempts.map((a) => `${a.source}: ${a.error}`).join("; ").slice(0, 400)})`
    : "nothing to try";
  await deps.report(itemId, "failed", msg, last ? last.source : "");
  return { ok: false, error: msg, attempts };
}
