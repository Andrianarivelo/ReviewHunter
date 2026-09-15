"""Benchmark G: server-side PDF acquisition, previous resolver (a484a5e) vs current v3.

Question: when the user clicks "Find PDF", does the ReviewHunter SERVER actually get a PDF file, and
when it does not, does it explain why? (The browser hand-off is not measured here: it needs a real
Chrome session and the user's institutional access.)

Design
    * Paired: the same DOIs for both versions, run back to back per DOI, alternating the order
      (previous first on even rows) to spread time-of-day and rate-limit drift.
    * previous = doi_hunter_web/sources.py at git PREVIOUS_COMMIT, downloading with plain ``requests``
                 and the old random User-Agent (exactly what library.py did at that commit);
      current  = the working tree (open-access cascade, Chrome TLS impersonation, Referer, OS trust
                 store, wall classification; candidates marked needs_browser are skipped, as the
                 server skips them).
    * Every version tries its candidate URLs in order (at most MAX_CANDIDATES) until one yields real
      PDF bytes (%PDF- magic, >= MIN_PDF_BYTES). Nothing is solved: walls are only detected.
    * Metrics per DOI and version: pdf_obtained, pdf_bytes, source_used, n_candidates, outcome
      (pdf | explained | silent), elapsed seconds (resolution + download attempts).
    * Statistics: exact McNemar on pdf_obtained and on "informative" (pdf or explained); Wilcoxon
      signed-rank on elapsed seconds (Shapiro-Wilk on the paired differences justifies the
      non-parametric test); raw values are reported with the p-values (n is small on purpose:
      politeness to the sources). Caveat: the DOI set includes the papers whose failures motivated v3.

Run (from the repo root, network required; measurement uses the app's venv)
    .venv/Scripts/python.exe benchmarks/benchmark_pdf_acquisition.py            # measure -> CSV
    RH_BENCH_G=plot <python with scipy+matplotlib> benchmarks/benchmark_pdf_acquisition.py   # stats + figure

Outputs (benchmarks/results): G_pdf_acquisition_per_doi.csv, G_pdf_acquisition_summary.csv,
G_pdf_acquisition_stats.csv, G_pdf_acquisition.{pdf,png,svg}
"""
# ============================================================================ PARAMETERS
REPO = r"D:\Apps\ReviewHunter"
OUT_DIR = r"D:\Apps\ReviewHunter\benchmarks\results"
PREVIOUS_COMMIT = "a484a5e"
# Mix: closed-access classics, Elsevier papers (one with a PubMed Central author manuscript), gold OA,
# a preprint. The first three are the papers reported by the user.
DOIS = [
    ("10.1177/0269881107082286", "Cornish 2007\nJ Psychopharm", "closed"),
    ("10.1016/j.drugalcdep.2017.04.019", "Drug Alc Dep\n2017", "closed"),
    ("10.1016/j.pbb.2017.10.008", "Aarde 2017\nPBB (NIHMS)", "manuscript"),
    ("10.1038/nature14539", "LeCun 2015\nNature", "closed"),
    ("10.1126/science.275.5306.1593", "Schultz 1997\nScience", "closed"),
    ("10.1016/j.neuron.2017.06.011", "Hassabis 2017\nNeuron", "closed"),
    ("10.1016/j.cell.2019.05.031", "Stuart 2019\nCell", "closed"),
    ("10.1093/cercor/bhx179", "Schaefer 2018\nCereb Cortex", "closed"),
    ("10.1073/pnas.79.8.2554", "Hopfield 1982\nPNAS", "free"),
    ("10.1113/jphysiol.1952.sp004764", "Hodgkin 1952\nJ Physiol", "free"),
    ("10.7554/eLife.00311", "Burnley 2012\neLife", "gold OA"),
    ("10.1101/2020.03.22.002386", "Gordon 2020\nbioRxiv", "preprint"),
    ("10.1038/s41586-020-2649-2", "Harris 2020\nNature (OA)", "gold OA"),
    ("10.1371/journal.pcbi.1005423", "Friedrich 2017\nPLOS CB", "gold OA"),
]
MAX_CANDIDATES = 6                   # download attempts per DOI and version
MIN_PDF_BYTES = 10 * 1024            # smaller "PDFs" are error stubs
MAX_PDF_BYTES = 60 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 45
PAUSE_BETWEEN_CALLS_S = 2.0          # be polite to the sources
ALPHA = 0.05

# Figure styling
FIG_DPI = 300
FONT = "DejaVu Sans"
COLORS = {"previous": "#8b94a9", "current": "#0284c7"}
OUTCOME_COLORS = {"pdf": "#16a34a", "explained": "#0284c7", "silent": "#cbd5e1"}
BAR_WIDTH = 0.32                     # thin bars
FIGSIZE = (7.6, 3.4)
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

sys.path.insert(0, REPO)

OUT = Path(OUT_DIR)
PER_DOI = OUT / "G_pdf_acquisition_per_doi.csv"
SUMMARY = OUT / "G_pdf_acquisition_summary.csv"
STATS = OUT / "G_pdf_acquisition_stats.csv"
FIELDS = ["doi", "label", "access", "version", "order", "elapsed_s", "n_candidates", "n_tried", "pdf_obtained",
          "pdf_bytes", "source_used", "outcome", "n_notes", "n_browser_steps", "notes", "attempts"]


# ----------------------------------------------------------------------------- versions

def load_previous():
    """Import the previous sources.py as an isolated module (it only needs doi_hunter.netinfo, requests, bs4)."""
    text = subprocess.run(["git", "-C", REPO, "show", f"{PREVIOUS_COMMIT}:doi_hunter_web/sources.py"],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout
    tmp = Path(tempfile.gettempdir()) / "rh_bench_sources_previous.py"
    tmp.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rh_sources_previous", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def download_previous(url: str) -> tuple[bool, int, str]:
    """What library.py did at PREVIOUS_COMMIT: requests + random old User-Agent, %PDF check."""
    import requests

    from doi_hunter.netinfo import get_headers

    try:
        with requests.get(url, headers=get_headers(), stream=True, timeout=(15, DOWNLOAD_TIMEOUT_S), allow_redirects=True) as r:
            r.raise_for_status()
            head, size = b"", 0
            for chunk in r.iter_content(64 * 1024):
                if len(head) < 1024:
                    head += chunk[: 1024 - len(head)]
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    return False, size, "too large"
        if b"%PDF" not in head:
            return False, size, "response was not a PDF"
        return size >= MIN_PDF_BYTES, size, "" if size >= MIN_PDF_BYTES else "tiny PDF"
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {str(e)[:90]}"


def download_current(cand) -> tuple[bool, int, str]:
    """What library.py does now: browser-like transport, Referer, wall classification."""
    from doi_hunter_web import transport
    from doi_hunter_web.transport import classify_challenge

    tmp = Path(tempfile.gettempdir()) / "rh_bench_current.part"
    try:
        r = transport.download(cand.url, tmp, referer=cand.referer or None, timeout=DOWNLOAD_TIMEOUT_S,
                               shadow="Sci-Hub" in cand.source or "SciDB" in cand.source, max_bytes=MAX_PDF_BYTES)
        size = tmp.stat().st_size if tmp.exists() else 0
        if r.content[:5] != b"%PDF-":
            text = tmp.read_bytes()[:20000].decode("utf-8", errors="replace") if tmp.exists() else ""
            ch = classify_challenge(text, r.status_code)
            return False, size, f"{ch.label} instead of the PDF" if ch else (f"HTTP {r.status_code}" if r.status_code >= 400 else "response was not a PDF")
        if r.status_code >= 400:
            return False, size, f"HTTP {r.status_code}"
        return size >= MIN_PDF_BYTES, size, "" if size >= MIN_PDF_BYTES else "tiny PDF"
    except transport.TransportError as e:
        return False, 0, transport.describe_failure(e, cand.url, DOWNLOAD_TIMEOUT_S)[:110]
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {str(e)[:90]}"
    finally:
        if tmp.exists():
            tmp.unlink()


def run_version(label: str, mod, doi: str) -> dict:
    """Resolve + try to download for one version and one DOI."""
    t0 = time.perf_counter()
    notes, steps = [], []
    if label == "current":
        with mod.collect_notes() as n, mod.collect_browser_steps() as s:
            found = list(mod.iter_pdf_urls(doi))
        notes, steps = list(n), list(s)
    else:
        with mod.collect_notes() as n:
            found = list(mod.iter_pdf_urls(doi))
        notes = list(n)
    attempts, obtained, size, used = [], False, 0, ""
    tried = 0
    for cand in found:
        if tried >= MAX_CANDIDATES:
            break
        if getattr(cand, "needs_browser", False):
            attempts.append(f"{cand.source}: left to the browser")
            continue
        tried += 1
        ok, n_bytes, err = download_current(cand) if label == "current" else download_previous(cand.url)
        if ok:
            obtained, size, used = True, n_bytes, cand.source
            attempts.append(f"{cand.source}: PDF {n_bytes} B")
            break
        attempts.append(f"{cand.source}: {err}")
    elapsed = time.perf_counter() - t0
    outcome = "pdf" if obtained else ("explained" if (notes or attempts) else "silent")
    return {"elapsed_s": round(elapsed, 2), "n_candidates": len(found), "n_tried": tried, "pdf_obtained": int(obtained),
            "pdf_bytes": size, "source_used": used, "outcome": outcome, "n_notes": len(notes), "n_browser_steps": len(steps),
            "notes": " | ".join(notes)[:900], "attempts": " | ".join(attempts)[:900]}


def measure() -> list[dict]:
    from doi_hunter_web import sources as current

    previous = load_previous()
    versions = {"previous": previous, "current": current}
    rows = []
    for i, (doi, label, access) in enumerate(DOIS):
        order = ["previous", "current"] if i % 2 == 0 else ["current", "previous"]
        for v in order:
            res = run_version(v, versions[v], doi)
            rows.append({"doi": doi, "label": label.replace("\n", " "), "access": access, "version": v, "order": order.index(v), **res})
            print(f"{v:8s} {doi:36s} {res['elapsed_s']:7.1f}s cand={res['n_candidates']:2d} {res['outcome']:9s} {res['source_used']}", flush=True)
            time.sleep(PAUSE_BETWEEN_CALLS_S)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(PER_DOI, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {PER_DOI}")
    return rows


# ----------------------------------------------------------------------------- statistics + figure

def mcnemar_exact(b: int, c: int) -> float:
    """Exact McNemar p-value (two-sided binomial on the discordant pairs)."""
    from scipy.stats import binomtest

    n = b + c
    if n == 0:
        return 1.0
    return float(binomtest(min(b, c), n, 0.5, alternative="two-sided").pvalue)


def analyse(rows: list[dict]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.stats import shapiro, wilcoxon

    plt.rcParams.update({"font.family": FONT, "font.size": 8, "axes.spines.top": False, "axes.spines.right": False})
    by = {(r["doi"], r["version"]): r for r in rows}
    dois = [d for d, _, _ in DOIS if (d, "previous") in by and (d, "current") in by]
    labels = {d: lab for d, lab, _ in DOIS}

    def val(d, v, key):
        return by[(d, v)][key]

    summary, stats = [], []
    for v in ("previous", "current"):
        sub = [by[(d, v)] for d in dois]
        el = [float(r["elapsed_s"]) for r in sub]
        summary.append({"version": v, "n_dois": len(sub), "pdf_obtained": sum(int(r["pdf_obtained"]) for r in sub),
                        "explained": sum(r["outcome"] == "explained" for r in sub), "silent": sum(r["outcome"] == "silent" for r in sub),
                        "median_elapsed_s": round(statistics.median(el), 2), "mean_elapsed_s": round(statistics.mean(el), 2),
                        "mean_candidates": round(statistics.mean(int(r["n_candidates"]) for r in sub), 2)})
    # McNemar: pdf obtained; informative (pdf or explained)
    for name, fn in (("pdf_obtained", lambda r: int(r["pdf_obtained"]) == 1), ("informative", lambda r: r["outcome"] in ("pdf", "explained"))):
        b = sum(fn(by[(d, "previous")]) and not fn(by[(d, "current")]) for d in dois)
        c = sum(fn(by[(d, "current")]) and not fn(by[(d, "previous")]) for d in dois)
        p = mcnemar_exact(b, c)
        stats.append({"metric": name, "test": "exact McNemar", "previous_only": b, "current_only": c, "n": len(dois),
                      "statistic": "", "p_value": round(p, 4), "significant": p < ALPHA, "assumption_check": "paired binary; discordant pairs only"})
    diffs = [float(val(d, "current", "elapsed_s")) - float(val(d, "previous", "elapsed_s")) for d in dois]
    sw_p = float(shapiro(diffs).pvalue) if len(diffs) >= 3 and len(set(diffs)) > 1 else float("nan")
    try:
        w = wilcoxon(diffs, zero_method="wilcox", alternative="two-sided", method="exact" if len(diffs) <= 25 else "auto")
        w_stat, w_p = float(w.statistic), float(w.pvalue)
    except ValueError:
        w_stat, w_p = float("nan"), 1.0
    stats.append({"metric": "elapsed_s (current - previous)", "test": "Wilcoxon signed-rank (exact)", "previous_only": "", "current_only": "",
                  "n": len(diffs), "statistic": round(w_stat, 3), "p_value": round(w_p, 4), "significant": w_p < ALPHA,
                  "assumption_check": f"Shapiro-Wilk on differences p={sw_p:.3f} ({'normal' if sw_p >= ALPHA else 'non-normal'}); median diff={statistics.median(diffs):.2f} s"})
    with open(SUMMARY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    with open(STATS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(stats[0].keys()))
        w.writeheader()
        w.writerows(stats)
    for s in summary:
        print(s)
    for s in stats:
        print(s)

    # ---- figure: (A) outcome per DOI, (B) PDFs obtained, (C) paired elapsed time
    fig, axes = plt.subplots(1, 3, figsize=FIGSIZE, gridspec_kw={"width_ratios": [3.2, 1.0, 1.3]})
    ax = axes[0]
    x = list(range(len(dois)))
    for j, v in enumerate(("previous", "current")):
        offs = (j - 0.5) * (BAR_WIDTH + 0.04)
        for i, d in enumerate(dois):
            r = by[(d, v)]
            ax.bar(i + offs, 1, width=BAR_WIDTH, color=OUTCOME_COLORS[r["outcome"]], edgecolor=COLORS[v], linewidth=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels([labels[d] for d in dois], rotation=90, fontsize=5.5)
    ax.set_yticks([])
    ax.set_ylim(0, 1.25)
    ax.set_title("A  Outcome per DOI (left: previous, right: current)", loc="left", fontsize=8)
    from matplotlib.patches import Patch

    ax.legend(handles=[Patch(color=OUTCOME_COLORS[k], label=k) for k in ("pdf", "explained", "silent")], loc="upper left",
              ncol=3, fontsize=6, frameon=False, bbox_to_anchor=(0, 1.0))
    ax = axes[1]
    counts = [s["pdf_obtained"] for s in summary]
    ax.bar([0, 1], counts, width=BAR_WIDTH, color=[COLORS["previous"], COLORS["current"]])
    for i, c in enumerate(counts):
        ax.text(i, c + 0.15, str(c), ha="center", fontsize=7)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["previous", "current"], fontsize=7)
    ax.set_ylim(0, len(dois) + 1.5)
    ax.set_ylabel(f"PDFs obtained by the server (of {len(dois)})", fontsize=7)
    p_pdf = [s for s in stats if s["metric"] == "pdf_obtained"][0]["p_value"]
    ax.set_title(f"B  McNemar p={p_pdf:.3g}", loc="left", fontsize=8)
    ax = axes[2]
    for d in dois:
        ax.plot([0, 1], [float(val(d, "previous", "elapsed_s")), float(val(d, "current", "elapsed_s"))], color="#94a3b8", linewidth=0.6, alpha=0.8)
    for j, v in enumerate(("previous", "current")):
        ys = [float(val(d, v, "elapsed_s")) for d in dois]
        ax.scatter([j] * len(ys), ys, s=10, color=COLORS[v], zorder=3)
        ax.hlines(statistics.median(ys), j - 0.18, j + 0.18, color=COLORS[v], linewidth=1.4)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["previous", "current"], fontsize=7)
    ax.set_xlim(-0.5, 1.5)
    ax.set_ylabel("seconds per DOI (resolve + download)", fontsize=7)
    p_t = [s for s in stats if s["metric"].startswith("elapsed")][0]["p_value"]
    ax.set_title(f"C  Wilcoxon p={p_t:.3g}", loc="left", fontsize=8)
    fig.suptitle(f"Benchmark G: server-side PDF acquisition, {PREVIOUS_COMMIT} vs current (n={len(dois)} DOIs, paired)", fontsize=8.5, y=1.02)
    fig.tight_layout()
    for ext in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"G_pdf_acquisition.{ext}", dpi=FIG_DPI, bbox_inches="tight")
    print(f"figure -> {OUT / 'G_pdf_acquisition.png'}")


def main() -> None:
    if os.environ.get("RH_BENCH_G") == "plot":
        with open(PER_DOI, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        analyse(rows)
        return
    measure()
    try:
        import matplotlib  # noqa: F401
        import scipy  # noqa: F401
    except ImportError:
        print("scipy / matplotlib not in this interpreter: run again with RH_BENCH_G=plot under an environment that has them")
        return
    with open(PER_DOI, encoding="utf-8", newline="") as f:
        analyse(list(csv.DictReader(f)))


if __name__ == "__main__":
    main()
