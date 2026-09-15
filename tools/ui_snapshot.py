"""Headless Chrome UI snapshots for visual QA (no Playwright needed).

Launches the locally installed Google Chrome in headless mode with a throw-away
profile, drives it through the Chrome DevTools Protocol (CDP) and saves PNG
screenshots after running optional JavaScript steps.

Usage (from the repo root, with the app running):

    .venv/Scripts/python.exe tools/ui_snapshot.py --url http://127.0.0.1:8775/ \
        --out shots/library.png --width 1600 --height 900 \
        --step "document.querySelector('[data-item-row]').click()" --wait 1.5

Each --step is JavaScript evaluated in the page (awaited if it returns a promise),
followed by --wait seconds. Use --shot-each to save one screenshot per step.
Console errors and uncaught exceptions are printed at the end (exit code 2 if any).
``--insights --fixture <fixture.json>`` checks all four maps against a disposable
library made by ``tools/insight_fixture.py``, saving one PNG per map and a JSON
validation report beside ``--out``. It refuses to run these checks on port 8765.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# ----------------------------------------------------------------------------- configuration
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1600, 900
INSIGHT_KINDS = ("concepts", "words", "goals", "methods")
INSIGHT_READY_TIMEOUT = 20


async def wait_expression(cdp: "CDP", expression: str, timeout: float = INSIGHT_READY_TIMEOUT):
    """Wait for a browser state without assuming a fixed network/render duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await cdp.eval(expression)
        if value:
            return value
        await asyncio.sleep(0.1)
    raise AssertionError(f"Browser state did not become ready: {expression}")


async def check_insights(cdp: "CDP", args: argparse.Namespace, out: Path) -> dict:
    """Exercise the real Alpine views and record render times on synthetic data."""
    from urllib.parse import urlparse

    address = urlparse(args.url)
    if address.hostname not in ("127.0.0.1", "localhost") or address.port != 8775:
        raise ValueError("Insight checks require the isolated localhost:8775 dev server")
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if fixture.get("synthetic") is not True:
        raise ValueError("A synthetic fixture manifest is required")
    await wait_expression(cdp, "typeof Alpine !== 'undefined' && document.querySelector('[x-data]') && typeof Alpine.$data(document.querySelector('[x-data]')).openInsight === 'function'")
    await cdp.eval("window.rhTestApp = Alpine.$data(document.querySelector('[x-data]'))")
    actual = await cdp.eval("(await (await fetch('/api/items')).json()).map(p => ({id:p.id,title:p.title}))")
    if {p["id"] for p in actual} != set(fixture["items"]) or {p["title"] for p in actual} != set(fixture["titles"]):
        raise AssertionError("Dev server is not using the expected synthetic fixture")
    report = {"synthetic": True, "url": args.url, "theme": "dark" if args.dark else "light", "maps": {}}
    report["csv_formula_safety"] = await cdp.eval("""InsightLayouts.csvCell(-1.2) === JSON.stringify('-1.2') && InsightLayouts.csvCell('=SUM(1)').includes("'=SUM(1)")""")
    if not report["csv_formula_safety"]:
        raise AssertionError("CSV formula protection changed a numeric value or left a formula active")
    for kind in INSIGHT_KINDS:
        started = time.perf_counter()
        await cdp.eval(f"rhTestApp.selectVirtual('all'); await rhTestApp.openInsight({json.dumps(kind)})")
        await wait_expression(cdp, f"rhTestApp.insight.kind === '{kind}' && !rhTestApp.insight.loading && !!rhTestApp.insight.data && !!document.querySelector('.insight-view')")
        await cdp.eval("await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        result = await cdp.eval("""(() => {
          const d = rhTestApp.insight.data;
          const root = document.querySelector('.insight-view');
          const svg = document.querySelector('#insight-svg');
          const viewBox = svg?.getAttribute('viewBox');
          const bounds = svg?.getBoundingClientRect();
          const markers = svg ? [...svg.querySelectorAll('circle,.insight-method-chip rect')].map(n=>n.getBoundingClientRect()) : [];
          return {items:d.scope.items, nodes:svg ? svg.querySelectorAll('circle,text,path,rect').length : 0,
                  chips:root.querySelectorAll('.insight-method-chip').length,
                  viewBox, markersInside:markers.every(r=>r.left>=bounds.left-1&&r.right<=bounds.right+1&&r.top>=bounds.top-1&&r.bottom<=bounds.bottom+1),
                  visible:root.getBoundingClientRect().width > 100,
                  documentOverflow:document.documentElement.scrollWidth > innerWidth + 2};
        })()""")
        result["render_seconds"] = round(time.perf_counter() - started, 3)
        if result["items"] != len(fixture["items"]) or not result["visible"]:
            raise AssertionError(f"{kind}: incorrect whole-library scope or hidden map: {result}")
        if kind == "methods" and not result["chips"]:
            raise AssertionError("Methodology pipeline has no method chips")
        if kind != "methods" and not result["nodes"]:
            raise AssertionError(f"{kind}: SVG has no rendered nodes")
        if result["documentOverflow"]:
            raise AssertionError(f"{kind}: page extends beyond viewport")
        if not result["viewBox"] or not result["markersInside"]:
            raise AssertionError(f"{kind}: missing viewBox or clipped visual markers: {result}")
        await cdp.screenshot(out.with_name(f"{out.stem}-{kind}{out.suffix}"))
        result["interaction"] = await check_insight_interaction(cdp, kind)
        result["exports"] = await check_insight_exports(cdp, kind, out.parent)
        await cdp.screenshot(out.with_name(f"{out.stem}-{kind}-detail{out.suffix}"))
        if kind == "methods":
            await cdp.eval("document.querySelector('.insight-diagnostics').scrollIntoView({block:'start'})")
            await cdp.screenshot(out.with_name(f"{out.stem}-gaps{out.suffix}"))
            if not await cdp.eval("document.querySelectorAll('.insight-check').length >= 11 && document.querySelectorAll('.insight-gaps p').length > 0"):
                raise AssertionError("Reporting checks or structural gaps did not render")
        report["maps"][kind] = result
    # Opening from a collection must inherit its scope rather than reset it.
    await cdp.eval(f"rhTestApp.includeSub = true; rhTestApp.selectCollection({fixture['collections']['parent']}); await rhTestApp.openInsight('methods')")
    await wait_expression(cdp, "!rhTestApp.insight.loading && !!rhTestApp.insight.data.scope")
    scoped = await cdp.eval("rhTestApp.insight.data.papers.map(p => p.id)")
    if set(scoped) != set(fixture["descendant_items"]):
        raise AssertionError(f"Collection inheritance included the wrong papers: {scoped}")
    report["inherited_collection_items"] = scoped
    await cdp.screenshot(out.with_name(f"{out.stem}-collection{out.suffix}"))
    await cdp.eval("rhTestApp.insight.includeSub = false; await rhTestApp.loadInsight()")
    scoped = await cdp.eval("rhTestApp.insight.data.papers.map(p => p.id)")
    if set(scoped) != set(fixture["parent_items"]):
        raise AssertionError(f"Subcollections toggle did not exclude child papers: {scoped}")
    report["direct_collection_items"] = scoped
    await cdp.eval("await rhTestApp.openInsight('words'); rhTestApp.insight.metric='distinct'; rhTestApp.insight.view='bars'; rhTestApp.renderInsight()")
    if not await cdp.eval("rhTestApp.insight.data.scope.comparison_available && rhTestApp.insight.data.words.some(w => w.distinct > 0) && !!document.querySelector('#insight-svg rect')"):
        raise AssertionError("Collection distinctiveness bars did not render")
    report["distinctiveness"] = True
    await cdp.eval("await rhTestApp.openInsight('methods')")
    await cdp.eval("rhTestApp.insight.scope = 'tag:cognition'; await rhTestApp.loadInsight()")
    if await cdp.eval("rhTestApp.insight.data.scope.items") != 1:
        raise AssertionError("Tag scope did not select the cognition paper")
    await cdp.eval("rhTestApp.insight.scope = 'author:Example, Alice'; await rhTestApp.loadInsight()")
    if await cdp.eval("rhTestApp.insight.data.scope.items") != 2:
        raise AssertionError("Author scope did not select Alice's two papers")
    await cdp.eval("await rhTestApp.resetInsightScope()")
    if await cdp.eval("rhTestApp.insight.data.scope.items") != 4:
        raise AssertionError("Reset did not restore whole-library scope")
    report["scopes"] = ["collection", "subcollections", "author", "tag", "reset"]
    report["stress_layout"] = await cdp.eval("""(() => {
      const nodes = Array.from({length:80}, (_,i) => ({id:String(i),label:'Synthetic concept '+i,count:1+i%4,cluster:i%5}));
      const edges = nodes.slice(1).map((n,i) => ({source:String(i),target:n.id,weight:1}));
      const started = performance.now(), layout = InsightLayouts.forceLayout(nodes,edges);
      const repeat = InsightLayouts.forceLayout(nodes,edges);
      return {nodes:layout.length,seconds:(performance.now()-started)/1000,
        finite:layout.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)),
        inside:layout.every(n => n.x>=0 && n.x<=1000 && n.y>=0 && n.y<=InsightLayouts.graphHeight(nodes.length)),
        deterministic:JSON.stringify(layout)===JSON.stringify(repeat)};
    })()""")
    if not all(report["stress_layout"][key] for key in ("finite", "inside", "deterministic")):
        raise AssertionError(f"80-node graph layout failed: {report['stress_layout']}")
    await cdp.eval("""await rhTestApp.openInsight('concepts');
      rhTestApp.insight.data = {...rhTestApp.insight.data,scope:{items:4,label:'80-node synthetic layout stress'},
        nodes:Array.from({length:80},(_,i)=>({id:String(i),label:'Synthetic concept '+i,count:1+i%4,cluster:i%5,items:[],evidence:[]})),
        edges:Array.from({length:79},(_,i)=>({source:String(i),target:String(i+1),weight:1})),clusters:[]};
      rhTestApp.insight.nodes = InsightLayouts.forceLayout(rhTestApp.insight.data.nodes,rhTestApp.insight.data.edges);
      rhTestApp.renderInsight();
      await new Promise(resolve=>requestAnimationFrame(resolve));""")
    report["stress_layout"]["label_overlaps"] = await cdp.eval("""(() => {
      const labels=[...document.querySelectorAll('#insight-svg .insight-label')].map(e=>e.getBoundingClientRect());
      let overlaps=0; for(let i=0;i<labels.length;i++) for(let j=i+1;j<labels.length;j++) {
        const a=labels[i],b=labels[j]; if(a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top) overlaps++;
      } return overlaps;
    })()""")
    await cdp.send("Emulation.setDeviceMetricsOverride", width=args.width, height=max(1400, args.height), deviceScaleFactor=args.scale, mobile=False)
    await cdp.screenshot(out.with_name(f"{out.stem}-stress80{out.suffix}"))
    await cdp.send("Emulation.setDeviceMetricsOverride", width=args.width, height=args.height, deviceScaleFactor=args.scale, mobile=False)
    await cdp.eval("await rhTestApp.openInsight('methods', {})")
    return report


async def check_insight_interaction(cdp: "CDP", kind: str) -> dict:
    """Use actual SVG click events, then exercise alternate map representations."""
    selector = {"concepts": "[data-node]", "words": ".insight-node", "goals": ".insight-goal-link", "methods": ".insight-method-chip"}[kind]
    await cdp.eval(f"document.querySelector('#insight-svg {selector}').dispatchEvent(new MouseEvent('click', {{bubbles:true}}))")
    detail = await cdp.eval("({papers:rhTestApp.insight.selected?.items?.length||0,evidence:rhTestApp.insight.selected?.evidence?.length||0})")
    if not detail["papers"] or not detail["evidence"]:
        raise AssertionError(f"{kind}: click did not reveal papers and supporting evidence: {detail}")
    if kind == "concepts":
        await cdp.eval("await rhTestApp.focusInsight()")
        if not await cdp.eval("rhTestApp.insight.focus && rhTestApp.insight.data.nodes.length"):
            raise AssertionError("Concept focus returned no nodes")
        await cdp.eval("await rhTestApp.resetGraphFocus(); rhTestApp.insight.cluster = rhTestApp.insight.nodes[0].cluster; rhTestApp.renderInsight()")
        if not await cdp.eval("document.querySelectorAll('#insight-svg [data-node]').length === rhTestApp.insight.nodes.filter(n => n.cluster === rhTestApp.insight.cluster).length"):
            raise AssertionError("Cluster selection rendered nodes outside the selected community")
        await cdp.eval("rhTestApp.insight.cluster = null; rhTestApp.renderInsight()")
    elif kind == "words":
        await cdp.eval("rhTestApp.insight.source = 'fulltext'; await rhTestApp.loadInsight(); rhTestApp.insight.view = 'bars'; rhTestApp.renderInsight()")
        if not await cdp.eval("!!document.querySelector('#insight-svg rect')"):
            raise AssertionError("Full-text ranked word bars did not render")
    elif kind == "methods":
        await cdp.eval("rhTestApp.insight.methodView = 'matrix'; rhTestApp.renderInsight()")
        await wait_expression(cdp, "!!document.querySelector('#insight-svg .insight-matrix-cell')")
        await cdp.eval("document.querySelector('#insight-svg .insight-matrix-cell').dispatchEvent(new MouseEvent('click', {bubbles:true}))")
        if not await cdp.eval("rhTestApp.insight.selected?.items?.length === 1"):
            raise AssertionError("Heatmap cell did not identify its paper")
    return detail


async def check_insight_exports(cdp: "CDP", kind: str, destination: Path) -> dict:
    """Capture real exported Blobs before browser download; inspect their contents."""
    await cdp.eval("""(() => {
      window.rhExportCapture = {blobs:new Map(),downloads:[],create:URL.createObjectURL,click:HTMLAnchorElement.prototype.click};
      URL.createObjectURL = function(blob) {const url=rhExportCapture.create.call(URL,blob); rhExportCapture.blobs.set(url,blob); return url;};
      HTMLAnchorElement.prototype.click = function() {if(this.download) rhExportCapture.downloads.push({name:this.download,blob:rhExportCapture.blobs.get(this.href)}); else return rhExportCapture.click.call(this);};
    })()""")
    results = {}
    try:
        for format_name in ("json", "csv", "png"):
            await cdp.eval(f"await rhTestApp.exportInsight('{format_name}')")
            data = await cdp.eval("""await (async () => {
              const item = rhExportCapture.downloads.at(-1);
              if(!item || !item.blob) return null;
              const b = new Uint8Array(await item.blob.arrayBuffer());
              let encoded=''; for(let i=0;i<b.length;i+=8192) encoded+=String.fromCharCode(...b.slice(i,i+8192));
              return {name:item.name,type:item.blob.type,size:b.length,base64:btoa(encoded)};
            })()""")
            if not data or not data["name"].endswith("." + format_name) or data["size"] < 50:
                state = await cdp.eval("""await (async () => ({downloads:rhExportCapture.downloads.map(d=>({name:d.name,blob:!!d.blob})),svgErrors:await Promise.all([...rhExportCapture.blobs.values()].filter(b=>b.type==='image/svg+xml').map(async b=>new DOMParser().parseFromString(await b.text(),'image/svg+xml').querySelector('parsererror')?.textContent))}))()""")
                raise AssertionError(f"{kind} {format_name} export missing or empty: {state}")
            raw = base64.b64decode(data.pop("base64"))
            if format_name == "json" and not json.loads(raw)["scope"]["items"]:
                raise AssertionError("JSON export has no scoped papers")
            if format_name == "csv":
                import csv
                import io
                rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
                if len(rows) < 2 or any(len(row) != len(rows[0]) for row in rows):
                    raise AssertionError("CSV export is empty or not rectangular")
                data["rows"] = len(rows)
            if format_name == "png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise AssertionError("PNG export is not a PNG image")
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"export-{kind}.{format_name}").write_bytes(raw)
            results[format_name] = data
    finally:
        await cdp.eval("URL.createObjectURL = rhExportCapture.create; HTMLAnchorElement.prototype.click = rhExportCapture.click; delete window.rhExportCapture")
    return results


def find_chrome() -> str:
    """Return the path of an installed Chrome executable."""
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if not found:
        raise SystemExit("Google Chrome not found")
    return found


def free_port() -> int:
    """Pick an unused local TCP port for the DevTools endpoint."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CDP:
    """Minimal CDP client over one page WebSocket."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.console_errors: list[str] = []
        self.repl_mode = False  # Existing callers retain automatic Promise unwrapping.

    async def listen(self) -> None:
        """Dispatch responses to waiting futures and collect console errors."""
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
            elif msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.console_errors.append(f"exception: {d.get('exception', {}).get('description') or d.get('text')}")
            elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] in ("error", "assert"):
                args = " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])
                self.console_errors.append(f"console.error: {args}")
            elif msg.get("method") == "Log.entryAdded" and msg["params"]["entry"]["level"] == "error":
                e = msg["params"]["entry"]
                self.console_errors.append(f"log: {e.get('text')} {e.get('url', '')}")

    async def send(self, method: str, **params) -> dict:
        """Send a CDP command and wait for its result."""
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = fut
        await self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 60)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def eval(self, expression: str):
        """Evaluate JavaScript (promises awaited) and return its JSON value."""
        res = await self.send("Runtime.evaluate", expression=expression, awaitPromise=True,
                              returnByValue=True, replMode=self.repl_mode)
        if res.get("exceptionDetails"):
            raise RuntimeError(res["exceptionDetails"].get("exception", {}).get("description", "JS error"))
        return res.get("result", {}).get("value")

    async def screenshot(self, path: Path) -> None:
        """Save a PNG of the current viewport."""
        res = await self.send("Page.captureScreenshot", format="png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(res["data"]))


async def run(args: argparse.Namespace) -> int:
    import websockets

    port = free_port()
    profile = tempfile.mkdtemp(prefix="rh-chrome-")
    proc = subprocess.Popen([
        find_chrome(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
        f"--window-size={args.width},{args.height}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        targets = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as r:
                    targets = [t for t in json.load(r) if t["type"] == "page"]
                if targets:
                    break
            except OSError:
                time.sleep(0.3)
        if not targets:
            raise SystemExit("Chrome DevTools endpoint did not start")
        async with websockets.connect(targets[0]["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            cdp.repl_mode = args.insights  # The map scenario uses explicit top-level await.
            listener = asyncio.create_task(cdp.listen())
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("Log.enable")
            await cdp.send("Emulation.setDeviceMetricsOverride", width=args.width, height=args.height,
                           deviceScaleFactor=args.scale, mobile=False)
            await cdp.send("Emulation.setEmulatedMedia", features=[{
                "name": "prefers-color-scheme", "value": "dark" if args.dark else "light",
            }])
            await cdp.send("Page.navigate", url=args.url)
            await asyncio.sleep(args.initial_wait)
            out = Path(args.out)
            if args.insights:
                report = await check_insights(cdp, args, out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report, indent=2))
            for i, step in enumerate(args.step or []):
                value = await cdp.eval(step)
                if value is not None:
                    print(f"step {i + 1}: {json.dumps(value)[:400]}")
                await asyncio.sleep(args.wait)
                if args.shot_each:
                    await cdp.screenshot(out.with_name(f"{out.stem}-{i + 1}{out.suffix}"))
            for assertion in args.assert_js or []:
                if not await cdp.eval(assertion):
                    raise AssertionError(f"JavaScript assertion failed: {assertion}")
            await cdp.screenshot(out)
            print(f"saved {out}")
            listener.cancel()
            if cdp.console_errors:
                print("CONSOLE ERRORS:")
                for e in cdp.console_errors:
                    print("  ", e[:500])
                return 2
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--out", default="shots/snapshot.png")
    p.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    p.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    p.add_argument("--scale", type=float, default=1.0, help="device scale factor")
    p.add_argument("--dark", action="store_true", help="emulate prefers-color-scheme: dark")
    p.add_argument("--initial-wait", type=float, default=2.5)
    p.add_argument("--step", action="append", help="JavaScript to run (repeatable)")
    p.add_argument("--wait", type=float, default=1.0, help="seconds after each step")
    p.add_argument("--shot-each", action="store_true")
    p.add_argument("--assert-js", action="append", help="JavaScript expression that must evaluate truthy")
    p.add_argument("--insights", action="store_true", help="validate and snapshot all four insight maps")
    p.add_argument("--fixture", help="fixture.json generated by tools/insight_fixture.py (required with --insights)")
    args = p.parse_args()
    if args.insights and not args.fixture:
        p.error("--insights requires --fixture")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
