"""Benchmark ReviewHunter v1 (commit ba7d079) against v2 (working tree).

Three analyses, each written to CSV and summarised in publication-style figures (PDF, PNG, SVG):

A. Legal PDF availability on a random DOI sample (paired design)
   v1 resolver: publisher landing page parsing only (the legal part of v1).
   v2 resolver: open-access indexes (OpenAlex, Europe PMC) THEN publisher page parsing.
   Sci-Hub / Anna's Archive resolvers exist in both versions but are deliberately NOT exercised.
   A DOI counts as a success when a candidate URL streams a real PDF (%PDF magic bytes).
   Test: exact McNemar test (binomial test on discordant pairs), Wilson 95% CIs.

B. API latency on synthetic libraries of increasing size (independent samples per endpoint)
   Same generated records are ingested by each version's own `library.upsert_item`.
   Distributions are checked with Shapiro-Wilk (latencies are typically right-skewed), hence the
   non-parametric Mann-Whitney U test, rank-biserial effect size, Holm correction across comparisons.

C. Feature coverage matrix: v1, v2 and Zotero 7 (documented features, 1 = available).

Run with the isolated analysis environment (pandas, scipy, matplotlib):
    C:/Users/andry/miniconda3/envs/rh-bench/python.exe benchmarks/benchmark_v1_v2.py
Workers that import application code run with the application venv (APP_PYTHON).
"""
from __future__ import annotations

# =====================================================================================
# PARAMETERS (edit here)
# =====================================================================================
REPO_V2 = r"D:\Apps\ReviewHunter"
REPO_V1 = r"C:\Users\andry\AppData\Local\Temp\claude\rh-v1"          # git worktree at ba7d079
APP_PYTHON = r"D:\Apps\ReviewHunter\.venv\Scripts\python.exe"
WORK_DIR = r"C:\Users\andry\AppData\Local\Temp\claude\rh-bench"       # throw-away libraries
OUT_DIR = r"D:\Apps\ReviewHunter\benchmarks\results"

RUN_PDF = __import__("os").environ.get("RH_BENCH_PDF", "1") == "1"          # env overrides for partial reruns
RUN_LATENCY = __import__("os").environ.get("RH_BENCH_LATENCY", "1") == "1"
SEED = 42
N_DOIS = 60                        # random DOI sample size (OpenAlex sample API)
DOI_FROM_YEAR = 2010
MAX_CANDIDATES = 3                 # candidate URLs tried per DOI and version
REQUEST_TIMEOUT = (10, 25)

LIBRARY_SIZES = [200, 2000]        # synthetic library sizes
REPEATS = 40                       # timed requests per endpoint
WARMUP = 5
PORTS = {"v1": 8811, "v2": 8812}
ALPHA = 0.05

# Figure styling
FIG_DPI = 300
FONT = "DejaVu Sans"
COLORS = {"v1": "#8b94a9", "v2": "#0284c7", "zotero": "#14b8a6"}
BAR_WIDTH = 0.28                   # thin bars

# =====================================================================================
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


# -------------------------------------------------------------------------------------
# Worker mode (runs inside the application venv, imports one version's code)
# -------------------------------------------------------------------------------------
def worker_main(argv: list[str]) -> None:
    """Entry for subprocesses: `--worker seed|pdf --version v1|v2 --root REPO ...`."""
    args = dict(zip(argv[::2], argv[1::2]))
    sys.path.insert(0, args["--root"])
    if args["--worker"] == "seed":
        os.environ["REVIEWHUNTER_HOME"] = args["--home"]
        from doi_hunter_web import library  # noqa: E402

        rng = random.Random(int(args["--seed"]))
        words = ("neuron dopamine cortex hippocampus spike plasticity reward decision model network learning "
                 "memory attention oscillation manifold synapse inhibition coding behaviour circuit dynamics").split()
        journals = ["Neuron", "Nature Neuroscience", "eLife", "PLOS Computational Biology", "Journal of Neuroscience"]
        for i in range(int(args["--n"])):
            title = " ".join(rng.choice(words) for _ in range(rng.randint(5, 11))).capitalize()
            authors = [f"Author{rng.randint(1, 400)}, {chr(65 + rng.randint(0, 25))}." for _ in range(rng.randint(1, 8))]
            library.upsert_item({
                "title": f"{title} {i}", "authors": authors, "year": rng.randint(1990, 2025),
                "journal": rng.choice(journals), "doi": f"10.9999/bench.{i}",
                "abstract": " ".join(rng.choice(words) for _ in range(120)),
                "keywords": rng.sample(words, 4), "subjects": rng.sample(words, 2), "status": "metadata_only",
            })
        print(json.dumps({"seeded": int(args["--n"])}))
        return

    if args["--worker"] == "pdf":
        import requests

        from doi_hunter.netinfo import get_headers
        from doi_hunter_web import sources

        resolvers = [sources._publisher_pdf_candidates]
        if hasattr(sources, "_open_access_candidates") and args["--version"] == "v2":
            resolvers.insert(0, sources._open_access_candidates)
        results = []
        for doi in json.loads(Path(args["--dois"]).read_text()):
            t0 = time.perf_counter()
            ok, src, tried, err = False, "", 0, ""
            try:
                for cand in sources.iter_pdf_urls(doi, resolvers):
                    if tried >= MAX_CANDIDATES:
                        break
                    tried += 1
                    try:
                        with requests.get(cand.url, headers=get_headers(), stream=True, timeout=REQUEST_TIMEOUT,
                                          allow_redirects=True) as r:
                            head = next(r.iter_content(2048), b"") if r.ok else b""
                        if b"%PDF" in head[:1024]:
                            ok, src = True, cand.source
                            break
                    except Exception as e:  # network errors count as a failed candidate
                        err = type(e).__name__
            except Exception as e:
                err = type(e).__name__
            results.append({"doi": doi, "success": ok, "source": src, "candidates_tried": tried,
                            "seconds": round(time.perf_counter() - t0, 2), "error": err})
        print(json.dumps(results))
        return
    raise SystemExit("unknown worker")


if "--worker" in sys.argv:
    worker_main(sys.argv[1:])
    sys.exit(0)

# -------------------------------------------------------------------------------------
# Orchestrator (analysis environment)
# -------------------------------------------------------------------------------------
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402
from scipy import stats  # noqa: E402

plt.rcParams.update({"font.family": FONT, "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
                     "legend.frameon": False, "savefig.bbox": "tight"})
OUT = Path(OUT_DIR)
OUT.mkdir(parents=True, exist_ok=True)
ROOTS = {"v1": REPO_V1, "v2": REPO_V2}


def save_fig(fig, name: str) -> None:
    """Every figure in PDF, PNG and SVG."""
    for ext in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=FIG_DPI)
    plt.close(fig)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def stars(p: float) -> str:
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "n.s."


def holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni step-down adjustment."""
    order = np.argsort(pvals)
    m = len(pvals)
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[idx]))
        adjusted[idx] = running
    return adjusted.tolist()


# ------------------------------------------------------------------ A. PDF availability
def run_pdf_benchmark() -> None:
    print("[A] sampling DOIs from OpenAlex ...")
    r = requests.get("https://api.openalex.org/works", timeout=30, params={
        "sample": N_DOIS, "seed": SEED, "per-page": N_DOIS,
        "filter": f"type:article,has_doi:true,from_publication_date:{DOI_FROM_YEAR}-01-01",
        "select": "doi,open_access,publication_year,primary_location"})
    r.raise_for_status()
    works = r.json()["results"]
    meta = pd.DataFrame([{
        "doi": w["doi"].replace("https://doi.org/", "").lower(), "year": w.get("publication_year"),
        "openalex_is_oa": (w.get("open_access") or {}).get("is_oa"),
        "openalex_oa_status": (w.get("open_access") or {}).get("oa_status"),
        "publisher_source": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
    } for w in works if w.get("doi")])
    doi_file = Path(WORK_DIR) / "dois.json"
    doi_file.parent.mkdir(parents=True, exist_ok=True)
    doi_file.write_text(json.dumps(meta["doi"].tolist()))

    frames = []
    for version in ("v1", "v2"):
        print(f"[A] resolving with {version} ({len(meta)} DOIs, sequential, polite) ...")
        out = subprocess.run([APP_PYTHON, __file__, "--worker", "pdf", "--version", version, "--root", ROOTS[version],
                              "--dois", str(doi_file)], capture_output=True, text=True, timeout=7200, cwd=ROOTS[version])
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-2000:])
        df = pd.DataFrame(json.loads(out.stdout.strip().splitlines()[-1]))
        df["version"] = version
        frames.append(df)
    long = pd.concat(frames).merge(meta, on="doi")
    long.to_csv(OUT / "A_pdf_availability_per_doi.csv", index=False)

    wide = long.pivot(index="doi", columns="version", values="success")
    b = int((wide["v1"] & ~wide["v2"]).sum())    # v1 only
    c = int((~wide["v1"] & wide["v2"]).sum())    # v2 only
    both = int((wide["v1"] & wide["v2"]).sum())
    neither = int((~wide["v1"] & ~wide["v2"]).sum())
    p_mcnemar = stats.binomtest(min(b, c), b + c, 0.5).pvalue if b + c else 1.0
    n = len(wide)
    rows = []
    for v in ("v1", "v2"):
        k = int(wide[v].sum())
        lo, hi = wilson(k, n)
        rows.append({"version": v, "successes": k, "n": n, "rate": k / n, "ci95_low": lo, "ci95_high": hi})
    summary = pd.DataFrame(rows)
    summary["mcnemar_exact_p"] = p_mcnemar
    summary["discordant_v1_only"] = b
    summary["discordant_v2_only"] = c
    summary["concordant_both"] = both
    summary["concordant_neither"] = neither
    summary.to_csv(OUT / "A_pdf_availability_summary.csv", index=False)
    by_source = long[long.success].groupby(["version", "source"]).size().rename("count").reset_index()
    by_source.to_csv(OUT / "A_pdf_sources.csv", index=False)
    print(summary.to_string(index=False))
    plot_pdf_figure()


def plot_pdf_figure() -> None:
    """Figure A from the saved CSVs (so styling can be changed without re-querying publishers)."""
    from matplotlib.ticker import MaxNLocator

    summary = pd.read_csv(OUT / "A_pdf_availability_summary.csv")
    by_source = pd.read_csv(OUT / "A_pdf_sources.csv")
    p_mcnemar = float(summary.mcnemar_exact_p.iloc[0])
    n = int(summary.n.iloc[0])
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.4), gridspec_kw={"width_ratios": [1, 1.5]})
    ax = axes[0]
    for i, row in summary.iterrows():
        ax.bar(i, row.rate * 100, width=BAR_WIDTH, color=COLORS[row.version])
        ax.errorbar(i, row.rate * 100, yerr=[[100 * (row.rate - row.ci95_low)], [100 * (row.ci95_high - row.rate)]],
                    color="black", lw=0.7, capsize=2)
        ax.text(i, row.ci95_high * 100 + 3, f"{row.successes}/{row.n}", ha="center", fontsize=7)
    top = summary.ci95_high.max() * 100 + 14
    ax.plot([0, 0, 1, 1], [top - 3, top, top, top - 3], color="black", lw=0.6)
    ax.text(0.5, top + 1.5, f"McNemar exact p={p_mcnemar:.3g} {stars(p_mcnemar)}", ha="center", fontsize=6.5)
    ax.set_xticks([0, 1], ["v1\npublisher", "v2\nOA + publisher"])
    ax.set_ylabel("Legal PDF found (% of DOIs)")
    ax.set_ylim(0, max(100, top + 8))
    ax.set_title(f"A. PDF availability (n={n} random DOIs)", fontsize=8, loc="left")

    ax = axes[1]
    pivot = by_source.pivot(index="source", columns="version", values="count").fillna(0)
    pivot = pivot.loc[pivot.sum(axis=1).sort_values().index]
    y = np.arange(len(pivot))
    for j, v in enumerate(("v1", "v2")):
        vals = pivot[v] if v in pivot else np.zeros(len(pivot))
        ax.barh(y + (j - 0.5) * BAR_WIDTH, vals, height=BAR_WIDTH, color=COLORS[v], label=v)
    ax.set_yticks(y, [s[:34] for s in pivot.index], fontsize=6.5)
    ax.set_xlabel("PDFs obtained per source")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(fontsize=7, loc="lower right")
    ax.set_title("Source of the successful PDF", fontsize=8, loc="left")
    fig.tight_layout()
    save_fig(fig, "A_pdf_availability")


# ------------------------------------------------------------------ B. latency
ENDPOINTS = [
    ("list all", "/api/items?limit={n}"),
    ("full-text search", "/api/items?q=dopamine&mode=fields&limit={n}"),
    ("get item", "/api/items/{mid}"),
    ("stats", "/api/stats"),
    ("facets", "/api/facets"),
]


def start_server(version: str, home: Path, port: int) -> subprocess.Popen:
    cmd = [APP_PYTHON, "-m", "doi_hunter_web", "--no-browser", "--port", str(port)]
    if version == "v2":
        cmd.append("--no-https")
    env = {**os.environ, "REVIEWHUNTER_HOME": str(home), "REVIEWHUNTER_PORT": str(port)}
    proc = subprocess.Popen(cmd, cwd=ROOTS[version], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1).ok:
                return proc
        except requests.RequestException:
            time.sleep(0.4)
    proc.kill()
    raise RuntimeError(f"{version} server did not start on {port}")


def run_latency_benchmark() -> None:
    records = []
    for n in LIBRARY_SIZES:
        for version in ("v1", "v2"):
            home = Path(WORK_DIR) / f"{version}-n{n}"
            shutil.rmtree(home, ignore_errors=True)
            home.mkdir(parents=True)
            print(f"[B] seeding {version} with {n} items ...")
            out = subprocess.run([APP_PYTHON, __file__, "--worker", "seed", "--version", version, "--root",
                                  ROOTS[version], "--home", str(home), "--n", str(n), "--seed", str(SEED)],
                                 capture_output=True, text=True, timeout=1800, cwd=ROOTS[version])
            if out.returncode != 0:
                raise RuntimeError(out.stderr[-2000:])
            proc = start_server(version, home, PORTS[version])
            try:
                base = f"http://127.0.0.1:{PORTS[version]}"
                session = requests.Session()
                for label, tmpl in ENDPOINTS:
                    url = base + tmpl.format(n=n, mid=max(1, n // 2))
                    for _ in range(WARMUP):
                        session.get(url, timeout=60).raise_for_status()
                    for rep in range(REPEATS):
                        t0 = time.perf_counter()
                        resp = session.get(url, timeout=60)
                        dt = (time.perf_counter() - t0) * 1000
                        resp.raise_for_status()
                        records.append({"library_size": n, "version": version, "endpoint": label, "rep": rep,
                                        "ms": dt, "bytes": len(resp.content)})
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    raw = pd.DataFrame(records)
    raw.to_csv(OUT / "B_latency_raw.csv", index=False)

    rows = []
    for (n, ep), g in raw.groupby(["library_size", "endpoint"], sort=False):
        a = g[g.version == "v1"].ms.to_numpy()
        b = g[g.version == "v2"].ms.to_numpy()
        u, p = stats.mannwhitneyu(b, a, alternative="two-sided")
        rows.append({
            "library_size": n, "endpoint": ep,
            "v1_median_ms": np.median(a), "v1_iqr_ms": np.subtract(*np.percentile(a, [75, 25])),
            "v2_median_ms": np.median(b), "v2_iqr_ms": np.subtract(*np.percentile(b, [75, 25])),
            "ratio_v2_over_v1": np.median(b) / np.median(a),
            "v1_shapiro_p": stats.shapiro(a).pvalue, "v2_shapiro_p": stats.shapiro(b).pvalue,
            "mannwhitney_U": u, "p_raw": p, "rank_biserial_r": 2 * u / (len(a) * len(b)) - 1,
            "v1_payload_kb": g[g.version == "v1"].bytes.mean() / 1024, "v2_payload_kb": g[g.version == "v2"].bytes.mean() / 1024,
        })
    summary = pd.DataFrame(rows)
    summary["p_holm"] = holm(summary.p_raw.tolist())
    summary["significant"] = summary.p_holm < ALPHA
    summary.to_csv(OUT / "B_latency_summary.csv", index=False)
    print(summary[["library_size", "endpoint", "v1_median_ms", "v2_median_ms", "ratio_v2_over_v1", "p_holm"]].to_string(index=False))

    # Figure B: one panel per endpoint, thin boxplots per library size, log scale
    eps = [e[0] for e in ENDPOINTS]
    fig, axes = plt.subplots(1, len(eps), figsize=(7.2, 2.3), sharey=False)
    for ax, ep in zip(axes, eps):
        positions, data, cols = [], [], []
        for i, n in enumerate(LIBRARY_SIZES):
            for j, v in enumerate(("v1", "v2")):
                positions.append(i + (j - 0.5) * 0.34)
                data.append(raw[(raw.endpoint == ep) & (raw.library_size == n) & (raw.version == v)].ms)
                cols.append(COLORS[v])
        bp = ax.boxplot(data, positions=positions, widths=0.22, patch_artist=True, showfliers=False,
                        medianprops={"color": "black", "lw": 0.8}, whiskerprops={"lw": 0.6}, capprops={"lw": 0.6},
                        boxprops={"lw": 0.6})
        for patch, col in zip(bp["boxes"], cols):
            patch.set_facecolor(col)
            patch.set_alpha(0.85)
        ax.set_yscale("log")
        ax.set_xticks(range(len(LIBRARY_SIZES)), [f"n={n}" for n in LIBRARY_SIZES], fontsize=6.5)
        ax.set_title(ep, fontsize=7.5)
        ymax = max(d.max() for d in data)
        for i, n in enumerate(LIBRARY_SIZES):
            row = summary[(summary.endpoint == ep) & (summary.library_size == n)].iloc[0]
            ax.text(i, ymax * 1.35, f"x{row.ratio_v2_over_v1:.2f}\n{stars(row.p_holm)}", ha="center", fontsize=5.8)
        ax.set_ylim(top=ymax * 3.2)
    axes[0].set_ylabel("Latency (ms, log)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[v]) for v in ("v1", "v2")]
    fig.legend(handles, ["v1", "v2"], loc="upper right", ncol=2, fontsize=7, bbox_to_anchor=(1.0, 1.08))
    fig.suptitle("B. API latency (median ratio v2/v1, Mann-Whitney U, Holm-corrected)", fontsize=8, x=0.01,
                 ha="left", y=1.08)
    fig.tight_layout()
    save_fig(fig, "B_api_latency")


ITERATIONS = [  # (file prefix, label, colour) - archived runs of the v2 optimisation loop, then the current run
    ("iter1_", "v2 it.1 per-item queries", "#bae6fd"),
    ("iter2_", "v2 it.2 batched tags/counts", "#38bdf8"),
    ("", "v2 it.3 + column select, direct JSON", COLORS["v2"]),
]


def plot_iterations() -> None:
    """D. Version history of v2 latency against v1 (medians with IQR/2 error bars, thin bars, log scale).

    it.1: decoration with 2 queries per item (N+1). it.2: grouped tag/count queries + compact payload.
    it.3: SELECT of list columns only + JSONResponse instead of FastAPI's jsonable_encoder (profiled).
    """
    frames = []
    for prefix, label, _col in ITERATIONS:
        f = OUT / f"{prefix}B_latency_summary.csv"
        if not f.exists():
            return
        d = pd.read_csv(f)
        d["iteration"] = label
        frames.append(d)
    allruns = pd.concat(frames)
    allruns.to_csv(OUT / "D_latency_iterations.csv", index=False)
    current = frames[-1]
    sizes = sorted(current.library_size.unique())
    fig, axes = plt.subplots(1, len(sizes), figsize=(7.4, 2.5), sharey=True)
    series = [("v1 (current run)", current, "v1_median_ms", "v1_iqr_ms", COLORS["v1"])]
    series += [(label, fr, "v2_median_ms", "v2_iqr_ms", col) for (prefix, label, col), fr in zip(ITERATIONS, frames)]
    width = 0.8 / len(series)
    for ax, n in zip(np.atleast_1d(axes), sizes):
        eps = current[current.library_size == n].endpoint.tolist()
        x = np.arange(len(eps))
        for k, (label, fr, med, iqr, col) in enumerate(series):
            sub = fr[fr.library_size == n].set_index("endpoint").loc[eps]
            ax.bar(x + (k - (len(series) - 1) / 2) * width, sub[med], width=width * 0.9, color=col, label=label,
                   yerr=sub[iqr] / 2, error_kw={"lw": 0.4, "capsize": 1})
        cur = current[current.library_size == n].set_index("endpoint").loc[eps]
        top = max(fr[fr.library_size == n][["v1_median_ms", "v2_median_ms"]].to_numpy().max() for fr in frames)
        for i, ep in enumerate(eps):
            ax.text(i, top * 2.2, f"x{cur.loc[ep, 'ratio_v2_over_v1']:.2f}\n{stars(cur.loc[ep, 'p_holm'])}",
                    ha="center", fontsize=5.6)
        ax.set_yscale("log")
        ax.set_ylim(top=top * 6)
        ax.set_xticks(x, eps, rotation=25, ha="right", fontsize=6.5)
        ax.set_title(f"library of {n} items", fontsize=7.5)
    np.atleast_1d(axes)[0].set_ylabel("Median latency (ms, log)")
    fig.legend(*np.atleast_1d(axes)[0].get_legend_handles_labels(), loc="lower center", ncol=4, fontsize=6.3,
               bbox_to_anchor=(0.5, -0.12))
    fig.suptitle("D. v2 optimisation history (labels: latest v2/v1 median ratio, Holm-corrected Mann-Whitney)",
                 fontsize=8, x=0.01, ha="left")
    fig.tight_layout()
    save_fig(fig, "D_latency_iterations")


# ------------------------------------------------------------------ C. feature coverage
FEATURES = [
    # (feature, v1, v2, zotero7)
    ("Online discovery (CrossRef, authors, books)", 1, 1, 0),
    ("Concept map of subjects", 1, 1, 0),
    ("Automatic PDF finding (publisher)", 1, 1, 1),
    ("Legal open-access PDF finding (OpenAlex / Europe PMC)", 0, 1, 1),
    ("Zotero item types + typed field editor", 0, 1, 1),
    ("Creators with roles", 0, 1, 1),
    ("Nested collections", 0, 1, 1),
    ("Tags with colors + tag selector", 0, 1, 1),
    ("Rich-text notes (child + standalone)", 0, 1, 1),
    ("Multiple attachments, links, snapshots", 0, 1, 1),
    ("Built-in PDF reader with annotations", 0, 1, 1),
    ("Full-text PDF search", 0, 1, 1),
    ("Advanced + saved searches", 0, 1, 1),
    ("Duplicate detection and merge", 0, 1, 1),
    ("Trash with restore", 0, 1, 1),
    ("Related items", 0, 1, 1),
    ("CSL citations and bibliographies", 0, 1, 1),
    ("Citation style installer", 0, 1, 1),
    ("Import BibTeX / RIS / CSL-JSON", 0, 1, 1),
    ("Export BibTeX / BibLaTeX / RIS / CSL-JSON / CSV", 0, 1, 1),
    ("Word bibliography sources export", 0, 1, 0),
    ("Add by DOI / ISBN / PMID / arXiv", 0, 1, 1),
    ("Retrieve metadata from PDF", 0, 1, 1),
    ("Citation keys (Better BibTeX style)", 0, 1, 1),
    ("Migrate a Zotero library", 0, 1, 0),
    ("Browser connector", 0, 1, 1),
    ("Word citation add-in", 0, 1, 1),
    ("Google Drive storage + backups", 0, 1, 0),
    ("Verified automatic backups with restore", 0, 1, 0),
    ("Localhost CSRF / DNS-rebinding guard", 0, 1, 1),
    ("Cloud sync across devices (native)", 0, 0, 1),
    ("Group libraries", 0, 0, 1),
    ("RSS feeds", 0, 0, 1),
]


def run_feature_matrix() -> None:
    df = pd.DataFrame(FEATURES, columns=["feature", "v1", "v2", "zotero7"])
    df.to_csv(OUT / "C_feature_coverage.csv", index=False)
    totals = df[["v1", "v2", "zotero7"]].sum()
    pd.DataFrame({"system": totals.index, "features": totals.values, "of": len(df)}).to_csv(
        OUT / "C_feature_totals.csv", index=False)
    fig, ax = plt.subplots(figsize=(4.6, 0.19 * len(df) + 0.8))
    for j, col in enumerate(("v1", "v2", "zotero7")):
        colour = COLORS["zotero" if col == "zotero7" else col]
        for i, val in enumerate(df[col]):
            ax.scatter(j, i, s=26, marker="o", facecolor=colour if val else "white", edgecolor=colour, lw=0.8)
    ax.set_xticks([0, 1, 2], [f"v1\n{totals.v1}/{len(df)}", f"v2\n{totals.v2}/{len(df)}", f"Zotero 7\n{totals.zotero7}/{len(df)}"])
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(df)), df.feature, fontsize=6.3)
    ax.set_ylim(len(df) - 0.5, -0.5)
    ax.set_xlim(-0.6, 2.6)
    for s in ("left", "bottom", "top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("C. Feature coverage (filled = available)", fontsize=8, loc="left", pad=26)
    save_fig(fig, "C_feature_coverage")


if __name__ == "__main__":
    Path(WORK_DIR).mkdir(parents=True, exist_ok=True)
    run_feature_matrix()
    if RUN_LATENCY:
        run_latency_benchmark()
    plot_iterations()
    if RUN_PDF:
        run_pdf_benchmark()
    elif os.environ.get("RH_BENCH_PDF") == "plot":
        plot_pdf_figure()
    print(f"results in {OUT}")
