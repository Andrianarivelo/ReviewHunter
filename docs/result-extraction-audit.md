# Directional findings and shared outcomes

The Results view extracts source-supported observations from abstracts and
detected Results sections. A finding retains the complete source sentence,
its clause, source section, endpoint and explicitly identified context. It
does not inherit a result from a paper's aim, title or hypothesis.

Supported directions are increase, decrease, no detected change, mixed and
unspecified. An explicitly negated increase remains unspecified; it is neither
a decrease nor proof of no change. A biphasic response remains mixed. Direction
must attach to its endpoint grammatically. An intervention that increases body
temperature is not itself marked increased. Regression predictor directions
are excluded because they do not establish the direction of a treatment effect.

## Two distinct ways to explore outcomes

**Compatible reported contexts** require the same stated factor, endpoint,
single drug, species, temperature, dose, route and comparator. Remaining
detected region, strain, sex, timing and condition qualifiers must also match.
These qualifiers distinguish, for example, acute/chronic treatment, withdrawal,
awake/anesthetized animals, pregnancy and day/night conditions. Unknown details
are not certified equivalent: the comparison is a candidate for source review,
not proof that the protocols match completely.
Ambiguous numeric lists and papers with a truncated findings list are excluded.
Each paper contributes one direction count, regardless of repeated sentences.
Minority directions describe counts, not statistical outliers. Even these
candidate comparisons require inspection of full protocols before synthesis.

**Shared outcomes** group the same endpoint with different or incompletely
specified experimental contexts. All
source evidence and contextual differences remain available. These groups never
receive minority or outlier flags, and their directions are explicitly labeled
as not directly comparable. This preserves useful discovery when strict context
requirements yield no comparable studies.
Groups already represented by one complete matching context are omitted from
this section to avoid labeling identical known contexts as different.

Both views are descriptive. No effect sizes are pooled, no significance is
inferred from wording and no experimental equivalence is assumed from a common
endpoint. Empty extraction does not establish that a paper had no result.

## Source audit and validation

The ten existing papers previously used for the goal audit were read using
SQLite `mode=ro` and `PRAGMA query_only=ON`. The existing indexed text was used
without reindexing or updating metadata. `result-extraction-coverage.csv`
records before/after exposed-finding coverage; the baseline had no directional
finding feature, so this is a feature-coverage comparison rather than an
accuracy benchmark. The rows are development checks, not independent human
annotations or held-out performance estimates.

Useful verified examples include cocaine-associated falls in core temperature
at thermoneutral conditions, MDMA self-administration changes under temperature
conditions, the biphasic brain-temperature response to increasing heroin doses,
and ethanol-associated increases in colonic temperature alongside decreases in
metabolic rate and evaporative water loss. Every displayed finding carries its
source sentence so those conditions and qualifications remain inspectable.

The detector exposes up to eight findings per paper and discloses truncation.
The current goal endpoint reads the first 60,000 indexed characters; results
outside that scan or without recognized headings may be missed. Rules also
abstain on unresolved references, complicated syntax, uncertain statements and
unrecognized endpoints. Readable evidence takes priority over filling every row.

`tests/test_result_extraction.py` checks opposite outcomes, conditional effects,
null wording, aims/background exclusion, mixed individual responses, comparator
requirements, drug/dose/species mismatches, per-paper counts and the separation
between shared endpoints and compatible comparisons. Fixture comparisons use
explicitly synthetic three-increase/one-decrease records with matched contexts.
An independent adversarial review also checks endpoint-local negation, singular
prior-study attribution, compound clauses, nested changes and unresolved
physiological conditions in `tests/test_result_review.py`.
