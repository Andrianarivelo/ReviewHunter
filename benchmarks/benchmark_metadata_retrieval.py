"""Benchmark E: 'Retrieve Metadata for PDF', previous algorithm vs current one.

Question: given only a PDF file, how often does each algorithm attach the metadata of the
right paper (correct), of another paper (wrong: the worst outcome, silently corrupts a library)
or nothing (none)?

Design
    * Random sample of open-access journal articles from the OpenAlex sample API (seeded).
      Ground truth = the DOI OpenAlex associates with the PDF location.
    * Each PDF is downloaded once and kept only if it really is a PDF (%PDF- magic bytes).
    * Both algorithms run on the same files (paired design) and share the same resolvers
      (CrossRef / doi.org): only the PDF -> identifier/title logic differs.
        previous = git 12bef20 logic: first resolvable DOI/arXiv found in the first 3 pages,
                   else CrossRef search on the first line longer than 25 characters (ratio >= 0.88)
        current  = fulltext._find_verified_metadata (broken-DOI repair, header / printed-title
                   verification, bibliographic search on page-1 text)
    * Statistics: Wilson 95% CI per version, exact McNemar test on the paired "correct"
      outcome (discordant pairs only), and the same test on "wrong".

Run (from the repo root)
    <conda rh-bench python> benchmarks/benchmark_metadata_retrieval.py          # sample + run + plot
    RH_BENCH_E=plot <python> benchmarks/benchmark_metadata_retrieval.py         # re-plot from CSV

Outputs (benchmarks/results): E_metadata_per_pdf.csv, E_metadata_summary.csv, E_metadata_retrieval.{pdf,png,svg}
"""
# ============================================================================ PARAMETERS
APP_PYTHON = r"D:\Apps\ReviewHunter\.venv\Scripts\python.exe"    # interpreter that has the app deps
PDF_CACHE = r"C:\Users\andry\AppData\Local\Temp\claude\rh-bench\pdfs"
OUT_DIR = r"D:\Apps\ReviewHunter\benchmarks\results"
SEED = 7
N_TARGET_PDFS = 40                 # usable PDFs to collect
SAMPLE_SIZE = 150                  # OpenAlex works sampled to reach the target (many OA links are not PDFs)
FROM_YEAR = 2005
MAX_PDF_MB = 25
DOWNLOAD_TIMEOUT = (10, 40)
ALPHA = 0.05

# Ground-truth audit (manual, from reading page 1 of each file; raw OpenAlex outcomes are kept untouched).
# OpenAlex links a DOI to a PDF location, but the file behind the link is sometimes another version or
# another article. "accept" lists DOIs that are also correct for the file content.
AUDIT = {
    "011.pdf": {"accept": ["10.48550/arxiv.2509.10967"],
                "note": "file is the arXiv preprint (arXiv id printed on page 1); its DataCite DOI is correct for the file"},
    "030.pdf": {"accept": ["10.48550/arxiv.physics/0502026"],
                "note": "file is the arXiv preprint (arXiv id printed on page 1); same rule as 011 for every arxiv.org file"},
    "014.pdf": {"accept":["10.1371/journal.pmed.0030063"],
                "note": "PLoS bundle of 12 correspondence letters; page 1 is the letter 'Major Potential Confounder Not Addressed'"},
    "031.pdf": {"accept": ["10.26583/sv.17.2.07"],
                "note": "publisher link serves another article: page 1 prints 'Implementation of the Set-Theoretic Subtraction...' DOI 10.26583/sv.17.2.07"},
    "039.pdf": {"accept": [],
                "note": "publisher link serves another IJSREM article whose printed DOI 10.55041/IJSREM63985 does not resolve: no attachable truth"},
}

# Figure styling
FIG_DPI = 300
FONT = "DejaVu Sans"
COLORS = {"previous": "#8b94a9", "current": "#0284c7"}
OUTCOME_ORDER = ["correct", "wrong", "none"]
BAR_WIDTH = 0.28                   # thin bars
FIGSIZE = (6.4, 3.4)
# ============================================================================

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = Path(OUT_DIR)
CACHE = Path(PDF_CACHE)
PER_PDF_CSV = OUT / "E_metadata_per_pdf.csv"
SUMMARY_CSV = OUT / "E_metadata_summary.csv"


# ---------------------------------------------------------------------------- worker (app venv)

def worker_main() -> None:
    """Runs inside the app venv: sample, download, then evaluate both algorithms on every PDF."""
    sys.path.insert(0, str(REPO))
    import difflib
    import re

    import requests
    from pypdf import PdfReader

    from doi_hunter_web import fulltext, identifiers
    from doi_hunter_web.metadata import search_crossref

    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = CACHE / "manifest.json"
    records: list[dict] = json.loads(manifest.read_text()) if manifest.exists() else []

    # 1) Collect PDFs (cached across runs so both algorithms and reruns see identical files).
    if len(records) < N_TARGET_PDFS:
        params = {"sample": SAMPLE_SIZE, "seed": SEED, "per-page": 200,
                  "filter": f"type:article,is_oa:true,has_doi:true,from_publication_date:{FROM_YEAR}-01-01"}
        works = requests.get("https://api.openalex.org/works", params=params, timeout=60).json()["results"]
        seen = {r["doi"] for r in records}
        for w in works:
            if len(records) >= N_TARGET_PDFS:
                break
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            urls = [loc.get("pdf_url") for loc in [w.get("best_oa_location") or {}, *(w.get("locations") or [])]]
            urls = [u for u in dict.fromkeys(urls) if u]
            if not doi or doi in seen or not urls:
                continue
            for url in urls[:2]:
                try:
                    r = requests.get(url, timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 ReviewHunter-bench"})
                except requests.RequestException:
                    continue
                if r.status_code == 200 and r.content[:5] == b"%PDF-" and len(r.content) < MAX_PDF_MB * 1e6:
                    path = CACHE / f"{len(records):03d}.pdf"
                    path.write_bytes(r.content)
                    records.append({"file": path.name, "doi": doi, "title": w.get("title") or "", "url": url})
                    seen.add(doi)
                    print(f"[E] pdf {len(records)}/{N_TARGET_PDFS} {doi}", flush=True)
                    manifest.write_text(json.dumps(records, indent=1))
                    break

    # 2) Previous algorithm, reproduced from commit 12bef20 (no DOI repair, no verification).
    doi_re, arxiv_re = identifiers.DOI_RE, identifiers.ARXIV_RE

    def previous(path: str):
        reader = PdfReader(path)
        meta = reader.metadata or {}
        blob = " ".join(str(v) for v in meta.values() if v)
        pages = []
        for page in reader.pages[:3]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pass
        text = blob + "\n" + "\n".join(pages)
        ids, seen_ids = [], set()
        for m in doi_re.finditer(text):
            key = ("doi", identifiers.clean_doi(m.group(1)))
            if key not in seen_ids:
                seen_ids.add(key); ids.append(key)
        for m in arxiv_re.finditer(text):
            key = ("arxiv", m.group(1))
            if key not in seen_ids:
                seen_ids.add(key); ids.append(key)
        title = str(meta.get("/Title") or "").strip()
        if not title or len(title) < 12 or re.search(r"\.(pdf|docx?)$|^untitled|^microsoft word", title, re.I):
            lines = [ln.strip() for ln in (pages[0] if pages else "").splitlines() if len(ln.strip()) > 25]
            title = lines[0] if lines else ""
        for kind, value in ids[:4]:
            payload = identifiers.resolve_doi(value) if kind == "doi" else identifiers.resolve_arxiv(value)
            if payload:
                return payload
        if title:
            norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower())  # noqa: E731
            for cand in search_crossref(title, rows=3):
                if difflib.SequenceMatcher(None, norm(cand.get("title", "")), norm(title)).ratio() >= 0.88 and cand.get("doi"):
                    return identifiers.resolve_doi(cand["doi"])
        return None

    def current(path: str):
        # The item title a PDF import creates is the file name: here an uninformative "000".
        payload, _source, _g = fulltext._find_verified_metadata({"title": Path(path).stem}, path)
        return payload

    def outcome(payload, truth: str) -> tuple[str, str]:
        if not payload or not payload.get("doi"):
            return "none", ""
        got = identifiers.clean_doi(payload["doi"])
        return ("correct" if got == truth else "wrong"), got

    rows = []
    for rec in records:
        path = str(CACHE / rec["file"])
        row = {"file": rec["file"], "true_doi": rec["doi"]}
        for name, fn in (("previous", previous), ("current", current)):
            try:
                res, got = outcome(fn(path), rec["doi"])
            except Exception as e:  # unreadable PDF counts as "none" for both
                res, got = "none", f"error: {e}"[:80]
            row[f"{name}_outcome"], row[f"{name}_doi"] = res, got
        print(f"[E] {rec['file']} prev={row['previous_outcome']} cur={row['current_outcome']}", flush=True)
        rows.append(row)
    OUT.mkdir(parents=True, exist_ok=True)
    with PER_PDF_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


# ---------------------------------------------------------------------------- statistics + figure

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval: accurate for small n and proportions near 0 or 1."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value = binomial test on discordant pairs (valid for any count)."""
    from scipy.stats import binomtest

    return 1.0 if b + c == 0 else binomtest(b, b + c, 0.5).pvalue


def analyse_and_plot() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = list(csv.DictReader(PER_PDF_CSV.open(encoding="utf-8")))
    n = len(rows)
    # Audited outcome: a DOI that is correct for the file content counts as correct; for files with
    # no attachable truth, any attached record is wrong and "none" stays "none".
    for r in rows:
        audit = AUDIT.get(r["file"])
        r["audit_note"] = audit["note"] if audit else ""
        for version in ("previous", "current"):
            oc = r[f"{version}_outcome"]
            if audit and oc != "none":
                ok = r[f"{version}_doi"] in audit["accept"] or (audit["accept"] and r[f"{version}_doi"] == r["true_doi"])
                oc = "correct" if ok else "wrong"
            r[f"{version}_audited"] = oc
    with (OUT / "E_metadata_per_pdf_audited.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    summary, tests = [], {}
    for column in ("outcome", "audited"):
        for version in ("previous", "current"):
            for oc in OUTCOME_ORDER:
                k = sum(r[f"{version}_{column}"] == oc for r in rows)
                lo, hi = wilson(k, n)
                summary.append({"ground_truth": "OpenAlex raw" if column == "outcome" else "audited",
                                "version": version, "outcome": oc, "count": k, "n": n, "rate": round(k / n, 4),
                                "ci95_low": round(lo, 4), "ci95_high": round(hi, 4)})
        for oc in ("correct", "wrong"):
            b = sum(r[f"previous_{column}"] == oc and r[f"current_{column}"] != oc for r in rows)   # previous only
            c = sum(r[f"previous_{column}"] != oc and r[f"current_{column}"] == oc for r in rows)   # current only
            tests[(column, oc)] = {"previous_only": b, "current_only": c, "mcnemar_exact_p": mcnemar_exact(b, c)}
    for srow in summary:
        key = ("outcome" if srow["ground_truth"] == "OpenAlex raw" else "audited", srow["outcome"])
        if key in tests:
            srow.update({f"discordant_{k}": v for k, v in tests[key].items()})
    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as f:
        fields = list(dict.fromkeys(k for s in summary for k in s))
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(summary)
    print(json.dumps({f"{a}/{b}": v for (a, b), v in tests.items()}, indent=1))

    plt.rcParams.update({"font.family": FONT, "font.size": 8, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(FIGSIZE[0] * 1.8, FIGSIZE[1]), constrained_layout=True, sharey=True)
    for ax, (column, label) in zip(axes, (("outcome", "OpenAlex DOI as truth (raw)"),
                                          ("audited", f"audited truth ({len(AUDIT)} files re-read)"))):
        gt = "OpenAlex raw" if column == "outcome" else "audited"
        for i, version in enumerate(("previous", "current")):
            xs, ys, err = [], [], [[], []]
            for j, oc in enumerate(OUTCOME_ORDER):
                srow = next(x for x in summary if x["ground_truth"] == gt and x["version"] == version and x["outcome"] == oc)
                x = j + (i - 0.5) * BAR_WIDTH * 1.15
                xs.append(x); ys.append(srow["rate"] * 100)
                err[0].append((srow["rate"] - srow["ci95_low"]) * 100); err[1].append((srow["ci95_high"] - srow["rate"]) * 100)
                ax.text(x, srow["ci95_high"] * 100 + 2, f"{srow['count']}", ha="center", va="bottom", fontsize=6.5)
            ax.bar(xs, ys, width=BAR_WIDTH, color=COLORS[version], label=version, yerr=err,
                   error_kw={"elinewidth": 0.7, "capsize": 2})
        for j, oc in enumerate(OUTCOME_ORDER):
            if (column, oc) in tests:
                ax.text(j, 112, f"McNemar p={tests[(column, oc)]['mcnemar_exact_p']:.3g}", ha="center", fontsize=6.5, color="#334155")
        ax.set_xticks(range(len(OUTCOME_ORDER)), ["correct paper", "wrong paper", "nothing found"])
        ax.set_ylim(0, 120); ax.set_title(label, fontsize=8.5)
    axes[0].set_ylabel(f"PDFs (%, n={n}) with Wilson 95% CI")
    axes[1].legend(frameon=False, loc="upper right", bbox_to_anchor=(1, 0.92), fontsize=7)
    fig.suptitle("Retrieve Metadata for PDF on random open-access PDFs", fontsize=9)
    for ext in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"E_metadata_retrieval.{ext}", dpi=FIG_DPI)
    plt.close(fig)
    print(f"results in {OUT}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker_main()
    else:
        if os.environ.get("RH_BENCH_E") != "plot":
            subprocess.run([APP_PYTHON, str(Path(__file__).resolve()), "worker"], check=True, cwd=str(REPO))
        analyse_and_plot()
