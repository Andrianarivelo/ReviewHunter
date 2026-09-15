/**
 * Browser hand-off tests (core/handoff.js) against fake sources served by test/serve.py.
 *
 * chrome.tabs / chrome.scripting do not exist in a plain page, so the runner's dependencies are
 * injected: a "tab" is a URL fetched by the page (same-origin), a "probe" returns that document,
 * and fetchInTab is a plain fetch. The server side is played by serve.py routes:
 *   /__handoff/pdf                  a PDF served directly
 *   /__handoff/ddg?key=K            DDoS-Guard page on the first two hits, then the article page
 *   /__handoff/article              article page embedding /__gen/pdf in an <embed>
 *   /__handoff/wall                 ALTCHA human check (never solved)
 *   /__handoff/block                web-filter block page
 *   /__handoff/connector/...        fake ReviewHunter connector (item status, attachment, hand-off status)
 *
 * Open http://127.0.0.1:8799/test/handoff_test.html (served by test/serve.py).
 *
 * @module handoff_test
 */

globalThis.chrome = {
  storage: {
    local: { get: async () => ({ serverUrl: `${location.origin}/__handoff` }) },
    session: { get: async () => ({}), set: async () => {} },
  },
  runtime: { getURL: (p) => p },
};

const { runHandoff, fetchFromPage, WALL_WAIT_MS } = await import("../src/core/handoff.js");
const { classifyWall } = await import("../src/shared/identifiers.js");
const { fetchInWorker, withReferer } = await import("../src/core/pdf.js");

const results = [];

/**
 * Record an assertion.
 *
 * @param {boolean} cond  Condition.
 * @param {string} name   Assertion name.
 * @param {*} [detail]    Shown on failure.
 * @returns {void}
 */
function check(cond, name, detail) {
  results.push({ name, ok: !!cond, detail: cond ? "" : JSON.stringify(detail) });
}

/**
 * Fake Chrome dependencies: tabs are fetched by the page itself.
 *
 * @param {object} [state]  Shared state receiving reports / uploads / opened pages.
 * @returns {object} deps for runHandoff.
 */
function fakeDeps(state = {}) {
  const tabs = new Map();
  let nextId = 1;
  state.reports = [];
  state.uploads = [];
  state.opened = [];
  state.pending = [];
  state.hasPdf = false;
  const fetchBytes = async (url) => {
    const res = await fetch(url, { cache: "no-store" });
    const buf = new Uint8Array(await res.arrayBuffer());
    const ctype = res.headers.get("content-type") || "";
    if (buf.length >= 5 && buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46) return { ok: true, bytes: buf, finalUrl: res.url };
    return { ok: false, error: res.ok ? `not a PDF (${ctype.split(";")[0]})` : `HTTP ${res.status}`, html: /html/.test(ctype) ? new TextDecoder().decode(buf) : "", finalUrl: res.url };
  };
  return {
    fetchInWorker: fetchBytes,
    fetchInTab: (_tabId, url) => fetchBytes(url),
    createTab: async (url) => {
      const id = nextId++;
      tabs.set(id, { id, url });
      return { id, url };
    },
    getTab: async (id) => ({ ...tabs.get(id), status: "complete" }),
    updateTab: async (id, url) => tabs.set(id, { id, url }),
    removeTab: async (id) => tabs.delete(id),
    probe: async (id) => {
      const t = tabs.get(id);
      const res = await fetch(t.url, { cache: "no-store" });
      const ctype = res.headers.get("content-type") || "";
      if (ctype.includes("pdf")) return { contentType: "application/pdf", href: res.url, html: "" };
      return { contentType: "text/html", href: res.url, html: await res.text() };
    },
    openForUser: async (url) => {
      state.opened.push(url);
      return 99;
    },
    addPending: async (entry) => state.pending.push(entry),
    upload: async (itemId, bytes, meta) => {
      state.uploads.push({ itemId, size: bytes.length, url: meta.url });
      state.hasPdf = true;
      return { ok: true };
    },
    itemStatus: async (itemId) => ({ id: itemId, has_pdf: state.hasPdf, in_trash: false, title: "T" }),
    report: async (itemId, st, message, step) => state.reports.push({ itemId, state: st, message, step }),
    sleep: (ms) => new Promise((r) => setTimeout(r, Math.min(ms, 30))),
  };
}

const O = location.origin;
const DOI = "10.1177/0269881107082286";

// 1. Direct PDF (kind pdf, no referer): fetched from the worker, uploaded, reported done.
{
  const state = {};
  const r = await runHandoff({ item_id: 1, doi: DOI, title: "T", plan: [{ url: `${O}/__handoff/pdf`, source: "Open access (Europe PMC)", kind: "pdf", referer: "" }] }, { deps: fakeDeps(state) });
  check(r.ok && r.via === "Open access (Europe PMC)", "direct PDF is attached", r);
  check(state.uploads.length === 1 && state.uploads[0].size === 4096, "bytes uploaded once", state.uploads);
  check(state.reports.some((x) => x.state === "done"), "done reported to the server", state.reports);
}

// 2. Sci-Hub proxy: kind pdf with referer -> PDF fetched from the article page context.
{
  const state = {};
  const r = await runHandoff({ item_id: 2, doi: DOI, title: "T", plan: [{ url: `${O}/__gen/pdf?size=2048`, source: "Sci-Hub (sci-hub.ren)", kind: "pdf", referer: `${O}/__handoff/article` }] }, { deps: fakeDeps(state) });
  check(r.ok && state.uploads[0].size === 2048, "PDF with Referer fetched from the page", { r, uploads: state.uploads });
}

// 3. Page behind a DDoS-Guard check: the tab waits until the article appears, then the embed is fetched.
{
  const state = {};
  const key = Math.random().toString(36).slice(2);
  const steps = [];
  const r = await runHandoff({ item_id: 3, doi: DOI, title: "T", plan: [{ url: `${O}/__handoff/ddg?key=${key}`, source: "Anna's Archive SciDB (annas-archive.pk)", kind: "page", referer: "", wall: "auto" }] }, { deps: fakeDeps(state), onStep: (s) => steps.push(s) });
  check(r.ok && r.via.startsWith("Anna's Archive"), "JavaScript wall cleared in the tab, embed downloaded", { r, steps });
  check(steps.some((s) => /DDoS-Guard browser check/.test(s)), "waiting on the wall is reported", steps);
}

// 4. Human check only: nothing solved, the page is opened for the user, item remembered as pending, state waiting.
{
  const state = {};
  const r = await runHandoff({ item_id: 4, doi: DOI, title: "T", plan: [
    { url: `${O}/__handoff/wall`, source: "Sci-Hub (sci-hub.fr)", kind: "page", referer: "", wall: "interactive" },
    { url: `${O}/__handoff/block`, source: "Sci-Hub (sci-hub.wf)", kind: "page", referer: "" },
  ] }, { deps: fakeDeps(state) });
  check(!r.ok && r.waiting, "human check leads to waiting, not failure", r);
  check(state.opened.length === 1 && state.opened[0].endsWith("/__handoff/wall"), "the wall page is opened for the user once", state.opened);
  check(state.pending.length === 1 && state.pending[0].itemId === 4, "item remembered for auto attach", state.pending);
  check(state.reports.at(-1).state === "waiting", "waiting reported", state.reports.at(-1));
  check(r.attempts.some((a) => /web filter/.test(a.error)), "block page reported as such", r.attempts);
}

// 5. Nothing works: failed with the list of attempts; already-attached items are skipped.
{
  const state = {};
  const r = await runHandoff({ item_id: 5, doi: DOI, title: "T", plan: [{ url: `${O}/__handoff/nope`, source: "X", kind: "pdf", referer: "" }] }, { deps: fakeDeps(state) });
  check(!r.ok && !r.waiting && /X: /.test(r.error), "failure lists the attempts", r);
  check(state.reports.at(-1).state === "failed", "failed reported", state.reports.at(-1));
  const s2 = {};
  const deps = fakeDeps(s2);
  s2.hasPdf = true;
  const r2 = await runHandoff({ item_id: 6, doi: DOI, title: "T", plan: [{ url: `${O}/__handoff/pdf`, source: "X", kind: "pdf", referer: "" }] }, { deps });
  check(r2.ok && r2.via === "already attached" && s2.uploads.length === 0, "an item that already has its PDF is not downloaded again", r2);
}

// 6. Classifier sanity (mirrors the server).
{
  check(classifyWall('<title>Just a moment...</title>').kind === "auto", "Cloudflare = auto");
  check(classifyWall('<title>UNIGE - Site bloqué</title><div>Fortinet</div>').kind === "blocked", "FortiGuard = blocked");
  check(classifyWall('<altcha-widget></altcha-widget>').kind === "interactive", "ALTCHA = interactive");
  check(classifyWall("<html><body>Deep learning</body></html>") === null, "article = content");
  check(WALL_WAIT_MS >= 20000, "walls are given time to clear");
  const p = await fetchFromPage(`${O}/__handoff/article`, "", fakeDeps({}), () => {});
  check(p.ok && p.bytes.length === 4096, "fetchFromPage finds the embed on a real page", p);
}

// 7. Referer injection: a session rule is installed for the exact URL while the worker downloads, then removed.
{
  const calls = [];
  globalThis.chrome.runtime.id = "abcdefghijklmnop";
  globalThis.chrome.declarativeNetRequest = { updateSessionRules: async (o) => calls.push(o) };
  const r = await fetchInWorker(`${O}/__handoff/pdf`, { referer: "https://sci-hub.ren/10.1177/x" });
  check(r.ok && r.bytes.length === 4096, "download under the Referer rule succeeds", r);
  const add = calls.find((c) => c.addRules);
  const rule = add && add.addRules[0];
  check(rule && rule.action.requestHeaders[0].header === "Referer" && rule.action.requestHeaders[0].value === "https://sci-hub.ren/10.1177/x", "rule sets the Referer header", add);
  check(rule && rule.condition.urlFilter === `${O}/__handoff/pdf` && rule.condition.initiatorDomains[0] === "abcdefghijklmnop", "rule is scoped to the URL and to this extension", rule);
  check(calls.some((c) => c.removeRuleIds && c.removeRuleIds[0] === rule.id && !c.addRules), "rule removed after the download", calls);
  const none = [];
  globalThis.chrome.declarativeNetRequest = { updateSessionRules: async (o) => none.push(o) };
  await fetchInWorker(`${O}/__handoff/pdf`);
  check(none.length === 0, "no rule without a referer", none);
  delete globalThis.chrome.declarativeNetRequest;
  check((await withReferer("https://x.example/a.pdf", "https://x.example/", async () => 42)) === 42, "withReferer degrades gracefully without the permission");
}

const passed = results.filter((r) => r.ok).length;
window.__results = { passed, total: results.length, failures: results.filter((r) => !r.ok) };
document.getElementById("summary").textContent = `${passed === results.length ? "PASS" : "FAIL"}: ${passed}/${results.length} assertions passed`;
document.getElementById("summary").className = passed === results.length ? "pass" : "fail";
for (const r of results) {
  const li = document.createElement("li");
  li.className = r.ok ? "pass" : "fail";
  li.textContent = `${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? `  ->  ${r.detail}` : ""}`;
  document.getElementById("results").append(li);
}
