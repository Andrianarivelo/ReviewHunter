/**
 * Sci-Hub / Anna's Archive SciDB fallback, run in the browser (service worker side).
 *
 * Why in the browser: these sites now answer automated clients (the ReviewHunter
 * server) with human-verification walls, and several domains are blocked by ISP
 * DNS while Chrome's secure DNS still reaches them. The browser also keeps the
 * cookie a user earns by passing a check once.
 *
 * What this module never does: solve a CAPTCHA / proof-of-work challenge. When a
 * wall is met, the caller opens the page for the user and waits (see
 * background.js "auto attach"), then the PDF is fetched with the user's session.
 *
 * @module core/shadow
 */

import * as api from "../api.js";
import { fetchInWorker, shortUrl } from "./pdf.js";
import { findPdfLinkInHtml, isBotChallengeHtml } from "../shared/identifiers.js";


// Firefox and Safari: their promise-based `browser.*` API; Chrome and Edge: `chrome.*` (also promises in MV3).
const chrome = globalThis.browser ?? globalThis.chrome;
/** Per-request timeout for landing pages: dead mirrors must not stall a save for minutes. */
const PAGE_TIMEOUT_MS = 20000;

/** Used when the ReviewHunter server is older and has no /connector/pdf-sources route. */
const DEFAULT_BASES = [
  ["Sci-Hub (sci-hub.ren)", "https://sci-hub.ren/"],
  ["Sci-Hub (sci-hub.fr)", "https://sci-hub.fr/"],
  ["Sci-Hub (sci-hub.ru)", "https://sci-hub.ru/"],
  ["Anna's Archive SciDB (annas-archive.li)", "https://annas-archive.li/scidb/"],
];

/**
 * Landing pages to try for a DOI (the server's configured mirrors, best first).
 *
 * @param {string} doi  DOI.
 * @returns {Promise<Array<{source: string, url: string}>>} Pages.
 */
export async function shadowPages(doi) {
  try {
    const { pages } = await api.pdfSources(doi);
    if (Array.isArray(pages) && pages.length) return pages;
  } catch (_) {
    /* older server: use the built-in list */
  }
  const path = String(doi).split("/").map(encodeURIComponent).join("/");
  return DEFAULT_BASES.map(([source, base]) => ({ source, url: `${base}${path}` }));
}

/**
 * Hostname of a URL ("" when invalid).
 *
 * @param {string} url  URL.
 * @returns {string} Lower-case host.
 */
function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (_) {
    return "";
  }
}

/**
 * Try every configured Sci-Hub / SciDB page for a DOI with the browser session.
 *
 * For each page: fetch it; a direct PDF is accepted; an article page is parsed for
 * its viewer (`<object data=...pdf>`) and that file is fetched; a verification wall
 * is recorded in `blocked` (first one wins) and skipped. Aliases redirecting to a
 * host already tried are skipped. Bytes are accepted only if they start with %PDF-.
 *
 * @param {string} doi  DOI of the item.
 * @param {{onStep?: (text: string) => void}} [opts]
 * @returns {Promise<{ok: boolean, bytes?: Uint8Array, finalUrl?: string, via?: string,
 *   blocked?: {url: string, source: string}, attempts: Array<{url: string, via: string, error: string}>}>}
 */
export async function acquireFromShadowLibraries(doi, opts = {}) {
  const onStep = opts.onStep || (() => {});
  const attempts = [];
  const visited = new Set();
  let blocked = null;
  for (const page of await shadowPages(doi)) {
    const host = hostOf(page.url);
    if (!host || visited.has(host)) continue;
    onStep(`Trying ${page.source}`);
    const res = await fetchInWorker(page.url, { timeoutMs: PAGE_TIMEOUT_MS });
    const finalHost = hostOf(res.finalUrl || page.url);
    if (finalHost !== host && visited.has(finalHost)) continue; // alias of a mirror already tried
    visited.add(host).add(finalHost);
    if (res.ok) return { ok: true, bytes: res.bytes, finalUrl: res.finalUrl, via: page.source, attempts };
    if (!res.html) {
      attempts.push({ url: page.url, via: page.source, error: res.error || "no answer" });
      continue;
    }
    if (isBotChallengeHtml(res.html)) {
      blocked = blocked || { url: res.finalUrl || page.url, source: page.source };
      attempts.push({ url: page.url, via: page.source, error: "human verification required" });
      continue;
    }
    // minScore 18: viewer objects / redirects only, never an unrelated help-page link.
    const link = findPdfLinkInHtml(res.html, res.finalUrl || page.url, { minScore: 18 });
    if (!link) {
      attempts.push({ url: page.url, via: page.source, error: "article not available there" });
      continue;
    }
    onStep(`Downloading from ${shortUrl(link)}`);
    // Proxy mirrors (sci-hub.ren -> sci.bban.top) serve the PDF only with the article page as Referer.
    const pdf = await fetchInWorker(link, { referer: res.finalUrl || page.url });
    if (pdf.ok) return { ok: true, bytes: pdf.bytes, finalUrl: pdf.finalUrl, via: page.source, attempts };
    attempts.push({ url: link, via: page.source, error: pdf.error || "download failed" });
  }
  return { ok: false, attempts, blocked };
}

/**
 * Open a verification page for the user, next to the current tab, without stealing focus
 * from the popup. Re-uses a tab that already shows the same address.
 *
 * @param {string} url  Page to open.
 * @param {?number} [nearTabId]  Tab the save started from (the new tab is placed after it).
 * @returns {Promise<?number>} Tab id, or null when it could not be opened.
 */
export async function openForUser(url, nearTabId) {
  try {
    const existing = await chrome.tabs.query({ url: url.replace(/#.*$/, "") }).catch(() => []);
    if (existing.length) return existing[0].id;
    let index;
    if (nearTabId !== null && nearTabId !== undefined) {
      const near = await chrome.tabs.get(nearTabId).catch(() => null);
      if (near) index = near.index + 1;
    }
    const tab = await chrome.tabs.create({ url, active: false, ...(index !== undefined ? { index } : {}) });
    return tab.id;
  } catch (_) {
    return null;
  }
}
