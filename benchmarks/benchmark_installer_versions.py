"""Benchmark L: clean-machine installation, previous installer (2.8.0) versus 2.9.0.

Both runs come from .github/workflows/installer.yml: one fresh container / runner per cell, each writing
one result.json (platform, scenario, method, success, seconds). Cells:
    linux     ubuntu 22.04 / 24.04, debian 12, fedora 41 with python3 ("python") and without ("bare")
    windows   double-click .bat ("installer"); 2.9.0 adds install.ps1 forced onto a private Python
    macos     2.9.0 only (system Python and private Python): the previous installer did not support macOS
Success in 2.9.0 is stricter than before: the app must also be opened through its shortcut and the
answering instance must be the installed one (Windows, macOS).

Statistics: one measurement per cell, so results are descriptive. Paired cells present in both runs are
compared with an exact sign test on duration (reported, not over-interpreted).

Run:  <python with matplotlib + scipy> benchmarks/benchmark_installer_versions.py
Outputs: benchmarks/results/L_installer_versions.{csv,png,pdf,svg}, L_installer_versions_summary.csv
"""
# ============================================================================ PARAMETERS
REPO = "Andrianarivelo/ReviewHunter"
RUNS = {"2.8.0": "40704774537", "2.9.0": "40721005042"}
OUT_DIR = r"D:\Apps\ReviewHunter\benchmarks\results"
DOWNLOAD_DIR = r"C:\Users\andry\AppData\Local\Temp\claude\rh-bench\installer-versions"
FIG_DPI = 300
FONT_SIZE = 8
COLORS = {"2.8.0": "#9aa3b2", "2.9.0": "#0284c7", "unsupported": "#e5e7eb"}
BAR_WIDTH = 0.34                   # thin bars
# ============================================================================

import csv
import json
import subprocess
from pathlib import Path


def fetch(version: str, run_id: str) -> list[dict]:
    dest = Path(DOWNLOAD_DIR) / run_id
    if not dest.exists():
        subprocess.run(["gh", "run", "download", run_id, "--repo", REPO, "--pattern", "result-*", "--dir", str(dest)],
                       check=True)
    rows = []
    for f in dest.rglob("result.json"):
        r = json.loads(f.read_text(encoding="utf-8-sig"))
        if r.get("method") == "legacy":
            continue                                   # the README's old `python run.py` baseline (Benchmark F)
        method = r.get("method")
        cell = f"{r['platform']} | {r['scenario']}" + ("" if method == "installer" else f" | {method}")
        rows.append({"version": version, "cell": cell, "success": r["success"] is True or str(r["success"]).lower() == "true",
                     "seconds": float(r["seconds"]), "python": r.get("python", ""), "run_id": run_id})
    return rows


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from scipy import stats

    rows = [r for v, rid in RUNS.items() for r in fetch(v, rid)]
    out = Path(OUT_DIR)
    with open(out / "L_installer_versions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["cell"], r["version"])))
    by = {(r["version"], r["cell"]): r for r in rows}
    cells = sorted({r["cell"] for r in rows}, key=lambda c: (c.split(" | ")[0].startswith(("macos", "windows")), c))
    old, new = "2.8.0", "2.9.0"

    paired = [c for c in cells if (old, c) in by and (new, c) in by]
    d = np.array([by[(new, c)]["seconds"] - by[(old, c)]["seconds"] for c in paired])
    nz = d[d != 0]
    sign_p = float(stats.binomtest(int((nz < 0).sum()), len(nz), 0.5).pvalue) if len(nz) else 1.0
    summary = []
    for v in (old, new):
        vs = [by[(v, c)] for c in cells if (v, c) in by]
        summary.append({"version": v, "cells_run": len(vs), "cells_success": sum(r["success"] for r in vs),
                        "platform_families": len({r["cell"].split(":")[0].split(" ")[0].split("-")[0] for r in vs}),
                        "median_seconds": float(np.median([r["seconds"] for r in vs]))})
    summary.append({"version": "paired cells", "cells_run": len(paired),
                    "cells_success": f"median duration change {np.median(d):+.0f} s", "platform_families": "",
                    "median_seconds": f"sign test p = {sign_p:.3f} (descriptive)"})
    with open(out / "L_installer_versions_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    plt.rcParams.update({"font.size": FONT_SIZE, "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"})
    fig, (a, b) = plt.subplots(1, 2, figsize=(10.0, 4.0), gridspec_kw={"width_ratios": [1, 1.4]})
    y = np.arange(len(cells))
    for k, v in enumerate((old, new)):
        for i, c in enumerate(cells):
            r = by.get((v, c))
            if r is None:
                a.scatter(k, i, marker="s", s=60, color=COLORS["unsupported"], edgecolor="#9ca3af", lw=0.5)
            else:
                a.scatter(k, i, marker="o" if r["success"] else "X", s=60,
                          color=COLORS[v] if r["success"] else "#dc2626")
    a.set_xticks([0, 1], [f"installer {old}", f"installer {new}"])
    a.set_yticks(y, cells, fontsize=FONT_SIZE - 1)
    a.set_xlim(-0.6, 1.6)
    a.invert_yaxis()
    a.set_title("a  clean-machine install\n(dot = ok, x = failed, grey square = not supported)", loc="left",
                fontsize=FONT_SIZE + 0.5)
    for k, v in enumerate((old, new)):
        vals = [by[(v, c)]["seconds"] if (v, c) in by else 0 for c in cells]
        b.barh(y + (k - 0.5) * BAR_WIDTH, vals, BAR_WIDTH, color=COLORS[v], label=f"installer {v}")
    b.set_yticks(y, [""] * len(cells))
    b.invert_yaxis()
    b.set_xlabel("wall time of the CI step, s (includes the shortcut launch check in 2.9.0)")
    b.set_title(f"b  duration\npaired cells: median {np.median(d):+.0f} s, sign test p = {sign_p:.2f}, n = {len(paired)}"
                " (descriptive)", loc="left", fontsize=FONT_SIZE + 0.5)
    b.legend(frameon=False, fontsize=FONT_SIZE - 1, loc="lower right", bbox_to_anchor=(1.0, 1.13), ncol=2)
    fig.tight_layout()
    for ext in ("png", "pdf", "svg"):
        fig.savefig(out / f"L_installer_versions.{ext}", dpi=FIG_DPI, bbox_inches="tight")
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
