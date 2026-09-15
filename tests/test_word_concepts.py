"""Word-map semantics on isolated synthetic source text, never the user's library."""
from __future__ import annotations

import string

import pytest


def create_paper(abstract: str, title: str = "Synthetic word-map paper") -> int:
    """Create one fixture reference with explicit abstract text."""
    from doi_hunter_web import catalog
    return catalog.create_item({"title": title, "abstract": abstract})["id"]


@pytest.mark.parametrize("term, expected", [
    ("serotonergic recordings", "recording"), ("ketamine", "drugs"),
    ("dopamine release", "neural"), ("working memory", "behavior"),
    ("mice", "biology"), ("alliesthesia", "physiology"),
    ("epilepsy", "disease"), ("regression", "statistics"),
    ("hysteresis", "computing"), ("ratios", "other"),
    ("fiber photometry", "recording"), ("fibre photometry", "recording"),
    ("dLight dopamine", "neural"), ("calcium imaging", "recording"),
])
def test_lexical_themes_are_word_bounded_and_stable(term, expected):
    """Only terms present in the phrase determine its explainable category."""
    from doi_hunter_web.word_concepts import classify_term
    assert classify_term(term)["concept_id"] == expected


def test_ambiguous_phrase_keeps_secondary_theme():
    """Display priority must not erase the neural theme of a recording phrase."""
    from doi_hunter_web.word_concepts import classify_term
    result = classify_term("serotonergic recordings")
    assert [category["id"] for category in result["concepts"]] == ["recording", "neural"]


def test_photometry_source_terms_preserve_technique_and_signal_distinction(library):
    """Word themes do not turn the recording technique into an inferred calcium signal."""
    from doi_hunter_web import insight
    create_paper("Fiber photometry recorded dLight dopamine. Fiber photometry detected dLight dopamine.")
    result = insight.words()
    entries = {word["word"]: word for word in result["words"]}
    assert entries["fiber photometry"]["concept_id"] == "recording"
    assert entries["dlight dopamine"]["concept_id"] == "neural"
    assert not any("calcium" in word for word in entries)
    assert all(entry["evidence"] for entry in (entries["fiber photometry"], entries["dlight dopamine"]))


def test_single_occurrence_is_not_repetition_in_one_paper(library):
    """Occurrence count and document frequency answer different rarity questions."""
    from doi_hunter_web import insight
    first = create_paper("Dopamine dopamine dopamine. Alliesthesia.")
    create_paper("Memory memory.")
    result = insight.words()
    rare = next(word for word in result["rare_words"] if word["word"] == "alliesthesia")
    repeated = next(word for word in result["words"] if word["word"] == "dopamine")
    assert (rare["count"], rare["n_items"], rare["rarity"], rare["rare"]) == (1, 1, "single_occurrence", True)
    assert (repeated["count"], repeated["n_items"], repeated["rarity"], repeated["rare"]) == (3, 1, "single_paper", False)
    assert rare["items"] == [first]
    assert rare["evidence"][0]["sentence"] == "Alliesthesia."
    assert not result["outlier_words"]
    assert rare["rest_count"] is None and rare["absent_from_rest"] is None


def test_rare_candidates_survive_frequent_cap(library):
    """An occurrence outside the old top-150 list remains available to the rare view."""
    from doi_hunter_web import insight
    terms = ["neuroterm" + a + b for a in string.ascii_lowercase for b in string.ascii_lowercase][:170]
    create_paper(". ".join(term + " " + term for term in terms) + ". Alliesthesia.")
    create_paper("Memory memory.")
    result = insight.words(limit=150)
    assert len(result["words"]) == 150 and result["limits"]["frequent_capped"]
    assert "alliesthesia" not in {word["word"] for word in result["words"]}
    assert "alliesthesia" in {word["word"] for word in result["rare_words"]}
    assert result["candidate_counts"]["frequent"] > 150


def test_local_unusual_terms_require_eligible_comparison(library):
    """Unusual means absent from comparison text with minimum within-scope support."""
    from doi_hunter_web import insight
    from doi_hunter_web.db import get_conn
    selected = create_paper("Hysteresis hysteresis hysteresis. Alliesthesia. " + "Cocaine. " * 10)
    create_paper("Dopamine. " * 30 + "Cocaine.")
    conn = get_conn()
    collection = conn.execute("INSERT INTO collections(name) VALUES ('Synthetic scope')").lastrowid
    conn.execute("INSERT INTO item_collections VALUES (?, ?)", (selected, collection))
    result = insight.words(collection_id=collection)
    outlier = next(word for word in result["outlier_words"] if word["word"] == "hysteresis")
    assert result["scope"]["comparison_available"]
    assert outlier["unusual"] and outlier["absent_from_rest"]
    assert outlier["count"] == 3 and outlier["rest_count"] == 0 and outlier["rest_n_items"] == 0
    assert outlier["distinct"] >= result["semantics"]["outlier_min_log_odds"]
    assert not {"alliesthesia", "cocaine"} & {word["word"] for word in result["outlier_words"]}
    assert result["scope"]["comparison_items_with_text"] == 1


def test_missing_comparison_source_does_not_imply_absence(library):
    """A different paper without an abstract cannot establish lexical unusualness."""
    from doi_hunter_web import insight
    from doi_hunter_web.db import get_conn
    selected = create_paper("Hysteresis hysteresis hysteresis.")
    create_paper("")
    conn = get_conn()
    collection = conn.execute("INSERT INTO collections(name) VALUES ('Synthetic empty comparator')").lastrowid
    conn.execute("INSERT INTO item_collections VALUES (?, ?)", (selected, collection))
    result = insight.words(collection_id=collection)
    assert not result["scope"]["comparison_available"] and not result["outlier_words"]
    assert all(word["absent_from_rest"] is None for word in result["words"])


def test_fulltext_rare_terms_exclude_references_and_abstract_fallback(library, tmp_path):
    """Apply identical rarity rules to indexed body text, excluding its references."""
    from doi_hunter_web import catalog, fulltext, insight
    item_id = create_paper("Ketamine ketamine. Abstractonly.")
    create_paper("Abstractonly abstractonly.")
    path = tmp_path / "synthetic-words.txt"
    path.write_text("Materials and methods\nDopamine was recorded in neurons. Dopamine signals were recorded again. Alliesthesia.\nResults\nMemory memory.\nReferences\nPsilocybin psilocybin psilocybin.", encoding="utf-8")
    catalog.register_file(item_id, path)
    assert fulltext.index_item(item_id) > 100
    result = insight.words(source="fulltext")
    rare = next(word for word in result["rare_words"] if word["word"] == "alliesthesia")
    assert rare["count"] == 1 and rare["evidence"][0]["source"] == "fulltext"
    every_word = {word["word"] for key in ("words", "rare_words", "outlier_words") for word in result[key]}
    assert not {"psilocybin", "ketamine", "abstractonly"} & every_word
    assert result["limits"]["no_fulltext"] == 1
