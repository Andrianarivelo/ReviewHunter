/**
 * Options page: server address with connection test, default collection and
 * default save toggles. Every change is saved immediately.
 *
 * @module options
 */

import * as api from "../api.js";
import { getSettings, setSettings, normalizeServerUrl } from "../settings.js";

/** Shorthand for getElementById. @param {string} id @returns {HTMLElement} */
const $ = (id) => document.getElementById(id);

/** Timer hiding the "Saved" flag. */
let flagTimer = 0;

/**
 * Briefly show "Settings saved".
 *
 * @returns {void}
 */
function flagSaved() {
  const el = $("saved-flag");
  el.textContent = "Settings saved";
  el.style.opacity = "1";
  clearTimeout(flagTimer);
  flagTimer = setTimeout(() => (el.style.opacity = "0"), 1500);
}

/**
 * Show the result of a connection test.
 *
 * @param {boolean} ok  Success.
 * @param {string} text  Message.
 * @returns {void}
 */
function showResult(ok, text) {
  const el = $("test-result");
  el.hidden = false;
  el.className = `result ${ok ? "ok" : "err"}`;
  el.textContent = text;
}

/**
 * Fill the default-collection select from the server (keeps the stored value).
 *
 * @param {?number} selectedId  Stored default collection.
 * @returns {Promise<void>}
 */
async function loadCollections(selectedId) {
  const select = $("default-collection");
  try {
    const cols = await api.getCollections();
    select.replaceChildren(new Option("My Library (no collection)", ""));
    for (const c of cols) {
      // Non-breaking spaces indent the tree inside a native <select>.
      select.append(new Option(`${"   ".repeat(c.depth)}${c.name}`, String(c.id)));
    }
    select.value = selectedId ? String(selectedId) : "";
    select.disabled = false;
  } catch (_) {
    select.disabled = true;
    select.title = "Start ReviewHunter to list collections";
  }
}

/**
 * Test the address typed in the field (without saving it first).
 *
 * @returns {Promise<void>}
 */
async function testConnection() {
  const base = normalizeServerUrl($("server-url").value);
  const btn = $("btn-test");
  btn.disabled = true;
  btn.textContent = "Testing...";
  const started = performance.now();
  try {
    const info = await api.ping({ base, timeout: 4000 });
    if (!info || info.app !== "ReviewHunter") throw new Error("another application answered on this address");
    showResult(true, `Connected to ReviewHunter ${info.version}: ${info.library.items} items (${Math.round(performance.now() - started)} ms).`);
    await setSettings({ serverUrl: base });
    await chrome.storage.session.remove("status");
    await loadCollections((await getSettings()).defaultCollectionId);
  } catch (e) {
    showResult(false, `Not connected: ${e.message}. Is ReviewHunter running (desktop shortcut) and is the address right?`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Test connection";
  }
}

/**
 * Load settings into the form and wire change handlers.
 *
 * @returns {Promise<void>}
 */
async function init() {
  const s = await getSettings();
  $("server-url").value = s.serverUrl;
  $("opt-pdf").checked = s.attachPdf;
  $("opt-enrich").checked = s.improveMetadata;
  $("opt-snapshot").checked = s.saveSnapshot;
  $("opt-bgtab").checked = s.useBackgroundTab;
  $("opt-shadow").checked = s.useShadowLibraries;
  await loadCollections(s.defaultCollectionId);

  $("btn-test").addEventListener("click", testConnection);
  $("server-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") testConnection();
  });
  $("server-url").addEventListener("change", async () => {
    await setSettings({ serverUrl: $("server-url").value });
    $("server-url").value = (await getSettings()).serverUrl;
    await chrome.storage.session.remove("status");
    flagSaved();
  });
  $("default-collection").addEventListener("change", async (e) => {
    await setSettings({ defaultCollectionId: e.target.value ? Number(e.target.value) : null });
    flagSaved();
  });
  const toggles = { "opt-pdf": "attachPdf", "opt-enrich": "improveMetadata", "opt-snapshot": "saveSnapshot", "opt-bgtab": "useBackgroundTab", "opt-shadow": "useShadowLibraries" };
  for (const [id, key] of Object.entries(toggles)) {
    $(id).addEventListener("change", async (e) => {
      await setSettings({ [key]: e.target.checked });
      flagSaved();
    });
  }
}

init();
