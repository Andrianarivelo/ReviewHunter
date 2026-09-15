"""Audit detailed methodology and word maps on an explicitly synthetic library.

Create a new fixture with ``tools/insight_fixture.py --detailed``, run its app on
localhost:8775 with REVIEWHUNTER_HOME set to that fixture, then pass its manifest
using --fixture. Screenshots and JSON results go to a new timestamped directory.
Normal runs never overwrite artifacts. The explicit --exports-only mode refreshes
exports for the identical synthetic fixture while retaining its UI screenshots.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse
import urllib.request

from insight_fixture import ROOT, TEMP_ROOT
from ui_snapshot import CDP, check_insight_exports, find_chrome, free_port

# Human-review viewports and layout acceptance values are intentionally editable.
VIEWPORTS = {"desktop": (1600, 1000), "laptop": (1366, 768)}
THEMES = ("light", "dark")
READY_SECONDS = 25
MIN_WORD_FONT_RATIO = 1.3
MIN_WORD_COLORS = 3


async def script(cdp: CDP, body: str):
    """Evaluate an async JavaScript body and await its complete, serializable result."""
    # Alpine's reactive proxies otherwise serialize to empty objects through CDP.
    return await cdp.eval("(async () => { const value = await (async () => {" + body +
                          "})(); return value === undefined ? null : JSON.parse(JSON.stringify(value)); })()")


async def ready(cdp: CDP, condition: str) -> None:
    """Poll a DOM or app-state predicate without assuming fixed load durations."""
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        if await cdp.eval(condition):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"Page did not become ready: {condition}")


async def settle(cdp: CDP) -> None:
    """Let Alpine and the next two animation frames finish before inspecting SVG."""
    await script(cdp, "await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));")


async def capture(cdp: CDP, directory: Path, name: str) -> dict:
    """Save the current visual and report overflow, chip count and word typography."""
    await settle(cdp)
    metrics = await script(cdp, """
      const svg = document.querySelector('#insight-svg');
      const words = [...document.querySelectorAll('#insight-svg .insight-node text')];
      const sizes = words.map(n => Number.parseFloat(getComputedStyle(n).fontSize)).filter(Number.isFinite);
      const chips = [...document.querySelectorAll('#insight-svg .insight-method-chip rect')];
      const bounds = chips.map(n => n.getBoundingClientRect());
      let overlaps = 0;
      for(let i=0;i<bounds.length;i++) for(let j=i+1;j<bounds.length;j++) {
        const a=bounds[i], b=bounds[j];
        if(a.left < b.right-1 && a.right > b.left+1 && a.top < b.bottom-1 && a.bottom > b.top+1) overlaps++;
      }
      return {scope:detailApp.insight.data.scope, svg:!!svg, chips:chips.length,
        chipOverlaps:overlaps, wordTexts:words.length, fontSizes:[...new Set(sizes)].sort((a,b)=>a-b),
        colors:[...new Set(words.map(n=>getComputedStyle(n).fill))],
        documentOverflow:document.documentElement.scrollWidth>innerWidth+2,
        text:svg?.textContent || ''};
    """)
    if not metrics["svg"] or metrics["documentOverflow"] or metrics["chipOverlaps"]:
        raise AssertionError(f"Visual geometry failed for {name}: {metrics}")
    await cdp.screenshot(directory / f"{name}.png")
    metrics.pop("text", None)
    return metrics


async def verify_fixture(cdp: CDP, manifest: dict) -> None:
    """Confirm the connected app contains exactly the requested synthetic papers."""
    await ready(cdp, "typeof Alpine !== 'undefined' && !!document.querySelector('[x-data]')")
    await cdp.eval("window.detailApp = Alpine.$data(document.querySelector('[x-data]'))")
    actual = await script(cdp, "return (await (await fetch('/api/items')).json()).map(p=>({id:p.id,title:p.title}));")
    if {p["id"] for p in actual} != set(manifest["items"]) or {p["title"] for p in actual} != set(manifest["titles"]):
        raise AssertionError("The dev server is not using the requested synthetic library")
    await ready(cdp, "typeof detailApp.openInsight === 'function'")


async def audit_page(cdp: CDP, manifest: dict, directory: Path) -> dict:
    """Verify fixture identity and collect details from actual rendered maps."""
    await verify_fixture(cdp, manifest)
    await script(cdp, "detailApp.selectVirtual('all'); await detailApp.openInsight('methods');")
    await ready(cdp, "!detailApp.insight.loading && !!detailApp.insight.data.papers")
    detected = await script(cdp, "return [...new Set(detailApp.insight.data.papers.flatMap(p=>p.detected.map(m=>m.id)))];")
    missing = sorted(set(manifest["detail_expectations"]["method_ids"]) - set(detected))
    if missing:
        raise AssertionError(f"Synthetic detailed methods were not detected: {missing}")
    report = {"method_ids": detected, "views": {}}
    by_paper = await script(cdp, "return Object.fromEntries(detailApp.insight.data.papers.map(p=>[p.id,p.detected.map(m=>m.id)]));")
    for expectation in manifest["detail_expectations"].get("paper_expectations", []):
        actual_ids = set(by_paper[str(expectation["id"])])
        missing = set(expectation["present"]) - actual_ids
        incorrect = set(expectation["absent"]) & actual_ids
        if missing or incorrect:
            raise AssertionError(f"Paper {expectation['id']} detail identity failed: missing={sorted(missing)}, false positives={sorted(incorrect)}")
    report["per_paper_method_ids"] = by_paper
    report["views"]["methods-library"] = await capture(cdp, directory, "methods-library")
    await script(cdp, "detailApp.insight.scope='tag:detail-stress'; await detailApp.loadInsight();")
    await ready(cdp, "!detailApp.insight.loading && detailApp.insight.data.scope.items===1")
    report["views"]["methods-dense"] = await capture(cdp, directory, "methods-dense")
    await script(cdp, "document.querySelector('#insight-svg [data-method=drug_cocaine]').dispatchEvent(new MouseEvent('click',{bubbles:true}));")
    if not await cdp.eval("detailApp.insight.selected?.evidence?.length>0"):
        raise AssertionError("Clicking a method did not reveal its evidence")
    report["views"]["methods-evidence"] = await capture(cdp, directory, "methods-evidence")
    await script(cdp, "await detailApp.resetInsightScope(); await detailApp.openInsight('words');")
    await ready(cdp, "!detailApp.insight.loading && !!detailApp.insight.data.words")
    report["views"]["words-frequency"] = await capture(cdp, directory, "words-frequency")
    word_metrics = report["views"]["words-frequency"]
    if len(word_metrics["fontSizes"]) < 3 or word_metrics["fontSizes"][-1] / word_metrics["fontSizes"][0] < MIN_WORD_FONT_RATIO:
        raise AssertionError("Word font sizes do not express frequency differences")
    if len(word_metrics["colors"]) < MIN_WORD_COLORS:
        raise AssertionError("Word cloud did not distinguish at least three concept categories")
    report["method_categories"] = await script(cdp, "return (await (await fetch('/api/insight/methods')).json()).matrix.map(m=>({id:m.id,category:m.category}));")
    categories = {m.get("category") for m in report["method_categories"]}
    if not {"drugs", "signals", "techniques_tools"} <= categories:
        raise AssertionError("Detailed methodology categories are incomplete")
    await script(cdp, "detailApp.insight.wordConcept='neural'; detailApp.renderInsight();")
    await settle(cdp)
    if not await cdp.eval("document.querySelectorAll('#insight-svg [data-concept=neural]').length>0 && [...document.querySelectorAll('#insight-svg .insight-node')].every(n=>n.dataset.concept==='neural')"):
        raise AssertionError("Concept filter displayed unrelated categories")
    report["views"]["words-concept-neural"] = await capture(cdp, directory, "words-concept-neural")
    await script(cdp, "const control=document.querySelector('[aria-label=\"Word group\"]');control.value='rare';control.dispatchEvent(new Event('change',{bubbles:true}));")
    await settle(cdp)
    rare_word = json.dumps(manifest["detail_expectations"]["single_occurrence_word"])
    rare_check = await script(cdp, f"""
      const wanted=detailApp.insightWords().find(w=>w.word==={rare_word});
      return {{wanted, allOnce:detailApp.insightWords().every(w=>w.count===1 && w.rare),
        markers:document.querySelectorAll('#insight-svg [data-rare=true]').length}};
    """)
    if not rare_check.get("wanted") or not rare_check["allOnce"] or not rare_check["markers"]:
        raise AssertionError(f"Single-occurrence filter failed: {rare_check}")
    report["rare_word"] = rare_check["wanted"]
    report["views"]["words-rare"] = await capture(cdp, directory, "words-rare")
    await script(cdp, f"detailApp.inspectWord(detailApp.insightWords().find(w=>w.word==={rare_word}));")
    if not await cdp.eval("detailApp.insight.selected?.evidence?.length>0"):
        raise AssertionError("Rare word did not reveal its source sentence")
    report["views"]["words-rare-evidence"] = await capture(cdp, directory, "words-rare-evidence")
    parent = manifest["collections"]["parent"]
    await script(cdp, f"detailApp.insight.scope='collection:{parent}';detailApp.insight.includeSub=true;await detailApp.loadInsight();")
    await ready(cdp, "!detailApp.insight.loading && detailApp.insight.data.scope.comparison_available")
    await script(cdp, "const control=document.querySelector('[aria-label=\"Word group\"]');control.value='unusual';control.dispatchEvent(new Event('change',{bubbles:true}));")
    await settle(cdp)
    subset_word = json.dumps(manifest["detail_expectations"]["subset_word"])
    unusual_check = await script(cdp, f"""
      const wanted=detailApp.insightWords().find(w=>w.word==={subset_word});
      return {{wanted, allUnusual:detailApp.insightWords().every(w=>w.unusual && w.count>=2 && w.rest_count===0),
        markers:document.querySelectorAll('#insight-svg [data-unusual=true]').length}};
    """)
    if not unusual_check.get("wanted") or not unusual_check["allUnusual"] or not unusual_check["markers"]:
        raise AssertionError(f"Scope-specific unusual-term filter failed: {unusual_check}")
    report["unusual_word"] = unusual_check["wanted"]
    report["views"]["words-unusual"] = await capture(cdp, directory, "words-unusual")
    return report


async def audit_exports(cdp: CDP, directory: Path) -> dict:
    """Verify the actual four-map export Blobs, including filtered full-text word data."""
    report = {}
    await cdp.eval("window.rhTestApp=detailApp")
    for kind in ("concepts", "goals", "methods", "words"):
        await script(cdp, f"await detailApp.resetInsightScope(); await detailApp.openInsight('{kind}');")
        if kind == "words":
            await script(cdp, "detailApp.insight.source='fulltext';await detailApp.loadInsight();detailApp.setInsightWordPool('rare');")
        await settle(cdp)
        target = directory / kind
        target.mkdir(parents=True, exist_ok=True)
        await script(cdp, """
          window.detailSvgExports=[]; window.detailOriginalCreate=URL.createObjectURL;
          URL.createObjectURL=function(blob){if(blob.type==='image/svg+xml')detailSvgExports.push(blob);return detailOriginalCreate.call(URL,blob);};
        """)
        try:
            # The established helper uses explicit top-level await expressions.
            cdp.repl_mode = True
            report[kind] = await check_insight_exports(cdp, kind, target)
        finally:
            cdp.repl_mode = False
            await script(cdp, "URL.createObjectURL=detailOriginalCreate;delete window.detailOriginalCreate;")
        svg_text = await script(cdp, "return await detailSvgExports.at(-1).text();")
        (target / f"export-{kind}.svg").write_text(svg_text, encoding="utf-8")
        payload = json.loads((target / f"export-{kind}.json").read_text(encoding="utf-8"))
        with (target / f"export-{kind}.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if kind == "methods":
            if len(rows) != len(payload["papers"]):
                raise AssertionError("Method matrix CSV omitted papers")
            required = {m["label"] for m in payload["matrix"] if m.get("category") in ("drugs", "signals", "techniques_tools")}
            if not required <= set(rows[0]):
                raise AssertionError("Method matrix CSV omitted detailed method columns")
            report[kind]["detailed_columns"] = len(required)
        if kind == "words":
            union = {w["word"] for pool in ("words", "rare_words", "outlier_words") for w in payload.get(pool, [])}
            if {row["word"] for row in rows} != union:
                raise AssertionError("Word CSV did not preserve the union of all three pools")
            if payload["scope"]["source"] != "fulltext":
                raise AssertionError("Filtered word export changed its full-text source")
            legend = await script(cdp, "return detailApp.insightWordLegend().map(c=>c.label);")
            missing = await script(cdp, "const content=new DOMParser().parseFromString(await detailSvgExports.at(-1).text(),'image/svg+xml').documentElement.textContent;return detailApp.insightWordLegend().filter(c=>!content.includes(c.label)).map(c=>c.label);")
            if missing or not legend:
                raise AssertionError(f"Word PNG source omitted concept legend: {missing}")
            if 'text:not([font-family])' not in svg_text or 'font-family="Georgia,serif"' not in svg_text:
                raise AssertionError("Word PNG source overrides the measured Georgia cloud font")
            report[kind].update({"csv_union_terms": len(union), "legend_categories": legend, "source": "fulltext", "visible_pool": "rare"})
            report[kind]["measured_font_preserved"] = True
        await script(cdp, "delete window.detailSvgExports;")
    return report


async def run(args: argparse.Namespace) -> dict:
    """Use a new headless Chrome profile for every theme and viewport combination."""
    import websockets

    manifest = json.loads(args.fixture.read_text(encoding="utf-8"))
    address = urlparse(args.url)
    home = Path(manifest.get("home", "")).resolve()
    if address.hostname not in ("127.0.0.1", "localhost") or address.port != 8775:
        raise ValueError("Only isolated localhost:8775 is permitted")
    if not manifest.get("synthetic") or not manifest.get("detailed") or not home.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError("A detailed synthetic fixture beneath Temp/claude is required")
    destination = args.out.resolve()
    if args.exports_only:
        report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        if not report.get("synthetic") or report["fixture"] != manifest:
            raise ValueError("Export refresh requires an existing report for this exact synthetic fixture")
    else:
        destination.mkdir(parents=True, exist_ok=False)
        report = {"synthetic": True, "fixture": manifest, "url": args.url, "runs": {}}
    for layout, (width, height) in VIEWPORTS.items():
        for theme in THEMES:
            key = f"{layout}-{theme}"
            if args.exports_only and key != "desktop-dark":
                continue
            directory = destination / key
            directory.mkdir(exist_ok=args.exports_only)
            profile = Path(tempfile.mkdtemp(prefix="insight-detail-chrome-"))
            port = free_port()
            process = subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--hide-scrollbars", f"--window-size={width},{height}", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                        await cdp.send("Page.enable")
                        await cdp.send("Runtime.enable")
                        await cdp.send("Log.enable")
                        await cdp.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
                        await cdp.send("Emulation.setEmulatedMedia", features=[{"name":"prefers-color-scheme", "value":theme}])
                        await cdp.send("Page.navigate", url=args.url)
                        if args.exports_only:
                            await verify_fixture(cdp, manifest)
                        else:
                            report["runs"][key] = await audit_page(cdp, manifest, directory)
                        if key == "desktop-dark":
                            report["exports"] = await audit_exports(cdp, directory / "exports")
                        report["runs"][key]["console_errors"] = cdp.console_errors
                        if cdp.console_errors:
                            raise AssertionError(f"Console errors: {cdp.console_errors}")
                    finally:
                        listener.cancel()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                # tempfile supplied this absolute directory; only this known profile is removed.
                if profile.resolve().parent != Path(tempfile.gettempdir()).resolve():
                    raise ValueError("Refusing to clean a Chrome profile outside the system temp directory")
                shutil.rmtree(profile, ignore_errors=True)
            (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Passed {key}", flush=True)
    return report


def main() -> None:
    """Parse paths, refuse overwritten artifacts and run the browser audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8775/")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "insight-details" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--exports-only", action="store_true", help="Refresh only exports in an existing report for the exact same fixture")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps({"runs": len(report["runs"]), "artifacts": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
