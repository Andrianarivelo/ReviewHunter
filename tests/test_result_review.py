"""Independent adversarial checks for observation direction and comparison context."""
import pytest

from doi_hunter_web.insight import sentences
from doi_hunter_web.result_extraction import compare_findings, extract_findings


def observations(text):
    """Exercise the public extractor with synthetic source text only."""
    return extract_findings("Cocaine study in rats", text, "", splitter=sentences)


@pytest.mark.parametrize("text", [
    "No increase in body temperature was observed after cocaine.",
    "Cocaine produced no significant increase in body temperature.",
])
def test_absent_increase_never_becomes_positive_direction(text):
    """An absent increase does not prove a decrease or an unchanged endpoint."""
    result = observations(text)
    assert all(finding["direction"] not in ("increase", "decrease") for finding in result)


def test_endpoint_own_predicate_overrides_earlier_enumeration():
    result = observations("Cocaine increased body temperature and dopamine release decreased.")
    assert {finding["outcome"]: finding["direction"] for finding in result} == {
        "body temperature": "increase", "dopamine release": "decrease"}


def test_negation_does_not_spread_across_endpoint_predicates():
    result = observations("Cocaine did not alter body temperature and increased locomotor activity.")
    assert {finding["outcome"]: finding["direction"] for finding in result} == {
        "body temperature": "no_detected_change", "locomotor activity": "increase"}


@pytest.mark.parametrize("text", [
    "A previous study found that cocaine increased body temperature.",
    "Prior evidence showed that cocaine increased body temperature.",
    "To assess the effect of cocaine, body temperature was increased experimentally.",
])
def test_background_and_experimental_setup_are_not_observations(text):
    assert observations(text) == []


@pytest.mark.parametrize("first,second", [
    ("after chronic treatment", "after acute treatment"),
    ("at night", "during the day"),
    ("in pregnant females", "in nonpregnant females"),
    ("following repeated exposure", "following a single exposure"),
    ("in anesthetized rats", "in awake rats"),
    ("after 24 hours", "after 48 hours"),
])
def test_explicit_incompatible_qualifiers_cannot_share_a_comparison(first, second):
    """Either separate contexts or conservatively exclude an unresolved context."""
    base = ("Cocaine (10 mg/kg, i.p.) at 30\u00b0C increased body temperature "
            "in rats compared with saline controls ")
    papers = [{"id": ident, "title": "Synthetic study in rats",
               "findings": observations(base + qualifier + ".")}
              for ident, qualifier in enumerate((first, second))]
    assert all(paper["findings"] for paper in papers)
    assert compare_findings(papers) == []


def test_plural_sex_labels_are_retained_in_context():
    result = observations("Cocaine increased body temperature in females.")
    assert result and result[0]["context"]["sex"] == ["female"]


def test_a_new_subject_does_not_inherit_the_first_drug_factor():
    result = observations("Cocaine increased body temperature and saline decreased locomotor activity.")
    locomotor = [finding for finding in result if finding["outcome"] == "locomotor activity"]
    assert all(finding["factor"] != "Cocaine" for finding in locomotor)


@pytest.mark.parametrize("text", [
    "Cocaine-induced increases in body temperature were reduced by saline.",
    "Cocaine decreased the reduction in body temperature.",
])
def test_nested_changes_do_not_become_simple_endpoint_direction(text):
    result = observations(text)
    assert all(finding["direction"] not in ("increase", "decrease") for finding in result)


def test_withdrawal_does_not_pool_with_administration():
    suffix = ("(10 mg/kg, i.p.) at 30\u00b0C increased body temperature in rats "
              "compared with saline controls.")
    papers = [{"id": ident, "title": "Synthetic study in rats",
               "findings": observations(prefix + " " + suffix)}
              for ident, prefix in enumerate(("Cocaine", "Cocaine withdrawal"))]
    assert compare_findings(papers) == []
