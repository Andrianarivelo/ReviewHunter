"""Exercise PDF imports in real Chromium and isolated ReviewHunter libraries.

Run from the repository root with its virtual environment:
    python browser_extension/test/pdf_import_e2e.py --pdf <existing-paper.pdf>

Use the Aarde et al. 2017 paper (DOI 10.1016/j.pbb.2017.10.008) as the fixture.
The supplied PDF is read once and copied to a temporary HTTP fixture. All database
writes go to temporary libraries. No browser, network, or application APIs are mocked.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from extension_e2e import find_browser, prepare_extension, http_json
from ui_snapshot import CDP, free_port

DOI = "10.1016/j.pbb.2017.10.008"
TITLE = "High ambient temperature facilitates the acquisition of 3,4-methylenedioxymethamphetamine (MDMA) self-administration"


def stop(process):
    """Close only the subprocess started by this test."""
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


async def scenario(pdf_url: str, digest: str, seeded: bool, folder: Path) -> dict:
    """Run one fresh browser against one fresh library and assert the resulting record."""
    import websockets

    port, debug_port = free_port(), free_port()
    server = f"http://127.0.0.1:{port}"
    home = folder / ("existing" if seeded else "new")
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"auto_retrieve_pdf_metadata": False}))
    env = {**os.environ, "REVIEWHUNTER_HOME": str(home), "REVIEWHUNTER_PORT": str(port)}
    # Explicitly isolate papers as well if the caller normally overrides their location.
    env["REVIEWHUNTER_PAPERS_DIR"] = str(home / "papers")
    logfile = (home / "server.log").open("w", encoding="utf-8")
    service = subprocess.Popen([sys.executable, "-m", "uvicorn", "doi_hunter_web.server:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=logfile, stderr=logfile)
    browser = None
    ext = None
    try:
        for _ in range(100):
            try:
                http_json(f"{server}/connector/ping")
                break
            except OSError:
                await asyncio.sleep(0.2)
        expected_id = None
        if seeded:
            record = http_json(f"{server}/api/items", {"item_type": "journalArticle", "title": TITLE, "field_values": {"DOI": DOI}}, server)
            expected_id = record["id"]
        ext = prepare_extension(server)
        browser = subprocess.Popen([find_browser(), "--headless=new", f"--remote-debugging-port={debug_port}", f"--user-data-dir={home / 'chrome'}", f"--load-extension={ext}", f"--disable-extensions-except={ext}", "--no-first-run", "--no-default-browser-check", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json") as response:
                    targets = json.load(response)
                worker = next((target for target in targets if target["type"] == "service_worker" and target["url"].endswith("/src/background.js")), None)
                if worker:
                    break
            except OSError:
                pass
            await asyncio.sleep(0.2)
        assert worker, "ReviewHunter extension failed to load"
        page = next(target for target in targets if target["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            listener = asyncio.create_task(cdp.listen())
            try:
                await cdp.send("Runtime.enable")
                await cdp.send("Page.enable")
                extension_id = worker["url"].split("/")[2]
                await cdp.send("Page.navigate", url=f"chrome-extension://{extension_id}/src/options/options.html")
                await asyncio.sleep(2)
                # Send the popup's production message protocol to the actual service worker.
                # The PDF opens in a real viewer tab; PDF fetch and multipart upload are real.
                await cdp.eval("globalThis.__pdfImportResult = null; globalThis.__pdfImportSteps = [];")
                expression = """(async () => {
                    const tab = await chrome.tabs.create({url: PDF_URL, active: false});
                    await new Promise(resolve => setTimeout(resolve, 1500));
                    const port = chrome.runtime.connect({name: 'rh-save'});
                    port.onMessage.addListener(message => {
                        if (message.type === 'progress') globalThis.__pdfImportSteps = message.steps;
                        if (message.type === 'done') {globalThis.__pdfImportResult = message.result; port.disconnect();}
                    });
                    port.postMessage({type: 'save', request: {tabId: tab.id,
                        detection: {kind: 'pdf', url: PDF_URL, items: [{title: 'main', item_type: 'document'}]},
                        tags: ['e2e-pdf'], note: 'Real Chromium PDF import'}});
                })().catch(error => {globalThis.__pdfImportResult = {error: String(error)}}); 'started'""".replace("PDF_URL", json.dumps(pdf_url))
                await cdp.eval(expression)
                start = time.monotonic()
                result = None
                while time.monotonic() - start < 180:
                    result = await cdp.eval("globalThis.__pdfImportResult")
                    if result is not None:
                        break
                    await asyncio.sleep(0.5)
                assert result and result.get("ok"), {"result": result, "steps": await cdp.eval("globalThis.__pdfImportSteps")}
                items = http_json(f"{server}/api/items")
                assert len(items) == 1, items
                item = items[0]
                assert item["doi"] == DOI, item
                assert item["title"] == TITLE, item
                assert len(item["creators"]) == 3 and item["year"] == 2017, item
                assert item["journal"] and item["publisher"] and item["pages"] == "38-49", item
                assert item["note_count"] == 1 and "e2e-pdf" in [tag if isinstance(tag, str) else tag["name"] for tag in item["tags"]], item
                assert result["itemIds"] == [item["id"]], result
                if expected_id is not None:
                    assert item["id"] == expected_id, item
                stored = Path(item["file_path"])
                assert stored.name.startswith("Aarde et al - 2017 - "), stored.name
                assert hashlib.sha256(stored.read_bytes()).hexdigest() == digest, "PDF bytes changed"
                return {"scenario": "existing reference" if seeded else "new reference", "ok": True, "seconds": round(time.monotonic() - start, 2), "item_id": item["id"], "filename": stored.name, "doi": item["doi"], "creators": len(item["creators"]), "pages": item["pages"], "sha256": digest}
            finally:
                listener.cancel()
    finally:
        if browser:
            stop(browser)
        stop(service)
        logfile.close()
        if ext:
            shutil.rmtree(ext.parent, ignore_errors=True)


async def run(args):
    """Read the fixture without modifying it, then run both independent scenarios."""
    data = Path(args.pdf).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    folder = Path(tempfile.mkdtemp(prefix="rh-pdf-import-e2e-"))
    (folder / "source.pdf").write_bytes(data)

    class PdfHandler(BaseHTTPRequestHandler):
        """Serve the real PDF from a URL shaped like the reported publisher URL."""
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            """Keep test output focused on outcomes."""

    http = ThreadingHTTPServer(("127.0.0.1", 0), PdfHandler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{http.server_port}/main.pdf?X-Amz-Security-Token=" + "a%2Bb" * 600
    try:
        results = []
        for seeded in (True, False):
            outcome = await scenario(url, digest, seeded, folder)
            results.append(outcome)
            print(json.dumps(outcome), flush=True)
        (folder / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"PASS: real Chromium imports; artifacts {folder}")
    finally:
        http.shutdown()
        http.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, help="Path to the Aarde et al. 2017 PDF; read-only")
    asyncio.run(run(parser.parse_args()))
