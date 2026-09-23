# Bulk import visual and behavioral audit

Final release evidence: [20260923-bulk-v5 summary](20260923-bulk-v5/summary.json), [scenario timings](20260923-bulk-v5/scenarios.csv), and [detailed report](20260923-bulk-v5/report.json). All 16 scenario runs passed across four viewport/theme combinations, with 40 screenshots, no application errors, no external font warnings, no unexpected resolver network calls, and unchanged original input hashes. All seven recorded implementation hashes matched the final source after the audit.

Six representative PNGs were visually inspected: [new-reference results](20260923-bulk-v5/desktop-light/paste-results.png), [mixed file selection](20260923-bulk-v5/desktop-light/files.png), [folder destination](20260923-bulk-v5/laptop-light/folder.png), [cancelled job](20260923-bulk-v5/desktop-dark/cancelled.png), [small-screen file results](20260923-bulk-v5/laptop-dark/file-results.png), and [small-screen progress](20260923-bulk-v5/laptop-dark/progress.png). Labels remained readable, modal controls stayed within the viewport, completed bars reached 100%, and report content scrolled within the dialog.

Run `tools/bulk_import_audit.py --prepare` to create an explicitly synthetic library and source files. Start its `--serve --fixture <manifest>` mode on disposable port 8775, then run `--fixture <manifest> --out <new-directory>` to audit it. The tool refuses other ports and homes outside the marked temporary fixture boundary. The normal application and user publications are never opened by this audit.

The server supplies invented registry responses and blocks unexpected resolver/download network requests. Parsing, HTTP uploads, background jobs, canonical PDF ingestion, library storage and browser rendering use the application implementation. The synthetic PDFs have matching visible titles, DOI identifiers and embedded metadata. CSV and DOCX inputs contain invented publications, and one deliberately invalid PDF tests independent entry failure.

The final audit covers 1440 x 900 and 1024 x 768 in light and dark themes. Each case tests:

- Capturing the current collection when opening Import, retaining that destination if background navigation changes, and attaching references to it.
- Explicit collection-menu import overrides a previous draft's destination while preserving its text. Changing from mixed files to PDF-folder mode clears incompatible file selections.
- Pasted identifiers, unavailable metadata, ambiguous exact titles and an unrelated first search result. Ambiguous/unrelated candidates cannot create references. A saved reference remains available when its PDF lookup fails, and that partial failure appears under Needs attention.
- A real mixed file input containing CSV, DOCX and multiple PDFs, with My Library selected. Existing PDF identity is reused, metadata is retained, and only one canonical attachment remains after repeated import.
- A recursive PDF folder, including a nested PDF, into a newly named collection.
- Progress, closing and reopening an active job, cancellation after the current entry, and restoring the same report after a full browser reload.
- CSV report export, modal bounds, horizontal overflow, completed progress bars, browser exceptions, and unchanged SHA-256 hashes of every original source file.

`summary.json` is the concise result. `report.json` retains entry-level evidence, layout measurements and implementation hashes. Optional Google Fonts resource failures are recorded separately from application errors, since the application uses local fallback fonts. `scenarios.csv` records observed scenario elapsed time. Those times include a deliberate 0.3-second synthetic metadata delay, browser polling and UI checks; they are reproducibility diagnostics, not production throughput estimates.

## Comparison with the previous implementation

Baseline source: commit `0cd9c3a1e702e2c7a739ad1ce481d71d90c655a9`. This is a capability comparison from the committed implementation, not an invented speed benchmark.

| Capability | Previous implementation | Audited importer |
| --- | --- | --- |
| Import dialog file selection | One selected file (`files[0]`) | Multiple mixed source files in one batch |
| CSV and Word sources | Not supported by that dialog | CSV and DOCX parsed into per-publication records |
| Multiple PDFs | Already supported through library drag/drop and `/api/import/pdfs` | Integrated into the same batch, destination and per-record report as citation files |
| Folder discovery | No dedicated recursive PDF-folder import in the dialog | Folder path and optional subfolder discovery |
| Progress and cancellation | Busy state and aggregate result | Background per-record progress, stop after current entry, report reopened across page reload |
| Failure visibility | Aggregate result | Individual errors, saved-reference PDF failures, review filter and report CSV |

No statistical significance or general real-world accuracy claim is made from this synthetic UI audit. Resolver unit tests separately cover conflicting authors/years, shortened citation titles, DOI mismatches and ambiguous candidates. Real publication acquisition depends on registry availability and accessible publisher files.

Only the final successful audit directory should be committed. Earlier local attempts document development interruptions and are not release evidence.
