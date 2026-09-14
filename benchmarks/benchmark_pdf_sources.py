"""Benchmark F: server-side Sci-Hub / SciDB resolution, previous resolver vs current one.

Question: when the ReviewHunter server looks for a closed-access paper on the shadow libraries,
(1) how long does it spend, (2) does it find a candidate PDF, and (3) when it finds nothing, does
it tell the user WHY (dead mirror, human-verification wall, article missing) or fail silently?

Design
    * Same DOIs for both versions (paired), resolvers run back to back per DOI, alternating the
      order (previous first on even rows) to spread any time-of-day or rate-limit drift.
    * previous = doi_hunter_web/sources.py at git PREVIOUS_COMMIT (mirrors .st/.ru/.se, SciDB with
                 annas-archive.gs, silent on bot walls)
      current  = working tree sources.py (sci-hub.fr first, alias de-duplication, bot-wall detection,
                 diagnostics through collect_notes)
    * Only the shadow-library resolvers are timed (publisher / open-access lookups are identical in
      both versions and would only add network noise).
    * Nothing is downloaded: candidate URLs are counted, PDFs are not fetched. No challenge is
      solved: walls are only detected.
    * Statistics: paired Wilcoxon signed-rank test on elapsed seconds (exact for small n) with the median
      of paired differences; normality of differences checked with
      Shapiro-Wilk to justify the non-parametric choice; "explained failure" compared with an exact
      McNemar test. n is small (network politeness), so power is low: effects are reported with
      their raw values, not only p-values.

Run (from the repo root, network required)
    <conda rh-bench python> benchmarks/benchmark_pdf_sources.py
    RH_BENCH_F=plot <python> benchmarks/benchmark_pdf_sources.py      # re-plot from CSV

Outputs (benchmarks/results): F_pdf_sources_per_doi.csv, F_pdf_sources_summary.csv,
F_pdf_sources_stats.csv, F_pdf_sources.{pdf,png,svg}
"""
# ============================================================================ PARAMETERS
REPO = r"D:\Apps\ReviewHunter"
OUT_DIR = r"D:\Apps\ReviewHunter\benchmarks\results"
PREVIOUS_COMMIT = "c03d648"
# Closed-access (or partly closed) papers; the first one is the paper reported by the user.
DOIS = [
    "10.1177/0269881107082286",          # Cornish et al. 2007, J Psychopharmacol (user report)
    "10.1016/j.drugalcdep.2017.04.019",  # Drug and Alcohol Dependence 2017 (earlier user report)
    "10.1038/nature14539",               # LeCun, Bengio, Hinton 2015, Nature
    "10.1073/pnas.79.8.2554",            # Hopfield 1982, PNAS
    "10.1113/jphysiol.1952.sp004764",    # Hodgkin & Huxley 1952, J Physiol
    "10.1126/science.275.5306.1593",     # Schultz, Dayan & Montague 1997, Science
]
# Short axis labels, same order as DOIS
DOI_LABELS = ["Cornish\n2007", "Drug Alc Dep\n2017", "LeCun\n2015", "Hopfield\n1982", "Hodgkin\n1952", "Schultz\n1997"]
PAUSE_BETWEEN_CALLS_S = 2.0          # be polite to the mirrors
ALPHA = 0.05

# Figure styling
FIG_DPI = 300
FONT = "DejaVu Sans"
COLORS = {"previous": "#8b94a9", "current": "#0284c7"}
REASON_COLORS = {"candidate found": "#16a34a", "explained": "#0284c7", "silent": "#cbd5e1"}
BAR_WIDTH = 0.32                     # thin bars
FIGSIZE = (7.2, 3.2)
# ============================================================================ IMPLEMENTATION

import csv
import importlib.util
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, REPO)  # sources.py imports doi_hunter.netinfo


def load_sources(label: str, source_text: str):
    """Import a sources.py text as an isolated module (both versions can live side by side)."""
    tmp = Path(tempfile.gettempdir()) / f"rh_bench_sources_{label}.py"
    tmp.write_text(source_text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"rh_sources_{label}", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def run_version(mod, doi: str) -> dict:
    """Time the shadow-library resolvers of one version for one DOI and collect diagnostics."""
    resolvers = [mod._scihub_candidates, mod._scidb_candidates]
    t0 = time.perf_counter()
    if hasattr(mod, "collect_notes"):
        with mod.collect_notes() as notes:
            found = list(mod.iter_pdf_urls(doi, resolvers))
        notes = list(notes)
    else:
        found = list(mod.iter_pdf_urls(doi, resolvers))
        notes = []
    elapsed = time.perf_counter() - t0
    if found:
        outcome = "candidate found"
    elif notes:
        outcome = "explained"
    else:
        outcome = "silent"
    return {"elapsed_s": round(elapsed, 3), "n_candidates": len(found), "n_notes": len(notes), "outcome": outcome,
            "walls": sum("human verification" in n for n in notes),
            "unreachable": sum(any(k in n for k in ("not reachable", "certificate", "does not resolve", "no answer", "request failed"))
                               for n in notes),
            "notes": " | ".join(notes)[:900], "first_candidate": found[0].url if found else ""}


def measure() -> list[dict]:
    previous_text = subprocess.run(["git", "-C", REPO, "show", f"{PREVIOUS_COMMIT}:doi_hunter_web/sources.py"],
                                   capture_output=True, text=True, encoding="utf-8", check=True).stdout
    versions = {"previous": load_sources("previous", previous_text),
                "current": load_sources("current", (Path(REPO) / "doi_hunter_web" / "sources.py").read_text(encoding="utf-8"))}
    rows = []
    for i, doi in enumerate(DOIS):
        order = ["previous", "current"] if i % 2 == 0 else ["current", "previous"]
        for label in order:
            res = run_version(versions[label], doi)
            rows.append({"doi": doi, "version": label, "order": order.index(label), **res})
            print(f"{label:8s} {doi:36s} {res['elapsed_s']:7.2f}s candidates={res['n_candidates']} {res['outcome']}")
            time.sleep(PAUSE_BETWEEN_CALLS_S)
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("elapsed_s",):
            r[k] = float(r[k])
        for k in ("n_candidates", "n_notes", "walls", "unreachable", "order"):
            r[k] = int(r[k])
    return rows


def statistics_for(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Summary per version + paired tests."""
    from scipy import stats

    by = {v: {r["doi"]: r for r in rows if r["version"] == v} for v in ("previous", "current")}
    dois = [d for d in DOIS if d in by["previous"] and d in by["current"]]
    summary = []
    for v in ("previous", "current"):
        rs = [by[v][d] for d in dois]
        t = [r["elapsed_s"] for r in rs]
        summary.append({
            "version": v, "n": len(rs), "median_elapsed_s": round(statistics.median(t), 2),
            "iqr_elapsed_s": round(float(stats.iqr(t)), 2), "mean_elapsed_s": round(statistics.mean(t), 2),
            "candidate_found": sum(r["outcome"] == "candidate found" for r in rs),
            "explained_failure": sum(r["outcome"] == "explained" for r in rs),
            "silent_failure": sum(r["outcome"] == "silent" for r in rs),
            "walls_detected": sum(r["walls"] for r in rs), "unreachable_reported": sum(r["unreachable"] for r in rs),
        })

    diffs = [by["current"][d]["elapsed_s"] - by["previous"][d]["elapsed_s"] for d in dois]
    test_rows = []
    if len(diffs) >= 3:
        sw = stats.shapiro(diffs)
        test_rows.append({"test": "Shapiro-Wilk on paired time differences (current - previous)", "statistic": round(sw.statistic, 4),
                          "p_value": round(sw.pvalue, 4), "n": len(diffs),
                          "interpretation": "normality not rejected" if sw.pvalue >= ALPHA else "normality rejected",
                          "note": "n is tiny: Wilcoxon is used regardless (no normality assumption)"})
    if any(diffs):
        wx = stats.wilcoxon(diffs, method="exact" if len(diffs) <= 25 else "auto")
        test_rows.append({"test": "Wilcoxon signed-rank, elapsed seconds (paired, two-sided, exact)", "statistic": round(wx.statistic, 4),
                          "p_value": round(wx.pvalue, 4), "n": len(diffs),
                          "interpretation": "significant" if wx.pvalue < ALPHA else "not significant",
                          "note": f"median paired difference {statistics.median(diffs):+.2f} s (negative = current faster)"})
    # Exact McNemar on "failure explained or candidate found" (informative outcome) vs silent.
    info_prev = [by["previous"][d]["outcome"] != "silent" for d in dois]
    info_cur = [by["current"][d]["outcome"] != "silent" for d in dois]
    b = sum(p and not c for p, c in zip(info_prev, info_cur))
    c = sum(c and not p for p, c in zip(info_prev, info_cur))
    p_mcnemar = stats.binomtest(min(b, c), b + c, 0.5).pvalue if b + c else 1.0
    test_rows.append({"test": "Exact McNemar, informative outcome (candidate or explained failure) vs silent", "statistic": f"b={b}, c={c}",
                      "p_value": round(p_mcnemar, 4), "n": len(dois),
                      "interpretation": "significant" if p_mcnemar < ALPHA else "not significant",
                      "note": "with n=6 the smallest achievable two-sided p is 0.031 (all 6 discordant in one direction)"})
    return summary, test_rows


def plot(rows: list[dict], summary: list[dict], tests: list[dict], out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": FONT, "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=FIGSIZE, gridspec_kw={"width_ratios": [2.3, 1]}, constrained_layout=True)

    # A. elapsed time per DOI (paired thin bars)
    labels = DOI_LABELS if len(DOI_LABELS) == len(DOIS) else [d.split("/", 1)[1][:10] for d in DOIS]
    x = list(range(len(DOIS)))
    top = 0.0
    for k, v in enumerate(("previous", "current")):
        vals = [next(r["elapsed_s"] for r in rows if r["doi"] == d and r["version"] == v) for d in DOIS]
        top = max(top, *vals)
        ax1.bar([i + (k - 0.5) * BAR_WIDTH for i in x], vals, BAR_WIDTH * 0.9, color=COLORS[v], label=v)
    ax1.set_xticks(x, labels, fontsize=6.5)
    ax1.set_ylim(0, top * 1.38)  # headroom so the annotation never touches a bar
    ax1.set_ylabel("Resolver time (s)")
    wil = next((t for t in tests if t["test"].startswith("Wilcoxon")), None)
    ax1.set_title("A  Time spent on Sci-Hub + SciDB per DOI", loc="left", fontsize=8.5, fontweight="bold")
    if wil:
        ax1.text(0.99, 0.97, f"Wilcoxon exact p = {wil['p_value']:.3f}\n{wil['note']}", transform=ax1.transAxes,
                 ha="right", va="top", fontsize=6.3, color="#334155")
    ax1.legend(frameon=False, fontsize=6.5, loc="upper left", ncol=2)

    # B. outcome composition
    order = ["candidate found", "explained", "silent"]
    for k, s in enumerate(summary):
        bottom = 0
        for o in order:
            n = s["candidate_found"] if o == "candidate found" else s["explained_failure"] if o == "explained" else s["silent_failure"]
            if n:
                ax2.bar(k, n, BAR_WIDTH, bottom=bottom, color=REASON_COLORS[o], label=o if k == 1 or o == "silent" else None)
                ax2.text(k, bottom + n / 2, str(n), ha="center", va="center", fontsize=6.5,
                         color="white" if o != "silent" else "#334155")
            bottom += n
    ax2.set_xticks([0, 1], [s["version"] for s in summary])
    ax2.set_xlim(-0.6, 1.6)
    ax2.set_ylabel("DOIs")
    ax2.set_ylim(0, len(DOIS) + 1.6)
    mc = next(t for t in tests if t["test"].startswith("Exact McNemar"))
    ax2.set_title("B  Outcome", loc="left", fontsize=8.5, fontweight="bold")
    ax2.text(0.5, 0.985, f"McNemar exact p = {mc['p_value']:.3f}", transform=ax2.transAxes, ha="center", va="top", fontsize=6.3, color="#334155")
    handles, labs = ax2.get_legend_handles_labels()
    uniq = dict(zip(labs, handles))
    ax2.legend(uniq.values(), uniq.keys(), frameon=False, fontsize=6, loc="upper center", bbox_to_anchor=(0.5, 0.93), ncol=1)

    for ext in ("pdf", "png", "svg"):
        fig.savefig(out / f"F_pdf_sources.{ext}", dpi=FIG_DPI)
    plt.close(fig)


def main() -> None:
    out = Path(OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    per_doi = out / "F_pdf_sources_per_doi.csv"
    if os.environ.get("RH_BENCH_F") == "plot":
        rows = read_csv(per_doi)
    else:
        rows = measure()
        write_csv(per_doi, rows)
        rows = read_csv(per_doi)
    summary, tests = statistics_for(rows)
    write_csv(out / "F_pdf_sources_summary.csv", summary)
    write_csv(out / "F_pdf_sources_stats.csv", tests)
    plot(rows, summary, tests, out)
    for s in summary:
        print(s)
    for t in tests:
        print(t)


if __name__ == "__main__":
    main()
