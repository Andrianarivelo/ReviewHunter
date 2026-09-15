# Insight maps visual audit

The screenshots use four synthetic papers in an isolated library on port 8775.
They do not contain the user's library or evaluate scientific extraction accuracy.

The repeatable audit covers 1600 x 900 desktop and 1366 x 768 laptop viewports,
light and dark themes, every map, and the evidence panel. Browser-only fixtures
also exercise one-node and 80-node concept graphs without changing stored data.

Run the same cases after starting `tools/insight_fixture.py`'s disposable server:

```powershell
.venv\Scripts\python.exe tools/insight_visual_audit.py --fixture <fixture.json> --out docs/insight-audit/after --baseline docs/insight-audit/before/metrics.json
```

The `before` folder records the original interface. The audit found:

- Concept count labels shrank to 8.7 pixels with the evidence panel open.
- The laptop methodology pipeline required horizontal scrolling, truncated most
  method names, and broke paper titles in the middle of words.
- The word cloud extended below the initial viewport, while the sparse goal map
  used a tall canvas that displaced the useful aim sentences.
- The 80-node graph had 30 overlapping text polygons in the visible viewport.
- The heatmap lacked a clear key for detected versus not-detected cells.

`metrics.json` records browser geometry. `comparison.csv` pairs measurements from
both runs. Text overlap uses transformed SVG polygons, so rotated heatmap labels
are not falsely reported as collisions merely because their bounding boxes cross.
Minimum label sizes include the numeric counts inside concept nodes. A scrollable
view is not itself a failure: full labels may legitimately require scrolling.
Screenshot inspection is necessary to interpret these measurements.

The final pass has zero text-polygon overlaps across every complete SVG, including
all 160 text elements in the 80-node graph. It has no page or plot horizontal
overflow in the captured cases, no visible SVG labels below 11 pixels, and no
browser console errors.

| Measurement | Before | After |
| --- | ---: | ---: |
| Concept minimum label size, evidence panel open | 8.7 px | 11.3 px |
| Laptop methodology minimum label size | 10 px | 11.9 px |
| Laptop methodology plot width / available width | 1150 / 1074 px | 1074 / 1074 px |
| Word cloud requires vertical scrolling | Yes | No |
| Visible text overlaps, 80-node graph | 30 | 0 |
| Minimum visible label size, 80-node graph | 10.7 px | 14 px |

The dense graph uses a 960-unit-tall canvas and vertical scrolling. This preserves
readable labels while retaining every node. It deliberately does not compress all
80 nodes into the laptop's initial viewport. The final audit additionally checks
the entire SVG for collisions, including the portion below the viewport.

The methodology view now wraps paper titles and full method names at word
boundaries. All five stages fit the laptop width. The heatmap adds an explicit
evidence-strength symbol key, complete method labels, and prevalence counts.
The evidence panel and reporting checks use larger, more readable text. Long
methodology tables and reporting lists still scroll vertically.

The cloud reports how many terms it displays; ranked bars expose the complete
selected list within that view's limit. In the final synthetic fixture screenshot,
the cloud displays 15 of 16 terms. This is disclosed in its caption.

| View | Original | Revised |
| --- | --- | --- |
| Concepts and evidence | [Screenshot](before/concepts-evidence-light-desktop.png) | [Screenshot](after/concepts-evidence-light-desktop.png) |
| Word cloud | [Screenshot](before/words-dark-desktop.png) | [Screenshot](after/words-dark-desktop.png) |
| Goals | [Screenshot](before/goals-light-laptop.png) | [Screenshot](after/goals-light-laptop.png) |
| Methodology pipelines | [Screenshot](before/methods-light-laptop.png) | [Screenshot](after/methods-light-laptop.png) |
| Methodology heatmap | [Screenshot](before/heatmap-dark-desktop.png) | [Screenshot](after/heatmap-dark-desktop.png) |
| Reporting checks | [Screenshot](before/gaps-dark-laptop.png) | [Screenshot](after/gaps-dark-laptop.png) |

[Dense 80-node graph, laptop viewport](after/stress-80.png) shows the intended
scrolling layout. [Paired measurements](after/comparison.csv) and
[complete results](after/metrics.json) record the evidence behind this report.

Four additional deterministic browser checks pass: signed distinctiveness bars
extend to the correct side of zero (including a zero-width zero score), the
phrase filter excludes single words, the actual theme toggle rerenders the SVG,
and a title-derived goal node retains its relation's evidence sentence rather
than substituting an unrelated aim. These checks inject synthetic browser state
only and do not write to the library.

These are deterministic interface checks, not independent experimental samples.
No significance tests or population-level performance claims are appropriate.
