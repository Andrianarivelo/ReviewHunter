# Methodology omissions: source audit

The four studies below were checked against their existing indexed PDF text in
the user's library using SQLite `mode=ro` and `PRAGMA query_only=ON`. No record,
attachment, metadata field or search index was changed. Tests use isolated text
and temporary libraries. This is a targeted development audit, not independent
validation of every method in these papers.

| Study | Source support | Result of the change |
| --- | --- | --- |
| [MDMA self-administration, 2017](https://doi.org/10.1016/j.pbb.2017.10.008) | Methods contains the explicit heading **ICSS procedure**, electrode implantation and reward-threshold analysis. | A distinct **Intracranial self-stimulation (ICSS)** chip identifies the assay, alongside the broader brain-stimulation intervention. |
| [Temperature dependence of ethanol depression, 1986](https://doi.org/10.1007/BF00181238) | The abstract explicitly describes separate groups. Methods lists chamber temperatures of 12, 15, 22, 28 and 34 °C, studied in random order. | Between-groups, temperature-condition comparison and randomised condition order become visible. Random order is not labeled random allocation. |
| [Thermoregulation after oral ethanol, 1990](https://doi.org/10.1016/0741-8329(90)90047-G) | Methods specifies gavage doses of 0, 2, 4, 6 and 8 g/kg, with five animals per dose group. | Dose comparison and between-groups design become visible. Different doses of different anesthetic drugs do not trigger this rule. |
| [Brain-temperature effects of heroin, 2017](https://doi.org/10.1016/j.neuropharm.2017.07.025) | Methods states that each rat underwent three drug sessions. A separate experiment explicitly used increasing doses. | Repeated treatment sessions and within-subject observations supplement the existing dose-comparison and time-course chips. |

## What changed

The new detail identifiers are `tool_icss`, `design_temperature_conditions`,
`design_random_order` and `design_repeated_exposure`. They retain the existing
five-stage payload and per-chip source evidence. Existing `betweensubject` and
`doseresponse` rules recognize explicit groups and numeric dose series. The
display label **Dose comparison / dose-response** distinguishes a manipulated
dose comparison from a claim about the shape or significance of a response.

The detector also matches an ephemeral view with PDF line wrapping and line-end
hyphenation normalized. This repairs phrases split across lines, including
**Each rat** and **between-subjects**, without rewriting source text. Evidence
retains the complete normalized sentence rather than a disconnected keyword.

The rules deliberately avoid inferring a factorial design from drug and ambient
temperature mentions alone. Recovery days, sample-storage temperatures, a single
injection in each of several animals, random reference ordering and descriptions
of earlier ICSS studies are negative controls in `tests/test_design_details.py`.
The existing context and negation guards remain active.

## Evaluation

The unchanged frozen five-paper benchmark annotations remain the comparison
standard. The preceding detector had 53 true positives, zero false positives and
18 false negatives. This audit produced 54 true positives, zero false positives
and 17 false negatives: precision 1.000 and recall 0.761. The newly added detail
labels are outside that original label universe and remain separately unscored.
The source-backed case checks and adversarial tests complement this small
benchmark; neither establishes general performance across the literature.
