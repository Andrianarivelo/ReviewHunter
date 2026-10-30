"""Benchmark M: front page before / after the 2.10 refresh, and starting ReviewHunter from the browser.

UI (rendered measurements in headless Chrome, same fixture library served by both versions):
    previous version  = git revision PREV_REV, served from a worktree
    current version   = this working tree
For every fixture paper (selected in the list) and every viewport: computed text sizes (sidebar, list,
detail fields, title), list rows visible without scrolling, field rows shown in the Info tab, and
whether the citation preview and the Collections section are visible without scrolling.
Launch: time for the native-messaging helper to answer "start" (cold: new library and server; warm:
server already running), LAUNCH_REPEATS times. The previous version has no helper (cannot start).

Usage:
    .venv/Scripts/python.exe benchmarks/benchmark_ui_refresh.py measure
    %USERPROFILE%/miniconda3/python.exe benchmarks/benchmark_ui_refresh.py plot
Outputs: benchmarks/results/M_ui_refresh.{png,pdf,svg}, M_ui_refresh_items.csv, M_launch.csv, M_statistics.csv
"""
from __future__ import annotations

# ======================================================================================
# Parameters
# ======================================================================================
PREV_REV = "a8279e0"                                  # last commit before the refresh
PREV_WORKTREE = r"C:\Users\andry\AppData\Local\Temp\claude\rh-prev-ui"
VIEWPORTS = [(1440, 900), (1280, 720)]
N_ITEMS = 12
LAUNCH_REPEATS = 5
FIG_DPI = 300
FONT_SIZE = 8
BAR_WIDTH = 0.32
COLORS = {"previous": "#9aa3b2", "current": "#0284c7"}

import asyncio
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "benchmarks" / "results"
sys.path.insert(0, str(REPO / "tools"))

MEASURE_JS = r"""
(() => {
  const px = (el) => el ? parseFloat(getComputedStyle(el).fontSize) : null;
  const list = document.querySelector('.items-body')?.parentElement;
  const lb = list ? list.getBoundingClientRect().bottom : innerHeight;
  const rows = [...document.querySelectorAll('.item-row')];
  const visible = rows.filter(r => { const b = r.getBoundingClientRect(); return b.height > 0 && b.bottom <= lb + 1; }).length;
  const labels = [...document.querySelectorAll('.field-grid .field-label, .field-grid .field-label-select')].filter(e => e.offsetParent);
  const pane = document.querySelector('.detail-pane');
  const paneBottom = pane ? pane.getBoundingClientRect().bottom : innerHeight;
  const rail = [...document.querySelectorAll('.detail-pane .rail-label')].find(e => /collections/i.test(e.textContent));
  const csl = document.querySelector('.detail-pane .csl-preview');
  const top = (e) => e && e.offsetParent ? e.getBoundingClientRect().top : null;
  return {
    sidebar_px: px(document.querySelector('.sidebar .nav-row')), list_px: px(rows[0]),
    field_px: px(document.querySelector('.field-input')), title_px: px(document.querySelector('.title-input')),
    rows_visible: visible, rows_total: rows.length, fields_shown: labels.length,
    // capacity: rows that fit in the list viewport (the fixture is too small to fill it)
    rows_per_screen: rows[0] && list ? Math.floor(list.getBoundingClientRect().height / rows[0].getBoundingClientRect().height) : null,
    collections_top: top(rail), citation_top: top(csl), pane_bottom: paneBottom,
  };
})()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(url, data=None, method="GET"):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def start_server(root: Path, home: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "REVIEWHUNTER_HOME": str(home), "REVIEWHUNTER_PORT": str(port), "REVIEWHUNTER_NO_HTTPS": "1",
           "REVIEWHUNTER_NO_SHORTCUTS": "1", "REVIEWHUNTER_NO_DIALOGS": "1"}
    proc = subprocess.Popen([str(REPO / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else sys.executable,
                             "-m", "doi_hunter_web", "--no-browser", "--no-https"], cwd=root, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            if http(f"http://127.0.0.1:{port}/connector/ping").get("app"):
                return proc
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server {root} did not start")


def make_fixture(port: int) -> list[int]:
    """Realistic papers: some complete, some sparse (as imported from a DOI vs a bare PDF)."""
    rng = random.Random(7)
    journals = ["Neuron", "Nature Neuroscience", "Journal of Neuroscience", "eLife", "Science", "PLOS Biology"]
    cid = http(f"http://127.0.0.1:{port}/api/collections", {"name": "Reward"}, "POST").get("id")
    ids = []
    for i in range(N_ITEMS):
        full = i % 3 != 2
        fv = {"publicationTitle": rng.choice(journals), "date": str(1995 + rng.randint(0, 30))}
        if full:
            fv.update(volume=str(rng.randint(5, 120)), issue=str(rng.randint(1, 12)), pages=f"{rng.randint(1, 900)}-{rng.randint(901, 1800)}",
                      DOI=f"10.5555/bench.{i}", abstractNote="Dopamine neurons signal reward prediction errors. " * 3)
        item = http(f"http://127.0.0.1:{port}/api/items", {"title": f"Benchmark paper {i:02d} on reward learning and dopamine",
                    "item_type": "journalArticle", "creators": [{"creatorType": "author", "firstName": "Ana", "lastName": f"Author{i}"}],
                    "field_values": fv, "collections": [cid] if cid else []}, "POST")
        ids.append(item["id"])
    return ids


async def measure_ui(port: int, version: str, ids: list[int]) -> list[dict]:
    import websockets

    from ui_snapshot import CDP, find_chrome

    rows = []
    devtools = free_port()
    chrome = subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={devtools}",
                               f"--user-data-dir={tempfile.mkdtemp(prefix='rh-ui-bench-')}", "--no-first-run",
                               "--force-device-scale-factor=1", "about:blank"])
    try:
        for _ in range(60):
            try:
                target = next(t for t in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{devtools}/json").read())
                              if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(0.25)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=2 ** 24) as ws:
            cdp = CDP(ws)
            task = asyncio.create_task(cdp.listen())
            for w, h in VIEWPORTS:
                await cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                await cdp.send("Page.navigate", url=f"http://127.0.0.1:{port}/")
                await asyncio.sleep(4)
                for iid in ids:
                    await cdp.eval(f"(async () => {{ const A = Alpine.$data(document.querySelector('[x-data]')); "
                                   f"A.selectedIds = [{iid}]; await A.openItem({iid}); }})()")
                    await asyncio.sleep(0.6)
                    m = await cdp.eval(MEASURE_JS)
                    m.update(version=version, viewport=f"{w}x{h}", height=h, item=iid)
                    for k in ("collections_top", "citation_top"):
                        m[k.replace("_top", "_visible")] = int(m[k] is not None and m[k] < min(h, m["pane_bottom"]) - 20)
                    rows.append(m)
            task.cancel()
    finally:
        chrome.terminate()
    return rows


def launch_times() -> list[dict]:
    """Native-messaging 'start' round trips (cold = new library, warm = already running)."""
    out = []
    for rep in range(LAUNCH_REPEATS):
        home = Path(tempfile.mkdtemp(prefix="rh-launch-bench-"))
        port = free_port()
        env = {**os.environ, "REVIEWHUNTER_HOME": str(home), "REVIEWHUNTER_PORT": str(port), "REVIEWHUNTER_NO_HTTPS": "1",
               "REVIEWHUNTER_NO_SHORTCUTS": "1", "REVIEWHUNTER_NO_DIALOGS": "1"}
        for kind in ("cold", "warm"):
            data = json.dumps({"cmd": "start"}).encode()
            t0 = time.perf_counter()
            res = subprocess.run([sys.executable, "-m", "doi_hunter_web.native_host"], cwd=REPO, env=env, timeout=200,
                                 input=struct.pack("<I", len(data)) + data, capture_output=True)
            dt = time.perf_counter() - t0
            reply = json.loads(res.stdout[4:]) if len(res.stdout) > 4 else {}
            out.append({"repeat": rep, "kind": kind, "seconds": round(dt, 3), "ok": bool(reply.get("ok")),
                        "state": reply.get("state", reply.get("error", ""))})
            print(f"launch {rep} {kind}: {dt:.2f} s {reply.get('state')}")
        try:
            http(f"http://127.0.0.1:{port}/api/system/shutdown", {}, "POST")
        except OSError:
            pass
        time.sleep(1.5)
        shutil.rmtree(home, ignore_errors=True)
    return out


def measure() -> None:
    import csv

    RESULTS.mkdir(parents=True, exist_ok=True)
    if not (Path(PREV_WORKTREE) / "doi_hunter_web").exists():
        subprocess.run(["git", "worktree", "add", "-f", PREV_WORKTREE, PREV_REV], cwd=REPO, check=True)
    home = Path(tempfile.mkdtemp(prefix="rh-ui-bench-lib-"))
    cur_port, prev_port = free_port(), free_port()
    cur = start_server(REPO, home, cur_port)
    try:
        ids = make_fixture(cur_port)
        prev = start_server(Path(PREV_WORKTREE), home, prev_port)
        try:
            rows = asyncio.run(measure_ui(prev_port, "previous", ids)) + asyncio.run(measure_ui(cur_port, "current", ids))
        finally:
            prev.terminate()
    finally:
        cur.terminate()
    with open(RESULTS / "M_ui_refresh_items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    launch = launch_times()
    with open(RESULTS / "M_launch.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(launch[0]))
        w.writeheader()
        w.writerows(launch)
    print("measurements written")


def plot() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from scipy import stats

    plt.rcParams.update({"font.size": FONT_SIZE, "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"})
    df = pd.read_csv(RESULTS / "M_ui_refresh_items.csv")
    launch = pd.read_csv(RESULTS / "M_launch.csv")
    rows = []

    def paired(metric):
        a = df[df.version == "previous"].sort_values(["viewport", "item"])[metric].to_numpy(float)
        b = df[df.version == "current"].sort_values(["viewport", "item"])[metric].to_numpy(float)
        d = b - a
        nz = d[d != 0]
        p = float(stats.binomtest(int((nz > 0).sum()), len(nz), 0.5).pvalue) if len(nz) else 1.0
        rows.append({"metric": metric, "previous_median": float(np.median(a)), "current_median": float(np.median(b)),
                     "n_pairs": len(d), "n_changed": int(len(nz)), "sign_test_p": p})
        return a, b, p

    fig, axes = plt.subplots(1, 4, figsize=(10.5, 2.9), gridspec_kw={"width_ratios": [1.5, 1, 1.25, 1]})
    ax = axes[0]
    metrics = [("sidebar_px", "sidebar"), ("list_px", "list rows"), ("field_px", "fields"), ("title_px", "item title")]
    x = np.arange(len(metrics))
    for k, v in enumerate(("previous", "current")):
        vals = [df[df.version == v][m].median() for m, _ in metrics]
        ax.bar(x + (k - 0.5) * BAR_WIDTH, vals, BAR_WIDTH, color=COLORS[v], label=v)
        for xi, val in zip(x, vals):
            ax.text(xi + (k - 0.5) * BAR_WIDTH, val + 0.25, f"{val:.1f}", ha="center", fontsize=FONT_SIZE - 2)
    for m, _ in metrics:
        paired(m)
    ax.set_xticks(x, [m[1] for m in metrics])
    ax.set_ylabel("rendered font size (px)")
    ax.set_ylim(0, 24)
    ax.set_title("a  text size", loc="left", fontsize=FONT_SIZE + 1)
    ax.legend(frameon=False, fontsize=FONT_SIZE - 1, loc="upper left", ncol=2)

    ax = axes[1]
    a, b, p = paired("fields_shown")
    for i in range(len(a)):
        ax.plot([0, 1], [a[i], b[i]], color="#cbd5e1", lw=0.7, zorder=1)
    ax.scatter(np.zeros(len(a)), a, s=10, color=COLORS["previous"], zorder=2)
    ax.scatter(np.ones(len(b)), b, s=10, color=COLORS["current"], zorder=2)
    ax.set_xticks([0, 1], ["previous", "current"])
    ax.set_xlim(-0.4, 1.4)
    ax.set_ylabel("field rows in the Info tab")
    ax.set_title(f"b  fields shown\n(sign test p = {p:.1e}, n = {len(a)})", loc="left", fontsize=FONT_SIZE + 1)

    ax = axes[2]
    groups = [("citation_visible", "citation\npreview"), ("collections_visible", "collections"), ("rows_per_screen", "list rows\nper screen")]
    for k, v in enumerate(("previous", "current")):
        vals = []
        for m, _ in groups:
            col = df[df.version == v][m]
            vals.append(col.mean() * 100 if m != "rows_per_screen" else col.median())
        ax.bar(np.arange(3) + (k - 0.5) * BAR_WIDTH, vals, BAR_WIDTH, color=COLORS[v])
        for xi, val in enumerate(vals):
            ax.text(xi + (k - 0.5) * BAR_WIDTH, val + 1.5, f"{val:.0f}", ha="center", fontsize=FONT_SIZE - 2)
    for m, _ in groups:
        paired(m)
    ax.set_xticks(range(3), [g[1] for g in groups])
    ax.set_ylabel("% of papers visible without scrolling\n(list rows: median count per screen)")
    ax.set_ylim(0, 115)
    ax.set_title("c  above the fold", loc="left", fontsize=FONT_SIZE + 1)

    ax = axes[3]
    ok = launch[launch.ok]
    for i, kind in enumerate(("cold", "warm")):
        v = ok[ok.kind == kind].seconds.to_numpy()
        ax.bar(i, np.median(v), BAR_WIDTH * 2, color=COLORS["current"])
        ax.scatter(np.full(len(v), i) + np.linspace(-.08, .08, len(v)), v, s=8, color="k", zorder=3)
        ax.text(i, np.median(v) + 0.4, f"{np.median(v):.1f} s", ha="center", fontsize=FONT_SIZE - 1)
        rows.append({"metric": f"launch_{kind}_seconds", "previous_median": "not possible", "current_median": float(np.median(v)),
                     "n_pairs": len(v), "n_changed": "", "sign_test_p": ""})
    ax.set_xticks([0, 1], ["cold start", "already\nrunning"])
    ax.set_ylabel("seconds until ReviewHunter answers")
    ax.set_title("d  start from the browser\n(previous: not possible)", loc="left", fontsize=FONT_SIZE + 1)
    fig.tight_layout()
    for ext in ("png", "pdf", "svg"):
        fig.savefig(RESULTS / f"M_ui_refresh.{ext}", dpi=FIG_DPI, bbox_inches="tight")
    pd.DataFrame(rows).to_csv(RESULTS / "M_statistics.csv", index=False)
    print(pd.DataFrame(rows).to_string())


if __name__ == "__main__":
    {"measure": measure, "plot": plot}[sys.argv[1] if len(sys.argv) > 1 else "measure"]()
