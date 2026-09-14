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
        res = await self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
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
            listener = asyncio.create_task(cdp.listen())
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("Log.enable")
            await cdp.send("Emulation.setDeviceMetricsOverride", width=args.width, height=args.height,
                           deviceScaleFactor=args.scale, mobile=False)
            if args.dark:
                await cdp.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
            await cdp.send("Page.navigate", url=args.url)
            await asyncio.sleep(args.initial_wait)
            out = Path(args.out)
            for i, step in enumerate(args.step or []):
                value = await cdp.eval(step)
                if value is not None:
                    print(f"step {i + 1}: {json.dumps(value)[:400]}")
                await asyncio.sleep(args.wait)
                if args.shot_each:
                    await cdp.screenshot(out.with_name(f"{out.stem}-{i + 1}{out.suffix}"))
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
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
