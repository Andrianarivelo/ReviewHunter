# Goal extraction and complete paper coverage

The goal atlas represents every paper in its scope. A paper no longer disappears
because a rule could not turn its purpose into a factor/outcome pair. Its main
text is a complete source sentence when an explicit aim is found, otherwise its
title with a **From title** badge. An untitled paper without an aim receives
**Text unavailable**. A title-derived research focus is not labelled an aim.

## API contract

Each member of `papers` retains the existing bibliography, `goal_type`, `aims`,
and `pairs`, and adds:

| Field | Meaning |
| --- | --- |
| `focus` | Complete extracted purpose sentence or original title; empty if unavailable |
| `focus_source` | `abstract`, `introduction`, `fulltext`, `title`, or empty |
| `focus_status` | `explicit_aim`, `title_focus`, or `unavailable` |
| `focus_label` | Explicit aim, From title, or Text unavailable |
| `themes` | Matched topic IDs, labels, exact matching text, and source |

Aim sentences carry their source and confidence. Relationship entries retain
complete sentence evidence, source, and `explicit` or `title_derived` confidence.
Their factors and outcomes are validated noun phrases, never blindly truncated
clauses. Normalised matching copies resolve explicit aliases and PDF typography;
the focus and evidence retain the source text.

`relation_label` distinguishes research questions, title-stated relationships,
and negative title findings. `negated` marks the supported negative title case.
Every aggregate link preserves these fields on its individual source papers.
The link itself is marked negative only when every member is negative, and its
label becomes **Mixed statements** when member labels differ. A relationship
between two terms is not proof of causation.

The response also includes `coverage`, a theme legend, and the existing factors,
outcomes, links, and missing-aim diagnostics. Multiple selected collections use
the shared scope union and deduplication rules.

## Read-only comparison with the earlier parser

The audit read the ten live library entries and their indexed text using SQLite
`mode=ro`. It ran the goal parser from revision `fe60485` and the revised extractor
against the same in-memory metadata and the same 60,000-character text budget.
It did not modify references, metadata, attachments, or indexed text.

[Per-paper measurements](goal-extraction-coverage.csv) contain bibliographic
identifiers and counts, without copying abstracts or full paper text into the
repository.

| Descriptive measurement | Earlier parser | Revised extractor |
| --- | ---: | ---: |
| Papers examined | 10 | 10 |
| Papers with extracted aim sentences | 7 | 8 |
| Papers with at least one returned relationship | 2 | 10 |
| Reported invalid `are modified...` outcome fragment | 1 | 0 |
| Explicitly labelled title-only focuses | Not exposed | 2 |

The revised extractor returns 15 source-backed relationships across these ten
papers. All papers have a focus entry: eight explicit aims and two title-derived
focuses. The counts demonstrate coverage of this library, not general extraction
precision or recall. The source review was not an independent blinded annotation
study; no significance test or population-level accuracy claim is appropriate.

## Findings that drove the changes

- The 1986 ethanol paper states its purpose in the passive voice. The relationship
  being investigated now supplies its aim, while the temperature-dependence title
  supplies an additional clearly labelled relationship.
- The 1990 ethanol paper uses **designed to assess** a mechanism. The complete aim
  is preserved, and ethanol administration and body-temperature elevation can be
  represented without adding a causal finding.
- The 2017 heroin paper asks **how** a drug affects temperature while embedding a
  dose clause in its subject. The dose remains in the evidence, while the factor
  label remains the drug rather than a sentence fragment.
- The 2017 MDMA paper has a structured **Objective** sentence with temperature and
  self-administration abbreviations. Explicit aliases improve labels while the
  original objective remains available unchanged.
- The 2007 methamphetamine aim and title describe the same temperature relation.
  Conservative singular/plural normalisation avoids duplicating that relationship.
- The cocaine sentence containing **are modified with frequent exposure** could
  previously become an outcome verb fragment. Clause validation rejects that
  fragment; passive modification constructions can instead supply noun arguments.
- **Small changes in temperature cause changes in...** contains the noun *changes*
  before the actual verb. The extractor considers later verb boundaries instead
  of splitting at the first ambiguous word.
- A title saying **Fails to Elicit Neurotoxicity** remains a negative statement
  throughout the paper and aggregate-link payloads.
- A coordinated action such as **and compared three dose groups** begins another
  research clause. It is removed from the outcome label while remaining in the
  full focus and evidence. Ordinary outcome coordination, including **memory and
  test performance**, remains intact.

## Limits and validation

The rules read metadata abstracts and introduction text within the existing
60,000-character indexed-text budget. They deliberately exclude generic study
mentions, operational measurement purposes, and recognisable prior-work or result
sentences. Some unusual phrasing, incomplete abstracts, OCR errors, and late aims
will still be missed. The title fallback keeps the paper visible without inventing
an objective or forcing a relationship.

Topics are explicit lexical matches, not inferred latent themes. Long or
grammatically ambiguous relationships can be omitted while their full research
focus remains visible. No text extraction rule establishes the truth of a paper's
claim.

`tests/test_goal_extraction.py` covers all three focus states, passive and mechanism
aims, how clauses with doses, MDMA aliases with unchanged evidence, the reported
verb-fragment regression, noun/verb ambiguity, negative and mixed-polarity links,
background and measurement exclusions, long evidence preservation, subtitle
disambiguation, and coordinated action versus outcome-noun distinctions. Together
with the existing insight tests, 46 tests passed. The ten-paper coverage CSV was
rechecked after the coordinated-clause correction and its counts were unchanged.
