"""End-to-end: start and open ReviewHunter from the Chrome extension, in a REAL Chromium.

What it proves (no fake browser API):
  1. with the server stopped, the extension's "startApp" goes through Chrome native messaging to
     doi_hunter_web/native_host.py, which starts the server; the server keeps running afterwards;
  2. "openApp" with an item opens the app in the SAME window the request came from, on that item;
  3. a second "openApp" reuses that tab (no second app tab) and switches item WITHOUT reloading;
  4. the popup, opened while ReviewHunter is stopped, starts it by itself.

Isolation: a throw-away library and port (inherited by the helper through Chromium's environment),
a throw-away browser profile, and the helper registered for Chromium only (the previous Chromium
registration is restored at the end). Google Chrome / Edge registrations are not touched.

Usage:  .venv/Scripts/python.exe tools/extension_launch_e2e.py [--browser <chromium.exe>] [--headed]
Exit code 0 when every check passes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extension_e2e import find_browser  # noqa: E402
from ui_snapshot import CDP, free_port  # noqa: E402

from doi_hunter_web import native_host  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser_extension"
ID = native_host.extension_path_id(EXT)   # unpacked: the ID comes from the folder path


def http(url: str, data: dict | None = None, method: str = "GET"):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def up(server: str) -> bool:
    try:
        return http(f"{server}/api/system/instance").get("app") == "ReviewHunter"
    except OSError:
        return False


async def page(port: int, url: str):
    """Open a tab and return (CDP client, target id)."""
    import websockets

    t = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")).read())
    ws = await websockets.connect(t["webSocketDebuggerUrl"], max_size=2 ** 24)
    cdp = CDP(ws)
    asyncio.create_task(cdp.listen())
    return cdp, t["id"]


async def targets(port: int) -> list[dict]:
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())


async def attach(port: int, target: dict):
    import websockets

    ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=2 ** 24)
    cdp = CDP(ws)
    asyncio.create_task(cdp.listen())
    return cdp


async def run(args) -> int:
    rh_port = free_port()
    server = f"http://127.0.0.1:{rh_port}"
    home = Path(tempfile.mkdtemp(prefix="rh-launch-e2e-"))
    keys = {"chromium": native_host.WINDOWS_KEYS["chromium"]} if os.name == "nt" else None
    dirs = None
    if os.name != "nt":
        base = Path.home() / (".config/chromium" if sys.platform != "darwin" else "Library/Application Support/Chromium")
        base.mkdir(parents=True, exist_ok=True)
        dirs = {"chromium": base / "NativeMessagingHosts"}
    before = native_host.status(ROOT, keys=keys, dirs=dirs)[0]
    reg = native_host.register(ROOT, keys=keys, dirs=dirs)
    assert reg.get("ok"), reg
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    devtools = free_port()
    profile = tempfile.mkdtemp(prefix="rh-launch-profile-")
    env = {**os.environ, "REVIEWHUNTER_HOME": str(home), "REVIEWHUNTER_PORT": str(rh_port),
           "REVIEWHUNTER_NO_HTTPS": "1", "REVIEWHUNTER_NO_DIALOGS": "1", "REVIEWHUNTER_NO_SHORTCUTS": "1"}
    browser = subprocess.Popen([find_browser(args.browser), f"--remote-debugging-port={devtools}",
                                f"--user-data-dir={profile}", f"--load-extension={EXT}",
                                f"--disable-extensions-except={EXT}", "--no-first-run", "--no-default-browser-check",
                                *([] if args.headed else ["--headless=new"]), "about:blank"], env=env)
    try:
        for _ in range(60):
            try:
                await targets(devtools)
                break
            except OSError:
                time.sleep(0.3)
        await asyncio.sleep(2)
        ext, _ = await page(devtools, f"chrome-extension://{ID}/src/options/options.html")
        await asyncio.sleep(1)
        loaded = await ext.eval("chrome.runtime.id")
        check("extension loaded with the ID computed from its folder", loaded == ID, loaded or "")
        await ext.eval(f"chrome.storage.local.set({{serverUrl: '{server}'}})")
        check("server stopped before the test", not up(server))

        # 1. start through native messaging
        t0 = time.time()
        st = await ext.eval("chrome.runtime.sendMessage({type: 'startApp'})")
        check("startApp starts ReviewHunter", bool(st and st.get("online")), f"{time.time() - t0:.1f} s, {json.dumps(st)[:120]}")
        await asyncio.sleep(1.5)
        info = http(f"{server}/api/system/instance") if up(server) else {}
        check("the server runs this install and outlives the helper", info.get("root") == str(ROOT), info.get("launched_by", ""))

        # 2. open on an item in the extension page's window
        a = http(f"{server}/api/items", {"title": "Paper A from the extension"}, "POST")["id"]
        b = http(f"{server}/api/items", {"title": "Paper B from the extension"}, "POST")["id"]
        win = await ext.eval("chrome.windows.getCurrent().then(w => w.id)")
        res = await ext.eval(f"chrome.runtime.sendMessage({{type: 'openApp', itemId: {a}, windowId: {win}}})")
        await asyncio.sleep(3)
        tabs = await ext.eval(f"chrome.tabs.query({{url: '{server}/*'}})")
        same_window = len(tabs) == 1 and tabs[0]["windowId"] == win
        check("openApp opens one app tab in the same window", bool(res and res.get("ok")) and same_window,
              f"{len(tabs)} tab(s)")
        app_target = next(t for t in await targets(devtools) if t["url"].startswith(server))
        app = await attach(devtools, app_target)
        shown = await app.eval("Alpine.$data(document.querySelector('[x-data]')).selectedItem?.id")
        check("the app shows the saved paper", shown == a, f"selected {shown}")
        await app.eval("window.__noReload = 1")

        # 3. second item: same tab, no reload
        await ext.eval(f"chrome.runtime.sendMessage({{type: 'openApp', itemId: {b}, windowId: {win}}})")
        await asyncio.sleep(2)
        tabs = await ext.eval(f"chrome.tabs.query({{url: '{server}/*'}})")
        shown = await app.eval("Alpine.$data(document.querySelector('[x-data]')).selectedItem?.id")
        kept = await app.eval("window.__noReload === 1")
        check("second open reuses the tab and switches item without reload", len(tabs) == 1 and shown == b and kept,
              f"{len(tabs)} tab(s), selected {shown}, same page {kept}")
        url = await app.eval("location.href")
        check("the deep link is removed from the address bar", "#item" not in url, url)

        # 4. popup auto-start
        http(f"{server}/api/system/shutdown", {}, "POST")
        for _ in range(40):
            if not up(server):
                break
            time.sleep(0.25)
        popup, _ = await page(devtools, f"chrome-extension://{ID}/src/popup/popup.html")
        t0 = time.time()
        state = ""
        for _ in range(120):
            state = await popup.eval("[...document.querySelectorAll('main > .state')].find(s => !s.hidden)?.id || ''")
            if state not in ("state-loading", "state-starting", ""):
                break
            await asyncio.sleep(0.5)
        conn = await popup.eval("document.getElementById('conn-text').textContent")
        check("the popup starts ReviewHunter by itself", up(server) and state != "state-offline",
              f"{time.time() - t0:.1f} s, state {state}, header '{conn}'")
    finally:
        try:
            http(f"{server}/api/system/shutdown", {}, "POST")
        except OSError:
            pass
        browser.terminate()
        try:
            browser.wait(timeout=20)
        except subprocess.TimeoutExpired:
            browser.kill()
        if not before["registered"]:
            native_host.unregister(ROOT, keys=keys, dirs=dirs)
        shutil.rmtree(profile, ignore_errors=True)
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--browser", default="")
    p.add_argument("--headed", action="store_true")
    sys.exit(asyncio.run(run(p.parse_args())))
