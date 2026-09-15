/**
 * ReviewHunter HTTP client (connector API). Used from the service worker, the
 * popup and the options page. Extension pages send `Origin: chrome-extension://...`,
 * which the server's LocalOriginGuard accepts for state-changing requests.
 *
 * @module api
 */

import { getSettings } from "./settings.js";

/** Error carrying the HTTP status and server message. */
export class ApiError extends Error {
  /**
   * @param {string} message  Human readable message.
   * @param {number} status   HTTP status (0 for network errors / timeouts).
   */
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * fetch() with a timeout.
 *
 * @param {string} url            URL.
 * @param {RequestInit} init      Fetch options.
 * @param {number} timeoutMs      Abort after this many milliseconds.
 * @returns {Promise<Response>} Response.
 */
export async function fetchWithTimeout(url, init = {}, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Call the ReviewHunter API and parse JSON.
 *
 * @param {string} path  Path starting with "/" (e.g. "/connector/ping").
 * @param {{method?: string, json?: any, body?: BodyInit, headers?: object, timeout?: number, base?: string}} [opts]
 * @returns {Promise<any>} Parsed JSON body.
 * @throws {ApiError} On network failure, timeout or non-2xx status.
 */
export async function request(path, opts = {}) {
  const base = opts.base || (await getSettings()).serverUrl;
  const init = { method: opts.method || (opts.json !== undefined || opts.body ? "POST" : "GET"), headers: { ...(opts.headers || {}) }, cache: "no-store" };
  if (opts.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  } else if (opts.body !== undefined) {
    init.body = opts.body;
  }
  let res;
  try {
    res = await fetchWithTimeout(base + path, init, opts.timeout || 60000);
  } catch (e) {
    const timeout = e && e.name === "AbortError";
    throw new ApiError(timeout ? "ReviewHunter did not answer in time" : "ReviewHunter is not reachable", 0);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = text;
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.reason || data.error);
    throw new ApiError(typeof detail === "string" ? detail : `HTTP ${res.status}`, res.status);
  }
  return data;
}

/**
 * GET /connector/ping.
 *
 * @param {{timeout?: number, base?: string}} [opts]  Short timeouts are used for badge checks.
 * @returns {Promise<{app: string, version: string, library: {items: number}}>} Server info.
 */
export function ping(opts = {}) {
  return request("/connector/ping", { timeout: opts.timeout || 2500, base: opts.base });
}

/**
 * GET /connector/collections (tree order with depth and path).
 *
 * @returns {Promise<Array<{id: number, name: string, parent_id: ?number, depth: number, path: string, count: number}>>}
 */
export function getCollections() {
  return request("/connector/collections", { timeout: 8000 });
}

/**
 * GET /connector/tags.
 *
 * @returns {Promise<Array<{name: string, color: ?string, count: number}>>}
 */
export function getTags() {
  return request("/connector/tags", { timeout: 8000 });
}

/**
 * POST /connector/lookup: is this reference already in the library?
 *
 * @param {{doi?: string, url?: string, title?: string}} q  Query.
 * @returns {Promise<{found: boolean, items: Array<{id: number, title: string}>}>}
 */
export function lookup(q) {
  return request("/connector/lookup", { json: q, timeout: 8000 });
}

/**
 * GET /connector/item/{id}: what is really stored (PDF attached? which collections?).
 * Used to confirm a save instead of assuming it worked.
 *
 * @param {number} itemId  Item id.
 * @returns {Promise<{id: number, title: string, doi: string, in_trash: boolean, has_pdf: boolean,
 *   attachments: Array<{title: string, kind: string, mime: string, size: number}>, collections: string[]}>}
 */
export function itemStatus(itemId) {
  return request(`/connector/item/${encodeURIComponent(itemId)}`, { timeout: 8000 });
}

/**
 * GET /connector/pdf-sources: Sci-Hub / SciDB landing pages configured in ReviewHunter for a DOI.
 *
 * @param {string} doi  DOI.
 * @returns {Promise<{doi: string, pages: Array<{source: string, url: string}>}>}
 */
export function pdfSources(doi) {
  return request(`/connector/pdf-sources?doi=${encodeURIComponent(doi)}`, { timeout: 8000 });
}

/**
 * POST /connector/save.
 *
 * @param {{items: object[], collection_id?: ?number, tags?: string[], note?: string, enrich?: boolean, pdf_url?: string}} body
 * @returns {Promise<{saved: Array<{id: number, title: string, existing: boolean, needs_pdf: boolean, pdf_url: ?string}>}>}
 */
export function saveItems(body) {
  return request("/connector/save", { json: body, timeout: 120000 });
}

/**
 * POST /connector/identifiers: resolve DOIs / arXiv ids / PMIDs server-side.
 *
 * @param {{text: string, collection_id?: ?number, tags?: string[]}} body
 * @returns {Promise<{saved: Array<{id: number, title: string, existing: boolean, needs_pdf?: boolean, pdf_url?: ?string}>, failed: string[]}>}
 */
export function saveIdentifiers(body) {
  return request("/connector/identifiers", { json: body, timeout: 180000 });
}

/**
 * GET /connector/handoffs: PDFs the server could not fetch, waiting for the browser.
 *
 * @returns {Promise<{handoffs: Array<{item_id: number, doi: string, title: string, plan: Array<object>, created_at: number, state: string}>}>}
 */
export function handoffs() {
  return request("/connector/handoffs", { timeout: 8000 });
}

/**
 * POST /connector/handoffs/{itemId}/status: progress of a hand-off (relayed to the app as live events).
 *
 * @param {number} itemId  Item id.
 * @param {{state: "running"|"waiting"|"done"|"failed", message?: string, step?: string}} body  Status.
 * @returns {Promise<{ok: boolean, state: string}>}
 */
export function handoffStatus(itemId, body) {
  return request(`/connector/handoffs/${encodeURIComponent(itemId)}/status`, { json: body, timeout: 8000 });
}

/**
 * POST /connector/attachment with raw bytes.
 *
 * @param {number} itemId            Item id.
 * @param {Blob|ArrayBuffer|Uint8Array} bytes  File content.
 * @param {{title?: string, url?: string, kind?: "file"|"snapshot", contentType?: string, filename?: string}} [meta]
 * @returns {Promise<{ok: boolean, item_id: number, attachment: object}>}
 */
export function uploadAttachment(itemId, bytes, meta = {}) {
  const params = new URLSearchParams({ item_id: String(itemId), kind: meta.kind || "file" });
  if (meta.title) params.set("title", meta.title);
  // Signed publisher URLs must remain byte-for-byte intact, including long tokens.
  if (meta.url) params.set("url", meta.url);
  if (meta.filename) params.set("filename", meta.filename);
  return request(`/connector/attachment?${params}`, {
    body: bytes,
    headers: { "Content-Type": meta.contentType || "application/pdf" },
    timeout: 300000,
  });
}

/**
 * POST /api/import/pdfs: identify the PDF, match its reference, then attach it.
 *
 * @param {Blob} pdfBlob         PDF blob.
 * @param {string} filename      Original file name, used only as a fallback.
 * @param {?number} collectionId Optional collection.
 * @param {{metadata?: object, sourceUrl?: string, tags?: string[], note?: string}} [options]
 * @returns {Promise<{imported: number, item_ids: number[], results?: object[]}>}
 */
export function importPdf(pdfBlob, filename, collectionId, options = {}) {
  const form = new FormData();
  form.append("files", pdfBlob, filename);
  if (collectionId) form.append("collection_id", String(collectionId));
  if (options.metadata) form.append("metadata", JSON.stringify(options.metadata));
  if (options.sourceUrl) form.append("source_url", options.sourceUrl);
  if (options.tags?.length) form.append("tags", JSON.stringify(options.tags));
  if (options.note) form.append("note", options.note);
  return request("/api/import/pdfs", { body: form, timeout: 300000 });
}
