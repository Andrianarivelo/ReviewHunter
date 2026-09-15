"""Source preservation and conservative goal extraction on isolated synthetic papers."""
from __future__ import annotations

import pytest

from doi_hunter_web.goal_extraction import clean_phrase, extract_goal, extract_pairs
from doi_hunter_web.insight import sentences


def extract(title: str, abstract: str = "", introduction: str = "") -> dict:
    """Use the application's real sentence splitter with an in-memory source fixture."""
    return extract_goal(title, abstract, introduction, splitter=sentences)


def test_every_paper_gets_an_honest_focus_status(library):
    """The atlas must include title-only and untitled papers without inventing aims."""
    from doi_hunter_web import catalog, insight
    for title, abstract in [("Working memory under distraction", ""),
                            ("Temperature study", "We investigated whether temperature affects locomotor activity."),
                            ("", "")]:
        catalog.create_item({"title": title, "abstract": abstract})
    result = insight.goals()
    assert len(result["papers"]) == 3
    assert result["coverage"] == {"papers": 3, "explicit_aim": 1, "title_focus": 1, "unavailable": 1, "with_relationships": 1}
    fallback = next(paper for paper in result["papers"] if paper["focus_status"] == "title_focus")
    assert fallback["focus"] == fallback["title"] and fallback["focus_source"] == "title"
    assert not fallback["aims"] and fallback["goal_type"] == "unknown"
    missing = next(paper for paper in result["papers"] if paper["focus_status"] == "unavailable")
    assert missing["focus"] == "" and missing["confidence"] == "none"


def test_passive_investigation_and_temperature_dependence():
    """A passive objective is retained even without 'we' or an Objective heading."""
    abstract = "The relationship between ambient temperature, body temperature, and brain sensitivity to ethanol was investigated in rats."
    result = extract("Temperature dependence of ethanol depression in rats", abstract)
    assert result["focus"] == abstract and result["focus_status"] == "explicit_aim"
    assert any(pair["factor"] == "temperature" and pair["outcome"] == "ethanol depression" for pair in result["pairs"])
    assert any(pair["outcome"] == "body temperature and brain sensitivity to ethanol" for pair in result["pairs"])


def test_mechanism_assessment_retains_full_focus_and_conditions():
    """The scientific purpose remains complete while a relation drops trailing context."""
    abstract = "This study was designed to assess the thermoregulatory mechanisms responsible for the elevation in body temperature following ethanol administration when exposed to a high ambient temperature."
    result = extract("Ethanol thermoregulation", abstract)
    assert result["focus"] == abstract and result["goal_type"] == "mechanism"
    pair = next(pair for pair in result["pairs"] if pair["source"] == "abstract")
    assert pair["factor"] == "ethanol administration" and pair["outcome"] == "elevation in body temperature"
    assert pair["evidence"] == abstract


def test_how_objective_and_dose_clause_do_not_pollute_factor():
    """A dose-defining relative clause is not part of the factor's noun label."""
    abstract = "Here we examined how intravenous heroin at a dose that maintains self-administration (0.1 mg/kg) affects brain temperature homeostasis in freely moving rats under conditions that mimic drug use."
    result = extract("Brain temperature effects of intravenous heroin", abstract)
    assert result["focus"] == abstract
    assert any(pair["factor"] == "intravenous heroin" and pair["outcome"] == "brain temperature homeostasis" for pair in result["pairs"])
    assert all("dose" not in pair["factor"] for pair in result["pairs"])


def test_mdma_abbreviations_keep_raw_evidence():
    """Matching aliases improve labels without rewriting the source sentence."""
    objective = "Objective: To determine how body temperature responses under high and low T A influence MDMA IVSA."
    result = extract("High ambient temperature facilitates the acquisition of 3,4-methylenedioxymethamphetamine (MDMA) self-administration", objective)
    assert result["focus"] == objective
    assert any(pair["outcome"] == "mdma intravenous self-administration" for pair in result["pairs"])
    assert result["pairs"][0]["evidence"] == objective


def test_passive_modification_never_becomes_a_verb_fragment():
    """The reported regression is rejected or converted to its actual noun arguments."""
    source = "We determined whether temperature changes are modified with frequent exposure to the drug."
    pairs = extract_pairs(source, "abstract")
    assert pairs and any(pair["outcome"] == "temperature changes" for pair in pairs)
    assert all("are modified" not in pair["outcome"] for pair in pairs)
    assert clean_phrase("are modified with frequent exposure to the drug") == ""


def test_noun_changes_not_mistaken_for_causal_verb():
    """Try later verb boundaries when an earlier 'changes' is a noun."""
    title = "Small Changes in Ambient Temperature Cause Large Changes in MDMA-Induced Serotonin Neurotoxicity and Core Body Temperature in the Rat"
    result = extract(title)
    assert result["focus_status"] == "title_focus" and not result["aims"]
    assert [(pair["factor"], pair["outcome"]) for pair in result["pairs"]] == [
        ("small changes in ambient temperature", "large changes in mdma-induced serotonin neurotoxicity and core body temperature")]


def test_negative_title_relationship_remains_explicitly_negative():
    """An edge from a null finding must not silently become a positive effect."""
    title = "Enhancement of Cocaine-Induced Hyperthermia Fails to Elicit Neurotoxicity"
    pair = extract(title)["pairs"][0]
    assert pair["negated"] and pair["relation_label"] == "No effect reported"
    assert pair["evidence"] == title and pair["source"] == "title"


def test_aggregate_link_does_not_hide_mixed_polarity(library):
    """Aggregate only unanimous negative statements as negative; preserve each source."""
    from doi_hunter_web import catalog, insight
    negative = "Enhancement of Cocaine-Induced Hyperthermia Fails to Elicit Neurotoxicity"
    catalog.create_item({"title": negative})
    single = insight.goals()["links"][0]
    assert single["negated"] and single["relation_label"] == "No effect reported"
    catalog.create_item({"title": "Effects of enhancement of cocaine-induced hyperthermia on neurotoxicity"})
    mixed = next(link for link in insight.goals()["links"] if link["factor"] == "enhancement of cocaine-induced hyperthermia")
    assert not mixed["negated"] and mixed["relation_label"] == "Mixed statements"
    assert {member["negated"] for member in mixed["items"]} == {True, False}
    assert any(member["evidence"] == negative for member in mixed["items"])


@pytest.mark.parametrize("abstract", [
    "Temperatures in this study were simultaneously recorded from three locations.",
    "To determine the thermal response, body temperatures were measured every 30 minutes.",
    "Previous studies investigated whether temperature affects memory.",
    "The results demonstrate that temperature increased locomotor activity.",
])
def test_measurement_and_background_sentences_are_not_aims(abstract):
    """A study mention or an operational measurement purpose is insufficient."""
    result = extract("Thermal responses", abstract)
    assert result["focus_status"] == "title_focus" and result["aims"] == []


def test_long_focus_and_intro_evidence_are_not_truncated():
    """Preserve the complete purpose sentence, including its limiting conditions."""
    source = "We investigated whether temperature affects memory " + "in a carefully controlled experimental context " * 12 + "without claiming a causal mechanism."
    result = extract("Temperature and memory", "A descriptive abstract without a stated purpose.", source)
    assert result["focus"] == source and len(result["focus"]) > 500
    assert result["focus_source"] == "introduction"
    assert result["aims"][0]["sentence"].endswith("without claiming a causal mechanism.")
    assert all(len(pair["outcome"].split()) <= 20 for pair in result["pairs"])


def test_ambiguous_colon_title_does_not_make_drug_an_outcome():
    """The explicit subtitle supplies the factor, while the outcome is body temperature."""
    title = "Cocaine and Body Temperature in the Rat: Effects of Ambient Temperature"
    assert [(pair["factor"], pair["outcome"]) for pair in extract(title)["pairs"]] == [
        ("ambient temperature", "body temperature")]


@pytest.mark.parametrize("action", ["and compared three dose groups", "and we assessed locomotor activity", "while separately measured temperature"])
def test_coordinated_research_action_does_not_leak_into_outcome(action):
    """Separate the following research clause while preserving the complete aim."""
    source = "We examined the effect of ambient temperature on stimulant intake " + action + "."
    result = extract("Synthetic stimulant experiment", source)
    assert result["focus"] == source
    assert result["pairs"][0]["outcome"] == "stimulant intake"
    assert result["pairs"][0]["evidence"] == source


@pytest.mark.parametrize("outcome", ["stimulant intake and locomotor activity", "working memory and test performance"])
def test_coordinated_outcome_nouns_remain_complete(outcome):
    """A coordinated noun phrase must not be mistaken for a new research action."""
    source = "We examined the effect of ambient temperature on " + outcome + "."
    assert extract("Synthetic experiment", source)["pairs"][0]["outcome"] == outcome
