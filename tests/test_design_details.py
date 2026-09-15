"""Source-grounded design omissions and negative controls, without library writes."""
import pytest

from doi_hunter_web.insight import _detect_methods


def detect(text, confidence="high"):
    """Run the real detector on a local sentence without accessing a library."""
    found = {}
    _detect_methods(text, confidence, found, len(text))
    return found


def test_icss_is_a_specific_assay_with_evidence():
    result = detect("ICSS procedure: Rats were tested in sound attenuating operant chambers, which contain a wheel manipulandum within one wall.")
    assert result["tool_icss"]["category"] == "techniques_tools"
    assert result["tool_icss"]["stage"] == "measurement"
    assert "ICSS procedure" in result["tool_icss"]["evidence"]


@pytest.mark.parametrize("text", [
    "A previous study tested ICSS reward thresholds in rats.",
    "This review discusses intracranial self-stimulation experiments.",
    "ICSS was not performed.",
    "Future studies could use ICSS to test reward.",
])
def test_icss_mentioned_elsewhere_is_not_attributed(text):
    assert "tool_icss" not in detect(text)


def test_ethanol_1986_separate_temperature_groups():
    """The real abstract explicitly describes separate groups, not a crossover."""
    text = "Immediately after injection, separate groups were exposed to one of five ambient temperatures from 12 to 34° C."
    found = detect(text, "low")
    assert {"betweensubject", "design_temperature_conditions"} <= found.keys()
    assert not {"withinsubject", "factorial", "rct"} & found.keys()


def test_ethanol_1986_temperature_order_is_not_random_allocation():
    found = detect("One chamber temperature was studied each day in a random order.")
    assert "design_random_order" in found
    assert "rct" not in found


def test_random_reference_order_is_not_a_study_design():
    assert "design_random_order" not in detect("References were listed in a random order.")


def test_ethanol_1990_dose_groups_are_detected_across_pdf_linebreaks():
    text = "A rat was weighed and then administered ethanol by gavage at a dose of 0,\n2.0, 4.0, 6.0, or 8.0 g/kg body mass (N = 5 per dose group)."
    found = detect(text)
    assert {"doseresponse", "betweensubject", "drug_ethanol"} <= found.keys()
    assert "factorial" not in found
    assert "8.0 g/kg" in found["doseresponse"]["evidence"]


def test_heroin_2017_same_subject_repeated_sessions():
    text = "Each\nrat in this experiment was exposed to three drug sessions, in which rats received three iv heroin injections."
    found = detect(text)
    assert {"design_repeated_exposure", "withinsubject", "drug_heroin"} <= found.keys()
    assert "three drug sessions" in found["design_repeated_exposure"]["evidence"]


@pytest.mark.parametrize("text", [
    "Rats recovered for three days following surgery.",
    "Samples were stored at 4 °C and analyzed two days later.",
    "Three rats received one injection each.",
    "A rat received ketamine at 50 mg/kg and xylazine at 5 mg/kg.",
])
def test_recovery_storage_or_different_drugs_do_not_invent_designs(text):
    found = detect(text)
    assert not {"design_repeated_exposure", "design_temperature_conditions", "doseresponse", "factorial"} & found.keys()
