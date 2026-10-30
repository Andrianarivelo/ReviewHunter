/**
 * Start ReviewHunter from the browser and open it in the user's current window.
 *
 * An extension cannot start programs itself. ReviewHunter registers a small helper with the browser
 * ("native messaging host" org.reviewhunter.launcher, see doi_hunter_web/native_host.py); the browser
 * runs it on request, it starts the ReviewHunter server in the background (or reuses the running one)
 * and answers. The registration is written by the installer and repaired at every ReviewHunter launch.
 *
 * @module core/launch
 */

import { getSettings } from "../settings.js";

/** Name registered by ReviewHunter for its helper. */
export const NATIVE_HOST = "org.reviewhunter.launcher";

/**
 * Send one message to the helper.
 *
 * @param {object} message  Request ({cmd: "hello" | "start"}).
 * @returns {Promise<object>} Answer. Rejects with err.missing = true when the helper is not registered.
 */
export function nativeMessage(message) {
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, message, (reply) => {
        const err = chrome.runtime.lastError;
        if (err) {
          const e = new Error(err.message || "the ReviewHunter helper did not answer");
          // "Specified native messaging host not found." / "Access to the specified native messaging host is forbidden."
          e.missing = /not found|forbidden/i.test(e.message);
          reject(e);
        } else {
          resolve(reply || {});
        }
      });
    } catch (e) {
      e.missing = true; // permission missing (older manifest) or API unavailable
      reject(e);
    }
  });
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
 * Ask the helper to start ReviewHunter (it returns once the server answers, or with an error).
 *
 * @returns {Promise<{ok: boolean, state?: string, error?: string, missing?: boolean}>}
 */
export async function startServer() {
  const { serverUrl } = await getSettings();
  if (!isLocalDefault(serverUrl)) {
    return { ok: false, error: `ReviewHunter is configured at ${serverUrl}: start it there.` };
  }
  try {
    const reply = await nativeMessage({ cmd: "start" });
    return reply.ok ? reply : { ok: false, error: reply.error || "ReviewHunter did not start" };
  } catch (e) {
    return { ok: false, missing: !!e.missing, error: e.message };
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
  const tabs = (await chrome.tabs.query({ url: [`${serverUrl}/*`] }).catch(() => [])).filter((t) => !/reader\.html/.test(t.url || ""));
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
