/**
 * ReviewHunter Connector service worker (Manifest V3, ES module).
 *
 * Responsibilities:
 * - answer popup messages (detect, status, pending attachments, open app);
 * - run saves through a long-lived port so they survive the popup closing;
 * - keep the toolbar icon in sync (item count badge, grey icon when offline, green / amber
 *   check when the page's reference is already saved with / without its PDF);
 * - context menus (save page / link / selection);
 * - auto attach: when a Sci-Hub / SciDB article page opens for an item still missing its PDF
 *   (typically after the user completed a human check), download and attach it, then notify.
 *
 * @module background
 */

import * as api from "./api.js";
import { detectInTab } from "./core/detect.js";
import { runSave, runAttachPending } from "./core/save.js";
import { acquirePdf } from "./core/pdf.js";
import { getSettings, setSettings, getPending, matchPending, removePending } from "./settings.js";
import { identifiersInText, isLikelyPdfUrl, fileNameFromUrl, doiFromUrl, shadowLibraryKind } from "./shared/identifiers.js";

/** How long a ping result is trusted (ms). */
const STATUS_TTL = 20000;

/** Icon sets for the two connection states. */
const ICONS = {
  online: { 16: "icons/icon-16.png", 32: "icons/icon-32.png", 48: "icons/icon-48.png", 128: "icons/icon-128.png" },
  offline: { 16: "icons/icon-grey-16.png", 32: "icons/icon-grey-32.png", 48: "icons/icon-grey-48.png", 128: "icons/icon-grey-128.png" },
};

// ------------------------------------------------------------------------------------------
// Connection status (cached in storage.session so a restarted worker does not re-ping)
// ------------------------------------------------------------------------------------------

/**
 * Get the ReviewHunter connection status, pinging at most every 20 s unless forced.
 *
 * @param {{force?: boolean}} [opts]
 * @returns {Promise<{online: boolean, info?: object, error?: string, at: number, serverUrl: string}>}
 */
async function getStatus(opts = {}) {
  const { serverUrl } = await getSettings();
  const { status } = await chrome.storage.session.get("status");
  if (!opts.force && status && status.serverUrl === serverUrl && Date.now() - status.at < STATUS_TTL) return status;
  let next;
  try {
    const info = await api.ping({ timeout: 2500 });
    next = { online: info && info.app === "ReviewHunter", info, at: Date.now(), serverUrl };
    if (!next.online) next.error = "Another application answered on this address.";
  } catch (e) {
    next = { online: false, error: e.message, at: Date.now(), serverUrl };
  }
  await chrome.storage.session.set({ status: next });
  await applyIcon(next.online);
  return next;
}

/**
 * Switch the toolbar icon between colour and grey and set its tooltip.
 *
 * @param {boolean} online  Connection state.
 * @returns {Promise<void>}
 */
async function applyIcon(online) {
  try {
    await chrome.action.setIcon({ path: online ? ICONS.online : ICONS.offline });
    await chrome.action.setTitle({ title: online ? "Save to ReviewHunter" : "ReviewHunter is not running (click for help)" });
  } catch (_) {
    /* the worker may be shutting down */
  }
}

// ------------------------------------------------------------------------------------------
// Badge: number of items detected on the active tab
// ------------------------------------------------------------------------------------------

/** Pending badge refresh timers per tab (debounce rapid navigation events). */
const badgeTimers = new Map();

/**
 * Schedule a badge refresh for a tab.
 *
 * @param {number} tabId  Tab id.
 * @param {number} [delay=400]  Debounce delay (ms).
 * @returns {void}
 */
function scheduleBadge(tabId, delay = 400) {
  clearTimeout(badgeTimers.get(tabId));
  badgeTimers.set(tabId, setTimeout(() => {
    badgeTimers.delete(tabId);
    updateBadge(tabId).catch(() => {});
  }, delay));
}

/**
 * Detect items on a tab and show the count on the badge.
 *
 * @param {number} tabId  Tab id.
 * @returns {Promise<void>}
 */
async function updateBadge(tabId) {
  const status = await getStatus();
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || !tab.active || !/^https?:/i.test(tab.url || "")) {
    await chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {});
    return;
  }
  if (tab.url.startsWith(status.serverUrl)) return;
  const det = await detectInTab(tabId);
  const count = det.kind === "multiple" ? det.items.length : det.kind === "single" || det.kind === "pdf" ? 1 : 0;
  const scholarly = det.kind === "multiple" || det.kind === "pdf" || (det.kind === "single" && det.scholarly);
  if (status.online && scholarly && (det.kind === "single" || det.kind === "pdf")) {
    const saved = await savedState(det.items[0]);
    if (saved) {
      await showSavedBadge(tabId, saved);
      return;
    }
  }
  // Plain web pages are still savable, but only scholarly finds get a number (like Zotero's icon change).
  await chrome.action.setBadgeText({ tabId, text: count && scholarly ? String(count > 99 ? "99+" : count) : "" });
  await chrome.action.setBadgeBackgroundColor({ tabId, color: status.online ? "#0284c7" : "#94a3b8" });
  if (chrome.action.setBadgeTextColor) await chrome.action.setBadgeTextColor({ tabId, color: "#ffffff" }).catch(() => {});
  const what = det.kind === "multiple" ? `${count} references` : det.kind === "pdf" ? "PDF" : det.items && det.items[0] ? det.items[0].item_type : "";
  await chrome.action.setTitle({
    tabId,
    title: status.online ? `Save to ReviewHunter${what ? ` (${det.translator}: ${what})` : ""}` : "ReviewHunter is not running (click for help)",
  });
}

/** Library lookups per reference key, reused for a few seconds (tab switches re-run detection). */
const savedCache = new Map();
const SAVED_TTL = 8000;

/**
 * Is the detected reference already in the library, and does it have its PDF?
 *
 * @param {object} item  Detected item (doi / url / title).
 * @returns {Promise<?{id: number, title: string, hasPdf: boolean}>} Null when not saved or unknown.
 */
async function savedState(item) {
  if (!item) return null;
  const q = item.doi ? { doi: item.doi } : { url: item.url || undefined, title: item.title || undefined };
  const key = JSON.stringify(q);
  const hit = savedCache.get(key);
  if (hit && Date.now() - hit.at < SAVED_TTL) return hit.value;
  let value = null;
  try {
    const r = await api.lookup(q);
    if (r.found && r.items.length) {
      const best = r.items.find((i) => i.has_pdf) || r.items[0];
      value = { id: best.id, title: best.title, hasPdf: !!best.has_pdf };
    }
  } catch (_) {
    value = null;
  }
  savedCache.set(key, { at: Date.now(), value });
  return value;
}

/** Forget cached lookups (after a save or an attachment). */
function invalidateSaved() {
  savedCache.clear();
}

/**
 * Green check: saved with its PDF. Amber check: saved, PDF still missing.
 *
 * @param {number} tabId  Tab id.
 * @param {{title: string, hasPdf: boolean}} saved  Saved state.
 * @returns {Promise<void>}
 */
async function showSavedBadge(tabId, saved) {
  await chrome.action.setBadgeText({ tabId, text: "\u2713" }).catch(() => {});
  await chrome.action.setBadgeBackgroundColor({ tabId, color: saved.hasPdf ? "#16a34a" : "#d97706" }).catch(() => {});
  if (chrome.action.setBadgeTextColor) await chrome.action.setBadgeTextColor({ tabId, color: "#ffffff" }).catch(() => {});
  await chrome.action.setTitle({
    tabId,
    title: saved.hasPdf ? `Saved in ReviewHunter with its PDF: ${saved.title}` : `Saved in ReviewHunter, PDF missing: ${saved.title}`,
  }).catch(() => {});
}

/**
 * Desktop notification (used when a save finishes outside the popup).
 *
 * @param {string} title    Title line.
 * @param {string} message  Body.
 * @returns {void}
 */
function notify(title, message) {
  if (!chrome.notifications) return;
  chrome.notifications.create(
    { type: "basic", iconUrl: chrome.runtime.getURL("icons/icon-128.png"), title, message: String(message).slice(0, 250), priority: 0 },
    () => void chrome.runtime.lastError,
  );
}

/**
 * Flash a short message on the badge (context-menu feedback), then restore the count.
 *
 * @param {number} tabId  Tab id.
 * @param {string} text   Up to 4 characters.
 * @param {string} color  Background colour.
 * @param {string} [title]  Tooltip while the message is shown.
 * @returns {Promise<void>}
 */
async function flashBadge(tabId, text, color, title) {
  await chrome.action.setBadgeText({ tabId, text }).catch(() => {});
  await chrome.action.setBadgeBackgroundColor({ tabId, color }).catch(() => {});
  if (title) await chrome.action.setTitle({ tabId, title }).catch(() => {});
  setTimeout(() => scheduleBadge(tabId, 0), 5000);
}

chrome.tabs.onActivated.addListener(({ tabId }) => scheduleBadge(tabId));
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status === "complete" && tab.active) scheduleBadge(tabId, 600);
  if (info.status === "complete" && shadowLibraryKind(tab.url || "")) autoAttach(tabId, tab.url).catch(() => {});
});

// ------------------------------------------------------------------------------------------
// Auto attach from Sci-Hub / SciDB article pages
// ------------------------------------------------------------------------------------------

/** Items currently being attached (avoid double uploads when a page fires several events). */
const attaching = new Set();
/** How long a verification wall is re-checked after the page loaded (ms). */
const CHALLENGE_WAIT_MS = 5 * 60 * 1000;

/**
 * A Sci-Hub / SciDB page finished loading. If its DOI belongs to an item that still has no
 * PDF (pending after a failed save, or found in the library without a file), download the
 * PDF shown on the page with the user's session and attach it.
 *
 * While the page is a human-verification wall, nothing is automated: the tab is re-checked
 * every 3 s (up to 5 min) until the user has completed the check and the article appears.
 *
 * @param {number} tabId  Tab id.
 * @param {string} url    Tab URL.
 * @returns {Promise<void>}
 */
async function autoAttach(tabId, url) {
  const settings = await getSettings();
  if (!settings.useShadowLibraries) return;
  const doi = doiFromUrl(url);
  if (!doi) return;
  const pending = (await getPending()).find((p) => p.doi && p.doi.toLowerCase() === doi.toLowerCase());
  let target = pending ? { id: pending.itemId, title: pending.title } : null;
  if (!target) {
    if (!(await getStatus()).online) return;
    const saved = await savedState({ doi });
    if (!saved || saved.hasPdf) return;
    target = { id: saved.id, title: saved.title };
  }
  if (attaching.has(target.id)) return;
  attaching.add(target.id);
  try {
    const started = Date.now();
    let det = await detectInTab(tabId);
    while (det.challenge && Date.now() - started < CHALLENGE_WAIT_MS) {
      await new Promise((r) => setTimeout(r, 3000));
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (!tab || doiFromUrl(tab.url || "").toLowerCase() !== doi.toLowerCase()) return; // closed or navigated away
      det = await detectInTab(tabId);
    }
    const item = det.items && det.items[0];
    const candidates = (item && item._pdfCandidates) || [];
    if (det.challenge || !candidates.length) return;
    const status = await api.itemStatus(target.id).catch(() => null);
    if (!status || status.in_trash) return;
    if (status.has_pdf) {
      await removePending(target.id);
      return;
    }
    await flashBadge(tabId, "...", "#0284c7", "ReviewHunter: attaching the PDF");
    const pdf = await acquirePdf(candidates, { tabId, allowBackgroundTab: false });
    if (!pdf.ok) {
      const last = pdf.attempts[pdf.attempts.length - 1];
      await flashBadge(tabId, "ERR", "#dc2626", `ReviewHunter: PDF not attached (${last ? last.error : "download failed"})`);
      return;
    }
    await api.uploadAttachment(target.id, pdf.bytes, { url: pdf.finalUrl, title: "Full Text PDF", contentType: "application/pdf" });
    await removePending(target.id);
    invalidateSaved();
    // Confirm from the server before telling the user it is saved.
    const after = await api.itemStatus(target.id).catch(() => null);
    if (after && after.has_pdf) {
      clearTimeout(badgeTimers.get(tabId));
      await showSavedBadge(tabId, { title: after.title, hasPdf: true });
      notify("PDF saved in ReviewHunter", `${after.title}${after.collections.length ? `\nCollection: ${after.collections.join(", ")}` : ""}`);
    }
  } catch (e) {
    await flashBadge(tabId, "ERR", "#dc2626", `ReviewHunter: ${e.message}`);
  } finally {
    attaching.delete(target.id);
  }
}
chrome.windows.onFocusChanged.addListener(async (windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  const [tab] = await chrome.tabs.query({ active: true, windowId });
  if (tab) scheduleBadge(tab.id);
});

// ------------------------------------------------------------------------------------------
// Popup messages and save ports
// ------------------------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // Failures travel in a dedicated key: normal payloads (e.g. an offline status) may carry their own "error" field.
  handleMessage(msg).then(sendResponse, (e) => sendResponse({ __rhError: (e && e.message) || String(e) }));
  return true; // async response
});

/**
 * Route one-shot messages from the popup / options page.
 *
 * @param {{type: string, [key: string]: any}} msg  Message.
 * @returns {Promise<any>} Response payload.
 */
async function handleMessage(msg) {
  switch (msg && msg.type) {
    case "status":
      return getStatus({ force: !!msg.force });
    case "detect": {
      const det = await detectInTab(msg.tabId);
      scheduleBadge(msg.tabId, 0);
      return det;
    }
    case "pending": {
      const doi = msg.doi || doiFromUrl(msg.url || "");
      const list = await getPending();
      return list.map((p) => ({ ...p, score: matchPending(p, msg.url || "", doi) })).sort((a, b) => b.score - a.score || b.at - a.at);
    }
    case "openApp":
      return openApp(msg.itemId);
    default:
      throw new Error(`unknown message ${msg && msg.type}`);
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "rh-save") return;
  let open = true;
  port.onDisconnect.addListener(() => {
    open = false;
  });
  const post = (m) => {
    if (open) {
      try {
        port.postMessage(m);
      } catch (_) {
        open = false;
      }
    }
  };
  port.onMessage.addListener(async (msg) => {
    const onProgress = (steps) => post({ type: "progress", steps });
    let result;
    if (msg.type === "save") {
      result = await runSave(msg.request, onProgress);
      if (msg.request.collectionId !== undefined) await setSettings({ lastCollectionId: msg.request.collectionId || 0 }); // 0 = library root
    } else if (msg.type === "attachPending") {
      result = await runAttachPending(msg.request, onProgress);
    } else {
      result = { ok: false, itemIds: [], message: "unknown request" };
    }
    invalidateSaved();
    post({ type: "done", result });
    if (msg.request && msg.request.tabId !== undefined && msg.request.tabId !== null) scheduleBadge(msg.request.tabId, 0);
  });
});

/**
 * Focus an existing ReviewHunter tab or open a new one.
 *
 * @param {number} [itemId]  Item to reveal (passed as ?item= for future deep links).
 * @returns {Promise<{ok: boolean}>}
 */
async function openApp(itemId) {
  const { serverUrl } = await getSettings();
  const tabs = await chrome.tabs.query({ url: [`${serverUrl}/*`] }).catch(() => []);
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true });
    await chrome.windows.update(tabs[0].windowId, { focused: true }).catch(() => {});
  } else {
    await chrome.tabs.create({ url: `${serverUrl}/${itemId ? `?item=${encodeURIComponent(itemId)}` : ""}` });
  }
  return { ok: true };
}

// ------------------------------------------------------------------------------------------
// Context menus
// ------------------------------------------------------------------------------------------

/**
 * (Re)create the context menu entries.
 *
 * @returns {void}
 */
function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "rh-page", title: "Save page to ReviewHunter", contexts: ["page", "frame"] });
    chrome.contextMenus.create({ id: "rh-link", title: "Save link to ReviewHunter", contexts: ["link"] });
    chrome.contextMenus.create({ id: "rh-selection", title: "Add selection as identifiers to ReviewHunter", contexts: ["selection"] });
  });
}

chrome.runtime.onInstalled.addListener(() => {
  createMenus();
  getStatus({ force: true }).catch(() => {});
});
chrome.runtime.onStartup.addListener(() => {
  createMenus();
  getStatus({ force: true }).catch(() => {});
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  handleMenu(info, tab).catch((e) => {
    if (tab && tab.id !== undefined) flashBadge(tab.id, "ERR", "#dc2626", `ReviewHunter: ${e.message}`);
  });
});

/**
 * Default save options for actions started outside the popup.
 *
 * @returns {Promise<{collectionId: ?number, attachPdf: boolean, enrich: boolean, snapshot: boolean}>}
 */
async function menuDefaults() {
  const s = await getSettings();
  return { collectionId: s.defaultCollectionId || s.lastCollectionId || null, attachPdf: s.attachPdf, enrich: s.improveMetadata, snapshot: s.saveSnapshot };
}

/**
 * Handle a context menu click.
 *
 * @param {chrome.contextMenus.OnClickData} info  Click data.
 * @param {chrome.tabs.Tab} tab  Tab where the menu was used.
 * @returns {Promise<void>}
 */
async function handleMenu(info, tab) {
  const tabId = tab && tab.id;
  const status = await getStatus({ force: true });
  if (!status.online) throw new Error("ReviewHunter is not running");
  const d = await menuDefaults();
  await flashBadge(tabId, "...", "#0284c7", "ReviewHunter: saving");

  if (info.menuItemId === "rh-page") {
    const det = await detectInTab(tabId);
    if (det.kind === "multiple") {
      // Several references: let the user choose in the popup when Chrome allows opening it.
      try {
        await chrome.action.openPopup();
        return;
      } catch (_) {
        throw new Error("several references found: click the toolbar button to choose");
      }
    }
    if (det.kind !== "single" && det.kind !== "pdf") throw new Error(det.error || "nothing to save");
    const res = await runSave({ tabId, detection: det, tags: [], ...d }, () => {});
    if (!res.ok) throw new Error(res.message);
    invalidateSaved();
    const st = res.itemIds.length ? await api.itemStatus(res.itemIds[0]).catch(() => null) : null;
    await flashBadge(tabId, "OK", "#16a34a", "ReviewHunter: saved");
    if (st) notify(st.has_pdf ? "Saved in ReviewHunter with its PDF" : "Saved in ReviewHunter (no PDF yet)", st.title);
    return;
  }

  if (info.menuItemId === "rh-link") {
    const link = info.linkUrl || "";
    const ids = identifiersInText(link);
    if (ids.length) {
      const text = ids.map((i) => (i.type === "arxiv" ? `arXiv:${i.value}` : i.type === "pmid" ? `PMID: ${i.value}` : i.value)).join("\n");
      const out = await api.saveIdentifiers({ text, collection_id: d.collectionId, tags: [] });
      if (!out.saved.length) throw new Error(`could not resolve ${text}`);
      if (d.attachPdf) {
        for (const s of out.saved.filter((x) => x.needs_pdf && x.pdf_url)) {
          const pdf = await acquirePdf([s.pdf_url], { tabId, allowBackgroundTab: false });
          if (pdf.ok) await api.uploadAttachment(s.id, pdf.bytes, { url: pdf.finalUrl, title: "Full Text PDF" }).catch(() => {});
        }
      }
      await flashBadge(tabId, "OK", "#16a34a", `ReviewHunter: saved ${out.saved[0].title}`);
      return;
    }
    if (isLikelyPdfUrl(link)) {
      const pdf = await acquirePdf([link], { tabId, allowBackgroundTab: true });
      if (!pdf.ok) throw new Error("the PDF could not be downloaded; open it in a tab and use the toolbar button");
      await api.importPdf(new Blob([pdf.bytes], { type: "application/pdf" }), fileNameFromUrl(pdf.finalUrl), d.collectionId);
      await flashBadge(tabId, "OK", "#16a34a", "ReviewHunter: PDF imported");
      return;
    }
    const { saved } = await api.saveItems({
      items: [{ item_type: "webpage", title: info.selectionText || link, url: link }],
      collection_id: d.collectionId,
      tags: [],
      enrich: false,
    });
    await flashBadge(tabId, "OK", "#16a34a", `ReviewHunter: saved ${saved[0].title}`);
    return;
  }

  if (info.menuItemId === "rh-selection") {
    const out = await api.saveIdentifiers({ text: info.selectionText || "", collection_id: d.collectionId, tags: [] });
    if (!out.saved.length) throw new Error("no DOI, arXiv id, PMID or ISBN recognised in the selection");
    await flashBadge(tabId, String(out.saved.length), "#16a34a", `ReviewHunter: ${out.saved.length} saved${out.failed.length ? `, ${out.failed.length} not found` : ""}`);
  }
}
