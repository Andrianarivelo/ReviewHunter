# Bulk publication import

The library Import window accepts pasted references, CSV/TSV, Word DOCX, mixed file
selections and PDF folders. The same window opens from a collection context menu
or a library file drop. Every accepted entry receives an explicit outcome.

## Workflow

1. Choose a source and review the selected files.
2. Choose My Library, an existing collection or a named new collection.
3. Enable citation metadata lookup and PDF lookup as needed, then import.
4. Reopen Import to follow progress. Review missing PDFs, incomplete metadata and
   ambiguous matches, open saved references, or export the report as CSV.

PDFs always use the canonical document identification pipeline. Original files are
copied without rewriting their bytes. Existing references are enriched only where
fields are empty. A matching DOI is authoritative; title-only citation matches
require a unique registry title without conflicting supplied year or author.
Unstructured complete citations additionally require a substantial complete title,
a matching publication year and a corroborating first author. Ambiguous search
results are shown as candidates rather than selected automatically.

## Before and after

This is a capability comparison against commit `0cd9c3a`, not a timing benchmark.
The earlier main-library drag/drop path already accepted multiple PDFs.

| Import-window workflow | Previous version | 2.7 |
| --- | --- | --- |
| Pasted identifiers and titles | Supported | Supported, conservative matching and entry report |
| Bibliography exports | Single file | Multiple files, preserved metadata |
| CSV/TSV and Word DOCX lists | Not supported in this window | Supported |
| PDF file selection | First selected file | Multiple PDFs, each identified and matched |
| Local PDF folder | No folder control in this window | Folder picker or path, optional subfolders |
| Collection destination | Manual selection | Current collection captured, or existing/new choice |
| Progress and failure review | Aggregate result | Per-entry results, close/reopen, cancellation and CSV |

## Boundaries

A batch accepts at most 500 references and 1 GB combined source data. Individual
PDFs are limited to 200 MB; other uploads to 10 MB; pasted text to 2 MB. DOCX parsing
reads the main document body and tables, not footnotes or headers. Legacy `.doc`
files must first be saved as `.docx`. Malformed or oversized inputs return an
explicit error rather than being silently truncated.

Jobs run in the local server process. Cancellation finishes the current entry and
retains already imported references. Closing the browser does not stop the server
job. Restarting the server stops it; completed progress records expire after an
hour. Export a CSV report before closing the server when a durable report matters.
Registry availability, access rights and PDF availability affect completion.

## Validation

`tests/test_bulk_sources.py` covers source formats and defensive parsing.
`tests/test_bulk_import.py` covers matching, collection scope, worker cleanup,
partial failure, cancellation, concurrency and HTTP boundaries.
`tools/bulk_import_audit.py` creates an isolated synthetic library and substitutes
only external metadata/PDF services. It exercises the real API, parser, matching,
file attachment and browser interface. Its JSON, CSV and screenshots record each
scenario in light/dark themes at desktop and laptop sizes. Synthetic timings are
regression diagnostics, not estimates of registry or publisher performance.

Run `.venv\Scripts\python.exe tools/bulk_import_audit.py --prepare`, then use the
printed fixture path with `--serve --fixture PATH` in one terminal and
`--fixture PATH --out docs/bulk-import/screens/RUN` in another. The server uses port
8775 and verifies its isolated library identity before any audit writes.
