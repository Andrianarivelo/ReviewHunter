/**
 * PDF acquisition tests (strategies a, b, c) run in a normal page with a mocked
 * chrome.scripting that executes the injected functions locally, so the real
 * code paths (in-page fetch, chunked base64 transfer, worker fetch, interstitial
 * following, %PDF- verification) are exercised against test/serve.py.
 *
 * Open http://127.0.0.1:8799/test/pdf_test.html (served by test/serve.py).
 *
 * @module pdf_test
 */

// Mock only what core/pdf.js needs. executeScript runs the serialisable function right here.
globalThis.chrome = {
  scripting: {
    executeScript: async ({ func, args }) => [{ result: await func(...(args || [])) }],
  },
};

const { acquirePdf, fetchInTab, fetchInWorker, MAX_PDF_BYTES } = await import("../src/core/pdf.js");

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
 * Verify the deterministic pattern produced by /__gen/pdf.
 *
 * @param {Uint8Array} bytes  Downloaded bytes.
 * @param {number} size       Expected size.
 * @returns {boolean} True when every sampled byte matches.
 */
function patternOk(bytes, size) {
  if (bytes.length !== size) return false;
  const header = "%PDF-1.4\n";
  for (let i = 0; i < header.length; i += 1) if (bytes[i] !== header.charCodeAt(i)) return false;
  for (let i = header.length; i < size; i += 9973) if (bytes[i] !== i % 251) return false;
  return bytes[size - 1] === (size - 1) % 251;
}

const origin = location.origin;
const steps = [];

// a) in-page fetch with a 9.5 MB body: 3 chunks of 4 MB through executeScript.
{
  const size = Math.round(9.5 * 1024 * 1024);
  const r = await fetchInTab(1, `${origin}/__gen/pdf?size=${size}`);
  check(r.ok && patternOk(r.bytes, size), "a) page fetch + chunked transfer of 9.5 MB is byte-exact", { ok: r.ok, len: r.bytes && r.bytes.length, error: r.error });
  await new Promise((res) => setTimeout(res, 50));
  check(!Object.keys(globalThis.__reviewHunterPdf || {}).length, "a) bytes kept in the page are released after transfer", Object.keys(globalThis.__reviewHunterPdf || {}));
}

// b) worker fetch following a redirect.
{
  const r = await fetchInWorker(`${origin}/__gen/redirect?to=/__gen/pdf%3Fsize%3D5000`);
  check(r.ok && patternOk(r.bytes, 5000) && r.finalUrl.includes("/__gen/pdf"), "b) worker fetch follows redirects, reports final URL", r.finalUrl);
}

// Login page instead of PDF: rejected by both a) and b), never returned as a PDF.
{
  const r = await acquirePdf([`${origin}/test/fixtures/login.html`], { tabId: 1, onStep: (s) => steps.push(s) });
  check(!r.ok, "login page is not accepted as a PDF", r);
  check(r.attempts.length === 2 && r.attempts.every((a) => /not a PDF/.test(a.error)), "login page: page + extension attempts reported", r.attempts);
}

// c) interstitial: meta refresh -> redirect -> PDF.
{
  const r = await acquirePdf([`${origin}/test/fixtures/interstitial_local.html`], { tabId: 1 });
  check(r.ok && r.via === "interstitial" && patternOk(r.bytes, 70000), "c) interstitial page followed to the real PDF", { ok: r.ok, via: r.via, attempts: r.attempts });
}

// Ordered candidates: 404 first, good PDF second.
{
  const r = await acquirePdf([`${origin}/nope/missing.pdf`, `${origin}/__gen/pdf?size=2048`], { tabId: 1 });
  check(r.ok && r.via === "page" && r.attempts.some((a) => a.error === "HTTP 404"), "404 candidate skipped, next candidate used", r.attempts);
}

// No tab available (context menu on a link): worker only.
{
  const r = await acquirePdf([`${origin}/__gen/pdf?size=3000`], {});
  check(r.ok && r.via === "extension", "without tab: extension fetch", r);
}

// Non-http candidates are ignored, background tab disabled -> clean failure.
{
  const r = await acquirePdf(["javascript:alert(1)", "chrome://settings"], { tabId: 1, allowBackgroundTab: false });
  check(!r.ok && r.attempts.length === 0, "non-http candidates ignored", r);
}

check(MAX_PDF_BYTES === 200 * 1024 * 1024, "size cap is 200 MB");
check(steps.some((s) => /Downloading PDF in the page/.test(s)), "progress steps emitted", steps);

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
