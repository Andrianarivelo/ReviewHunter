"""Render the ReviewHunter logo (doi_hunter_web/static/logo.svg) to every PNG the project ships.

    browser_extension/icons/icon-{16,32,48,128}.png        toolbar / extension page (online)
    browser_extension/icons/icon-grey-{16,32,48,128}.png   toolbar while ReviewHunter is not running
    doi_hunter_web/static/icons/icon-{180,512}.png         touch icon; macOS app icon (shortcuts.py)

Rendering is done by headless Chrome (exact SVG rendering, real alpha channel through a transparent
default background), so no image library is needed. Run after editing logo.svg:

    .venv/Scripts/python.exe tools/make_icons.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_snapshot import CDP, find_chrome, free_port  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "doi_hunter_web" / "static" / "logo.svg"
EXT_ICONS = ROOT / "browser_extension" / "icons"
APP_ICONS = ROOT / "doi_hunter_web" / "static" / "icons"
# Offline variant: same mark, desaturated and lighter, like a disabled toolbar button.
GREY_FILTER = "grayscale(1) brightness(1.18) contrast(.82)"


async def render(cdp: CDP, size: int, css_filter: str = "") -> bytes:
    svg = base64.b64encode(LOGO.read_bytes()).decode()
    html = (f"<html><body style='margin:0;background:transparent'><img src='data:image/svg+xml;base64,{svg}' "
            f"width='{size}' height='{size}' style='display:block;filter:{css_filter or 'none'}'></body></html>")
    await cdp.send("Page.navigate", url="data:text/html;base64," + base64.b64encode(html.encode()).decode())
    await asyncio.sleep(0.4)
    shot = await cdp.send("Page.captureScreenshot", format="png",
                          clip={"x": 0, "y": 0, "width": size, "height": size, "scale": 1})
    return base64.b64decode(shot["data"])


async def main() -> int:
    import websockets

    port = free_port()
    chrome = subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={port}",
                               f"--user-data-dir={tempfile.mkdtemp(prefix='rh-icons-')}", "--no-first-run",
                               "--force-device-scale-factor=1", "about:blank"])
    try:
        for _ in range(60):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
                page = next(t for t in targets if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(0.25)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2 ** 26) as ws:
            cdp = CDP(ws)
            listener = asyncio.create_task(cdp.listen())
            await cdp.send("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
            await cdp.send("Emulation.setDeviceMetricsOverride", width=600, height=600, deviceScaleFactor=1, mobile=False)
            APP_ICONS.mkdir(parents=True, exist_ok=True)
            for size in (16, 32, 48, 128):
                (EXT_ICONS / f"icon-{size}.png").write_bytes(await render(cdp, size))
                (EXT_ICONS / f"icon-grey-{size}.png").write_bytes(await render(cdp, size, GREY_FILTER))
            for size in (180, 512):
                (APP_ICONS / f"icon-{size}.png").write_bytes(await render(cdp, size))
            listener.cancel()
    finally:
        chrome.terminate()
    for f in sorted([*EXT_ICONS.glob("icon-*.png"), *APP_ICONS.glob("icon-*.png")]):
        data = f.read_bytes()
        w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        rgba = data[25] == 6   # PNG colour type 6 = truecolour with alpha
        print(f"{f.relative_to(ROOT)}  {w}x{h}  {'RGBA' if rgba else 'no alpha!'}  {len(data)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
