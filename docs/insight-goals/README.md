# Research-question atlas audit

This audit uses twelve explicitly synthetic papers in a disposable library. It does not assess scientific accuracy on a representative publication corpus and does not modify the real library.

The fixture includes an explicit passive aim, a title-derived research focus, missing source text, a negative title statement, an unsafe outcome fragment, intracranial self-stimulation with MDMA, and separate temperature and dose groups. Three collections exercise overlapping parent/child selections and a disjoint sibling union.

Run from the repository root:

```powershell
.venv\Scripts\python.exe tools/insight_fixture.py --goals
```

Serve the resulting synthetic home using the normal development launcher with both `REVIEWHUNTER_HOME` and `REVIEWHUNTER_PORT=8775`. Then run:

```powershell
.venv\Scripts\python.exe tools/insight_goals_audit.py --fixture <temporary-home>\fixture.json --out docs/insight-goals/<new-audit-directory>
```

The script verifies the server's exact paper IDs and titles before interacting. It creates a fresh headless Chrome profile for each of four viewport/theme combinations, uses actual collection checkboxes and the Apply button, and rejects existing output directories. It never stops an app server.

Checks cover card boundaries and overlap across all rendered papers, provenance labels, question type and theme filtering, search, evidence, negative relationship captions, scope persistence across all four maps, descendant inclusion, deduplication, and the empty-selection Apply guard. CSV and JSON exports must include every scoped paper even when only one paper is displayed. PNG exports are captured from the app's actual download Blobs.

Expected collection sizes: parent with descendants 7; parent plus child 7; parent plus sibling with descendants 11; parent plus sibling without descendants 8; whole library 12.

Partial audit directories preserve evidence of issues discovered before fixes. Only the final successful report should be used as release validation.

## Final validation, 15 September 2026

[The final report](20260915-goals-v5/report.json) passed all four combinations of light/dark themes and 1600 by 1000 / 1366 by 768 viewports. Its 28 screenshots contain no detected card overlap, text crossing card boundaries, document horizontal overflow, console errors, or scope-control/state mismatches. Five representative final PNGs, including the exported image, were also visually inspected.

| Check | Result |
| --- | --- |
| Whole-library representation | 12 of 12 papers |
| Provenance | 9 explicit aims, 2 title-derived focuses, 1 unavailable |
| Parent plus child | 7 papers, no duplicate IDs |
| Parent plus sibling | 11 papers with descendants, 8 without |
| Scope persistence | Concepts, words, goals, methodology all preserve the union |
| Existing controls | Single collection and author selection pass in every viewport/theme |
| Filters and evidence | Question type, theme, search, evidence open/close pass |
| Negative statement | Visible caption and dashed relationship in atlas and connections |
| Outcome guards | Unsafe fragment rejected; coordinated research-action clause excluded |
| Methods | MDMA and ICSS detected; separate temperature/dose design identified |
| Export completeness | All 12 IDs retained in CSV and JSON while display is filtered to 1 paper |

The CSV contains 13 data rows because one paper has two supported relationships. The PNG faithfully shows the current filtered view and states that complete scoped data are in CSV/JSON.

Review the [combined-collection picker](20260915-goals-v5/laptop-light/collection-picker.png), [negative connections](20260915-goals-v5/desktop-dark/connections-negative.png), [evidence panel](20260915-goals-v5/laptop-dark/atlas-evidence.png), and [exported atlas](20260915-goals-v5/desktop-dark/exports/export-goals.png). All text is synthetic. Screenshots show viewport crops; geometric checks inspect every rendered card, including cards below the fold.

The audit exposed and prompted fixes for missing negative captions, a multi-scope parsing error, source-unavailable wording, dynamic scope-select initialization, export grammar, and outcome clause leakage. These are deterministic integration checks, not statistical estimates of performance on real papers.
