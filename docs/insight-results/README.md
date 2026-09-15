# Concept analytics and reported findings audit

This reproducible UI audit uses twelve synthetic papers in a disposable library. It tests interface behavior and exact directional grouping rules, without estimating scientific accuracy, novelty, significance or statistical outliers.

Create a new library from the repository root:

```powershell
.venv\Scripts\python.exe tools/insight_fixture.py --results
```

Serve that temporary home with the usual development launcher, explicitly setting both `REVIEWHUNTER_HOME` and `REVIEWHUNTER_PORT=8775`. Run the audit against its manifest:

```powershell
.venv\Scripts\python.exe tools/insight_results_audit.py --fixture <temporary-home>\fixture.json --out docs/insight-results/<new-directory>
```

The audit verifies the exact item IDs and titles before browser interaction. It never stops an app server or writes to the library. Every viewport/theme uses a fresh headless Chrome profile, and output directories must be new.

The controlled comparison comprises three reports of increased body temperature and one report of decreased body temperature after MDMA in rats at 22 degrees C, 5 mg/kg intraperitoneally, compared with saline controls. A fifth report at 30 degrees C belongs only to the broader related-outcome group. Two cocaine reports have unchanged locomotor activity under a matching context. Missing findings remain visible and are retained in exports.

Concept checks cover full long labels, non-overlapping cards, overlap-matrix dimensions, broad indexing labels, prevalence and link evidence. Outcome checks cover direction filters, unchanged full-group counts under filtering, minority-direction evidence, separate contexts, missing findings and complete exports. Source code hashes and exact comparison evidence are recorded in each report.

## Final validation, 15 September 2026

[The final v3 report](20260915-results-v3/report.json) passed all four combinations of light/dark themes and 1600 by 1000 / 1366 by 768 viewports. All 48 screenshots passed complete-label, card-boundary, overlap, matrix-label and document-overflow checks, with no console errors. Five representative final images were inspected, including both exported PNGs. Recorded source hashes matched the working source files after capture.

| Check | Result |
| --- | --- |
| Concept network | 20 visible recurring non-broad concepts, full measured labels |
| Overlap matrix | 400 cells for 20 concepts; unchanged when the serialized edge list is removed |
| Analytical drilldowns | Concept, strongest-link and unusual-combination evidence present |
| Broad metadata | Hidden by default, retained in data and recoverable with the filter |
| Atlas results bands | All 12 paper cards checked; focused decrease band visible and unclipped |
| Strict comparison | Three increases and one decrease, matched context and comparator |
| Different temperature | Excluded from strict comparison, available in related-outcome evidence |
| Similar reports | Two no-change reports retain their own compatible group |
| Direction filter | One decrease paper shown; complete comparison remains three versus one |
| Missing findings | Four papers retain explicit absence of extracted findings |
| Outcomes CSV | All 12 paper IDs retained, including missing findings, despite a one-paper display filter |
| Concepts CSV | All 27 retained nodes and 161 edges; prevalence, Jaccard, broad flags and shared paper IDs preserved |

The [concept network](20260915-results-v3/desktop-light/concept-network.png), [focused atlas result](20260915-results-v3/laptop-dark/atlas-results-focused.png), and [outcomes overview](20260915-results-v3/desktop-dark/outcomes-overview.png) show the final interface. The [exported overlap matrix](20260915-results-v3/desktop-dark/exports/concepts/export-concepts.png) and [exported filtered findings](20260915-results-v3/desktop-dark/exports/goals/export-goals.png) were captured from the application's actual download Blobs. JSON retains all scoped analytical data and evidence.

These checks demonstrate deterministic behavior on controlled examples. They do not measure extraction accuracy on a representative literature corpus or establish that similarly described experiments are scientifically interchangeable. Earlier v1/v2 directories contain intermediate audit evidence and are excluded from release artifacts.
