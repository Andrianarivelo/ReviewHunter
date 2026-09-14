/*
 * Tiny REST client for the ReviewHunter API.
 *
 * Every helper throws an Error whose message is the server's `detail` (FastAPI) or
 * `error` field, so UI code can simply `try { ... } catch (e) { toast(e.message) }`.
 */
(function (global) {
  'use strict';

  /**
   * Perform a request and decode JSON (or text for non-JSON responses).
   * @param {string} method HTTP verb.
   * @param {string} url Relative API URL.
   * @param {any} [body] JSON body (objects) or FormData.
   * @returns {Promise<any>}
   */
  async function request(method, url, body) {
    const init = { method, headers: {} };
    if (body instanceof FormData) {
      init.body = body;
    } else if (body !== undefined) {
      init.headers['content-type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const r = await fetch(url, init);
    const type = r.headers.get('content-type') || '';
    const data = type.includes('application/json') ? await r.json().catch(() => ({})) : await r.text();
    if (!r.ok) {
      const msg = (data && (data.detail || data.error || data.reason)) || `${r.status} ${r.statusText}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  /**
   * Build a query string, repeating keys for array values (?tag=a&tag=b) and skipping empties.
   * @param {Object<string, any>} params
   */
  function qs(params) {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      if (v === undefined || v === null || v === '' || v === false) continue;
      if (Array.isArray(v)) v.forEach(x => sp.append(k, x));
      else sp.set(k, v);
    }
    const s = sp.toString();
    return s ? `?${s}` : '';
  }

  global.api = {
    qs,
    get: (url, params) => request('GET', url + qs(params)),
    post: (url, body) => request('POST', url, body === undefined ? {} : body),
    patch: (url, body) => request('PATCH', url, body),
    put: (url, body) => request('PUT', url, body),
    del: (url, params) => request('DELETE', url + qs(params)),
    /** Multipart upload: fields is an object, files an array of [fieldName, File]. */
    upload(url, files, fields = {}) {
      const fd = new FormData();
      for (const [name, file] of files) fd.append(name, file, file.name);
      for (const [k, v] of Object.entries(fields)) if (v !== undefined && v !== null && v !== '') fd.append(k, v);
      return request('POST', url, fd);
    },
  };

  /**
   * Merge several Alpine component parts into one object while preserving getters/setters
   * (Object.assign would evaluate getters once and freeze their value).
   * @param {...object} parts
   */
  global.mixParts = function mixParts(...parts) {
    const out = {};
    for (const part of parts) {
      for (const key of Object.getOwnPropertyNames(part)) {
        Object.defineProperty(out, key, Object.getOwnPropertyDescriptor(part, key));
      }
    }
    return out;
  };
})(window);
