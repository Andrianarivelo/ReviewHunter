/**
 * Extension settings stored in chrome.storage.local, plus the small pieces of
 * session state kept in chrome.storage.session (pending PDF attachments, ping cache).
 *
 * @module settings
 */

/** Default values for every user setting. */
export const DEFAULTS = Object.freeze({
  serverUrl: "http://127.0.0.1:8765",
  defaultCollectionId: null,
  lastCollectionId: null,
  attachPdf: true,
  improveMetadata: true,
  saveSnapshot: false,
  useBackgroundTab: true,
});

/**
 * Read all settings merged over defaults.
 *
 * @returns {Promise<typeof DEFAULTS>} Settings object.
 */
export async function getSettings() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  const merged = { ...DEFAULTS, ...stored };
  merged.serverUrl = normalizeServerUrl(merged.serverUrl);
  return merged;
}

/**
 * Persist a subset of settings.
 *
 * @param {Partial<typeof DEFAULTS>} patch  Keys to write.
 * @returns {Promise<void>}
 */
export async function setSettings(patch) {
  if (patch.serverUrl !== undefined) patch = { ...patch, serverUrl: normalizeServerUrl(patch.serverUrl) };
  await chrome.storage.local.set(patch);
}

/**
 * Normalise a server URL typed by the user ("127.0.0.1:8765/" -> "http://127.0.0.1:8765").
 *
 * @param {string} url  Raw URL.
 * @returns {string} URL with scheme and without trailing slash.
 */
export function normalizeServerUrl(url) {
  let s = String(url || DEFAULTS.serverUrl).trim();
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  return s.replace(/\/+$/, "");
}

/** Pending attachments older than this are forgotten (ms). */
const PENDING_TTL = 24 * 3600 * 1000;

/**
 * @typedef {object} PendingAttachment
 * @property {number} itemId   ReviewHunter item id waiting for a PDF.
 * @property {string} title    Item title (shown in the popup).
 * @property {string} [doi]    DOI, used to match a PDF tab.
 * @property {string[]} urls   PDF URLs that failed (host/path used for matching).
 * @property {number} at       Timestamp.
 */

/**
 * List pending attachments (items saved without their PDF), newest first.
 *
 * @returns {Promise<PendingAttachment[]>} Pending entries still within TTL.
 */
export async function getPending() {
  const { pending = [] } = await chrome.storage.session.get("pending");
  return pending.filter((p) => Date.now() - p.at < PENDING_TTL).sort((a, b) => b.at - a.at);
}

/**
 * Remember that an item still needs its PDF.
 *
 * @param {PendingAttachment} entry  Entry (replaces an existing one with the same itemId).
 * @returns {Promise<void>}
 */
export async function addPending(entry) {
  const list = (await getPending()).filter((p) => p.itemId !== entry.itemId);
  list.unshift({ ...entry, at: Date.now() });
  await chrome.storage.session.set({ pending: list.slice(0, 20) });
}

/**
 * Forget a pending attachment (after success or when dismissed).
 *
 * @param {number} itemId  Item id.
 * @returns {Promise<void>}
 */
export async function removePending(itemId) {
  const list = (await getPending()).filter((p) => p.itemId !== itemId);
  await chrome.storage.session.set({ pending: list });
}

/**
 * Score how well a PDF tab matches a pending entry (0 = no match).
 *
 * @param {PendingAttachment} entry  Pending entry.
 * @param {string} pdfUrl            URL of the PDF tab.
 * @param {string} [doi]             DOI found in the tab URL.
 * @returns {number} 3 same DOI, 2 same URL, 1 same host, 0 otherwise.
 */
export function matchPending(entry, pdfUrl, doi) {
  if (doi && entry.doi && doi.toLowerCase() === entry.doi.toLowerCase()) return 3;
  const strip = (u) => String(u || "").replace(/[?#].*$/, "");
  let host = "";
  try {
    host = new URL(pdfUrl).hostname.replace(/^www\./, "");
  } catch (_) {
    return 0;
  }
  for (const u of entry.urls || []) {
    if (strip(u) === strip(pdfUrl)) return 2;
  }
  const hostKey = host.split(".").slice(-2).join(".");
  for (const u of [...(entry.urls || []), entry.pageUrl || ""]) {
    try {
      if (new URL(u).hostname.replace(/^www\./, "").endsWith(hostKey)) return 1;
    } catch (_) {
      /* ignore bad URL */
    }
  }
  return 0;
}
