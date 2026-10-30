/**
 * Start ReviewHunter from the browser and open it in the user's current window.
 *
 * An extension cannot start programs itself. Two routes, both set up by ReviewHunter (installer, and
 * repaired at every launch):
 *   1. Chrome, Edge, Brave, Firefox: a "native messaging host" (org.reviewhunter.launcher, see
 *      doi_hunter_web/native_host.py) that the browser runs on request; it starts the server in the
 *      background (or reuses the running one) and answers once it is up.
 *   2. Safari (its native messaging only reaches the extension's wrapper app), or when the helper is not
 *      registered: the reviewhunter:// link, which the operating system hands to ReviewHunter.
 *
 * @module core/launch
 */

import { getSettings } from "../settings.js";


// Firefox and Safari: their promise-based `browser.*` API; Chrome and Edge: `chrome.*` (also promises in MV3).
const chrome = globalThis.browser ?? globalThis.chrome;
/** Name registered by ReviewHunter for its helper. */
export const NATIVE_HOST = "org.reviewhunter.launcher";
/** Link handled by the operating system: ReviewHunter registers itself for it (all platforms). */
export const START_URL = "reviewhunter://start";

/** Safari: native messaging reaches the extension's own wrapper app, not ReviewHunter's helper. */
export function isSafari() {
  try {
    return chrome.runtime.getURL("").startsWith("safari-web-extension:");
  } catch (_) {
    return false;
  }
}

/**
 * Send one message to the helper (promise form: Chrome MV3, Firefox and Safari all support it).
 *
 * @param {object} message  Request ({cmd: "hello" | "start"}).
 * @returns {Promise<object>} Answer. Rejects with err.missing = true when the helper is not registered.
 */
export async function nativeMessage(message) {
  try {
    const reply = await chrome.runtime.sendNativeMessage(NATIVE_HOST, message);
    return reply || {};
  } catch (e) {
    const err = e instanceof Error ? e : new Error(String((e && e.message) || e));
    // Chrome: "Specified native messaging host not found." / "Access to the specified native messaging host is
    // forbidden."; Firefox: "No such native application"; no permission / API: TypeError.
    err.missing = /not found|forbidden|no such native|is not a function|undefined/i.test(err.message);
    throw err;
  }
}

/**
 * Is the configured server address this computer's default ReviewHunter (the only one the helper can start)?
 *
 * @param {string} serverUrl  Configured address.
 * @returns {boolean}
 */
export function isLocalDefault(serverUrl) {
  try {
    const u = new URL(serverUrl);
    return ["127.0.0.1", "localhost"].includes(u.hostname);
  } catch (_) {
    return false;
  }
}

/**
 * Ask the operating system to start ReviewHunter through its reviewhunter:// link. The browser asks the
 * user once ("Open ReviewHunter?", with an "always allow" choice); the helper tab closes by itself.
 *
 * @returns {Promise<void>}
 */
async function startThroughLink() {
  const tab = await chrome.tabs.create({ url: START_URL, active: false });
  setTimeout(() => chrome.tabs.remove(tab.id).catch(() => {}), 4000);
}

/**
 * Ask the helper to start ReviewHunter (it returns once the server answers, or with an error). Safari, and
 * any browser without the helper, use the reviewhunter:// link instead: the caller then polls the server.
 *
 * @returns {Promise<{ok: boolean, state?: string, error?: string, missing?: boolean, viaLink?: boolean}>}
 */
export async function startServer() {
  const { serverUrl } = await getSettings();
  if (!isLocalDefault(serverUrl)) {
    return { ok: false, error: `ReviewHunter is configured at ${serverUrl}: start it there.` };
  }
  if (!isSafari()) {
    try {
      const reply = await nativeMessage({ cmd: "start" });
      if (reply.ok) return reply;
      return { ok: false, error: reply.error || "ReviewHunter did not start" };
    } catch (e) {
      if (!e.missing) return { ok: false, error: e.message };
    }
  }
  try {
    await startThroughLink();
    return { ok: true, state: "requested", viaLink: true };
  } catch (e) {
    return { ok: false, missing: true, error: e.message };
  }
}

/**
 * Open (or focus) the ReviewHunter app in a given window, optionally on one item.
 *
 * Preference: an app tab already in that window, then an app tab from another window (moved here, so
 * there is still one app tab), then a new tab. Only the URL fragment changes for an open app tab, so
 * the page does not reload (the app listens to hashchange and opens the item).
 *
 * @param {{itemId?: number|string, windowId?: number}} [opts]
 * @returns {Promise<{ok: boolean, tabId: number}>}
 */
export async function openAppInWindow(opts = {}) {
  const { serverUrl } = await getSettings();
  const target = `${serverUrl}/${opts.itemId ? `#item=${encodeURIComponent(opts.itemId)}` : ""}`;
  let windowId = opts.windowId;
  if (windowId === undefined || windowId === null) {
    const win = await chrome.windows.getLastFocused({ windowTypes: ["normal"] }).catch(() => null);
    windowId = win ? win.id : undefined;
  }
  // Compare URLs ourselves: Firefox's match patterns do not match the port (":8765") like Chrome's do.
  const tabs = (await chrome.tabs.query({}).catch(() => []))
    .filter((t) => (t.url || "").startsWith(`${serverUrl}/`) && !/reader\.html/.test(t.url || ""));
  let tab = tabs.find((t) => t.windowId === windowId);
  if (!tab && tabs.length && windowId !== undefined) {
    tab = tabs[0];
    await chrome.tabs.move(tab.id, { windowId, index: -1 }).catch(() => {});
  }
  if (tab) {
    const update = { active: true };
    if (opts.itemId) update.url = target;
    await chrome.tabs.update(tab.id, update);
  } else {
    tab = await chrome.tabs.create({ url: target, windowId, active: true });
  }
  if (windowId !== undefined) await chrome.windows.update(windowId, { focused: true }).catch(() => {});
  return { ok: true, tabId: tab.id };
}
