# Benchmark I corpus and annotation protocol

All five nondeleted papers with indexed full text in the local library on 15 September 2026 are included. No papers were invented or selected based on detector success. The requested target of approximately ten papers exceeds the available corpus.

`annotations.json` records DOI identity, full-text and abstract SHA-256 digests, and 71 positive paper-method labels with original evidence passages. A coding agent manually read the Methods sections and relevant Results to assign these labels. **Independent human annotation and review are still pending.** These are transparent development annotations, not expert-validated ground truth or held-out accuracy claims.

The comparison scores the complete fixed initial taxonomy. Unlisted methods were audited as absent from the focal experiments. Mentions of another study, ordinary feeding/housing, or apparatus names that resemble unrelated methods do not establish a method. New taxonomy labels are exported separately without being scored against an annotation universe that did not include them. This keeps the baseline and current precision/recall denominators comparable. Full text and PDFs remain outside the repository; reproduction requires the same five indexed texts.

The `baseline/` source files preserve the initial untracked implementation supplied with `new_features.md`, before this feature implementation. Their hashes are recorded in the numeric cache alongside the evaluated current sources. This baseline cannot be recovered from the preceding Git commit because those source files were not yet tracked.

Run `benchmarks/Run_Methodology_Benchmark.cmd`, or use the documented `measure` and `plot` commands in the Python script. Reading uses SQLite `mode=ro`, `query_only` and a consistent read transaction. Application database calls are disabled during detection; indexing is never invoked. Numeric cache validation includes source text, annotations and detector dependencies. `--recompute` forces measurement; `plot` or `--figures-only` redraws without opening the library.

The observational unit is presence of a method within a paper. Precision and recall pool TP/FP/FN within each stage. Methods within one paper are dependent; the five papers form a narrow convenience corpus. No p-values, independence assumptions, confidence intervals or claims of generalisation are made. One-call CPU timings are descriptive diagnostics only. Detailed decisions and unmatched/new labels are exported so errors can be inspected rather than hidden.

## Recorded result

The frozen baseline produced 59 true positives, 19 false positives and 12 false negatives: precision 75.6%, recall 83.1%. The evaluated implementation produced 53 true positives, zero false positives and 18 false negatives: precision 100%, recall 74.6%. Both use the same 71 positive labels and 330 possible paper-method combinations (5 papers times 66 original methods). These results expose the conservative detector's recall cost; they do not establish perfect precision outside this development corpus. The 34 added taxonomy methods are retained in a separate unscored export.

Stage counts, individual decisions, source provenance and exact evaluated code hashes are in `benchmarks/results/I_methodology_metrics.csv`, `I_methodology_decisions.csv` and `I_methodology_cache.json`. The benchmark refuses to plot stale numbers after detector or annotation changes.
