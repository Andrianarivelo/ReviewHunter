# Word-map colours, rarity and local unusualness

Word colours identify lexical themes in the term itself. They do not infer a
paper's methods or establish a scientific finding. Matching uses deterministic,
word-bounded rules in `doi_hunter_web/word_concepts.py`. For example, `dopamine`
matches neural signals, `ketamine` matches drugs, and `calcium imaging` matches
recording and imaging. An unknown term remains **Other terms**.

The stable categories are:

| ID | Label |
| --- | --- |
| `recording` | Recording & imaging |
| `drugs` | Drugs & pharmacology |
| `neural` | Neural signals & circuits |
| `behavior` | Behaviour & cognition |
| `biology` | Organisms & biology |
| `physiology` | Physiology & environment |
| `disease` | Disease & health |
| `statistics` | Statistics & study design |
| `computing` | Computation & models |
| `other` | Other terms |

A phrase can match several categories. `concepts` preserves all matches; the first
category in the table supplies `concept_id` and `concept_label` for its primary
colour. This priority is an explicit display rule, not a confidence score.

Occurrence count (`count`) and document frequency (`n_items`) remain separate:

- `rare: true`, `rarity: "single_occurrence"`: exactly one occurrence in one paper
  in the selected source text.
- `rare: false`, `rarity: "single_paper"`: several occurrences, all in one paper.
  A term repeated 40 times in one paper is not labelled a single-occurrence rare word.
- `rarity: "shared"`: found in more than one selected paper.

The API independently selects three lists from the complete candidate vocabulary:

- `words`: frequency-ranked terms, retaining the previous minimum of two occurrences
  for scopes with more than one paper. Single-paper scopes may include singletons.
- `rare_words`: exactly one occurrence. Single words precede two-word phrases to
  avoid incidental word pairs crowding out rare words. Ties are alphabetical.
- `outlier_words`: at least two scoped occurrences, no occurrence in the available
  comparison text, and smoothed log-odds of at least 1 versus the rest of the library.
  Sort order is decreasing log-odds, then document frequency, occurrence count, and
  alphabetical order.

Each list has its own cap using the existing `limit` argument (default 150; bounded
to 20 through 400). A rare term can therefore appear even when it is outside the
frequent list's first 150 terms. `candidate_counts` reports the complete eligible
counts, and `limits` exposes each list's cap. The lists may overlap, so callers
combining them should deduplicate by `word`.

`unusual` uses unrounded log-odds for thresholding; `distinct` remains rounded to
two decimals for display. The score compares the candidate term with all other
candidate terms, applying 0.5 smoothing in both populations. It is a descriptive
score, not a significance test. Small libraries can produce unstable scores.

The comparison uses the same selected source for both populations: abstracts or
indexed full text. Detected reference sections are removed from full-text counts.
Missing full text does not fall back to an abstract in the full-text view.
`comparison_items_with_text` reports available comparison coverage. Without a
nonempty comparison vocabulary, `outlier_words` is empty and `rest_count`,
`rest_n_items`, and `absent_from_rest` are `null`, rather than a misleading zero.

**Not found elsewhere in this library is not scientific novelty.** It means the
term was not found in the available, selected comparison source. Unindexed text,
missing abstracts, spelling variants, and the library's scope cap limit that
statement. `semantics` in the response includes these definitions for the interface.

Every returned word retains its source-paper IDs, occurrence count, document
frequency, and evidence text. Validation in `tests/test_word_concepts.py` checks
word boundaries, ambiguous category matches, singleton versus one-paper repetition,
rare terms surviving the frequent cap, comparison requirements, and full-text
source parity including reference exclusion.
