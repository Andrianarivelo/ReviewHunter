/** Regression tests for signed PDF downloads and canonical reference imports.
 * Run with: node browser_extension/test/pdf_import_test.mjs
 * Network and Chrome APIs are isolated; the production save pipeline runs intact.
 */
import assert from "node:assert/strict";

const session = { pending: [] };
globalThis.chrome = {
  scripting: { executeScript: async ({ func, args }) => [{ result: await func(...(args || [])) }] },
  storage: {
    local: { get: async () => ({ useBackgroundTab: false }) },
    session: { get: async () => session, set: async (patch) => Object.assign(session, patch) },
  },
};
const { fetchInWorker, fetchInTab } = await import("../src/core/pdf.js");
const { runSave, runAttachPending } = await import("../src/core/save.js");
const { uploadAttachment } = await import("../src/api.js");
const bytes = new TextEncoder().encode("%PDF-1.4\nexample PDF bytes\n%%EOF");
const signed = `https://pdf.sciencedirectassets.com/main.pdf?X-Amz-Security-Token=${"a%2Bb".repeat(600)}&X-Amz-Signature=abcdef`;
let passed = 0;

/** Run each contract against both worker and injected-page download strategies. */
for (const [name, download] of [["worker", (url) => fetchInWorker(url)], ["page", (url) => fetchInTab(1, url)]]) {
  let calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(options.cache);
    assert.equal(url, signed, "the signed URL is preserved");
    return options.cache === "force-cache"
      ? new Response(bytes, { headers: { "Content-Type": "application/pdf" } })
      : new Response("expired link", { status: 403 });
  };
  assert.equal((await download(signed)).ok, true, `${name}: cached PDF survives expired signature`);
  assert.deepEqual(calls, ["force-cache"]);
  passed += 1;

  calls = [];
  globalThis.fetch = async (_, options) => {
    calls.push(options.cache);
    return options.cache === "force-cache"
      ? new Response("cached challenge", { headers: { "Content-Type": "text/html" } })
      : new Response(bytes);
  };
  assert.equal((await download(signed)).ok, true, `${name}: stale HTML gets fresh retry`);
  assert.deepEqual(calls, ["force-cache", "reload"]);
  passed += 1;

  globalThis.fetch = async () => new Response(bytes, { status: 206 });
  assert.equal((await download(signed)).ok, false, `${name}: partial PDF is rejected`);
  passed += 1;

  globalThis.fetch = async () => new Response("login", { headers: { "Content-Type": "text/html" } });
  assert.equal((await download(signed)).ok, false, `${name}: HTML never passes as PDF`);
  passed += 1;
}

/** A PDF import always lets the server match contents, even with an identifier hint. */
for (const doi of ["", "10.1016/j.pbb.2017.10.008"]) {
  let imported = false;
  globalThis.fetch = async (url, options) => {
    if (url === signed) return new Response(bytes);
    assert.equal(url, "http://127.0.0.1:8765/api/import/pdfs", "no provisional connector/save item");
    const form = options.body;
    assert.equal(form.get("source_url"), signed);
    assert.equal(form.get("collection_id"), "9");
    assert.deepEqual(JSON.parse(form.get("tags")), ["temperature", "MDMA"]);
    assert.equal(form.get("note"), "Read methods");
    assert.equal(JSON.parse(form.get("metadata")).doi || "", doi);
    assert.deepEqual(new Uint8Array(await form.get("files").arrayBuffer()), bytes);
    imported = true;
    return Response.json({ imported: 1, item_ids: [42], results: [{ item_id: 42, title: "Matched paper" }] });
  };
  const result = await runSave({ tabId: 1, detection: { kind: "pdf", url: signed, items: [{ title: "main", doi }] }, collectionId: 9, tags: ["temperature", "MDMA"], note: "Read methods" });
  assert.equal(imported && result.ok, true);
  assert.deepEqual(result.itemIds, [42]);
  passed += 1;
}

/** Reconciliation can move the attachment to another reference; return its real ID. */
globalThis.fetch = async (url, options) => {
  if (url === signed) return new Response(bytes);
  const params = new URL(url).searchParams;
  assert.equal(params.get("url"), signed, "long attachment URL is not truncated");
  assert.equal(params.get("item_id"), "7");
  return Response.json({ ok: true, item_id: 42, attachment: { id: 1 } });
};
const attached = await runAttachPending({ tabId: 1, url: signed, itemId: 7, title: "main" });
assert.deepEqual(attached.itemIds, [42]);
assert.equal(attached.ok, true);
assert.equal((await uploadAttachment(7, bytes, { url: signed })).item_id, 42);
passed += 1;
console.log(`PASS: ${passed}/${passed} PDF import/download regression cases`);
