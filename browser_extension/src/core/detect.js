/**
 * Service-worker side of detection: injects the extractor into a tab and
 * returns its (JSON-serialisable) result.
 *
 * The extractor is a set of ES modules. Content scripts injected with
 * chrome.scripting cannot be modules, so a tiny injected function dynamically
 * imports `src/extract/index.js` (listed in web_accessible_resources) inside the
 * page's isolated world and runs `detect(document, location.href)`.
 *
 * @module core/detect
 */

import { detectPdf } from "../extract/index.js";
import { isLikelyPdfUrl } from "../shared/identifiers.js";

/** URL schemes where injection is impossible or pointless. */
const UNSUPPORTED = /^(chrome|chrome-extension|edge|about|devtools|view-source|data|blob|javascript|file):/i;

/**
 * Injected function (isolated world). Self-contained by design.
 *
 * @param {string} moduleUrl  chrome-extension:// URL of extract/index.js.
 * @param {?object} pageData  Data read from the page's main world (IEEE metadata).
 * @returns {Promise<object>} Detection result (plain JSON).
 */
async function injectedDetect(moduleUrl, pageData) {
  const mod = await import(moduleUrl);
  const result = mod.detect(document, location.href, { pageData });
  return JSON.parse(JSON.stringify(result));
}

/**
 * Injected into the MAIN world on IEEE Xplore: copy `xplGlobal.document.metadata`.
 *
 * @returns {?object} Metadata object or null.
 */
function injectedIeeeMetadata() {
  try {
    const md = window.xplGlobal && window.xplGlobal.document && window.xplGlobal.document.metadata;
    return md ? JSON.parse(JSON.stringify(md)) : null;
  } catch (_) {
    return null;
  }
}

/**
 * Detect references in a tab.
 *
 * @param {number} tabId  Tab id.
 * @returns {Promise<import("../extract/index.js").Detection & {error?: string}>} Detection (kind "none" with error when impossible).
 */
export async function detectInTab(tabId) {
  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch (e) {
    return { kind: "none", items: [], url: "", error: "tab not found" };
  }
  const url = tab.url || tab.pendingUrl || "";
  if (!url || UNSUPPORTED.test(url)) {
    return { kind: "none", items: [], url, error: "This kind of page cannot be read by extensions." };
  }
  let pageData = null;
  if (/^https?:\/\/ieeexplore\.ieee\.org\/(abstract\/)?document\/\d+/i.test(url)) {
    try {
      [{ result: pageData }] = await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func: injectedIeeeMetadata });
    } catch (_) {
      pageData = null;
    }
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: injectedDetect,
      args: [chrome.runtime.getURL("src/extract/index.js"), pageData],
    });
    if (result) return result;
    throw new Error("empty result");
  } catch (e) {
    // Chrome's PDF viewer or pages that block injection: fall back to URL-only knowledge.
    if (isLikelyPdfUrl(url)) return detectPdf(url);
    return { kind: "none", items: [], url, error: `Could not read this page (${(e && e.message) || e}).` };
  }
}

/**
 * Read the full HTML of a tab (for "save snapshot").
 *
 * @param {number} tabId  Tab id.
 * @returns {Promise<string>} Serialised document ("" on failure).
 */
export async function snapshotHtml(tabId) {
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        // A <base> keeps relative images/styles working when the snapshot is opened offline.
        let html = document.documentElement.outerHTML;
        if (!/<base\s/i.test(html)) html = html.replace(/<head[^>]*>/i, (m) => `${m}<base href="${location.href.replace(/"/g, "&quot;")}">`);
        return `<!DOCTYPE html>\n${html}`;
      },
    });
    return result || "";
  } catch (_) {
    return "";
  }
}
