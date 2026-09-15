/**
 * In-browser PDF acquisition (service worker side).
 *
 * Closed-access publishers (ScienceDirect, Wiley, IEEE...) refuse PDF downloads
 * from the ReviewHunter server (HTTP 403, bot protection) but serve them to the
 * user's browser, which carries the institutional session cookies. This module
 * downloads PDFs *inside the browser* and returns verified bytes.
 *
 * For every candidate URL, in order:
 *   a) "page"         fetch() executed in the tab (same-origin cookies, passes bot checks);
 *   b) "extension"    fetch() from the service worker with credentials (host permissions bypass CORS);
 *   c) "interstitial" when a) or b) returned HTML, look inside it for the real PDF link
 *                     (meta refresh, JS redirect, viewer iframe, JSON) and retry a) + b) once;
 * then, if every candidate failed:
 *   d) "background tab" open the best URL in an inactive tab, wait for Chrome's PDF viewer,
 *                     fetch the final URL from the tab / worker and close the tab.
 * Bytes are accepted only when they start with "%PDF-" and are at most 200 MB.
 *
 * @module core/pdf
 */

import { isPdfBytes, findPdfLinkInHtml } from "../shared/identifiers.js";

/** Maximum PDF size accepted (the server enforces the same limit). */
export const MAX_PDF_BYTES = 200 * 1024 * 1024;

/** Raw bytes transferred per executeScript round trip when reading from the page. */
const CHUNK_BYTES = 4 * 1024 * 1024;

/**
 * @typedef {object} PdfResult
 * @property {boolean} ok
 * @property {Uint8Array} [bytes]    PDF bytes (ok only).
 * @property {string} [finalUrl]     URL after redirects (ok only).
 * @property {string} [via]          Strategy that worked: page | extension | interstitial | background tab.
 * @property {Array<{url: string, via: string, error: string}>} attempts  Every failed attempt, for the UI.
 */

/**
 * Try every strategy for a list of candidate URLs.
 *
 * @param {string[]} candidates  PDF URLs, best first (duplicates and non-http URLs are ignored).
 * @param {{tabId?: number, onStep?: (text: string) => void, allowBackgroundTab?: boolean, referer?: string}} [opts]
 *   referer: page the PDF host expects as Referer (Sci-Hub proxies), applied to the worker download.
 * @returns {Promise<PdfResult>} Result with bytes or the list of failed attempts.
 */
export async function acquirePdf(candidates, opts = {}) {
  const onStep = opts.onStep || (() => {});
  const attempts = [];
  const tried = new Set();
  const urls = [...new Set((candidates || []).filter((u) => /^https?:\/\//i.test(u || "")))].slice(0, 5);
  let interstitialBest = "";

  /**
   * Run strategies a) and b) on one URL.
   *
   * @param {string} url    URL.
   * @param {string} label  Strategy label prefix for reporting.
   * @returns {Promise<?PdfResult>} Success or null (HTML kept for step c).
   */
  const tryUrl = async (url, label) => {
    const htmlSeen = [];
    if (opts.tabId !== undefined && opts.tabId !== null) {
      onStep(`Downloading PDF in the page: ${shortUrl(url)}`);
      const r = await fetchInTab(opts.tabId, url);
      if (r.ok) return { ok: true, bytes: r.bytes, finalUrl: r.finalUrl, via: label || "page", attempts };
      attempts.push({ url, via: label ? `${label} (page)` : "page", error: r.error });
      if (r.html) htmlSeen.push(r);
    }
    onStep(`Downloading PDF with your browser session: ${shortUrl(url)}`);
    const w = await fetchInWorker(url, { referer: opts.referer });
    if (w.ok) return { ok: true, bytes: w.bytes, finalUrl: w.finalUrl, via: label || "extension", attempts };
    attempts.push({ url, via: label ? `${label} (extension)` : "extension", error: w.error });
    if (w.html) htmlSeen.push(w);
    // c) interstitial HTML -> real PDF link.
    if (!label) {
      for (const h of htmlSeen) {
        // minScore 18: redirects, viewers, citation_pdf_url and JSON links only. Plain anchors are
        // ignored so a login page linking to some unrelated "guide.pdf" is never attached.
        const link = findPdfLinkInHtml(h.html, h.finalUrl || url, { minScore: 18 });
        if (link && !tried.has(link)) {
          tried.add(link);
          interstitialBest = interstitialBest || link;
          onStep(`Following the publisher's redirect page: ${shortUrl(link)}`);
          const again = await tryUrl(link, "interstitial");
          if (again) return again;
        }
      }
    }
    return null;
  };

  for (const url of urls) {
    if (tried.has(url)) continue;
    tried.add(url);
    const res = await tryUrl(url, "");
    if (res) return res;
  }

  // d) background tab, last resort (only one tab, for the most promising URL).
  const tabUrl = interstitialBest || urls[0];
  if (opts.allowBackgroundTab && tabUrl) {
    onStep("Opening the PDF in a background tab");
    const r = await fetchViaBackgroundTab(tabUrl);
    if (r.ok) return { ok: true, bytes: r.bytes, finalUrl: r.finalUrl, via: "background tab", attempts };
    attempts.push({ url: tabUrl, via: "background tab", error: r.error });
  }
  return { ok: false, attempts };
}

/**
 * Shorten a URL for status lines ("host/.../last-segment").
 *
 * @param {string} url  URL.
 * @returns {string} Short label.
 */
export function shortUrl(url) {
  try {
    const u = new URL(url);
    const last = u.pathname.split("/").filter(Boolean).pop() || "";
    return `${u.hostname.replace(/^www\./, "")}${last ? `/.../${last.slice(0, 40)}` : ""}`;
  } catch (_) {
    return String(url).slice(0, 60);
  }
}

/** Session rule ids used for Referer injection (one per concurrent download). */
let refererRuleId = 4000;

/**
 * Run `fn` while the extension's own requests to `url` carry `referer` as Referer header.
 *
 * fetch() cannot set a cross-origin Referer from the service worker (the Fetch standard downgrades it
 * to the client), but PDF hosts of Sci-Hub proxies refuse requests without the article page as Referer
 * (hot-link protection), and their answers carry no CORS header, so a page-context fetch is not an
 * option either. A temporary declarativeNetRequest session rule scoped to this URL and to requests
 * initiated by this extension sets the header, and is removed right after.
 *
 * @param {string} url      Exact URL being downloaded.
 * @param {string} referer  Referer value ("" = nothing to do).
 * @param {() => Promise<T>} fn  Work to run under the rule.
 * @returns {Promise<T>}
 * @template T
 */
export async function withReferer(url, referer, fn) {
  const dnr = globalThis.chrome && chrome.declarativeNetRequest;
  if (!referer || !dnr || !dnr.updateSessionRules) return fn();
  const id = (refererRuleId += 1);
  const rule = {
    id,
    priority: 1,
    action: { type: "modifyHeaders", requestHeaders: [{ header: "Referer", operation: "set", value: referer }] },
    condition: { urlFilter: url.split("#")[0], initiatorDomains: [chrome.runtime.id], resourceTypes: ["xmlhttprequest"] },
  };
  let installed = false;
  try {
    await dnr.updateSessionRules({ addRules: [rule], removeRuleIds: [id] });
    installed = true;
  } catch (e) {
    console.warn(`ReviewHunter: cannot set Referer for ${url}: ${(e && e.message) || e}`);
  }
  try {
    return await fn();
  } finally {
    if (installed) dnr.updateSessionRules({ removeRuleIds: [id] }).catch(() => {});
  }
}

/**
 * b) Download from the service worker with the user's cookies.
 *
 * @param {string} url  URL.
 * @param {{timeoutMs?: number, referer?: string}} [opts]  timeoutMs: abort after this delay (default 90 s);
 *   referer: Referer the host requires (set through a temporary declarativeNetRequest rule).
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, html?: string, error?: string}>}
 */
export async function fetchInWorker(url, opts = {}) {
  return withReferer(url, opts.referer || "", () => fetchInWorkerPlain(url, opts));
}

/**
 * fetchInWorker without the Referer rule (see withReferer).
 *
 * @param {string} url  URL.
 * @param {{timeoutMs?: number}} [opts]
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, html?: string, error?: string}>}
 */
async function fetchInWorkerPlain(url, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs || 90000);
  let failure = { ok: false, error: "PDF download failed" };
  try {
    // Reuse bytes already loaded by the browser before revalidating a short-lived
    // signed link. A cached HTML challenge must still get a fresh network retry.
    for (const cache of ["force-cache", "reload"]) {
      try {
        const res = await fetch(url, { credentials: "include", redirect: "follow", signal: ctrl.signal, cache });
        const finalUrl = res.url || url;
        const len = Number(res.headers.get("content-length") || 0);
        if (len > MAX_PDF_BYTES) return { ok: false, error: "file larger than 200 MB" };
        const buf = new Uint8Array(await res.arrayBuffer());
        if (buf.length > MAX_PDF_BYTES) return { ok: false, error: "file larger than 200 MB" };
        if (res.ok && res.status !== 206 && isPdfBytes(buf)) return { ok: true, bytes: buf, finalUrl };
        const ctype = res.headers.get("content-type") || "";
        const error = res.status === 206 ? "incomplete PDF response" : res.ok ? `not a PDF (${ctype.split(";")[0] || "unknown type"})` : `HTTP ${res.status}`;
        const html = /html|text/i.test(ctype) && buf.length < 3_000_000 ? new TextDecoder().decode(buf) : "";
        failure = { ok: false, error, html: html || failure.html || "", finalUrl };
      } catch (e) {
        if (ctrl.signal.aborted) throw e;
        failure = { ...failure, error: `network error (${(e && e.message) || e})` };
      }
    }
    return failure;
  } catch (e) {
    return { ok: false, error: e && e.name === "AbortError" ? "timed out" : `network error (${(e && e.message) || e})` };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * a) Download from inside the tab (isolated world of the page's frame): the request
 * is same-origin for the page, so session cookies and bot-protection tokens apply.
 * Bytes stay in the tab and are pulled in base64 chunks to avoid a giant message.
 *
 * @param {number} tabId  Tab to run in.
 * @param {string} url    URL to fetch.
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, html?: string, error?: string}>}
 */
export async function fetchInTab(tabId, url) {
  const token = `rh${Date.now()}${Math.random().toString(36).slice(2)}`;
  let head;
  try {
    [{ result: head }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: pageFetchStart,
      args: [url, token, MAX_PDF_BYTES],
    });
  } catch (e) {
    return { ok: false, error: `cannot run in this tab (${(e && e.message) || e})` };
  }
  if (!head) return { ok: false, error: "no result from the page" };
  if (!head.ok) return { ok: false, error: head.error, html: head.html || "", finalUrl: head.finalUrl };
  try {
    const bytes = new Uint8Array(head.size);
    for (let offset = 0; offset < head.size; offset += CHUNK_BYTES) {
      const [{ result: b64 }] = await chrome.scripting.executeScript({
        target: { tabId },
        func: pageReadChunk,
        args: [token, offset, CHUNK_BYTES],
      });
      if (typeof b64 !== "string") throw new Error("page lost the downloaded bytes");
      const bin = atob(b64);
      for (let i = 0; i < bin.length; i += 1) bytes[offset + i] = bin.charCodeAt(i);
    }
    if (!isPdfBytes(bytes)) return { ok: false, error: "not a PDF" };
    return { ok: true, bytes, finalUrl: head.finalUrl || url };
  } catch (e) {
    return { ok: false, error: `transfer failed (${(e && e.message) || e})` };
  } finally {
    chrome.scripting.executeScript({ target: { tabId }, func: pageRelease, args: [token] }).catch(() => {});
  }
}

/**
 * Injected into the page (isolated world). Must be self-contained: it is
 * serialised by chrome.scripting, so it cannot use imports or outer variables.
 *
 * @param {string} url       URL to fetch.
 * @param {string} token     Key under which bytes are kept until read.
 * @param {number} maxBytes  Size cap.
 * @returns {Promise<object>} {ok, size, finalUrl} or {ok:false, error, html?, finalUrl?}.
 */
async function pageFetchStart(url, token, maxBytes) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 90000);
  let failure = { ok: false, error: "PDF download failed" };
  try {
    // This function is serialised into the page, so keep the cache/network loop
    // self-contained. Never alter query parameters of publisher signatures.
    for (const cache of ["force-cache", "reload"]) {
      try {
        const res = await fetch(url, { credentials: "include", redirect: "follow", cache, signal: ctrl.signal });
        const finalUrl = res.url || url;
        const len = Number(res.headers.get("content-length") || 0);
        if (len > maxBytes) return { ok: false, error: "file larger than 200 MB", finalUrl };
        const buf = new Uint8Array(await res.arrayBuffer());
        if (buf.length > maxBytes) return { ok: false, error: "file larger than 200 MB", finalUrl };
        const isPdf = buf.length >= 5 && buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46 && buf[4] === 0x2d;
        if (res.ok && res.status !== 206 && isPdf) {
          globalThis.__reviewHunterPdf = globalThis.__reviewHunterPdf || {};
          globalThis.__reviewHunterPdf[token] = buf;
          return { ok: true, size: buf.length, finalUrl };
        }
        const ctype = res.headers.get("content-type") || "";
        const html = /html|text/i.test(ctype) && buf.length < 3000000 ? new TextDecoder().decode(buf) : "";
        const error = res.status === 206 ? "incomplete PDF response" : res.ok ? `not a PDF (${ctype.split(";")[0] || "unknown type"})` : `HTTP ${res.status}`;
        failure = { ok: false, error, html: html || failure.html || "", finalUrl };
      } catch (e) {
        if (ctrl.signal.aborted) throw e;
        failure = { ...failure, error: `blocked in page (${(e && e.message) || e})` };
      }
    }
    return failure;
  } catch (e) {
    // Typical: cross-origin redirect blocked by CORS in the page context.
    return { ok: false, error: e?.name === "AbortError" ? "timed out" : `blocked in page (${(e && e.message) || e})` };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Injected: return one base64 chunk of the stored bytes.
 *
 * @param {string} token   Storage key.
 * @param {number} offset  Byte offset.
 * @param {number} length  Chunk length.
 * @returns {?string} Base64 string, or null when the bytes are gone.
 */
function pageReadChunk(token, offset, length) {
  const store = globalThis.__reviewHunterPdf || {};
  const buf = store[token];
  if (!buf) return null;
  const part = buf.subarray(offset, Math.min(offset + length, buf.length));
  let bin = "";
  for (let i = 0; i < part.length; i += 0x8000) bin += String.fromCharCode.apply(null, part.subarray(i, i + 0x8000));
  return btoa(bin);
}

/**
 * Injected: free stored bytes.
 *
 * @param {string} token  Storage key.
 * @returns {boolean} Always true.
 */
function pageRelease(token) {
  if (globalThis.__reviewHunterPdf) delete globalThis.__reviewHunterPdf[token];
  return true;
}

/**
 * Injected: describe the document loaded in a tab.
 *
 * @returns {{contentType: string, href: string, html: string}} Content type, URL and (small) HTML.
 */
function pageProbe() {
  const html = document.contentType && document.contentType.includes("html") ? document.documentElement.outerHTML : "";
  return { contentType: document.contentType || "", href: location.href, html: html.length < 400000 ? html : html.slice(0, 400000) };
}

/**
 * d) Open the URL in an inactive tab, wait until Chrome shows the PDF (the
 * publisher's redirects and JavaScript run like in a normal visit), then fetch
 * the final URL from inside that tab or from the worker. The tab is always closed.
 *
 * @param {string} url  URL to open.
 * @param {number} [timeoutMs=45000]  Give up after this delay.
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, error?: string}>}
 */
export async function fetchViaBackgroundTab(url, timeoutMs = 45000) {
  let tab;
  try {
    tab = await chrome.tabs.create({ url, active: false });
  } catch (e) {
    return { ok: false, error: `cannot open a tab (${(e && e.message) || e})` };
  }
  const deadline = Date.now() + timeoutMs;
  let lastError = "the page never displayed a PDF";
  let followed = false;
  let noProbeFailures = 0;
  try {
    while (Date.now() < deadline) {
      await sleep(900);
      let t;
      try {
        t = await chrome.tabs.get(tab.id);
      } catch (_) {
        return { ok: false, error: "the tab was closed (Chrome may be set to download PDFs instead of opening them)" };
      }
      if (t.status !== "complete" || !t.url || t.url === "about:blank") continue;
      let probe = null;
      try {
        [{ result: probe }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: pageProbe });
      } catch (_) {
        probe = null;
      }
      const href = (probe && probe.href) || t.url;
      if (!probe || probe.contentType === "application/pdf") {
        const inTab = probe ? await fetchInTab(tab.id, href) : { ok: false };
        if (inTab.ok) return inTab;
        const w = await fetchInWorker(href);
        if (w.ok) return w;
        lastError = w.error || inTab.error || lastError;
        // A PDF viewer whose bytes we cannot read will not improve by waiting; an uninjectable
        // page (still redirecting, or a Chrome-internal viewer) gets a few more chances.
        noProbeFailures += 1;
        if (probe || noProbeFailures >= 3) return { ok: false, error: lastError };
        continue;
      }
      // Still an HTML page: interstitial with a link we can follow once, otherwise keep waiting for JS redirects.
      if (!followed && probe.html) {
        const link = findPdfLinkInHtml(probe.html, href, { minScore: 18 });
        if (link) {
          followed = true;
          const w = await fetchInWorker(link);
          if (w.ok) return w;
          await chrome.tabs.update(tab.id, { url: link }).catch(() => {});
        }
      }
      lastError = /captcha|are you a robot|verify you are human|cf-challenge/i.test(probe.html || "")
        ? "the site asks for a CAPTCHA / human verification"
        : "the page never displayed a PDF (login required?)";
    }
    return { ok: false, error: lastError };
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

/**
 * Promise-based delay.
 *
 * @param {number} ms  Milliseconds.
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
