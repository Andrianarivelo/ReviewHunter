"""Explainable lexical themes and rarity rules for the word map.

Categories describe words that occur in the selected source text. They are not
inferred study methods, learned semantic clusters, or claims of scientific novelty.
The first matching category supplies the display colour; all matches remain visible
in the payload so ambiguous phrases do not silently lose their secondary themes.
"""
from __future__ import annotations

import re


# Stable IDs are an API contract. Order resolves phrases with more than one theme.
# Keep lexical patterns word-bounded so, for example, 'rat' does not match 'rate'.
CONCEPT_RULES = (
    ("recording", "Recording & imaging", r"electrophysiolog\w*|electroencephalograph\w*|eeg|meg|fmri|mri|imaging|microscop\w*|photometr\w*|microdialys\w*|voltammetr\w*|chromatograph\w*|spectrometr\w*|telemetr\w*|electrodes?|patch[- ]clamp|recordings?"),
    ("drugs", "Drugs & pharmacology", r"drugs?|pharmacolog\w*|cocaine|methamphetamine|amphetamine|mdma|methylphenidate|ketamine|nicotine|ethanol|alcohol|opioids?|morphine|fentanyl|heroin|cannabinoids?|cannabis|thc|psilocybin|antidepressants?|antagonists?|agonists?|doses?|dosage|intravenous|intraperitoneal|infusions?"),
    ("neural", "Neural signals & circuits", r"neural|neurons?|neuronal|neurotransmi\w*|dopamine|dopaminergic|serotonin|serotonergic|glutamate|glutamatergic|gaba|gabaergic|acetylcholine|cholinergic|noradrenaline|norepinephrine|synap\w*|cortex|cortical|hippocamp\w*|striat\w*|accumbens|amygdala|brain|spikes?|oscillations?|circuits?"),
    ("behavior", "Behaviour & cognition", r"behavio\w*|cognit\w*|memory|attention|learning|reinforcement|reward\w*|punishment|perception|perceptual|visual|auditory|locomot\w*|self[- ]administration|operant|conditioning|decision\w*|impulsiv\w*|motivation\w*|anxiety|fear|discrimination|habituation|extinction"),
    ("biology", "Organisms & biology", r"rats?|mice|mouse|rodents?|humans?|participants?|primates?|monkeys?|macaques?|zebrafish|drosophila|animals?|species|organisms?|cells?|cellular|tissue\w*|proteins?|genes?|genetic\w*|genom\w*|transcri\w*|molecular|male|female|sex|receptors?"),
    ("physiology", "Physiology & environment", r"physiolog\w*|temperature\w*|thermoregulat\w*|hypertherm\w*|hypotherm\w*|ambient|heat|thermal|metabol\w*|circadian|sleep|arousal|stress|hormones?|cortisol|cardiovascular|respirat\w*|oxygen|alliesthesia|homeostas\w*"),
    ("disease", "Disease & health", r"diseases?|disorders?|patients?|clinical|health|addiction|dependence|parkinson\w*|alzheimer\w*|epilep\w*|seizures?|depression|schizophren\w*|autism|adhd|inflammation|inflammatory|pain|injur\w*|lesions?|stroke|tumou?rs?|cancer"),
    ("statistics", "Statistics & study design", r"statistic\w*|anova|regression|correlat\w*|bayesian|probabil\w*|randomi[sz]\w*|blinding|blinded|variance|confidence|significan\w*|effect[- ]size|sample[- ]size|power[- ]analysis|bootstrap\w*|permutation\w*|longitudinal|cross[- ]sectional|cohort\w*|placebo"),
    ("computing", "Computation & models", r"comput\w*|algorithm\w*|models?|modeling|modelling|simulat\w*|networks?|decod\w*|encod\w*|classif\w*|optim\w*|dimension\w*|dynamics?|hysteresis|attractors?|bifurcat\w*|machine[- ]learning|artificial|transformers?|embeddings?"),
)
OTHER_CATEGORY = {"id": "other", "label": "Other terms"}
OUTLIER_MIN_OCCURRENCES = 2
OUTLIER_MIN_LOG_ODDS = 1.0
_COMPILED = tuple((key, label, re.compile(r"\b(?:" + pattern + r")\b", re.I))
                  for key, label, pattern in CONCEPT_RULES)


def categories() -> list[dict[str, str]]:
    """Return the stable legend, including an explicit unclassified category."""
    return [{"id": key, "label": label} for key, label, _ in CONCEPT_RULES] + [dict(OTHER_CATEGORY)]


def classify_term(term: str) -> dict:
    """Match only the displayed source term, preserving every lexical theme match."""
    matched = [{"id": key, "label": label} for key, label, pattern in _COMPILED if pattern.search(term)]
    matched = matched or [dict(OTHER_CATEGORY)]
    return {"concept_id": matched[0]["id"], "concept_label": matched[0]["label"], "concepts": matched}


def term_flags(count: int, n_items: int, rest_count: int, rest_n_items: int,
               distinct: float, comparison_available: bool) -> dict:
    """Separate one occurrence, one-paper concentration, and local comparison outliers."""
    rare = count == 1
    unusual = bool(comparison_available and rest_count == 0 and
                   count >= OUTLIER_MIN_OCCURRENCES and distinct >= OUTLIER_MIN_LOG_ODDS)
    return {
        "rarity": "single_occurrence" if rare else "single_paper" if n_items == 1 else "shared",
        "rare": rare, "unusual": unusual,
        "rest_count": rest_count if comparison_available else None,
        "rest_n_items": rest_n_items if comparison_available else None,
        "absent_from_rest": rest_count == 0 if comparison_available else None,
    }


def semantics() -> dict:
    """Expose exact display rules and limits rather than implying learned novelty."""
    return {
        "concepts": "Lexical themes matched in each displayed term; first matching theme supplies its colour. These are not inferred methods or semantic clusters.",
        "rare": "Exactly one occurrence in the selected source text, in one selected paper.",
        "single_paper": "More than one occurrence, all in one selected paper. This is not a single-occurrence rare term.",
        "unusual": "Not found in the available comparison text outside this scope, at least two scoped occurrences, and smoothed log-odds at least 1. This describes this library, not scientific novelty.",
        "outlier_min_occurrences": OUTLIER_MIN_OCCURRENCES,
        "outlier_min_log_odds": OUTLIER_MIN_LOG_ODDS,
        "comparison": "Uses the same selected source (abstracts or indexed full text) for the scope and the rest of the library. Missing source text is not evidence of absence in a paper.",
    }
