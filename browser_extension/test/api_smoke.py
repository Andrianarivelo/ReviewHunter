"""Smoke test of the connector API exactly as the extension calls it.

Sends the same requests as src/api.js (JSON bodies, raw PDF bytes, multipart
import) with an ``Origin: chrome-extension://...`` header, against a running
ReviewHunter instance. Use a disposable library, never the real one:

    REVIEWHUNTER_HOME=<temp dir> REVIEWHUNTER_PORT=8785 .venv/Scripts/python.exe run.py -- --no-browser --port 8785 --no-https
    .venv/Scripts/python.exe browser_extension/test/api_smoke.py http://127.0.0.1:8785

Network-dependent steps (identifier resolution through CrossRef) are reported
as SKIP when the machine is offline instead of failing.
"""
from __future__ import annotations

import sys
import time
import uuid

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8785").rstrip("/")
EXT = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
RESULTS: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    """Record one assertion."""
    RESULTS.append(("PASS" if cond else "FAIL", name, "" if cond else repr(detail)[:300]))


def minimal_pdf(text: str) -> bytes:
    """Build a tiny but structurally valid one-page PDF (xref offsets computed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def main() -> int:
    """Run every step and print a PASS/FAIL table; exit code 1 on any failure."""
    s = requests.Session()
    ping = s.get(f"{BASE}/connector/ping", timeout=5).json()
    check("ping identifies ReviewHunter", ping.get("app") == "ReviewHunter" and "items" in ping.get("library", {}), ping)

    cols = s.get(f"{BASE}/connector/collections", timeout=5).json()
    check("collections is a tree list", isinstance(cols, list) and all({"id", "path", "depth"} <= set(c) for c in cols), cols[:2])
    tags = s.get(f"{BASE}/connector/tags", timeout=5).json()
    check("tags list", isinstance(tags, list), tags[:2])

    # Security guard: a web page origin must not be able to save.
    evil = s.post(f"{BASE}/connector/save", json={"items": [{"title": "evil"}]}, headers={"Origin": "https://evil.example"}, timeout=5)
    check("foreign origin rejected (403)", evil.status_code == 403, evil.status_code)

    # Save a synthetic article (enrich=false: deterministic, no network). Unique DOI per run.
    doi = f"10.5555/rh-ext-smoke.{uuid.uuid4().hex[:10]}"
    payload = {"item_type": "journalArticle", "title": "Connector smoke test article", "doi": doi,
               "creators": [{"creatorType": "author", "lastName": "Tester", "firstName": "Ada"}],
               "journal": "Journal of Extension Tests", "date": "2026-09-14", "url": "https://example.org/article/1"}
    r = s.post(f"{BASE}/connector/save", headers=EXT, timeout=30,
               json={"items": [payload], "collection_id": cols[0]["id"] if cols else None, "tags": ["rh-ext-smoke"],
                     "note": "saved by api_smoke.py", "enrich": False, "pdf_url": "https://example.org/article/1.pdf"})
    saved = r.json()["saved"][0] if r.ok else {}
    check("save returns new item needing a PDF", r.ok and saved.get("needs_pdf") is True and saved.get("existing") is False, r.text)
    check("save echoes pdf_url for the browser download", saved.get("pdf_url") == "https://example.org/article/1.pdf", saved)

    look = s.post(f"{BASE}/connector/lookup", headers=EXT, json={"doi": doi.upper()}, timeout=5).json()
    check("lookup finds the DOI (case-insensitive)", look.get("found") and look["items"][0]["id"] == saved.get("id"), look)

    # Login page instead of PDF must be refused.
    bad = s.post(f"{BASE}/connector/attachment", params={"item_id": saved.get("id"), "url": "https://idp.example.edu/login"},
                 data=b"<html><body>Sign in</body></html>", headers={**EXT, "Content-Type": "application/pdf"}, timeout=10)
    check("HTML body posted as PDF is rejected (415)", bad.status_code == 415, bad.status_code)

    pdf = minimal_pdf("ReviewHunter connector smoke test")
    up = s.post(f"{BASE}/connector/attachment", params={"item_id": saved.get("id"), "url": "https://example.org/article/1.pdf",
                                                      "title": "Full Text PDF", "kind": "file"},
                data=pdf, headers={**EXT, "Content-Type": "application/pdf"}, timeout=30)
    att = up.json().get("attachment", {}) if up.ok else {}
    check("PDF attachment stored", up.ok and att.get("mime") == "application/pdf", up.text)

    again = s.post(f"{BASE}/connector/save", headers=EXT, timeout=30,
                   json={"items": [{"title": "dup", "doi": doi}], "tags": ["second-tag"], "note": "second note", "enrich": False}).json()["saved"][0]
    check("re-save reuses item, no PDF needed any more", again["id"] == saved.get("id") and again["existing"] and not again["needs_pdf"], again)
    item = s.get(f"{BASE}/api/items/{saved.get('id')}", timeout=5).json()
    tag_names = {t["name"] for t in item.get("tags", [])}
    check("tags from both saves applied", {"rh-ext-smoke", "second-tag"} <= tag_names, tag_names)
    notes = [n["title"] for n in item.get("notes_list", [])]
    check("exactly one copy of each note", notes.count("saved by api_smoke.py") == 1 and notes.count("second note") == 1, notes)

    snap = s.post(f"{BASE}/connector/attachment", params={"item_id": saved.get("id"), "kind": "snapshot", "title": "Snapshot",
                                                        "url": "https://example.org/article/1"},
                  data="<!DOCTYPE html><html><body><h1>Snapshot</h1></body></html>".encode(),
                  headers={**EXT, "Content-Type": "text/html"}, timeout=10)
    check("HTML snapshot stored", snap.ok, snap.text)

    # "Save PDF" tab without identifiers: multipart import.
    imp = s.post(f"{BASE}/api/import/pdfs", headers=EXT, timeout=30,
                 files={"files": ("smoke_import.pdf", minimal_pdf("imported"), "application/pdf")})
    check("PDF import creates an item", imp.ok and imp.json().get("imported") == 1, imp.text)

    # Identifiers (needs Internet for CrossRef): Hopfield 1982.
    try:
        started = time.time()
        ids = s.post(f"{BASE}/connector/identifiers", headers=EXT, timeout=90,
                     json={"text": "see doi:10.1073/pnas.79.8.2554 please", "tags": ["rh-ext-smoke"]}).json()
        if ids.get("saved"):
            first = ids["saved"][0]
            check("identifiers resolves a DOI", "neural" in first["title"].lower(), ids)
            check("identifiers reports needs_pdf / pdf_url keys", "needs_pdf" in first and "pdf_url" in first, first)
        else:
            RESULTS.append(("SKIP", f"identifiers (resolution failed, offline? {time.time() - started:.0f}s)", repr(ids)[:200]))
    except requests.RequestException as e:
        RESULTS.append(("SKIP", "identifiers (request error)", str(e)[:200]))

    width = max(len(n) for _, n, _ in RESULTS)
    for status, name, detail in RESULTS:
        print(f"{status:4}  {name.ljust(width)}  {detail}")
    failed = sum(1 for st, _, _ in RESULTS if st == "FAIL")
    passed = sum(1 for st, _, _ in RESULTS if st == "PASS")
    print(f"\n{passed} passed, {failed} failed, {len(RESULTS) - passed - failed} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
