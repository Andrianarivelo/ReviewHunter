"""Benchmark F: installing ReviewHunter on clean machines, previous method vs one-click installer.

Question: starting from a fresh operating system, does the method produce a running app, and how long
does it take?

Design (executed by .github/workflows/installer.yml, one fresh container or runner per cell)
    platforms   ubuntu:22.04, ubuntu:24.04, debian:12, fedora:41 (containers), windows-latest (runner)
    scenario    "python": the image plus python3 only (typical desktop); "bare": no Python at all
    previous    README method `python3 run.py` (bootstraps .venv, serves on 8765); success = /connector/ping answers
    installer   `bash install.sh --cli --yes` / `Install-ReviewHunter.bat --yes`; success = exit 0, install.json,
                menu launcher and the installer's own self-test (app started and answered /connector/ping)
Statistics
    Paired by platform on the "python" scenario: exact McNemar test on success (discordant platforms only).
    With 5 platforms the test can only reach p = 0.0625 at best: the result is reported as descriptive
    evidence, not as a significance claim. Durations are single measurements per cell (no dispersion).

Run
    <python with matplotlib+scipy> benchmarks/benchmark_installer.py            # latest successful CI run
    RH_BENCH_RUN_ID=123456 <python> benchmarks/benchmark_installer.py           # a specific run
Outputs (benchmarks/results): F_installer_runs.csv, F_installer_summary.csv, F_installer.{pdf,png,svg}
"""
# ============================================================================ PARAMETERS
REPO = "Andrianarivelo/ReviewHunter"
WORKFLOW = "installer.yml"
OUT_DIR = r"D:\Apps\ReviewHunter\benchmarks\results"
DOWNLOAD_DIR = r"C:\Users\andry\AppData\Local\Temp\claude\rh-bench\installer-artifacts"
PLATFORM_ORDER = ["ubuntu:22.04", "ubuntu:24.04", "debian:12", "fedora:41", "windows-latest"]
METHOD_LABEL = {"legacy": "previous (python run.py)", "installer": "one-click installer"}

# Figure styling
FIG_DPI = 300
FONT = "DejaVu Sans"
COLORS = {"legacy": "#8b94a9", "installer": "#0284c7", "bare": "#38bdf8"}
BAR_WIDTH = 0.26                   # thin bars
FIGSIZE = (10.5, 3.6)
# ============================================================================

import csv
import json
import os
import shutil
import subprocess
from pathlib import Path

OUT = Path(OUT_DIR)


def fetch_results() -> list[dict]:
    """Download the JSON artifacts of a CI run with the GitHub CLI and load them."""
    run_id = os.environ.get("RH_BENCH_RUN_ID")
    if not run_id:
        run_id = subprocess.run(["gh", "run", "list", "--repo", REPO, "--workflow", WORKFLOW, "--limit", "1",
                                 "--json", "databaseId", "-q", ".[0].databaseId"],
                                capture_output=True, text=True, check=True).stdout.strip()
    dest = Path(DOWNLOAD_DIR) / str(run_id)
    shutil.rmtree(dest, ignore_errors=True)
    subprocess.run(["gh", "run", "download", str(run_id), "--repo", REPO, "--pattern", "result-*", "--dir", str(dest)],
                   check=True)
    rows = []
    for f in sorted(dest.rglob("result.json")):
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        data["success"] = str(data["success"]).lower() == "true"
        data["seconds"] = int(data["seconds"])
        rows.append(data)
    print(f"run {run_id}: {len(rows)} results")
    return rows


def mcnemar_exact(b: int, c: int) -> float:
    from scipy.stats import binomtest
    return 1.0 if b + c == 0 else binomtest(b, b + c, 0.5).pvalue


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = fetch_results()
    OUT.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (PLATFORM_ORDER.index(r["platform"]) if r["platform"] in PLATFORM_ORDER else 99,
                             r["scenario"], r["method"]))
    with (OUT / "F_installer_runs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["platform", "scenario", "method", "success", "seconds", "python", "run_id"])
        w.writeheader()
        w.writerows(rows)

    cell = {(r["platform"], r["scenario"], r["method"]): r for r in rows}
    platforms = [p for p in PLATFORM_ORDER if any(k[0] == p for k in cell)]
    paired = [p for p in platforms if (p, "python", "legacy") in cell and (p, "python", "installer") in cell]
    b = sum(cell[(p, "python", "legacy")]["success"] and not cell[(p, "python", "installer")]["success"] for p in paired)
    c = sum(not cell[(p, "python", "legacy")]["success"] and cell[(p, "python", "installer")]["success"] for p in paired)
    p_value = mcnemar_exact(b, c)
    summary = []
    for method in ("legacy", "installer"):
        ok = [cell[(p, "python", method)] for p in paired if cell[(p, "python", method)]["success"]]
        summary.append({"scenario": "python", "method": method, "platforms": len(paired), "successes": len(ok),
                        "median_seconds_successful": sorted(r["seconds"] for r in ok)[len(ok) // 2] if ok else "",
                        "discordant_previous_only": b, "discordant_installer_only": c, "mcnemar_exact_p": round(p_value, 4)})
    bare = [r for r in rows if r["scenario"] == "bare"]
    summary.append({"scenario": "bare", "method": "installer", "platforms": len(bare),
                    "successes": sum(r["success"] for r in bare),
                    "median_seconds_successful": sorted(r["seconds"] for r in bare if r["success"])[
                        len([r for r in bare if r["success"]]) // 2] if any(r["success"] for r in bare) else ""})
    with (OUT / "F_installer_summary.csv").open("w", newline="", encoding="utf-8") as f:
        fields = list(dict.fromkeys(k for s in summary for k in s))
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary)
    print(json.dumps(summary, indent=1))

    plt.rcParams.update({"font.family": FONT, "font.size": 8, "axes.spines.top": False, "axes.spines.right": False})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=FIGSIZE, constrained_layout=True, gridspec_kw={"width_ratios": [1, 1.6]})

    # Panel A: success counts on the paired "python" scenario (+ bare machines for the installer)
    labels = ["previous\n(python run.py)", "one-click\ninstaller", "installer on\nbare OS (no Python)"]
    counts = [summary[0]["successes"], summary[1]["successes"], summary[2]["successes"]]
    totals = [summary[0]["platforms"], summary[1]["platforms"], summary[2]["platforms"]]
    colors = [COLORS["legacy"], COLORS["installer"], COLORS["bare"]]
    xs = range(3)
    ax1.bar(xs, [100 * k / n if n else 0 for k, n in zip(counts, totals)], width=BAR_WIDTH * 1.6, color=colors)
    for x, k, n in zip(xs, counts, totals):
        ax1.text(x, (100 * k / n if n else 0) + 3, f"{k}/{n}", ha="center", fontsize=7.5)
    ax1.set_xticks(list(xs), labels, fontsize=7)
    ax1.set_ylim(0, 118)
    ax1.set_ylabel("clean machines with a running app (%)")
    ax1.set_title(f"Success (paired McNemar p={p_value:.3g}, n={len(paired)} platforms)", fontsize=8.5)

    # Panel B: time per platform, failures marked
    for i, method in enumerate(("legacy", "installer")):
        for j, plat in enumerate(platforms):
            r = cell.get((plat, "python", method))
            if not r:
                continue
            x = j + (i - 0.5) * BAR_WIDTH * 1.15
            if r["success"]:
                ax2.bar(x, r["seconds"], width=BAR_WIDTH, color=COLORS[method],
                        label=METHOD_LABEL[method] if j == 0 or not ax2.get_legend_handles_labels()[1].count(METHOD_LABEL[method]) else None)
                ax2.text(x, r["seconds"] + 4, f"{r['seconds']}", ha="center", fontsize=6.5)
            else:
                ax2.text(x, 6, "failed", ha="center", va="bottom", rotation=90, fontsize=7, color="#dc2626")
    ax2.set_xticks(range(len(platforms)), platforms, fontsize=7)
    ax2.set_ylabel("seconds to a running app")
    handles, lbls = ax2.get_legend_handles_labels()
    uniq = dict(zip(lbls, handles))
    ax2.legend(uniq.values(), uniq.keys(), frameon=False, fontsize=7, loc="upper left")
    ax2.set_title("Time on a fresh machine (python3 present, single run per cell)", fontsize=8.5)
    ymax = max([r["seconds"] for r in rows if r["success"]] + [60])
    ax2.set_ylim(0, ymax * 1.25)
    for ext in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"F_installer.{ext}", dpi=FIG_DPI)
    plt.close(fig)
    print(f"results in {OUT}")


if __name__ == "__main__":
    main()
