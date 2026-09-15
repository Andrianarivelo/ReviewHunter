"""Render the paired aim-coverage audit without reopening the paper library.

Run this file directly in an IDE or use:
    python tools/plot_insight_coverage.py
Input is the reviewed per-paper CSV. Counts describe coverage, not correctness.
"""
from __future__ import annotations

from pathlib import Path

# User-adjustable inputs and appearance. All outputs share the requested stem.
REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "docs" / "aim-source-coverage.csv"
OUTPUT = REPO / "docs" / "aim-source-coverage"
FIGURE_SIZE = (7.4, 3.1)
FIGURE_DPI = 240
BAR_HEIGHT = 0.24
COLORS = {"aim": "#008b91", "fallback": "#cfd5df", "text": "#253246"}

import argparse
import csv
import hashlib
import json


def read_coverage(path: Path) -> tuple[list[dict], list[dict]]:
    """Validate a paired sample and summarize only its recorded source statuses."""
    with path.open(encoding="utf-8-sig", newline="") as source:
        papers = list(csv.DictReader(source))
    if not papers or len({row["item_id"] for row in papers}) != len(papers):
        raise ValueError("Coverage input must contain a nonempty set of unique papers")
    valid = {"explicit_aim", "title_focus", "unavailable"}
    records = []
    for version in ("before", "after"):
        statuses = [row[f"{version}_focus_status"] for row in papers]
        if set(statuses) - valid:
            raise ValueError(f"Unknown coverage status in {version} column")
        records.append({"version": version, "papers": len(papers),
                        "explicit_aim": statuses.count("explicit_aim"),
                        "title_focus": statuses.count("title_focus"),
                        "unavailable": statuses.count("unavailable"),
                        "explicit_aim_fraction": statuses.count("explicit_aim") / len(papers)})
    return papers, records


def render(source: Path, output: Path) -> None:
    """Export the same validated counts as CSV, provenance JSON and three figures."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    papers, records = read_coverage(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_name(output.name + "-summary.csv").open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    provenance = {"input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "paper_ids": [row["item_id"] for row in papers], "counts": records,
                  "interpretation": "Descriptive source-coverage audit on the same convenience sample. Not extraction accuracy or a statistical test."}
    output.with_name(output.name + "-plot.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.fonttype": "none", "pdf.fonttype": 42,
                         "text.color": COLORS["text"], "axes.labelcolor": COLORS["text"]})
    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    total = len(papers)
    for y, record in enumerate(records):
        count = record["explicit_aim"]
        ax.barh(y, total, height=BAR_HEIGHT, color=COLORS["fallback"])
        ax.barh(y, count, height=BAR_HEIGHT, color=COLORS["aim"])
        ax.text(total + total * 0.035, y, f"{count}/{total}", va="center", fontweight="bold")
    ax.set_yticks([0, 1], ["Before", "After"])
    ax.invert_yaxis()
    ax.set_xlim(0, total * 1.16)
    ax.set_ylim(1.4, -0.45)
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0, pad=10)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.suptitle("Explicit aims recovered from source text", x=0.06, y=0.95,
                 ha="left", fontsize=14, fontweight="bold")
    fig.text(0.06, 0.83, f"Same {total} papers | Abstract, introduction and retrospective purpose statements", fontsize=9)
    delta = records[1]["explicit_aim"] - records[0]["explicit_aim"]
    fig.text(0.06, 0.22, f"{delta:+d} papers with a source-backed aim. Grey indicates a title fallback or unavailable aim.", fontsize=8.5)
    fig.text(0.06, 0.12, "Descriptive convenience-sample coverage, not extraction accuracy.", fontsize=9)
    fig.text(0.06, 0.05, "No significance test or population-level performance claim. Underlying library data unchanged.", fontsize=8)
    fig.subplots_adjust(left=0.16, right=0.97, top=0.76, bottom=0.32)
    for extension in ("png", "pdf", "svg"):
        fig.savefig(output.with_suffix("." + extension), dpi=FIGURE_DPI, facecolor="white")
    plt.close(fig)
    svg = output.with_suffix(".svg")
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    print(f"Coverage: {records[0]['explicit_aim']}/{total} -> {records[1]['explicit_aim']}/{total}")
    print(f"Rendered {output}.[png|pdf|svg]")


def main() -> None:
    """Expose reusable inputs while keeping IDE execution useful without arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    render(args.input, args.output)


if __name__ == "__main__":
    main()
