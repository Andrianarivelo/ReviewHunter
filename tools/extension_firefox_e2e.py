"""End-to-end: the Firefox connector in a REAL Firefox (temporary add-on, as about:debugging loads it).

Checks the same promises as tools/extension_launch_e2e.py (Chromium):
  1. with ReviewHunter stopped, "startApp" goes through Firefox native messaging to
     doi_hunter_web/native_host.py (Firefox registration: allowed_extensions = the add-on ID) and the
     server keeps running afterwards;
  2. "openApp" on a paper opens the app in the same window, on that paper;
  3. a second "openApp" reuses the tab and switches paper without reloading;
  4. the popup started while ReviewHunter is stopped starts it by itself;
  5. the API calls the connector makes work with Firefox's promise-based `browser` namespace.

Isolation: throw-away library, port and Firefox profile; the helper's Firefox registration is restored.
Usage:  .venv/Scripts/python.exe tools/extension_firefox_e2e.py --firefox <firefox> --geckodriver <geckodriver>
        [--pylib <folder with selenium>] [--headed]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(url, data=None, method="GET"):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def up(server: str) -> bool:
    try:
        return http(f"{server}/api/system/instance").get("app") == "ReviewHunter"
    except OSError:
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--firefox", default=os.environ.get("RH_FIREFOX", shutil.which("firefox") or ""))
    p.add_argument("--geckodriver", default=os.environ.get("RH_GECKODRIVER", shutil.which("geckodriver") or ""))
    p.add_argument("--pylib", default="")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--source", default="", help="connector source folder (default: browser_extension/); "
                                                "the benchmark passes an older connector here")
    p.add_argument("--json", default="", help="also write the checks to this JSON file")
    args = p.parse_args()
    if args.pylib:
        sys.path.insert(0, args.pylib)
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service

    import build_extensions

    if args.source:
        build_extensions.SOURCE = Path(args.source).resolve()
    from doi_hunter_web import native_host

    work = Path(tempfile.mkdtemp(prefix="rh-ff-e2e-"))
    built = build_extensions.build(work / "connectors")
    port = free_port()
    server = f"http://127.0.0.1:{port}"
    ext_uuid = str(uuid.uuid4())
    if os.name == "nt":
        where = {"ff_keys": {"firefox": native_host.FIREFOX_WINDOWS_KEYS["firefox"]}}
    else:
        folder = Path.home() / ".mozilla" / "native-messaging-hosts"
        folder.parent.mkdir(parents=True, exist_ok=True)
        where = {"ff_dirs": {"firefox": folder}}
    before = native_host.status(ROOT, **where)[0]
    reg = native_host.register(ROOT, **where)
    assert reg.get("ok"), reg

    checks, records, error = [], [], ""

    def check(name, ok, detail=""):
        checks.append(ok)
        records.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    # A stand-in PDF host that records the Referer header of each request (check "Referer").
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    referers: list = []

    class RefererProbe(BaseHTTPRequestHandler):
        def do_GET(self):
            referers.append(self.headers.get("Referer") or "")
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n%%EOF\n")

        def log_message(self, *a):
            pass

    probe = ThreadingHTTPServer(("127.0.0.1", 0), RefererProbe)
    referer_port = probe.server_address[1]
    threading.Thread(target=probe.serve_forever, daemon=True).start()

    opts = Options()
    opts.binary_location = args.firefox
    if not args.headed:
        opts.add_argument("-headless")
    # A fixed internal UUID for the add-on, so its pages have a known moz-extension:// address.
    opts.set_preference("extensions.webextensions.uuids", json.dumps({build_extensions.GECKO_ID: ext_uuid}))
    env = {**os.environ, "REVIEWHUNTER_HOME": str(work / "lib"), "REVIEWHUNTER_PORT": str(port),
           "REVIEWHUNTER_NO_HTTPS": "1", "REVIEWHUNTER_NO_DIALOGS": "1", "REVIEWHUNTER_NO_SHORTCUTS": "1"}
    # Firefox (and the helper it starts) inherit env. WebDriver may not navigate to moz-extension:// pages:
    # tabs are opened from the browser ("chrome") context, which geckodriver must allow.
    service = Service(executable_path=args.geckodriver, env=env, service_args=["--allow-system-access"])
    driver = webdriver.Firefox(options=opts, service=service)
    driver.set_script_timeout(150)
    base = f"moz-extension://{ext_uuid}"

    def open_ext_tab(url: str) -> str:
        """Open an extension page in a new tab (privileged context) and switch to it."""
        handles = set(driver.window_handles)
        with driver.context(driver.CONTEXT_CHROME):
            driver.execute_script("gBrowser.selectedTab = gBrowser.addTab(arguments[0], "
                                  "{triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()});", url)
        for _ in range(40):
            new = set(driver.window_handles) - handles
            if new:
                driver.switch_to.window(new.pop())
                time.sleep(1)
                return driver.current_window_handle
            time.sleep(0.25)
        raise RuntimeError(f"could not open {url}")

    def call(js: str):
        return driver.execute_async_script("const done = arguments[arguments.length - 1];"
                                           f"(async () => {{ {js} }})().then(done, e => done({{__error: String(e)}}));")
    try:
        driver.install_addon(built["xpi"], temporary=True)
        time.sleep(2)
        open_ext_tab(f"{base}/src/options/options.html")
        info = call("return {id: browser.runtime.id, v: browser.runtime.getManifest().version};")
        check("add-on loaded in Firefox", info.get("id") == build_extensions.GECKO_ID, json.dumps(info))
        call(f"await browser.storage.local.set({{serverUrl: '{server}'}}); return 1;")
        check("server stopped before the test", not up(server))

        t0 = time.time()
        st = call("return await browser.runtime.sendMessage({type: 'startApp'});")
        check("startApp starts ReviewHunter (Firefox native messaging)", bool(st and st.get("online")),
              f"{time.time() - t0:.1f} s {json.dumps(st)[:140]}")
        time.sleep(1.5)
        inst = http(f"{server}/api/system/instance") if up(server) else {}
        check("the server runs this install and outlives the helper", inst.get("root") == str(ROOT),
              inst.get("launched_by", ""))

        a = http(f"{server}/api/items", {"title": "Firefox paper A"}, "POST")["id"]
        b = http(f"{server}/api/items", {"title": "Firefox paper B"}, "POST")["id"]
        win = call("return (await browser.windows.getCurrent()).id;")
        res = call(f"return await browser.runtime.sendMessage({{type: 'openApp', itemId: {a}, windowId: {win}}});")
        time.sleep(3)
        tabs = call(f"return (await browser.tabs.query({{}})).filter(t => (t.url || '').startsWith('{server}/'));")
        check("openApp opens one app tab in the same window",
              bool(res and res.get("ok")) and len(tabs) == 1 and tabs[0]["windowId"] == win, f"{len(tabs)} tab(s)")
        options_handle = driver.current_window_handle
        app_handle = None
        for h in driver.window_handles:          # the app tab is the one showing the server's address
            driver.switch_to.window(h)
            if driver.current_url.startswith(server):
                app_handle = h
                break
        assert app_handle, "no app tab"
        driver.switch_to.window(app_handle)
        shown = driver.execute_script("return Alpine.$data(document.querySelector('[x-data]')).selectedItem?.id")
        check("the app shows the saved paper", shown == a, f"selected {shown}")
        driver.execute_script("window.__noReload = 1")
        driver.switch_to.window(options_handle)
        call(f"return await browser.runtime.sendMessage({{type: 'openApp', itemId: {b}, windowId: {win}}});")
        time.sleep(2)
        tabs = call(f"return (await browser.tabs.query({{}})).filter(t => (t.url || '').startsWith('{server}/'));")
        driver.switch_to.window(app_handle)
        shown = driver.execute_script("return Alpine.$data(document.querySelector('[x-data]')).selectedItem?.id")
        kept = driver.execute_script("return window.__noReload === 1")
        check("second open reuses the tab and switches paper without reload", len(tabs) == 1 and shown == b and kept,
              f"{len(tabs)} tab(s), selected {shown}, same page {kept}")
        driver.switch_to.window(options_handle)

        # The PDF path's Referer rule, for real: the connector's own withReferer() downloads from a local
        # server that records the Referer it receives (Sci-Hub proxies refuse PDFs without it). Firefox
        # matches the rule's initiator against the moz-extension host, not the add-on ID.
        pdf_url = f"http://127.0.0.1:{referer_port}/paper.pdf?t={int(time.time())}"
        got = call("const m = await import(browser.runtime.getURL('src/core/pdf.js'));"
                   f"const status = await m.withReferer('{pdf_url}', 'https://article.example/page',"
                   f" () => fetch('{pdf_url}').then(r => r.status));"
                   "return {status};")
        sent = referers[-1] if referers else None
        check("the PDF download carries the article page as Referer", sent == "https://article.example/page",
              f"{json.dumps(got)} server saw Referer {sent!r}")

        http(f"{server}/api/system/shutdown", {}, "POST")
        for _ in range(40):
            if not up(server):
                break
            time.sleep(0.25)
        open_ext_tab(f"{base}/src/popup/popup.html")
        t0 = time.time()
        state = ""
        for _ in range(120):
            state = driver.execute_script("return [...document.querySelectorAll('main > .state')].find(s => !s.hidden)?.id || ''")
            if state not in ("state-loading", "state-starting", ""):
                break
            time.sleep(0.5)
        check("the popup starts ReviewHunter by itself", up(server) and state != "state-offline",
              f"{time.time() - t0:.1f} s, state {state}")
    except Exception as e:   # a step that cannot run: later checks are "not reached" (counted as failed)
        error = f"{type(e).__name__}: {e}"
        print(f"ERROR  {error}")
    finally:
        try:
            http(f"{server}/api/system/shutdown", {}, "POST")
        except OSError:
            pass
        driver.quit()
        probe.shutdown()
        if not before["registered"]:
            native_host.unregister(ROOT, **where)
    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    if args.json:
        Path(args.json).write_text(json.dumps({"source": str(build_extensions.SOURCE), "checks": records,
                                               "error": error}, indent=2), encoding="utf-8")
    return 0 if all(checks) and not error else 1


if __name__ == "__main__":
    sys.exit(main())
