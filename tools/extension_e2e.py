"""End-to-end test of the browser hand-off in a REAL Chrome (headless) with the extension loaded.

What it proves: app page -> content-script bridge -> service worker -> background tab fetch ->
upload to /connector/attachment -> status relayed to the server. No fake Chrome API is involved.

Steps
  1. copy browser_extension/ to a temporary folder and let its content script match the test
     server address (the shipped manifest only matches the default port 8765);
  2. start Chrome headless=new with --load-extension, a throw-away profile and a DevTools port;
  3. open the extension's options page and point it at the test server (chrome.storage.local);
  4. open the ReviewHunter page; the bridge announces itself ("hello");
  5. create an item with a DOI whose PDF the server itself cannot fetch and call
     POST /api/items/{id}/handoff: the server registers a plan and announces it on the live socket,
     the page forwards it to the extension, which fetches the PDF and uploads it;
  6. poll /connector/item/{id} until has_pdf (or the hand-off reports failed / waiting).

Usage (a ReviewHunter test instance must be running, e.g. REVIEWHUNTER_PORT=8775):
    .venv/Scripts/python.exe tools/extension_e2e.py --server http://127.0.0.1:8775 --doi 10.1038/s41586-020-2649-2

Exit code 0 on success, 1 on failure. Prints the hand-off progress lines reported by the extension.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_snapshot import CDP, find_chrome, free_port  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXT_SRC = ROOT / "browser_extension"


def find_browser(explicit: str = "") -> str:
    """A Chromium that honours --load-extension.

    Branded Google Chrome 137+ silently ignores --load-extension (verified with Chrome 152: only Chrome's
    own component extensions appear). Playwright's Chromium or Chrome for Testing still accept it.
    Order: --browser / RH_BROWSER, Playwright chromium-*, Chrome for Testing, then Google Chrome (with a warning).
    """
    import glob
    import os

    if explicit:
        return explicit
    if os.environ.get("RH_BROWSER"):
        return os.environ["RH_BROWSER"]
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "ms-playwright", "chromium-*", "chrome-win*", "chrome.exe"),
        os.path.join(local, "ms-playwright", "chromium-*", "chrome-linux*", "chrome"),
        os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright", "chromium-*", "chrome-linux*", "chrome"),
        os.path.join(os.path.expanduser("~"), ".cache", "puppeteer", "chrome", "*", "chrome-win64", "chrome.exe"),
        os.path.join(os.path.expanduser("~"), ".cache", "puppeteer", "chrome", "*", "chrome-linux64", "chrome"),
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    print("warning: no Chromium / Chrome for Testing found; Google Chrome 137+ ignores --load-extension, the test will fail")
    return find_chrome()


def http_json(url: str, data: dict | None = None, origin: str = "") -> dict:
    """Small JSON helper (POST when data is given)."""
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def prepare_extension(server: str) -> Path:
    """Copy of the extension whose content script also matches the test server."""
    dst = Path(tempfile.mkdtemp(prefix="rh-ext-")) / "ext"
    shutil.copytree(EXT_SRC, dst, ignore=shutil.ignore_patterns("test", "__pycache__", "*.md"))
    manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    match = f"{server.rstrip('/')}/*"
    for cs in manifest.get("content_scripts", []):
        if match not in cs["matches"]:
            cs["matches"].append(match)
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Default server address of the copy = the test server (what the user would type in the options page).
    settings_js = dst / "src" / "settings.js"
    text = settings_js.read_text(encoding="utf-8")
    patched = text.replace('serverUrl: "http://127.0.0.1:8765"', f'serverUrl: {json.dumps(server)}', 1)
    if patched == text:
        raise SystemExit("settings.js default serverUrl not found: update tools/extension_e2e.py")
    settings_js.write_text(patched, encoding="utf-8")
    return dst


async def run(args: argparse.Namespace) -> int:
    import websockets

    server = args.server.rstrip("/")
    origin = server
    ext_dir = prepare_extension(server)
    port = free_port()
    profile = tempfile.mkdtemp(prefix="rh-chrome-e2e-")
    browser = find_browser(args.browser)
    print(f"browser: {browser}")
    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        f"--load-extension={ext_dir}", f"--disable-extensions-except={ext_dir}", "--no-first-run",
        "--no-default-browser-check", "--window-size=1200,800", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 25
        targets: list[dict] = []
        ext_id = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as r:
                    targets = json.load(r)
                # Our worker is src/background.js; Chrome's own component extensions have other names.
                sw = [t for t in targets if t["type"] == "service_worker" and t["url"].endswith("/src/background.js")]
                if sw:
                    ext_id = sw[0]["url"].split("/")[2]
                    break
            except OSError:
                pass
            time.sleep(0.4)
        if not ext_id:
            print("FAIL: the ReviewHunter extension did not load (branded Chrome 137+ ignores --load-extension: "
                  "pass --browser <Chromium or Chrome for Testing>)")
            return 1
        print(f"extension loaded: {ext_id}")
        # 3. the copy's default server address is the test server (see prepare_extension); a fresh profile
        #    has no stored setting, so the extension talks to the test server from the start.
        print(f"extension server URL: {server} (default of the test copy)")
        page = [t for t in targets if t["type"] == "page"][0]
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            listener = asyncio.create_task(cdp.listen())
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            # 4. open the app page and wait for the bridge
            await cdp.send("Page.navigate", url=f"{server}/")
            await asyncio.sleep(3.5)
            hello = await cdp.eval("""(async () => {
                const got = [];
                addEventListener('message', (e) => { if (e.data && e.data.source === 'reviewhunter-connector') got.push(e.data.type); });
                const app = document.querySelector('[x-data]');
                const data = window.Alpine && app ? Alpine.$data(app) : null;
                for (let i = 0; i < 20 && !(data && data.connector && data.connector.present); i++) {
                    postMessage({source: 'reviewhunter-app', type: 'ping'}, location.origin);
                    await new Promise(r => setTimeout(r, 250));
                }
                return {present: !!(data && data.connector && data.connector.present),
                        version: data && data.connector ? String(data.connector.version || '') : '',
                        marker: document.documentElement.getAttribute('data-reviewhunter-connector'),
                        messages: got, alpine: !!window.Alpine};
            })()""")
            print(f"bridge: {hello}")
            if not hello or not hello.get("present"):
                print("FAIL: the app page did not detect the connector (content script bridge)")
                return 1
            # 5. item + hand-off
            created = http_json(f"{server}/api/items/identify", {"text": args.doi, "auto_download": False}, origin)
            await asyncio.sleep(4)
            items = http_json(f"{server}/api/items?q={urllib.request.quote(args.doi)}&mode=everything")
            item = next((i for i in items if (i.get("doi") or "").lower() == args.doi.lower()), None) or (items[0] if items else None)
            if not item:
                print(f"FAIL: item not created for {args.doi}: {created}")
                return 1
            print(f"item {item['id']}: {item.get('title', '')[:60]}")
            res = http_json(f"{server}/api/items/{item['id']}/handoff", {}, origin)
            print(f"hand-off: ok={res.get('ok')} steps={len(res.get('plan', []))} reason={res.get('reason', '')}")
            if not res.get("ok"):
                return 1
            # 6. wait for the extension
            t0 = time.time()
            last = ""
            while time.time() - t0 < args.timeout:
                st = http_json(f"{server}/connector/item/{item['id']}")
                if st.get("has_pdf"):
                    print(f"PASS: PDF attached by the extension in {time.time() - t0:.1f} s: {st['attachments']}")
                    listener.cancel()
                    return 0
                hs = http_json(f"{server}/connector/handoffs?include_failed=true")["handoffs"]
                rec = next((h for h in hs if h["item_id"] == item["id"]), None)
                line = f"{rec['state']}: {rec['message']}" if rec else "(no record)"
                if line != last:
                    print(f"  {time.time() - t0:5.1f}s {line}")
                    last = line
                if rec and rec["state"] in ("failed", "waiting"):
                    print(f"FAIL: hand-off ended with state {rec['state']}: {rec['message']}")
                    return 1
                await asyncio.sleep(1.5)
            print("FAIL: timeout")
            return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(ext_dir.parent, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default="http://127.0.0.1:8775")
    p.add_argument("--doi", default="10.1038/s41586-020-2649-2")
    p.add_argument("--timeout", type=float, default=150)
    p.add_argument("--browser", default="", help="Chromium executable honouring --load-extension (default: auto-detect)")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
