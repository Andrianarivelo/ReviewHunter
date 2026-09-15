"""Benchmark H: deterministic PDF identity regression cases, baseline vs working tree.

Run measurement with the app environment, then plot with a matplotlib environment:
    .venv/Scripts/python.exe benchmarks/benchmark_pdf_identity.py measure
    python benchmarks/benchmark_pdf_identity.py plot

The real source PDF is read once and SHA-256 checked again at the end. Every
reference, attachment, and database created by this benchmark lives in a unique
TemporaryDirectory. Synthetic documents and registry responses are explicit
fixtures, not observations about the scientific literature or download success.

Baseline executes catalog/fulltext from the pinned commit and reconstructs that
commit's /import/pdfs orchestration, including its metadata and rename callback.
Current executes ingest_pdf. Both use the current compatible DB schema and common
filename helper. Network metadata and async indexing are mocked identically.
"""
from __future__ import annotations

# All configurable paths, fixture identity and figure styling live here.
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "benchmarks" / "results"
BASELINE_COMMIT = "9018274"
SOURCE_PDF = Path(r"C:\Users\andry\ReviewHunter\papers\Unknown - n.d. - main (2).pdf")
REAL_DOI = "10.1016/j.pbb.2017.10.008"
REAL_TITLE = "High ambient temperature facilitates the acquisition of 3,4-methylenedioxymethamphetamine (MDMA) self-administration"
FIGSIZE = (9.2, 4.8)
DPI = 220
COLORS = {"baseline": "#8c96a8", "current": "#008b91", "pass": "#d3eeeb", "fail": "#f8dcdb"}
METRICS = ["correct_target", "no_duplicate", "metadata_complete", "citation_filename", "bytes_preserved"]

import copy
import csv
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import types
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, str(REPO))


def load_baseline(name):
    """Execute an exact git source snapshot under an isolated package module name."""
    source = subprocess.run(["git", "show", f"{BASELINE_COMMIT}:doi_hunter_web/{name}.py"],
                            cwd=REPO, capture_output=True, check=True).stdout
    module = types.ModuleType(f"doi_hunter_web._benchmark_previous_{name}")
    module.__package__ = "doi_hunter_web"
    module.__file__ = f"git:{BASELINE_COMMIT}:doi_hunter_web/{name}.py"
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def synthetic_pdf(title, doi="", *, xmp_only=False):
    """Generate explicitly synthetic, valid PDF bytes with controllable identity."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from xml.sax.saxutils import escape

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if xmp_only:
        packet = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/">
          <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
            <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"
              xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/" prism:doi="{escape(doi)}">
              <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{escape(title)}</rdf:li></rdf:Alt></dc:title>
              <dc:creator><rdf:Seq><rdf:li>Alex Test</rdf:li></rdf:Seq></dc:creator>
            </rdf:Description>
          </rdf:RDF></x:xmpmeta>'''
        stream = DecodedStreamObject()
        stream.set_data(packet.encode())
        stream[NameObject("/Type")] = NameObject("/Metadata")
        stream[NameObject("/Subtype")] = NameObject("/XML")
        writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    else:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        text = title + "\nAlex Test\n" + ("doi:" + doi if doi else "")
        commands = ["BT /F1 10 Tf 36 720 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.extend([f"({escaped}) Tj", "0 -15 Td"])
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def fixture_payload(doi, title):
    """Return rich fictional metadata used only for synthetic software controls."""
    return {"doi": doi, "title": title, "item_type": "journalArticle", "year": 2024,
        "date": "2024-01-01", "creators": [{"creatorType": "author", "firstName": "Alex", "lastName": "Test"}],
        "journal": "Synthetic Fixture Journal", "volume": "8", "issue": "2", "pages": "11-19",
        "publisher": "Synthetic Fixture Publisher", "abstract": "Synthetic registry fixture, not scientific evidence."}


def cases(real_data):
    """Define one real regression and eight engineered controls with explicit expectations."""
    real = {"doi": REAL_DOI, "title": REAL_TITLE, "item_type": "journalArticle", "year": 2017,
        "date": "2017", "journal": "Pharmacology, Biochemistry and Behavior", "volume": "163", "pages": "38-49",
        "publisher": "Elsevier", "creators": [
            {"creatorType": "author", "lastName": "Aarde", "firstName": "Shawn M."},
            {"creatorType": "author", "lastName": "Huang", "firstName": "Pai-Kai"},
            {"creatorType": "author", "lastName": "Taffe", "firstName": "Michael A."}]}
    payload = fixture_payload("10.9999/rh.synthetic.own", "Synthetic neural reward identity benchmark article")
    other = fixture_payload("10.9999/rh.synthetic.other", "A different synthetic reference about visual cortex")
    data = synthetic_pdf(payload["title"], payload["doi"])
    title_only = {k: v for k, v in payload.items() if k != "doi"}
    return [
        dict(name="Reported Aarde PDF", kind="real PDF + fixed metadata", data=real_data, payload=real, seed=real),
        dict(name="Existing DOI", kind="synthetic", data=data, payload=payload, seed=payload),
        dict(name="Existing title, no DOI", kind="synthetic", data=data, payload=payload, seed=title_only),
        dict(name="New rich reference", kind="synthetic", data=data, payload=payload),
        dict(name="Repeated identical upload", kind="synthetic", data=data, payload=payload, repeat=2),
        dict(name="XMP-only document", kind="synthetic", data=synthetic_pdf(payload["title"], payload["doi"], xmp_only=True), payload=payload),
        dict(name="Explicit wrong target", kind="synthetic", data=data, payload=payload, seed=other, explicit=True, reject=True),
        dict(name="Malformed PDF bytes", kind="synthetic", data=b"%PDF-1.7\nNot a valid PDF", payload=None, reject=True),
        dict(name="Offline title recognition", kind="synthetic", data=synthetic_pdf(payload["title"]), payload=payload, seed=payload, offline=True),
    ]


def old_import(data, catalog, fulltext, *, item_id=None):
    """Reproduce pinned API upload or import flow, including background metadata work."""
    if item_id is not None:
        catalog.store_file_bytes(item_id, data, "main.pdf")
        return item_id, "attached without identity validation"
    item = catalog.create_item({"item_type": "document", "title": "main", "status": "downloaded"}, dedupe_doi=False)
    catalog.store_file_bytes(item["id"], data, "main.pdf")
    try:
        result = fulltext.retrieve_metadata(item["id"])
        if result.get("ok"):
            catalog.rename_files_from_metadata(item["id"])
        reason = result.get("source") or result.get("reason") or ""
    except Exception as exc:
        # The old background task reported failure after the generic item existed.
        reason = f"background metadata failed: {type(exc).__name__}"
    return item["id"], reason


def run_case(case, version, previous_catalog, previous_fulltext, library_path):
    """Measure observable identity outcomes in one fresh temporary library."""
    from doi_hunter_web import catalog, db, fulltext, identifiers, library, metadata, pdf_ingest

    result = {"case": case["name"], "case_kind": case["kind"], "version": version}
    payload = case["payload"]
    registry = {} if case.get("offline") or not payload else {payload["doi"]: payload}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "REVIEWHUNTER_HOME": str(library_path), "DOIHUNTER_HOME": str(library_path),
            "REVIEWHUNTER_PAPERS_DIR": str(library_path / "papers"), "DOIHUNTER_PAPERS_DIR": str(library_path / "papers")}))
        stack.enter_context(patch.object(identifiers, "resolve_doi", side_effect=lambda doi: copy.deepcopy(registry.get(doi))))
        stack.enter_context(patch.object(identifiers, "resolve_arxiv", return_value=None))
        stack.enter_context(patch.object(metadata, "search_crossref", side_effect=lambda *a, **kw: copy.deepcopy(list(registry.values()))))
        stack.enter_context(patch.object(library, "_index_fulltext_async", return_value=None))
        # Fail fast on accidental network rather than silently measuring a live response.
        stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden in identity benchmark")))
        db.reset_connections()
        seed = catalog.create_item(copy.deepcopy(case["seed"])) if case.get("seed") else None
        chosen, rejected, reason = None, False, ""
        for _ in range(case.get("repeat", 1)):
            try:
                target = seed["id"] if seed and case.get("explicit") else None
                if version == "baseline":
                    chosen, reason = old_import(case["data"], previous_catalog, previous_fulltext, item_id=target)
                else:
                    response = pdf_ingest.ingest_pdf(case["data"], "main.pdf", item_id=target)
                    chosen, reason = response["item_id"], response.get("source", "")
            except ValueError as exc:
                rejected, reason = True, str(exc)
        live = db.get_conn().execute("SELECT id FROM items WHERE deleted_at IS NULL").fetchall()
        attachments = db.get_conn().execute("SELECT * FROM attachments WHERE kind='file'").fetchall()
        result.update(live_references=len(live), attachments=len(attachments), rejected=int(rejected), detail=reason)
        if case.get("reject"):
            correct = rejected and not attachments and len(live) == (1 if seed else 0)
            result.update({metric: "" for metric in METRICS})
            result["correct_target"] = int(correct)
            result["case_pass"] = int(correct)
        else:
            item = catalog.get_item(chosen) if chosen else {}
            target_correct = chosen == seed["id"] if seed else bool(item and item.get("doi") == payload["doi"])
            files = catalog.list_attachments(chosen) if chosen else []
            path = next((Path(a["path"]) for a in files if a.get("path")), None)
            metadata_differences = [key for key in ["doi", "title", "year", "journal", "volume", "pages", "publisher", "creators"]
                                    if not item or item.get(key) != payload.get(key)]
            metadata_correct = not metadata_differences
            filename_correct = bool(path and payload["creators"][0]["lastName"] in path.name
                                    and str(payload["year"]) in path.name and "main" not in path.stem.lower())
            result.update(correct_target=int(target_correct), no_duplicate=int(len(live) == 1 and len(attachments) == 1),
                          metadata_complete=int(metadata_correct), citation_filename=int(filename_correct),
                          bytes_preserved=int(bool(path and path.is_file() and path.read_bytes() == case["data"])),
                          saved_filename=path.name if path else "", metadata_differences=";".join(metadata_differences))
            result["case_pass"] = int(all(result[metric] for metric in METRICS))
        db.reset_connections()
    return result


def write_csv(path, rows):
    """Write UTF-8 structured results with stable field ordering and explicit missing cells."""
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def locate_source():
    """Find the read-only fixture again if normal app repair renamed it after a run."""
    override = os.environ.get("RH_BENCH_H_PDF")
    if override:
        return Path(override)
    if SOURCE_PDF.is_file():
        return SOURCE_PDF
    manifest = OUT / "H_pdf_identity_manifest.json"
    digest = json.loads(manifest.read_text(encoding="utf-8")).get("source_sha256") if manifest.exists() else None
    if digest:
        for candidate in SOURCE_PDF.parent.glob("*.pdf"):
            if hashlib.sha256(candidate.read_bytes()).hexdigest() == digest:
                return candidate
    raise FileNotFoundError("Set RH_BENCH_H_PDF to the original Aarde PDF or an unchanged copy")


def measure():
    """Execute both versions on each case and save descriptive, non-inferential results."""
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    source_path = locate_source()
    real_data = source_path.read_bytes()
    source_hash = hashlib.sha256(real_data).hexdigest()
    previous_catalog, catalog_hash = load_baseline("catalog")
    previous_fulltext, fulltext_hash = load_baseline("fulltext")
    previous_fulltext.catalog = previous_catalog
    rows = []
    with tempfile.TemporaryDirectory(prefix="rh-benchmark-identity-") as temporary:
        for index, case in enumerate(cases(real_data)):
            for version in ("baseline", "current"):
                row = run_case(case, version, previous_catalog, previous_fulltext, Path(temporary) / f"{index}_{version}")
                rows.append(row)
                print(f"{version:8} {case['name']}: {'PASS' if row['case_pass'] else 'FAIL'}")
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("Source PDF changed during the benchmark; results are invalid")
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "H_pdf_identity_cases.csv", rows)
    summary = []
    for version in ("baseline", "current"):
        subset = [row for row in rows if row["version"] == version]
        for metric in ["case_pass", *METRICS]:
            available = [row[metric] for row in subset if row.get(metric) != ""]
            summary.append(dict(version=version, metric=metric, passed=sum(available), applicable=len(available)))
    write_csv(OUT / "H_pdf_identity_summary.csv", summary)
    write_csv(OUT / "H_pdf_identity_statistics.csv", [{"analysis": "Paired deterministic regression outcomes",
        "inferential_test": "Not applicable", "reason": "Engineered, non-random fixed cases; no population inference or significance claim",
        "real_cases": 1, "synthetic_cases": 8, "repeats": "One deterministic run per version; repeat-upload case imports twice",
        "raw_pdf_sha256": source_hash, "raw_pdf_unchanged": True}])
    (OUT / "H_pdf_identity_manifest.json").write_text(json.dumps({"baseline_commit": BASELINE_COMMIT,
        "baseline_catalog_sha256": catalog_hash, "baseline_fulltext_sha256": fulltext_hash,
        "source_pdf": str(source_path), "source_sha256": source_hash, "source_unchanged": True,
        "scope": "Identity/import only; network, browser session capture and PDF acquisition are not benchmarked",
        "baseline": "Pinned catalog/fulltext, reconstructed pinned API import callback; shared compatible DB and filename helper",
        "registry": "Fixed responses for both versions. Real-paper fields transcribed from PDF; eight synthetic controls.",
        "statistics": "Descriptive counts only; synthetic regressions do not support inferential statistics"}, indent=2), encoding="utf-8")


def plot():
    """Render a compact outcome matrix and metric counts without misleading error bars."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    with (OUT / "H_pdf_identity_cases.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    fig, (left, right) = plt.subplots(1, 2, figsize=FIGSIZE, gridspec_kw={"width_ratios": [1.32, 1]})
    labels = list(dict.fromkeys(row["case"] for row in rows))
    for index, label in enumerate(labels):
        for x, version in enumerate(("baseline", "current")):
            row = next(row for row in rows if row["case"] == label and row["version"] == version)
            success = row["case_pass"] == "1"
            left.add_patch(Rectangle((x - .46, index - .39), .92, .78,
                                    facecolor=COLORS["pass" if success else "fail"], edgecolor="white"))
            left.text(x, index, "Pass" if success else "Fail", ha="center", va="center", fontsize=8)
    left.set(xlim=(-.5, 1.5), ylim=(len(labels) - .5, -.5), xticks=[0, 1], xticklabels=["Baseline", "Current"],
             yticks=range(len(labels)), yticklabels=[label + (" *" if i == 0 else "") for i, label in enumerate(labels)])
    left.tick_params(length=0)
    left.spines[["left", "bottom"]].set_visible(False)
    left.set_title("Case requirements", loc="left", fontweight="bold", pad=12)
    metric_labels = ["Correct identity", "No duplicate", "Full fixture metadata", "Citation filename", "Unchanged PDF bytes"]
    for offset, version in [(-.16, "baseline"), (.16, "current")]:
        subset = [row for row in rows if row["version"] == version]
        values = [sum(int(row[metric]) for row in subset if row[metric]) for metric in METRICS]
        totals = [sum(bool(row[metric]) for row in subset) for metric in METRICS]
        right.barh([i + offset for i in range(len(METRICS))], values, height=.25,
                   color=COLORS[version], label="Baseline" if version == "baseline" else "Current")
        for index, (value, total) in enumerate(zip(values, totals)):
            right.text(value + .12, index + offset, f"{value}/{total}", va="center", fontsize=8)
    right.set(yticks=range(len(METRICS)), yticklabels=metric_labels, xlim=(0, 10.5), xticks=[0, 3, 6, 9], xlabel="Requirements met")
    right.invert_yaxis()
    right.spines[["left"]].set_visible(False)
    right.tick_params(axis="y", length=0)
    handles, legend_labels = right.get_legend_handles_labels()
    fig.legend(handles, legend_labels, frameon=False, loc="upper right", bbox_to_anchor=(.975, .89), ncol=2, fontsize=8)
    right.set_title("Observable outcomes", loc="left", fontweight="bold", pad=12)
    fig.suptitle("PDF identity and attachment regression checks", x=.04, ha="left", fontsize=13, fontweight="bold")
    fig.text(.04, .055, "* Real Aarde PDF, read-only. Eight synthetic controls. Fixed registry fixtures; no download/network benchmark.", fontsize=8, color="#475569")
    fig.text(.04, .02, "Descriptive counts only. Engineered cases do not estimate population performance or statistical significance.", fontsize=8, color="#475569")
    fig.tight_layout(rect=(.02, .105, .99, .91), w_pad=2.4)
    for extension in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"H_pdf_identity.{extension}", dpi=DPI, facecolor="white")
    # Matplotlib adds trailing spaces to SVG paths; remove them without changing geometry.
    svg = OUT / "H_pdf_identity.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(fig)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "measure"
    if action == "measure":
        measure()
    elif action == "plot":
        plot()
    else:
        raise SystemExit("Usage: benchmark_pdf_identity.py [measure|plot]")
