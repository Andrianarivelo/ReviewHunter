/**
 * util.js - Shared helpers for the ReviewHunter PDF reader.
 *
 * Contents:
 *   - COLORS: the Zotero annotation palette.
 *   - ICONS + applyIcons(): inline SVG icon set (no external assets, works offline).
 *   - api(): JSON fetch wrapper with typed errors (offline, HTTP status).
 *   - toast(): transient notifications.
 *   - debounce(), clamp(), el(), storage helpers, clipboard helper.
 */

/** Zotero annotation colors, in Zotero's order. */
export const COLORS = [
  { hex: '#ffd400', name: 'Yellow' },
  { hex: '#ff6666', name: 'Red' },
  { hex: '#5fb236', name: 'Green' },
  { hex: '#2ea8e5', name: 'Blue' },
  { hex: '#a28ae5', name: 'Purple' },
  { hex: '#e56eee', name: 'Magenta' },
  { hex: '#f19837', name: 'Orange' },
  { hex: '#aaaaaa', name: 'Gray' },
];

/**
 * Minimal stroke icon set (24x24 viewBox, currentColor). Kept as path data so
 * every icon inherits text color and scales crisply.
 */
const ICON_PATHS = {
  back: '<path d="M15 18l-6-6 6-6"/>',
  sidebarLeft: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  sidebarRight: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  chevUp: '<path d="M6 15l6-6 6 6"/>',
  chevDown: '<path d="M6 9l6 6 6-6"/>',
  minus: '<path d="M5 12h14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  cursor: '<path d="M5 3l14 8-6.2 1.6L10 19z"/>',
  highlighter: '<path d="M15 4l5 5-8.5 8.5H6.5v-5z"/><path d="M4 21h8"/>',
  underline: '<path d="M7 4v6a5 5 0 0 0 10 0V4"/><path d="M5 20h14"/>',
  note: '<path d="M5 4h14v10l-6 6H5z"/><path d="M13 20v-6h6"/>',
  notePlus: '<path d="M5 4h14v10l-6 6H5z"/><path d="M12 8v6M9 11h6"/>',
  caret: '<path d="M8 10l4 4 4-4"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  file: '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  wifiOff: '<path d="M2 8.8a15 15 0 0 1 4.2-2.6M9.5 5.2A15 15 0 0 1 22 8.8M5 12.5a10 10 0 0 1 3.6-2.2M15.8 10.6A10 10 0 0 1 19 12.5M8.5 16a5 5 0 0 1 7 0M12 20h.01M3 3l18 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
};

/**
 * Build an SVG string for an icon name.
 * @param {string} name - key of ICON_PATHS
 * @param {number} [size=16] - rendered size in CSS px
 * @returns {string} SVG markup ('' if the icon is unknown)
 */
export function icon(name, size = 16) {
  const body = ICON_PATHS[name];
  if (!body) return '';
  return `<svg class="rh-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/**
 * Inject icons into every element carrying a data-icon attribute (prepends
 * the SVG so existing text labels are kept).
 * @param {ParentNode} [root=document]
 */
export function applyIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((node) => {
    if (node.dataset.iconDone) return;
    node.insertAdjacentHTML('afterbegin', icon(node.dataset.icon));
    node.dataset.iconDone = '1';
  });
}

/** Error raised by api(); `status` is 0 when the server is unreachable. */
export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {number} status - HTTP status, 0 for network failure
   */
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/**
 * JSON fetch helper against the same-origin backend. Same-origin fetch
 * automatically sends the Origin header required for mutating requests.
 * @param {string} path - URL path starting with /api
 * @param {{method?: string, body?: any}} [opts]
 * @returns {Promise<any>} parsed JSON (or null for empty bodies)
 * @throws {ApiError}
 */
export async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new ApiError('Cannot reach the ReviewHunter server', 0);
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!res.ok) {
    const detail = data && typeof data === 'object' && data.detail ? data.detail : res.statusText;
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), res.status);
  }
  return data;
}

/**
 * Show a transient toast.
 * @param {string} message
 * @param {'info'|'success'|'error'} [kind='info']
 * @param {number} [ms=3200] - display duration
 */
export function toast(message, kind = 'info', ms = 3200) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const node = el('div', `rh-toast rh-toast-${kind}`);
  node.innerHTML = icon(kind === 'error' ? 'alert' : 'check', 15);
  node.append(document.createTextNode(message));
  host.append(node);
  requestAnimationFrame(() => node.classList.add('is-in'));
  setTimeout(() => {
    node.classList.remove('is-in');
    setTimeout(() => node.remove(), 250);
  }, ms);
}

/**
 * Classic trailing-edge debounce.
 * @template {(...args: any[]) => void} F
 * @param {F} fn
 * @param {number} wait - milliseconds
 * @returns {F & {flush: () => void}} debounced function with flush()
 */
export function debounce(fn, wait) {
  let timer = null;
  let lastArgs = null;
  const debounced = (...args) => {
    lastArgs = args;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn(...lastArgs); }, wait);
  };
  debounced.flush = () => {
    if (timer) { clearTimeout(timer); timer = null; fn(...lastArgs); }
  };
  return debounced;
}

/**
 * Clamp a number into [lo, hi].
 * @param {number} v
 * @param {number} lo
 * @param {number} hi
 * @returns {number}
 */
export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

/**
 * Create an element with an optional class name and text.
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 * @returns {HTMLElement}
 */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Read a JSON value from localStorage, tolerating unavailable storage.
 * @param {string} key
 * @param {any} fallback
 * @returns {any}
 */
export function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch (e) {
    return fallback;
  }
}

/**
 * Write a JSON value to localStorage (silently ignores failures).
 * @param {string} key
 * @param {any} value
 */
export function saveJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
}

/**
 * Copy text to the clipboard with a textarea fallback for non-secure contexts.
 * @param {string} text
 * @returns {Promise<boolean>} true on success
 */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = el('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    ta.remove();
    return ok;
  }
}

/**
 * Collapse runs of whitespace into single spaces (soft hyphenation at line
 * ends is handled by the selection code, which knows where lines break).
 * @param {string} s
 * @returns {string}
 */
export function normalizeText(s) {
  return (s || '').replace(/\s+/g, ' ').trim();
}

/**
 * Format creators into a short author string ("Song, Yang & Wang" or "Song et al.").
 * @param {Array<string|{lastName?: string, last_name?: string, name?: string, creatorType?: string}>} creators
 * @returns {string}
 */
export function formatAuthors(creators) {
  if (!Array.isArray(creators) || !creators.length) return '';
  const names = creators
    .filter((c) => {
      if (typeof c === 'string') return true;
      const role = c.creatorType || c.creator_type;
      return !role || /author/i.test(role);
    })
    // Strings look like "Last, First"; objects carry lastName or a single name.
    .map((c) => (typeof c === 'string' ? c.split(',')[0] : (c.lastName || c.last_name || c.name || c.family || '')))
    .filter(Boolean);
  if (!names.length) return '';
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} & ${names[1]}`;
  if (names.length === 3) return `${names[0]}, ${names[1]} & ${names[2]}`;
  return `${names[0]} et al.`;
}
