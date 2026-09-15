"""Benchmark I: evidence-audited methodology detection on five real library papers.

Measure with the application environment, then render with a matplotlib environment:
    .venv/Scripts/python.exe benchmarks/benchmark_methodology.py measure --recompute
    python benchmarks/benchmark_methodology.py plot

The library is opened through SQLite mode=ro and query_only. Detector functions run
on in-memory records with their database entry points disabled. No PDF is indexed,
no library metadata is edited, and no source full text is copied into the repository.
The committed annotation excerpts were manually reviewed by a coding agent; they
are not independent human ground truth. This is a small descriptive development
benchmark, not a held-out estimate of performance across the scientific literature.
"""
from __future__ import annotations

# User-adjustable measurement inputs and rendering parameters.
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "benchmarks" / "fixtures" / "methodology"
ANNOTATIONS = FIXTURES / "annotations.json"
DEFAULT_LIBRARY = Path.home() / "ReviewHunter" / "library.db"
OUTPUT = REPO / "benchmarks" / "results"
STAGES = ["subjects", "design", "manipulation", "measurement", "analysis"]
STAGE_LABELS = ["Subjects", "Design", "Manipulations", "Measurements", "Analyses"]
FIGURE_SIZE = (9.0, 4.6)
FIGURE_DPI = 240
BAR_WIDTH = 0.22
COLORS = {"baseline": "#8b94a7", "current": "#008b91"}
CACHE_VERSION = 1

import argparse
import ast
import copy
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(REPO))


def sha256_bytes(data: bytes) -> str:
    """Return a reproducible digest for code, source text and annotations."""
    return hashlib.sha256(data).hexdigest()


def dependency_hashes() -> dict[str, str]:
    """Fingerprint all numeric code dependencies, excluding figure style settings."""
    paths = [ANNOTATIONS, FIXTURES / "baseline" / "insight.py",
             FIXTURES / "baseline" / "method_taxonomy.py",
             REPO / "doi_hunter_web" / "insight.py",
             REPO / "doi_hunter_web" / "method_taxonomy.py"]
    hashes = {str(path.relative_to(REPO)).replace("\\", "/"): sha256_bytes(path.read_bytes())
              for path in paths}
    # Changes to scoring or read validation invalidate numbers; figure styling
    # changes can reuse them. Hash numeric function source without plot/main.
    own_source = Path(__file__).read_text(encoding="utf-8")
    numeric_source = "\n".join(ast.get_source_segment(own_source, node) or ""
                               for node in ast.parse(own_source).body
                               if isinstance(node, ast.FunctionDef) and node.name not in ("plot", "main"))
    numeric_settings = json.dumps({"schema": CACHE_VERSION, "stages": STAGES}, sort_keys=True)
    hashes["benchmark_numeric_functions"] = sha256_bytes((numeric_source + numeric_settings).encode())
    return hashes


def load_baseline():
    """Execute the preserved initial detector with its own preserved taxonomy."""
    modules = {}
    for name in ("method_taxonomy", "insight"):
        source_path = FIXTURES / "baseline" / f"{name}.py"
        source = source_path.read_text(encoding="utf-8")
        if name == "insight":
            source = source.replace("from .method_taxonomy import", "from ._benchmark_i_method_taxonomy import")
        module = types.ModuleType(f"doi_hunter_web._benchmark_i_{name}")
        module.__package__ = "doi_hunter_web"
        module.__file__ = str(source_path)
        sys.modules[module.__name__] = module
        exec(compile(source, str(source_path), "exec"), module.__dict__)
        modules[name] = module
    return modules["insight"], modules["method_taxonomy"]


def read_papers(library: Path, annotations: dict) -> list[dict]:
    """Read only the annotated DOI records and validate their exact text versions."""
    uri = library.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    papers = []
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        for annotation in annotations["papers"]:
            rows = conn.execute("SELECT * FROM items WHERE doi=? AND deleted_at IS NULL",
                                (annotation["doi"],)).fetchall()
            if len(rows) != 1:
                raise ValueError(f"Expected exactly one nondeleted item for {annotation['doi']}; got {len(rows)}")
            paper = dict(rows[0])
            if paper["title"] != annotation["title"]:
                raise ValueError(f"Title changed for {annotation['doi']}; re-audit source provenance before scoring")
            paper["fulltext"] = "\n".join(row[0] for row in conn.execute(
                "SELECT content FROM fulltext_fts WHERE item_id=? ORDER BY attachment_id", (paper["id"],)) if row[0])
            for field, key in (("fulltext", "indexed_fulltext_sha256"), ("abstract", "abstract_sha256")):
                if sha256_bytes((paper[field] or "").encode()) != annotation[key]:
                    raise ValueError(f"{field} changed for {annotation['doi']}; re-audit annotations before scoring")
            normalized = re.sub(r"\s+", " ", paper["fulltext"])
            for label in annotation["labels"]:
                if label["evidence"] not in normalized:
                    raise ValueError(f"Evidence no longer matches source: {annotation['doi']} / {label['method_id']}")
            paper["authors"] = json.loads(paper.get("authors") or "[]")
            papers.append(paper)
        return papers
    finally:
        conn.close()


def run_detector(detector, papers: list[dict]) -> tuple[dict, float]:
    """Run the actual methods aggregation while forbidding application database access."""
    by_id = {paper["id"]: paper for paper in papers}
    start = time.perf_counter()
    with patch.object(detector, "scope_items", return_value=copy.deepcopy(papers)), \
            patch.object(detector, "scope_label", return_value="Benchmark I audited corpus"), \
            patch.object(detector, "fulltext_of", side_effect=lambda item_id, max_chars=None: by_id[item_id]["fulltext"][:max_chars]), \
            patch.object(detector, "get_conn", side_effect=AssertionError("Benchmark detector may not access a database")):
        result = detector.methods()
    result_ids = [paper["id"] for paper in result["papers"]]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(by_id):
        raise AssertionError("Detector omitted or duplicated an annotated paper")
    if any(not method.get("evidence") for paper in result["papers"] for method in paper["detected"]):
        raise AssertionError("A detected method has no evidence")
    return result, time.perf_counter() - start


def score(result: dict, version: str, papers: list[dict], annotations: dict, taxonomy: list[dict]):
    """Calculate micro precision/recall by stage over the fixed original label universe."""
    expected = {paper["doi"]: {label["method_id"]: label["evidence"] for label in paper["labels"]}
                for paper in annotations["papers"]}
    by_id = {paper["id"]: paper for paper in papers}
    methods = {method["id"]: method for method in taxonomy}
    rows, unscored = [], []
    for paper in result["papers"]:
        doi = by_id[paper["id"]]["doi"]
        actual = {method["id"]: method for method in paper["detected"]}
        for method_id, method in methods.items():
            truth, predicted = method_id in expected[doi], method_id in actual
            rows.append({"version": version, "doi": doi, "title": paper["title"], "stage": method["stage"],
                         "method_id": method_id, "method": method["label"], "expected": int(truth),
                         "predicted": int(predicted), "outcome": "TP" if truth and predicted else
                         "FN" if truth else "FP" if predicted else "TN",
                         "reference_evidence": expected[doi].get(method_id, ""),
                         "detector_evidence": actual.get(method_id, {}).get("evidence", ""),
                         "confidence": actual.get(method_id, {}).get("confidence", "")})
        for method_id in sorted(actual.keys() - methods.keys()):
            unscored.append({"version": version, "doi": doi, "method_id": method_id,
                             "stage": actual[method_id]["stage"], "evidence": actual[method_id]["evidence"],
                             "status": "New taxonomy label, outside frozen annotation universe"})
    metrics = []
    for stage in [*STAGES, "all"]:
        selected = [row for row in rows if stage == "all" or row["stage"] == stage]
        counts = {outcome: sum(row["outcome"] == outcome for row in selected) for outcome in ("TP", "FP", "FN", "TN")}
        tp, fp, fn = counts["TP"], counts["FP"], counts["FN"]
        metrics.append({"version": version, "stage": stage, "n_papers": len(papers), **counts,
                        "positive_labels": tp + fn, "predicted_labels": tp + fp,
                        "precision": tp / (tp + fp) if tp + fp else None,
                        "recall": tp / (tp + fn) if tp + fn else None,
                        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                        "inference": "Descriptive only; no independent human annotations or held-out papers"})
    return metrics, rows, unscored


def write_csv(path: Path, records: list[dict]) -> None:
    """Export a tidy UTF-8 table for review and supplementary material."""
    if not records:
        path.write_text("status\nNo records\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def measure(library: Path, output: Path, recompute: bool = False) -> dict:
    """Validate source provenance, measure both detectors, then cache numeric results."""
    annotations = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    papers = read_papers(library, annotations)
    dependencies = dependency_hashes()
    cache_path = output / "I_methodology_cache.json"
    if cache_path.exists() and not recompute:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache.get("schema_version") == CACHE_VERSION and cache.get("dependencies") == dependencies:
            print(f"Reusing validated cache: {cache_path}")
            return cache
    # Even accidental future module initialization would only see a throwaway home.
    with tempfile.TemporaryDirectory(prefix="reviewhunter-method-benchmark-") as home, \
            patch.dict(os.environ, {"REVIEWHUNTER_HOME": home}):
        from doi_hunter_web import insight
        baseline, taxonomy = load_baseline()
        valid_ids = {method["id"] for method in taxonomy.METHODS}
        unknown = {label["method_id"] for paper in annotations["papers"] for label in paper["labels"]} - valid_ids
        if unknown:
            raise ValueError(f"Annotation IDs outside frozen taxonomy: {sorted(unknown)}")
        metrics, decisions, expanded, timings = [], [], [], []
        for version, detector in (("baseline", baseline), ("current", insight)):
            result, seconds = run_detector(detector, papers)
            scored, detail, extra = score(result, version, papers, annotations, taxonomy.METHODS)
            metrics.extend(scored)
            decisions.extend(detail)
            expanded.extend(extra)
            timings.append({"version": version, "n_papers": len(papers), "seconds": seconds,
                            "protocol": "One local CPU call; descriptive timing, not a stable speed benchmark"})
    cache = {"schema_version": CACHE_VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
             "dependencies": dependencies, "n_papers": len(papers), "annotation_status": annotations["annotator"],
             "selection": annotations["selection"], "label_universe": len(taxonomy.METHODS),
             "metrics": metrics, "timings": timings, "decisions": decisions, "expanded_unscored": expanded,
             "limitations": annotations["rules"], "annotation_notes": annotations["notes"]}
    output.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(output / "I_methodology_metrics.csv", metrics)
    write_csv(output / "I_methodology_decisions.csv", decisions)
    write_csv(output / "I_methodology_expanded_unscored.csv", expanded)
    write_csv(output / "I_methodology_timing.csv", timings)
    for row in metrics:
        if row["stage"] == "all":
            print(f"{row['version']}: precision={row['precision']:.3f}, recall={row['recall']:.3f}, "
                  f"TP={row['TP']} FP={row['FP']} FN={row['FN']} (N={len(papers)})")
    return cache


def plot(output: Path) -> None:
    """Render compact descriptive bars in editable PDF/SVG plus high-resolution PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    cache_path = output / "I_methodology_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if cache.get("schema_version") != CACHE_VERSION or cache.get("dependencies") != dependency_hashes():
        raise ValueError("Numeric cache is stale; rerun measure before plotting")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "svg.fonttype": "none",
                         "pdf.fonttype": 42, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.edgecolor": "#c7cdd5", "text.color": "#253246"})
    fig, axes = plt.subplots(1, 2, figsize=FIGURE_SIZE, sharey=True)
    for ax, metric in zip(axes, ("precision", "recall")):
        for index, version in enumerate(("baseline", "current")):
            records = {row["stage"]: row for row in cache["metrics"] if row["version"] == version}
            values = [records[stage][metric] for stage in STAGES]
            positions = [x + (index - 0.5) * BAR_WIDTH for x in range(len(STAGES))]
            ax.bar(positions, [v if v is not None else 0 for v in values], width=BAR_WIDTH,
                   color=COLORS[version], label=version.title(), zorder=3)
            for pos, value in zip(positions, values):
                ax.text(pos, (value or 0) + 0.025, f"{value:.0%}" if value is not None else "n/a",
                        ha="center", va="bottom", fontsize=6.8, rotation=90)
        ax.set_title(metric.title(), loc="left", fontsize=11, fontweight="bold")
        ax.set_ylim(0, 1.20)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.set_xticks(range(len(STAGES)), STAGE_LABELS, rotation=23, ha="right")
        ax.grid(axis="y", alpha=0.22, linewidth=0.6, zorder=0)
        ax.tick_params(axis="both", length=0)
    fig.suptitle(f"Methodology detection | {cache['n_papers']} real papers", x=0.07, y=0.97,
                 ha="left", fontsize=14, fontweight="bold")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper right", bbox_to_anchor=(0.96, 0.98),
               frameon=False, ncol=2)
    counts = {row["stage"]: row["positive_labels"] for row in cache["metrics"] if row["version"] == "current"}
    fig.text(0.07, 0.12, "Positive labels by stage: " + " / ".join(str(counts[s]) for s in STAGES) +
             f". Fixed original taxonomy: {cache['label_universe']} methods.", fontsize=8)
    fig.text(0.07, 0.075, "Agent-audited evidence; independent human review pending. Small convenience corpus, descriptive only.", fontsize=8)
    fig.text(0.07, 0.032, "Baseline: preserved initial detector. Current: working tree. Expanded taxonomy labels are reported separately, unscored.", fontsize=7.7)
    fig.subplots_adjust(left=0.07, right=0.97, bottom=0.28, top=0.80, wspace=0.16)
    for extension in ("png", "pdf", "svg"):
        fig.savefig(output / f"I_methodology.{extension}", dpi=FIGURE_DPI, facecolor="white")
    # Matplotlib adds trailing spaces inside SVG path data; normalize whitespace for clean diffs.
    svg_path = output / "I_methodology.svg"
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(fig)
    print(f"Rendered {output / 'I_methodology'}.[png|pdf|svg]")


def main() -> int:
    """Expose independent measurement and figures-only workflows for IDE and CLI use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("measure", "plot"), nargs="?", default="measure")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--recompute", action="store_true", help="Recompute instead of reusing a valid numeric cache")
    parser.add_argument("--figures-only", action="store_true", help="Render validated cached values without library access")
    args = parser.parse_args()
    if args.mode == "plot" or args.figures_only:
        plot(args.output)
    else:
        measure(args.library, args.output, args.recompute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
