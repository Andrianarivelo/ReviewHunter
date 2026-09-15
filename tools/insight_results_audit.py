"""Validate concept analytics and reported-result comparisons on synthetic data.

Create ``insight_fixture.py --results`` and serve its isolated home on 8775.
Every run verifies exact fixture identity, opens disposable Chrome profiles and
writes new screenshots and exported files. The real app/library are never used.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlparse

from insight_fixture import ROOT, TEMP_ROOT
from insight_detail_audit import ready, script, settle, verify_fixture
from ui_snapshot import CDP, check_insight_exports, find_chrome, free_port

VIEWPORTS = {"desktop": (1600, 1000), "laptop": (1366, 768)}
THEMES = ("light", "dark")


async def scroll_view(cdp: CDP, graph: bool = False) -> None:
    """Expose either the analytical overview or the rendered graph in the viewport."""
    await script(cdp, f"""
      const pane=document.querySelector('.insight-scroll'),svg=document.querySelector('#insight-svg');
      const top={str(graph).lower()} && svg ? pane.scrollTop+svg.getBoundingClientRect().top-pane.getBoundingClientRect().top-12 : 0;
      pane.scrollTo({{top,behavior:'instant'}});
    """)
    await settle(cdp)


async def capture(cdp: CDP, folder: Path, name: str, *, graph: bool = False) -> dict:
    """Check full labels and card boundaries across all rendered SVG elements."""
    await scroll_view(cdp, graph)
    metrics = await script(cdp, """
      const cards=[...document.querySelectorAll('#insight-svg .insight-concept-card,#insight-svg .insight-goal-card,#insight-svg .insight-finding')];
      let clipped=[],overlaps=[];
      const boxes=cards.map(g=>g.querySelector('rect').getBoundingClientRect());
      cards.forEach((g,i)=>{const b=boxes[i];for(const text of g.querySelectorAll('text')){
        const r=text.getBoundingClientRect();
        if(r.left<b.left-2||r.right>b.right+2||r.top<b.top-2||r.bottom>b.bottom+2)clipped.push(text.textContent);
      }});
      boxes.forEach((a,i)=>boxes.slice(i+1).forEach((b,j)=>{
        if(cards[i].contains(cards[i+j+1])||cards[i+j+1].contains(cards[i]))return;
        if(a.left<b.right-2&&a.right>b.left+2&&a.top<b.bottom-2&&a.bottom>b.top+2)overlaps.push([i]);
      }));
      const labels=[...document.querySelectorAll('#insight-svg .insight-node:not(.insight-concept-card)')].map(g=>g.getBoundingClientRect());
      let matrixLabelOverlaps=0;
      labels.forEach((a,i)=>labels.slice(i+1).forEach(b=>{if(a.top<b.bottom-1&&a.bottom>b.top+1)matrixLabelOverlaps++;}));
      const nodes=[...document.querySelectorAll('#insight-svg .insight-concept-card')];
      const complete=nodes.every(g=>{
        const node=detailApp.insight.data.nodes.find(n=>n.id===g.dataset.node);
        return [...g.querySelectorAll('text')].map(t=>t.textContent).join(' ').replace(/\\s+/g,' ').includes(node.label);
      });
      return {cards:cards.length,nodes:nodes.length,clipped,overlaps,matrixLabelOverlaps,complete,
        matrixCells:document.querySelectorAll('#insight-svg .insight-matrix-cell').length,
        findingRows:document.querySelectorAll('#insight-svg .insight-finding').length,
        overflow:document.documentElement.scrollWidth>innerWidth+2,selected:!!detailApp.insight.selected};
    """)
    await cdp.screenshot(folder / f"{name}.png")
    assert not metrics["clipped"] and not metrics["overlaps"] and not metrics["matrixLabelOverlaps"] and not metrics["overflow"] and metrics["complete"], (name, metrics)
    return metrics


async def select_button(cdp: CDP, label: str) -> None:
    """Click a real segmented control by its complete visible label."""
    await script(cdp, f"[...document.querySelectorAll('.insight-segment button')].find(b=>b.textContent.trim()==={json.dumps(label)}).click();")
    await settle(cdp)


async def audit_page(cdp: CDP, manifest: dict, folder: Path) -> dict:
    """Exercise the new visualizations and their source-evidence drilldowns."""
    await verify_fixture(cdp, manifest)
    expected = manifest["result_expectations"]
    await script(cdp, "detailApp.selectVirtual('all');await detailApp.openInsight('concepts');")
    await ready(cdp, "!detailApp.insight.loading && !!detailApp.insight.data.analytics")
    report = {"views": {}}
    assert await cdp.eval("detailApp.insight.data.nodes.some(n=>n.broad_metadata) && detailApp.visibleConcepts().every(n=>!n.broad_metadata && n.count>=2)")
    assert await cdp.eval(f"detailApp.visibleConcepts().some(n=>n.label==={json.dumps(expected['long_concept'])})")
    report["concept_summary"] = await script(cdp, "return detailApp.insight.data.analytics.summary;")
    report["views"]["concept-overview"] = await capture(cdp, folder, "concept-overview")
    report["views"]["concept-network"] = await capture(cdp, folder, "concept-network", graph=True)
    assert report["views"]["concept-network"]["nodes"] >= 4
    await script(cdp, "document.querySelector('#insight-svg .insight-concept-card').dispatchEvent(new MouseEvent('click',{bubbles:true}));")
    assert await cdp.eval("detailApp.insight.selected.evidence.length>0")
    report["views"]["concept-evidence"] = await capture(cdp, folder, "concept-evidence", graph=True)
    await script(cdp, "detailApp.closeInsightEvidence();")
    await select_button(cdp, "Overlap matrix")
    report["views"]["concept-matrix"] = await capture(cdp, folder, "concept-matrix", graph=True)
    assert report["views"]["concept-matrix"]["matrixCells"] == (await cdp.eval("detailApp.visibleConcepts().length")) ** 2
    # A capped edge list must not turn actual shared memberships into zero cells.
    matrix_complete = await script(cdp, """
      const before=document.querySelector('#insight-svg').textContent,edges=detailApp.insight.data.edges;
      detailApp.insight.data.edges=[];detailApp.renderInsight();await new Promise(r=>requestAnimationFrame(r));
      const unchanged=document.querySelector('#insight-svg').textContent===before;
      detailApp.insight.data.edges=edges;detailApp.renderInsight();return unchanged;
    """)
    assert matrix_complete
    await scroll_view(cdp)
    await script(cdp, "document.querySelector('.insight-analysis-grid > div:nth-child(2) button').click();")
    assert await cdp.eval("detailApp.insight.selected.evidence.length>0 && detailApp.insight.selected.items.length>0")
    report["views"]["concept-link-evidence"] = await capture(cdp, folder, "concept-link-evidence")
    await script(cdp, "detailApp.closeInsightEvidence();document.querySelector('.insight-analysis-grid > div:nth-child(3) button').click();")
    assert await cdp.eval("detailApp.insight.selected.evidence.length>0 && detailApp.insight.selected.items.length===1")
    await script(cdp, "detailApp.closeInsightEvidence();detailApp.insight.conceptBroad=true;detailApp.insight.conceptSingles=true;detailApp.renderInsight();")
    assert await cdp.eval("detailApp.visibleConcepts().some(n=>n.broad_metadata)")
    await script(cdp, "detailApp.insight.conceptBroad=false;detailApp.insight.conceptSingles=false;await detailApp.openInsight('goals');")
    await ready(cdp, "!detailApp.insight.loading && !!detailApp.insight.data.result_comparisons")
    payload = await script(cdp, "return detailApp.insight.data;")
    groups = payload["result_comparisons"]
    comparison = next(g for g in groups if {f["paper_id"] for f in g["findings"]} == set(expected["comparison_ids"]))
    assert comparison["direction_counts"] == expected["directions"] and comparison["minority_directions"] == ["decrease"]
    assert expected["different_context_id"] not in {f["paper_id"] for f in comparison["findings"]}
    assert next(p for p in payload["papers"] if p["id"] == expected["missing_id"])["findings"] == []
    related = next(g for g in payload["related_outcomes"] if g["outcome"] == "body temperature")
    assert expected["different_context_id"] in {f["paper_id"] for f in related["findings"]}
    assert related["comparison_level"] == "different_contexts" and not related.get("minority_directions")
    report["comparison"] = comparison
    report["related_outcome"] = related
    report["views"]["atlas-results-all"] = await capture(cdp, folder, "atlas-results-all", graph=True)
    await script(cdp, "detailApp.insight.goalQuery='experiment 4';detailApp.filterGoals();")
    report["views"]["atlas-results-focused"] = await capture(cdp, folder, "atlas-results-focused", graph=True)
    assert await cdp.eval("document.querySelector('#insight-svg .insight-finding').textContent.includes('Decrease')")
    await script(cdp, "detailApp.clearGoalFilters();")
    await select_button(cdp, "Outcomes")
    report["views"]["outcomes-overview"] = await capture(cdp, folder, "outcomes-overview")
    report["views"]["outcomes-table"] = await capture(cdp, folder, "outcomes-table", graph=True)
    assert await cdp.eval("document.querySelector('#insight-svg').textContent.includes('No supported directional finding')")
    await select_button(cdp, "Different directions")
    assert await cdp.eval("detailApp.resultComparisons().length===1")
    await script(cdp, "document.querySelector('.insight-comparison').click();")
    evidence = await script(cdp, "return detailApp.insight.selected;")
    assert {p["id"] for p in evidence["items"]} == set(expected["comparison_ids"])
    assert any(e["id"] == expected["decrease_id"] and "decreased" in e["sentence"] for e in evidence["evidence"])
    report["views"]["outcomes-minority-evidence"] = await capture(cdp, folder, "outcomes-minority-evidence")
    await script(cdp, "detailApp.closeInsightEvidence();")
    await script(cdp, "const related=document.querySelector('.insight-related-outcomes');if(!related.open)related.querySelector('summary').click();")
    await settle(cdp)
    await script(cdp, "document.querySelector('.insight-related-outcomes .insight-comparison').click();")
    assert await cdp.eval(f"detailApp.insight.selected.items.some(p=>p.id==={expected['different_context_id']})")
    report["views"]["outcomes-related-evidence"] = await capture(cdp, folder, "outcomes-related-evidence")
    await script(cdp, "detailApp.closeInsightEvidence();")
    await select_button(cdp, "Similar directions")
    assert await cdp.eval("detailApp.resultComparisons().some(g=>g.direction_counts.no_detected_change===2)")
    await select_button(cdp, "All comparisons")
    await script(cdp, "const d=document.querySelector('[aria-label=\"Finding direction\"]');d.value='decrease';d.dispatchEvent(new Event('change',{bubbles:true}));")
    await settle(cdp)
    assert await cdp.eval("detailApp.goalFilteredPapers().length===1")
    filtered_comparison = await script(cdp, "return detailApp.resultComparisons()[0];")
    assert filtered_comparison["direction_counts"] == expected["directions"]
    report["views"]["outcomes-filtered"] = await capture(cdp, folder, "outcomes-filtered", graph=True)
    return report


async def audit_exports(cdp: CDP, manifest: dict, folder: Path) -> dict:
    """Capture the app's real download Blobs for concepts and filtered outcomes."""
    await cdp.eval("window.rhTestApp=detailApp")
    output = {}
    for kind in ("goals", "concepts"):
        if kind == "concepts":
            await script(cdp, "await detailApp.openInsight('concepts');detailApp.insight.conceptView='matrix';detailApp.renderInsight();")
        await settle(cdp)
        target = folder / kind
        cdp.repl_mode = True
        try:
            output[kind] = await check_insight_exports(cdp, kind, target)
        finally:
            cdp.repl_mode = False
        payload = json.loads((target / f"export-{kind}.json").read_text(encoding="utf-8"))
        with (target / f"export-{kind}.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if kind == "goals":
            assert {int(r["paper_id"]) for r in rows} == set(manifest["items"])
            assert any("decreas" in str(r).lower() for r in rows)
            assert any("increase" in str(r).lower() for r in rows)
            assert payload["result_comparisons"]
        else:
            assert payload["analytics"]["strongest_links"] and any(n["broad_metadata"] for n in payload["nodes"])
            assert {"prevalence", "jaccard_overlap", "broad_indexing_metadata"} <= set(rows[0])
            node_rows = [row for row in rows if row["record_type"] == "node"]
            assert {row["id_or_source"] for row in node_rows} == {node["id"] for node in payload["nodes"]}
            assert any(row["broad_indexing_metadata"] == "true" for row in node_rows)
            for row in (row for row in rows if row["record_type"] == "edge"):
                assert 0 < float(row["jaccard_overlap"]) <= 1
                assert len(row["paper_ids"].split(";")) == int(row["paper_count_or_weight"])
        output[kind]["csv_columns"] = list(rows[0])
    return output


async def run(args: argparse.Namespace) -> dict:
    """Launch disposable Chrome profiles and refuse non-synthetic server targets."""
    import websockets

    manifest = json.loads(args.fixture.read_text(encoding="utf-8"))
    address = urlparse(args.url)
    home = Path(manifest.get("home", "")).resolve()
    if address.hostname not in ("127.0.0.1", "localhost") or address.port != 8775:
        raise ValueError("Only disposable localhost:8775 is permitted")
    if not manifest.get("synthetic") or not manifest.get("results") or not home.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError("A synthetic --results fixture beneath Temp/claude is required")
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    source_files = ["doi_hunter_web/insight.py", "doi_hunter_web/goal_extraction.py",
                    "doi_hunter_web/static/js/analysis_views.js", "doi_hunter_web/static/js/goal_views.js",
                    "doi_hunter_web/static/js/insights.js", "doi_hunter_web/result_extraction.py", "doi_hunter_web/concept_analysis.py"]
    report = {"synthetic": True, "fixture": manifest, "url": args.url, "runs": {},
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files}}
    for layout, (width, height) in VIEWPORTS.items():
        for theme in THEMES:
            key = f"{layout}-{theme}"
            folder = destination / key
            folder.mkdir()
            profile = Path(tempfile.mkdtemp(prefix="insight-results-chrome-"))
            port = free_port()
            process = subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--hide-scrollbars", f"--window-size={width},{height}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                targets = []
                for _ in range(80):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as response:
                            targets = [t for t in json.load(response) if t["type"] == "page"]
                        if targets:
                            break
                    except OSError:
                        pass
                    await asyncio.sleep(0.2)
                if not targets:
                    raise RuntimeError("Headless Chrome did not start")
                async with websockets.connect(targets[0]["webSocketDebuggerUrl"], max_size=50*1024*1024) as socket:
                    cdp = CDP(socket)
                    listener = asyncio.create_task(cdp.listen())
                    try:
                        for domain in ("Page", "Runtime", "Log"):
                            await cdp.send(f"{domain}.enable")
                        await cdp.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
                        await cdp.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": theme}])
                        await cdp.send("Page.navigate", url=args.url)
                        report["runs"][key] = await audit_page(cdp, manifest, folder)
                        if key == "desktop-dark":
                            report["exports"] = await audit_exports(cdp, manifest, folder / "exports")
                        report["runs"][key]["console_errors"] = cdp.console_errors
                        assert not cdp.console_errors, cdp.console_errors
                    finally:
                        listener.cancel()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if profile.resolve().parent != Path(tempfile.gettempdir()).resolve():
                    raise ValueError("Refusing to remove a profile outside the system temp folder")
                shutil.rmtree(profile, ignore_errors=True)
            (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Passed {key}", flush=True)
    return report


def main() -> None:
    """Parse isolated paths and write a fresh audit without changing application data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8775/")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "insight-results" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps({"runs": len(report["runs"]), "artifacts": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
