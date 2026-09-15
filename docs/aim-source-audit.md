# Flexible aim-source audit

The extractor now searches useful indexed-text regions even when a metadata
abstract exists. This resolves two missed aims without adding rules for their
paper IDs, titles, years, authors, or compounds.

| Missed paper | Cause | Recovered source |
| --- | --- | --- |
| Small Changes in Ambient Temperature... (1998) | Its introduction has no heading, and the earlier fallback was disabled by the existing abstract | The explicit purpose sentence before Materials and Methods |
| Effects of ambient temperature on locomotor activity and place conditioning... (2023) | Its clearest purpose occurs after the Introduction | The primary-aim statement in Conclusion; an additional retrospective purpose in Discussion |

The 1998 sentence identifies the interaction of ambient temperature, core
temperature, and MDMA-induced neurotoxicity. The 2023 sentence explicitly asks
whether the chemical ring changes results at different temperatures. These are
the authors' stated purposes, not questions generated from the titles. Complete
source sentences remain in the API's `focus` and `aims` fields.

## Search rules and safeguards

- Search the metadata abstract and the complete supplied Introduction, rather
  than only its final twelve sentences.
- Search indexed abstract/front sections and text before Methods even when a
  metadata abstract is present. Indexed-text provenance remains `fulltext` so an
  unheaded introduction is not incorrectly presented as metadata abstract text.
- Search Discussion and Conclusion only for explicit current-study purpose
  statements, such as a primary aim or a description of the studies reported
  here. Specific purpose statements take precedence over generic investigation
  language.
- Exclude Methods, Results, References, and acknowledgements from general aim
  searching. Structured abstract Results/Conclusions also require an explicit
  purpose statement.
- Reject deferred research, recognisable prior-work descriptions, hypotheses
  proposed from results, and editorial text that interrupts a sentence.
- Distinguish a declared hypothesis or an explicit test of a hypothesis from
  results merely supporting a hypothesis. Speculative Discussion hypotheses do
  not become aims.

The existing 60,000-character indexed-text budget remains unchanged. Sources can
now be `discussion` and `conclusion`, alongside `abstract`, `introduction`,
`fulltext`, and the labelled `title` fallback. A title fallback remains available
when no reliable purpose sentence is found.

## Paired descriptive comparison

[Per-paper CSV](aim-source-coverage.csv) and [run metadata](aim-source-coverage.json)
record the comparison. Both parsers ran on the same in-memory source text read
through SQLite `mode=ro`. The baseline helper came from revision
`d8558bd46f4f2c79685b85adc00e5a1fac201c53`; the JSON records both helper hashes.

The comparison keeps the same ten reference IDs as the preceding audit. The
library grew to fourteen live entries during this work, so new references were
not silently added to this paired sample.

| Measurement, same ten papers | Before | After |
| --- | ---: | ---: |
| Explicit aim found | 8 | 10 |
| Title fallback required | 2 | 0 |

These counts demonstrate the two reported coverage fixes. They are not a blinded
precision/recall estimate and do not establish accuracy on other libraries.
Abstracts, full text, and raw metadata were not altered or copied into the audit
artifacts.

The focused goal and existing insight suites passed 58 tests. New cases cover an
unheaded introduction with an existing abstract, a purpose in Conclusion,
whole-introduction searching, speculative and deferred hypotheses, prior work,
structured abstract results, editorial interruption, and explicit hypothesis
testing versus a statement of support.

The paired coverage figure is available as `aim-source-coverage.png`, `.pdf`, and
`.svg`. Reproduce it with `python tools/plot_insight_coverage.py`. The script reads
only the audited CSV, validates unique paper IDs and statuses, and writes the
derived counts to `aim-source-coverage-summary.csv`. Its companion
`aim-source-coverage-plot.json` records the input SHA256 and sample IDs. Figure
fonts, dimensions, colors, and paths are configurable at the top of the script.
The exported PNG was visually inspected for clipping and readable labels.
