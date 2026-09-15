"""Capture repeatable Insight screenshots and browser geometry on synthetic data.

Run a disposable fixture server on port 8775, then use::

    python tools/insight_visual_audit.py --fixture PATH/fixture.json --out docs/insight-audit/before

This uses the existing ui_snapshot Chrome driver. It never writes library data.
Geometry metrics flag review targets; they are not a substitute for reading PNGs.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

import ui_snapshot


# Keep benchmark cases and viewport sizes stable between before/after runs.
CASES = (
    ("concepts-evidence-light-desktop", "concepts", False, 1600, 900, "evidence"),
    ("words-dark-desktop", "words", True, 1600, 900, ""),
    ("goals-light-laptop", "goals", False, 1366, 768, ""),
    ("methods-light-laptop", "methods", False, 1366, 768, ""),
    ("heatmap-dark-desktop", "methods", True, 1600, 900, "matrix"),
    ("gaps-dark-laptop", "methods", True, 1366, 768, "gaps"),
)

METRICS_JS = """(() => {
  const root=document.querySelector('.insight-view'), svg=document.querySelector('#insight-svg');
  const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth};
  const allLabels=[...svg.querySelectorAll('text')];
  const labels=allLabels.filter(visible);
  // Use transformed SVG polygons: rotated heatmap text has overlapping axis-aligned
  // bounding boxes even when the glyph lines are clearly separate.
  const polygon=e=>{const b=e.getBBox(),m=e.getScreenCTM();return [[b.x,b.y],[b.x+b.width,b.y],[b.x+b.width,b.y+b.height],[b.x,b.y+b.height]].map(([x,y])=>({x:m.a*x+m.c*y+m.e,y:m.b*x+m.d*y+m.f}))};
  const polygons=labels.map(polygon),allPolygons=allLabels.map(polygon);
  const intersects=(a,b)=>{for(const p of [a,b])for(let i=0;i<4;i++){
    const q=p[(i+1)%4],r=p[i],dx=-(q.y-r.y),dy=q.x-r.x,n=Math.hypot(dx,dy);
    if(!n)continue;const project=s=>s.map(t=>(t.x*dx+t.y*dy)/n),ap=project(a),bp=project(b);
    if(Math.max(...ap)<Math.min(...bp)+1||Math.max(...bp)<Math.min(...ap)+1)return false;
  }return true};
  let labelOverlaps=0;
  let allLabelOverlaps=0;
  for(let i=0;i<allPolygons.length;i++)for(let j=i+1;j<allPolygons.length;j++)if(intersects(allPolygons[i],allPolygons[j]))allLabelOverlaps++;
  const overlapExamples=[];
  for(let i=0;i<polygons.length;i++)for(let j=i+1;j<polygons.length;j++)if(intersects(polygons[i],polygons[j])){labelOverlaps++;if(overlapExamples.length<12)overlapExamples.push([labels[i].textContent,labels[j].textContent]);}
  const rect=svg.getBoundingClientRect(), container=svg.parentElement;
  const scroll=document.querySelector('.insight-scroll');
  const fonts=labels.map(e=>{const m=e.getScreenCTM();return parseFloat(getComputedStyle(e).fontSize)*Math.hypot(m.a,m.b)});
  const clipped=labels.filter(e=>{const r=e.getBoundingClientRect();return r.left<rect.left-1||r.right>rect.right+1||r.top<rect.top-1||r.bottom>rect.bottom+1});
  const controls=[...root.querySelectorAll('button,select,label')].filter(visible);
  return {viewport:[innerWidth,innerHeight],totalPlotLabels:allLabels.length,allLabelOverlaps, visiblePlotLabels:labels.length,labelOverlaps,overlapExamples,
    clippedSvgLabels:clipped.map(e=>e.textContent),minLabelPixels:fonts.length?Math.round(Math.min(...fonts)*10)/10:null,
    labelsBelow11px:fonts.filter(f=>f<11).length, documentOverflow:document.documentElement.scrollWidth>innerWidth+2,
    plotHorizontalScroll:container.scrollWidth>container.clientWidth+2,
    plotVisibleWidth:Math.round(container.clientWidth),plotIntrinsicWidth:Math.round(container.scrollWidth),
    contentVerticalScroll:scroll.scrollHeight>scroll.clientHeight+2,
    clippedControls:controls.filter(e=>e.clientWidth>0&&e.scrollWidth>e.clientWidth+3).map(e=>e.textContent.trim().slice(0,100)),
    evidenceVisible:!!document.querySelector('.insight-detail'),
    labelSamples:labels.slice(0,8).map(e=>e.textContent)};
})()"""


async def audit(cdp, args, out: Path) -> dict:
    """Verify the isolated fixture, then render identical views and record geometry."""
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if not fixture.get("synthetic") or args.url != "http://127.0.0.1:8775/":
        raise ValueError("Only a declared synthetic fixture on localhost:8775 is allowed")
    await ui_snapshot.wait_expression(cdp, "typeof Alpine !== 'undefined' && document.querySelector('[x-data]') && typeof Alpine.$data(document.querySelector('[x-data]')).openInsight === 'function'")
    await cdp.eval("window.rhTestApp = Alpine.$data(document.querySelector('[x-data]'))")
    actual = await cdp.eval("(await (await fetch('/api/items')).json()).map(p=>({id:p.id,title:p.title}))")
    if {p["id"] for p in actual} != set(fixture["items"]) or {p["title"] for p in actual} != set(fixture["titles"]):
        raise AssertionError("Server library does not match the synthetic fixture")
    report = {"synthetic": True, "cases": {}}
    for name, kind, dark, width, height, mode in CASES:
        await cdp.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
        await cdp.eval(f"rhTestApp.dark={str(dark).lower()};rhTestApp.selectVirtual('all'); await rhTestApp.openInsight({json.dumps(kind)}); rhTestApp.insight.selected=null")
        await ui_snapshot.wait_expression(cdp, "!rhTestApp.insight.loading && !!document.querySelector('#insight-svg')")
        if mode == "evidence":
            await cdp.eval("document.querySelector('#insight-svg [data-node]').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        if mode == "matrix":
            await cdp.eval("rhTestApp.insight.methodView='matrix';rhTestApp.renderInsight()")
        if mode == "gaps":
            await cdp.eval("rhTestApp.insight.methodView='pipeline';rhTestApp.renderInsight()")
        await cdp.eval("await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        if mode == "gaps":
            await cdp.eval("document.querySelector('.insight-diagnostics').scrollIntoView({block:'start',behavior:'instant'})")
            await cdp.eval("await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        else:
            await cdp.eval("document.querySelector('.insight-scroll').scrollTop=0")
        report["cases"][name] = await cdp.eval(METRICS_JS)
        await cdp.screenshot(out.parent / (name + ".png"))
    # Replace only browser state for synthetic sparse and dense layout edge cases.
    # No API mutations are used, and each case reloads the real fixture first.
    for count in (1, 80):
        await cdp.eval(f"""await rhTestApp.openInsight('concepts');rhTestApp.insight.selected=null;
          rhTestApp.insight.data={{...rhTestApp.insight.data,scope:{{items:4,label:'Synthetic stress'}},
          nodes:Array.from({{length:{count}}},(_,i)=>({{id:String(i),label:'Synthetic concept '+i,count:1+i%4,cluster:i%5,items:[],evidence:[]}})),
          edges:Array.from({{length:{max(0,count-1)}}},(_,i)=>({{source:String(i),target:String(i+1),weight:1}})),clusters:[]}};
          rhTestApp.insight.nodes=InsightLayouts.forceLayout(rhTestApp.insight.data.nodes,rhTestApp.insight.data.edges);
          rhTestApp.renderInsight();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
          document.querySelector('.insight-scroll').scrollTop=0""")
        report["cases"][f"stress-{count}"] = await cdp.eval(METRICS_JS)
        if count == 80:
            await cdp.screenshot(out.parent / "stress-80.png")
    report["synthetic_logic_checks"] = await check_logic(cdp)
    if cdp.console_errors:
        raise AssertionError("Browser console errors: " + repr(cdp.console_errors))
    return report


async def check_logic(cdp) -> dict:
    """Exercise signed values, phrase filters and relation-specific evidence in UI state."""
    result = await cdp.eval("""await (async () => {
      await rhTestApp.openInsight('words');
      rhTestApp.insight.data.words=[
        {word:'positive phrase',bigram:true,count:3,distinct:2,n_items:1,items:[],evidence:[]},
        {word:'negative',bigram:false,count:1,distinct:-2,n_items:1,items:[],evidence:[]},
        {word:'zero',bigram:false,count:2,distinct:0,n_items:1,items:[],evidence:[]}];
      rhTestApp.insight.metric='distinct';rhTestApp.insight.view='bars';rhTestApp.insight.terms='all';rhTestApp.renderInsight();
      await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
      const bars=[...document.querySelectorAll('#insight-svg [data-score]')].map(e=>({
        score:Number(e.dataset.score),x:Number(e.querySelector('rect').getAttribute('x')),width:Number(e.querySelector('rect').getAttribute('width'))}));
      const negative=bars.find(b=>b.score===-2),positive=bars.find(b=>b.score===2),zero=bars.find(b=>b.score===0);
      const signedBars=bars.length===3&&negative.x<615&&negative.x+negative.width===615&&positive.x===615&&positive.width>0&&zero.x===615&&zero.width===0;
      rhTestApp.insight.terms='phrases';rhTestApp.renderInsight();
      await new Promise(r=>requestAnimationFrame(r));
      const phraseFilter=rhTestApp.insightWords().length===1&&document.querySelectorAll('#insight-svg [data-score]').length===1;
      const oldDark=rhTestApp.dark,oldSvg=rhTestApp.insight.svg;rhTestApp.toggleDark();
      await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
      const themeToggle=rhTestApp.dark!==oldDark&&document.documentElement.classList.contains('dark')===rhTestApp.dark&&rhTestApp.insight.svg!==oldSvg;
      await rhTestApp.openInsight('goals');
      const paper={id:901,title:'Synthetic title-only question',evidence:'Synthetic title-derived relation evidence',aims:[{sentence:'Unrelated aim sentence must not replace link evidence'}]};
      rhTestApp.insight.data={...rhTestApp.insight.data,
        papers:[paper],factors:[{id:'factor',label:'Synthetic factor',count:1,items:[901]}],
        outcomes:[{id:'outcome',label:'Synthetic outcome',count:1,items:[901]}],
        links:[{factor:'factor',outcome:'outcome',items:[paper]}]};
      rhTestApp.renderInsight();await new Promise(r=>requestAnimationFrame(r));
      document.querySelector('#insight-svg .insight-node').dispatchEvent(new MouseEvent('click',{bubbles:true}));
      await new Promise(r=>requestAnimationFrame(r));
      const goalEvidence=rhTestApp.insight.selected.evidence.length===1&&rhTestApp.insight.selected.evidence[0].sentence===paper.evidence;
      return {signedBars,barGeometry:bars,phraseFilter,themeToggle,goalEvidence};
    })()""")
    failed = [key for key in ("signedBars", "phraseFilter", "themeToggle", "goalEvidence") if not result[key]]
    if failed:
        raise AssertionError("Synthetic logic checks failed: " + ", ".join(failed))
    return result


def main() -> None:
    """Reuse the maintained browser launcher with the visual audit scenario."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--out", required=True, help="Directory for six screenshots and metrics.json")
    parser.add_argument("--baseline", help="Earlier metrics.json to compare in comparison.csv")
    args = parser.parse_args()
    args.url = "http://127.0.0.1:8775/"
    args.out = str(Path(args.out) / "metrics.png")
    args.width, args.height, args.scale = 1600, 900, 1
    args.dark, args.insights = False, True
    args.step, args.assert_js, args.shot_each = [], [], False
    args.initial_wait, args.wait = 2.5, 0.1
    ui_snapshot.check_insights = audit
    result = asyncio.run(ui_snapshot.run(args))
    # The launcher captures a final generic image; the six named views are the report.
    Path(args.out).unlink(missing_ok=True)
    if args.baseline and result == 0:
        write_comparison(Path(args.baseline), Path(args.out).with_suffix(".json"))
    raise SystemExit(result)


def write_comparison(baseline: Path, current: Path) -> None:
    """Export paired geometry measurements without treating observations as trials."""
    before = json.loads(baseline.read_text(encoding="utf-8"))["cases"]
    after = json.loads(current.read_text(encoding="utf-8"))["cases"]
    metrics = ("visiblePlotLabels", "labelOverlaps", "minLabelPixels", "labelsBelow11px",
               "documentOverflow", "plotHorizontalScroll", "plotVisibleWidth", "plotIntrinsicWidth",
               "contentVerticalScroll")
    with current.with_name("comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("case", "metric", "before", "after"))
        for case, values in after.items():
            for metric in metrics:
                writer.writerow((case, metric, before.get(case, {}).get(metric), values.get(metric)))


if __name__ == "__main__":
    main()
