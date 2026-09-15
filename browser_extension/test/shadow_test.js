/**
 * Sci-Hub / SciDB fallback tests (core/shadow.js) against fake mirrors served by test/serve.py.
 *
 * chrome.storage is mocked so that api.js talks to serve.py, whose
 * /__shadow/<scenario>/connector/pdf-sources route plays the ReviewHunter server.
 * Two host names (127.0.0.1 and localhost) stand for two mirror domains.
 *
 * Open http://127.0.0.1:8799/test/shadow_test.html (served by test/serve.py).
 *
 * @module shadow_test
 */

/** Scenario selected through the mocked server URL. */
let scenario = "";

globalThis.chrome = {
  storage: {
    local: { get: async () => ({ serverUrl: `${location.origin}/__shadow/${scenario}` }) },
    session: { get: async () => ({}), set: async () => {} },
  },
  scripting: { executeScript: async ({ func, args }) => [{ result: await func(...(args || [])) }] },
};

const { acquireFromShadowLibraries, shadowPages } = await import("../src/core/shadow.js");

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

const DOI = "10.1177/0269881107082286";
const port = location.port;

// 1. First mirror shows a human check, second mirror serves the article: PDF acquired, wall not "solved".
{
  scenario = "wall-then-article";
  const steps = [];
  const r = await acquireFromShadowLibraries(DOI, { onStep: (s) => steps.push(s) });
  check(r.ok && r.bytes.length === 4096 && r.via === "Sci-Hub (b)", "article on the second mirror is downloaded", { ok: r.ok, via: r.via, attempts: r.attempts });
  check(r.ok && /^%PDF-/.test(new TextDecoder().decode(r.bytes.slice(0, 5))), "bytes are a PDF");
  check(r.attempts.length === 1 && r.attempts[0].error === "human verification required", "the wall is reported, not bypassed", r.attempts);
  check(steps.some((s) => /Trying Sci-Hub \(a\)/.test(s)) && steps.some((s) => /Downloading from/.test(s)), "progress steps emitted", steps);
}

// 2. Only a wall, plus an alias redirecting to it: blocked page returned for the user, alias skipped.
{
  scenario = "wall-and-alias";
  const r = await acquireFromShadowLibraries(DOI, {});
  check(!r.ok, "no PDF when every mirror shows a wall", r);
  check(r.blocked && r.blocked.url === `http://127.0.0.1:${port}/__shadow/wall` && r.blocked.source === "Sci-Hub (a)", "blocked page is returned so it can be opened for the user", r.blocked);
  check(r.attempts.length === 1, "alias redirecting to an already tried mirror is skipped", r.attempts);
}

// 3. Older ReviewHunter without /connector/pdf-sources: built-in mirror list.
{
  scenario = "unknown-scenario-returns-empty";
  const pages = await shadowPages(DOI);
  check(pages.length >= 2 && pages[0].url === `https://sci-hub.ren/${DOI}`, "built-in mirror list used when the server gives none", pages);
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
