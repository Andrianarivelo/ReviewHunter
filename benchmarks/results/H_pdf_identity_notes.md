# PDF identity regression benchmark

The current import pipeline met all requirements in nine fixed cases. The baseline
identified the new-reference case correctly, but omitted its available publisher
field. Consequently it did not satisfy every requirement in any complete case.
This strict aggregate must not be interpreted as a general download success rate.

| Observable requirement | Baseline 9018274 | Current |
| --- | ---: | ---: |
| Correct identity or justified rejection | 1/9 | 9/9 |
| Exactly one reference and one attachment for valid imports | 2/7 | 7/7 |
| All evaluated fixture metadata retained | 0/7 | 7/7 |
| Author and year in citation filename | 2/7 | 7/7 |
| Original PDF bytes preserved on valid imports | 7/7 | 7/7 |

One case uses the reported Aarde PDF, read without modification, with bibliographic
fields transcribed from that document. Eight cases use explicitly synthetic PDFs
and fictional registry metadata. Both versions receive the same deterministic
registry responses. No live registry, download, browser-session or challenge-bypass
performance is measured.

The baseline executes the original catalog and metadata modules from commit
9018274, plus a reconstruction of that commit's PDF import and background metadata
callback. An explicit-target control reconstructs its direct attachment endpoint.
The compatible database schema and filename helper are shared. The current path
executes the working-tree `ingest_pdf` function. All writes occur in fresh temporary
libraries, never in the user's library. The source PDF's SHA-256 hash is checked
before and after each run and recorded in the manifest.

Metadata completeness compares DOI, title, year, journal, volume, pages, publisher,
and structured creators with the fixtures. The CSV records mismatching fields.
Citation filename verification checks for the expected author and year and rejects
the generic `main` placeholder. This is a behavioral regression check, not a test
of arbitrary citation-style conformance.

The cases are selected, engineered, paired controls. They are neither a random
sample of papers nor independent population observations. Inferential tests,
confidence intervals and significance claims would therefore be inappropriate.
Counts and individual outcomes are reported directly. There is no timing claim.

Run `benchmarks/Run_PDF_Identity_Benchmark.cmd`, or use the measure and plot commands
documented at the start of the Python script. If the original PDF has been renamed,
the script locates an unchanged file by its recorded SHA-256 hash. Alternatively,
set `RH_BENCH_H_PDF` to an unchanged copy of the source PDF.
