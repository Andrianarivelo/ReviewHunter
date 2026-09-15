"""Directional findings are observations, not aims or statistical meta-analysis."""
import copy

import pytest

from doi_hunter_web.insight import sentences
from doi_hunter_web.result_extraction import compare_findings, extract_findings, related_outcomes


def findings(text, title="Cocaine study in rats"):
    return extract_findings(title, text, "", splitter=sentences)


@pytest.mark.parametrize("verb,direction", [("increased", "increase"), ("decreased", "decrease"),
                                           ("did not alter", "no_detected_change"), ("did not increase", "unspecified")])
def test_explicit_direction_and_negation(verb, direction):
    text = f"Cocaine (10 mg/kg, i.p.) at 30°C {verb} body temperature in rats."
    result = findings(text)
    assert len(result) == 1 and result[0]["direction"] == direction
    assert result[0]["evidence"] == text
    assert result[0]["context"]["drugs"] == ["Cocaine"]
    assert result[0]["context"]["species"] == ["rats"]


@pytest.mark.parametrize("text", [
    "We hypothesized that cocaine increased body temperature.",
    "We tested whether cocaine increased body temperature.",
    "Previous studies found that cocaine increased body temperature.",
    "Cocaine may increase body temperature.",
    "The objective was to determine whether cocaine decreased body temperature.",
])
def test_aims_background_and_uncertainty_do_not_become_findings(text):
    assert findings(text) == []


def test_coordinated_opposite_outcomes_are_kept_separate():
    text = "Ethanol elicited a significant increase in colonic temperature and decrease in MR, EWL, and motor activity."
    result = {f["outcome"]: f["direction"] for f in findings(text, "Ethanol in rats")}
    assert result == {"colonic temperature": "increase", "metabolic rate": "decrease", "evaporative water loss": "decrease", "motor activity": "decrease"}


def test_direction_is_not_borrowed_from_another_measure_or_a_marker():
    text = "Locomotor activity during methamphetamine self-administration was reduced. Cocaine failed to induce an increase in GFAP, a marker for neurotoxicity."
    result = findings(text)
    assert not any(f["outcome"] in ("self-administration", "neurotoxicity") for f in result)
    active = findings("Methamphetamine self-administration increased body temperature.")
    assert len(active) == 1 and active[0]["outcome"] == "body temperature"


def test_regression_predictor_direction_is_not_a_drug_effect():
    text = "Regression indicated that ethanol-induced elevation in body temperature was associated with an increase in metabolic rate."
    assert findings(text) == []


def test_opposite_responses_of_different_animals_remain_mixed():
    result = findings("One rat showed a fall in core temperature and the other a rise.")
    assert result[0]["direction"] == "mixed"


def test_condition_specific_effects_do_not_collapse():
    text = "Cocaine at 20°C decreased body temperature in rats, whereas cocaine at 35°C increased body temperature in rats."
    result = findings(text)
    assert [(f["direction"], f["context"]["temperatures"]) for f in result] == [("decrease", ["20°C"]), ("increase", ["35°C"])]
    assert all(f["evidence"] == text for f in result)


def test_biphasic_response_remains_mixed():
    result = findings("When the dose of heroin was increased, brain temperature showed a biphasic down-up response.", "Heroin in rats")
    assert result[0]["direction"] == "mixed"
    assert result[0]["factor"] == "Heroin dose"


def test_results_source_and_findings_cap_are_visible():
    text = "Cocaine increased body temperature in rats."
    result = extract_findings("Cocaine in rats", "", text, splitter=sentences)
    assert result[0]["source"] == "results"
    many = findings(" ".join(f"Cocaine ({dose} mg/kg) increased body temperature in rats." for dose in range(1, 12)))
    assert len(many) == 8 and many.capped


def paper(ident, direction="increased", dose="10", temperature="30", species="rats"):
    """Build compatible records through the actual extractor, not fabricated flags."""
    title = f"Cocaine study {ident} in {species}"
    result = findings(f"Cocaine ({dose} mg/kg, i.p.) at {temperature}°C {direction} body temperature in {species} compared with saline controls.", title)
    return {"id": ident, "title": title, "findings": result}


def test_compatible_paper_counts_and_minority_are_descriptive():
    papers = [paper(1), paper(2), paper(3, "decreased")]
    # Repeated sentences/records from one paper do not gain extra votes.
    papers[0]["findings"].append(copy.deepcopy(papers[0]["findings"][0]))
    groups = compare_findings(papers)
    assert len(groups) == 1
    assert groups[0]["direction_counts"] == {"increase": 2, "decrease": 1}
    assert groups[0]["paper_count"] == 3 and groups[0]["minority_directions"] == ["decrease"]
    assert all("paper_id" in f for f in groups[0]["findings"])
    assert "not statistical outliers" in groups[0]["interpretation"]


@pytest.mark.parametrize("different", [{"dose": "20"}, {"temperature": "35"}, {"species": "mice"}])
def test_incompatible_contexts_are_not_compared(different):
    assert compare_findings([paper(1), paper(2, **different)]) == []


def test_unknown_context_and_ties_are_not_outliers():
    unknown = {"id": 1, "title": "Paper", "findings": findings("Cocaine increased body temperature.", "Paper")}
    assert compare_findings([unknown, {**unknown, "id": 2}]) == []
    groups = compare_findings([paper(1), paper(2, "decreased")])
    assert groups[0]["minority_directions"] == []


def test_related_endpoints_keep_different_experiments_visible_without_outliers():
    papers = [paper(1), paper(2, "decreased", dose="20")]
    assert compare_findings(papers) == []
    related = related_outcomes(papers)
    assert related[0]["direction_counts"] == {"increase": 1, "decrease": 1}
    assert related[0]["comparison_level"] == "different_contexts"
    assert "minority_directions" not in related[0]
    assert "not directly comparable" in related[0]["interpretation"]


def test_dose_lists_and_unknown_comparators_are_not_compatible():
    for text in ["Cocaine (5 or 10 mg/kg) at 30°C increased body temperature in rats compared with saline controls.",
                 "Cocaine (10 mg/kg) at 30°C increased body temperature in rats."]:
        assert not findings(text)[0]["comparison_eligible"]


def test_goals_endpoint_integrates_findings_without_changing_aim(library):
    from doi_hunter_web import catalog, insight
    catalog.create_item({"title": "Cocaine study in rats", "abstract": "We tested whether cocaine alters body temperature. Cocaine (10 mg/kg, i.p.) at 30°C increased body temperature in rats."})
    result = insight.goals()
    paper_result = result["papers"][0]
    assert paper_result["aims"] and paper_result["findings"]
    assert paper_result["findings"][0]["paper_id"] == paper_result["id"]
    assert result["result_comparisons"] == []
    assert result["coverage"]["with_findings"] == 1
