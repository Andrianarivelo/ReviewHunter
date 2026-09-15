"""Audit research-question cards and collection unions against disposable data.

Create ``insight_fixture.py --goals`` and ask the development launcher to serve
that home on port 8775. This script only reads that synthetic library and writes
screenshots, export files and a machine-readable report into a new directory.
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


async def capture(cdp: CDP, folder: Path, name: str) -> dict:
    """Check each full SVG card, including below-fold cards, before capture."""
    await settle(cdp)
    metrics = await script(cdp, """
      const cards=[...document.querySelectorAll('#insight-svg .insight-goal-card')];
      const boxes=cards.map(g=>g.querySelector('rect').getBoundingClientRect());
      const clipped=[];let overlaps=0;
      cards.forEach((g,i)=>{const b=boxes[i];for(const t of g.querySelectorAll('text')){
        const r=t.getBoundingClientRect();
        if(r.left<b.left-2||r.right>b.right+2||r.top<b.top-2||r.bottom>b.bottom+2)
          clipped.push({paper:g.dataset.paper,text:t.textContent});
      }});
      boxes.forEach((a,i)=>boxes.slice(i+1).forEach(b=>{
        if(a.left<b.right-2&&a.right>b.left+2&&a.top<b.bottom-2&&a.bottom>b.top+2)overlaps++;
      }));
      return {cards:cards.length,cardIds:cards.map(g=>Number(g.dataset.paper)),
        statuses:cards.map(g=>g.dataset.focusStatus),clipped,overlaps,
        scopeControl:document.querySelector('[aria-label="Insight scope"]').value,
        scopeState:detailApp.insight.scope,
        documentOverflow:document.documentElement.scrollWidth>innerWidth+2,
        selected:!!detailApp.insight.selected,scope:detailApp.insight.data.scope};
    """)
    await cdp.screenshot(folder / f"{name}.png")
    if metrics["clipped"] or metrics["overlaps"] or metrics["documentOverflow"] or metrics["scopeControl"] != metrics["scopeState"]:
        raise AssertionError(f"Card geometry failed {name}: {metrics}")
    return metrics


async def choose_collections(cdp: CDP, ids: list[int]) -> None:
    """Use the actual checkbox dialog and Apply button, including deselection."""
    await script(cdp, "document.querySelector('.insight-collection-trigger').click();")
    await settle(cdp)
    await script(cdp, f"""
      const wanted={json.dumps(ids)}, choices=detailApp.insightCollectionChoices();
      [...document.querySelectorAll('.insight-collection-choices label')].forEach((label,i)=>{{
        const input=label.querySelector('input');
        if(input.checked!==wanted.includes(choices[i].id))input.click();
      }});
    """)
    await settle(cdp)
    await script(cdp, "document.querySelector('.insight-apply-collections').click();")
    await ready(cdp, "!detailApp.insight.loading && !detailApp.insight.collectionPicker")
    error = await cdp.eval("detailApp.insight.error")
    if error:
        raise AssertionError(f"Applying collection union failed: {error}")


async def set_query(cdp: CDP, text: str) -> None:
    """Exercise the search field through its real input handler."""
    await script(cdp, f"const q=document.querySelector('[aria-label=\"Search research questions\"]');q.value={json.dumps(text)};q.dispatchEvent(new Event('input',{{bubbles:true}}));")
    await asyncio.sleep(0.25)
    await settle(cdp)


async def audit_page(cdp: CDP, manifest: dict, folder: Path) -> dict:
    """Validate provenance, filters, evidence, negative relations and scope unions."""
    await verify_fixture(cdp, manifest)
    await script(cdp, "detailApp.selectVirtual('all');await detailApp.openInsight('goals');")
    await ready(cdp, "!detailApp.insight.loading && !!detailApp.insight.data.papers")
    data = await script(cdp, "return detailApp.insight.data;")
    papers = {p["id"]: p for p in data["papers"]}
    expected = manifest["goal_expectations"]
    for item_id, status in expected["statuses"].items():
        assert papers[int(item_id)]["focus_status"] == status
    negative = papers[expected["negative"]]["pairs"]
    assert negative and negative[0]["negated"] and negative[0]["relation_label"] == "No effect reported"
    assert papers[expected["malformed"]]["pairs"] == []
    assert papers[expected["passive"]]["pairs"][0]["outcome"] == "methamphetamine self-administration"
    assert any(pair["outcome"] == "stimulant intake" and pair["source"] == "abstract"
               for pair in papers[expected["groups"]]["pairs"])
    report = {"provenance": {i: p["focus_status"] for i, p in papers.items()}, "views": {}}
    methods = await script(cdp, "return (await (await fetch('/api/insight/methods')).json()).papers;")
    detected = {p["id"]: {m["id"] for m in p["detected"]} for p in methods}
    assert {"drug_mdma", "tool_icss", "stimulation"} <= detected[expected["negative"]]
    assert {"betweensubject", "doseresponse", "factorial", "environment", "temperature"} <= detected[expected["groups"]]
    report["method_design_checks"] = {i: sorted(detected[i]) for i in (expected["negative"], expected["groups"])}
    report["views"]["atlas-all"] = await capture(cdp, folder, "atlas-all")
    assert report["views"]["atlas-all"]["cards"] == len(manifest["items"])
    # Question-type and theme buttons must filter the same paper records shown.
    await script(cdp, "document.querySelector('.insight-goal-types button:nth-of-type(2)').click();")
    await settle(cdp)
    assert await cdp.eval("detailApp.goalFilteredPapers().length>0 && detailApp.goalFilteredPapers().every(p=>p.goal_type===detailApp.insight.goalKind)")
    await script(cdp, "detailApp.clearGoalFilters();document.querySelector('.insight-goal-themes button').click();")
    await settle(cdp)
    assert await cdp.eval("detailApp.goalFilteredPapers().length>0 && detailApp.goalFilteredPapers().every(p=>p.themes.some(t=>t.id===detailApp.insight.goalTheme))")
    await script(cdp, "detailApp.clearGoalFilters();")
    await set_query(cdp, "fails to elicit")
    assert await cdp.eval("detailApp.goalFilteredPapers().length===1")
    assert await cdp.eval("document.querySelector('#insight-svg').textContent.includes('No effect reported')")
    report["views"]["atlas-negative"] = await capture(cdp, folder, "atlas-negative")
    await script(cdp, "document.querySelector('#insight-svg .insight-goal-card').dispatchEvent(new MouseEvent('click',{bubbles:true}));")
    await settle(cdp)
    assert await cdp.eval("detailApp.insight.selected.evidence.length>0")
    report["views"]["atlas-evidence"] = await capture(cdp, folder, "atlas-evidence")
    await script(cdp, "detailApp.closeInsightEvidence();")
    await settle(cdp)
    assert await cdp.eval("!detailApp.insight.selected")
    await script(cdp, "[...document.querySelectorAll('.insight-segment button')].find(b=>b.textContent.includes('connections')).click();")
    report["views"]["connections-negative"] = await capture(cdp, folder, "connections-negative")
    assert await cdp.eval("document.querySelector('#insight-svg').textContent.includes('No effect reported')")
    await script(cdp, "detailApp.insight.goalView='atlas';detailApp.clearGoalFilters();")
    # All three no-aim cases remain visible, with title provenance or unavailable.
    await script(cdp, "detailApp.insight.goalKind='unknown';detailApp.filterGoals();")
    report["views"]["atlas-fallbacks"] = await capture(cdp, folder, "atlas-fallbacks")
    assert expected["unavailable"] in report["views"]["atlas-fallbacks"]["cardIds"]
    await script(cdp, "detailApp.clearGoalFilters();")
    parent, child, sibling = (manifest["collections"][n] for n in ("parent", "child", "sibling"))
    await choose_collections(cdp, [parent, child])
    assert await cdp.eval("detailApp.insight.data.scope.items") == len(manifest["descendant_items"])
    await choose_collections(cdp, [parent, sibling])
    expected_union = len(manifest["union_items"])
    assert await cdp.eval("detailApp.insight.data.scope.items") == expected_union
    await settle(cdp)
    assert await cdp.eval("document.querySelector('[aria-label=\"Insight scope\"]').value === 'collections'")
    report["views"]["atlas-union"] = await capture(cdp, folder, "atlas-union")
    await script(cdp, "document.querySelector('.insight-collection-trigger').click();")
    report["views"]["collection-picker"] = await capture(cdp, folder, "collection-picker")
    await script(cdp, "detailApp.insight.collectionPicker=false;")
    report["scope_maps"] = {}
    for kind in ("concepts", "words", "methods", "goals"):
        await script(cdp, f"await detailApp.openInsight('{kind}');")
        scope = await script(cdp, "return detailApp.insight.data.scope;")
        assert scope["items"] == expected_union and scope["collection_ids"] == [parent, sibling], scope
        report["scope_maps"][kind] = scope
    await script(cdp, "detailApp.insight.includeSub=false;await detailApp.loadInsight();")
    assert await cdp.eval("detailApp.insight.data.scope.items") == len(manifest["parent_items"]) + len(manifest["sibling_items"])
    await script(cdp, "detailApp.insight.includeSub=true;await detailApp.resetInsightScope();")
    assert await cdp.eval("detailApp.insight.data.scope.items") == len(manifest["items"])
    await settle(cdp)
    assert await cdp.eval("document.querySelector('[aria-label=\"Insight scope\"]').value === 'all'")
    await script(cdp, "document.querySelector('.insight-collection-trigger').click();")
    await settle(cdp)
    assert await cdp.eval("document.querySelector('.insight-apply-collections').disabled")
    await script(cdp, "detailApp.insight.collectionPicker=false;")
    # Existing single-collection and author options must survive dynamic mounting.
    report["legacy_scope_controls"] = []
    for value in (f"collection:{parent}", "author"):
        chosen = await script(cdp, f"""
          const select=document.querySelector('[aria-label="Insight scope"]');
          const value={json.dumps(value)}==='author'
            ? [...select.options].find(option=>option.value.startsWith('author:'))?.value
            : {json.dumps(value)};
          if(!value)throw new Error('Expected synthetic author option is absent');
          select.value=value;select.dispatchEvent(new Event('change',{{bubbles:true}}));return value;
        """)
        await ready(cdp, "!detailApp.insight.loading")
        await settle(cdp)
        actual = await script(cdp, "return {value:document.querySelector('[aria-label=\"Insight scope\"]').value,state:detailApp.insight.scope,scope:detailApp.insight.data.scope};")
        assert actual["value"] == actual["state"] == chosen and actual["scope"]["items"] > 0, actual
        report["legacy_scope_controls"].append(actual)
    await script(cdp, "await detailApp.resetInsightScope();")
    await settle(cdp)
    assert await cdp.eval("document.querySelector('[aria-label=\"Insight scope\"]').value === 'all'")
    return report


async def audit_exports(cdp: CDP, manifest: dict, folder: Path) -> dict:
    """Ensure filtered displays still export every scoped paper with provenance."""
    await set_query(cdp, "fails to elicit")
    await cdp.eval("window.rhTestApp=detailApp")
    cdp.repl_mode = True
    try:
        results = await check_insight_exports(cdp, "goals", folder)
    finally:
        cdp.repl_mode = False
    payload = json.loads((folder / "export-goals.json").read_text(encoding="utf-8"))
    with (folder / "export-goals.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    id_key = "paper_id" if "paper_id" in rows[0] else "id"
    assert {int(r[id_key]) for r in rows} == set(manifest["items"]), rows[0]
    assert {p["id"] for p in payload["papers"]} == set(manifest["items"])
    assert any(r.get("negated") == "true" for r in rows), rows[0]
    results.update({"complete_paper_count": len(manifest["items"]), "visible_filter": "fails to elicit", "columns": list(rows[0])})
    return results


async def run(args: argparse.Namespace) -> dict:
    """Launch disposable Chrome profiles and refuse non-synthetic server targets."""
    import websockets

    manifest = json.loads(args.fixture.read_text(encoding="utf-8"))
    address = urlparse(args.url)
    home = Path(manifest.get("home", "")).resolve()
    if address.hostname not in ("127.0.0.1", "localhost") or address.port != 8775:
        raise ValueError("Only disposable localhost:8775 is permitted")
    if not manifest.get("synthetic") or not manifest.get("goals") or not home.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError("A synthetic --goals fixture beneath Temp/claude is required")
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    source_files = ["doi_hunter_web/insight.py", "doi_hunter_web/goal_extraction.py",
                    "doi_hunter_web/static/js/goal_views.js", "doi_hunter_web/static/js/insights.js"]
    report = {"synthetic": True, "fixture": manifest, "url": args.url, "runs": {},
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files}}
    for layout, (width, height) in VIEWPORTS.items():
        for theme in THEMES:
            key = f"{layout}-{theme}"
            folder = destination / key
            folder.mkdir()
            profile = Path(tempfile.mkdtemp(prefix="insight-goals-chrome-"))
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
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "insight-goals" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps({"runs": len(report["runs"]), "artifacts": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
