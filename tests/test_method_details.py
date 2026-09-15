"""Specific drug, signal and instrument detections on isolated synthetic text."""
import pytest


def detect(text, confidence="high"):
    """Exercise the actual detector without a database or a real library."""
    from doi_hunter_web.insight import _detect_methods

    found = {}
    _detect_methods(text, confidence, found, len(text))
    return found


@pytest.mark.parametrize("name,expected", [
    ("cocaine", "cocaine"), ("d-amphetamine", "amphetamine"),
    ("methamphetamine", "methamphetamine"), ("MDMA", "mdma"),
    ("3,4-methylenedioxymethamphetamine", "mdma"),
    ("morphine", "morphine"), ("nicotine", "nicotine"), ("ketamine", "ketamine"),
    ("esketamine", "esketamine"), ("CNO", "cno"), ("clozapine-N-oxide", "cno"),
    ("SCH 23390", "sch23390"), ("tetrodotoxin", "ttx"), ("D-AP5", "ap5"),
])
def test_specific_drug_is_an_intervention_with_traceable_evidence(name, expected):
    """A named substance and an explicit procedure generate one grounded chip."""
    sentence = f"Rats received {name} before the recording session."
    result = detect(sentence)
    detail = result["drug_" + expected]
    assert detail["category"] == "drugs" and detail["stage"] == "manipulation"
    assert detail["match"].lower() in detail["evidence"].lower()
    assert "received" in detail["evidence"]


def test_similar_drug_names_are_not_substring_aliases():
    """Chemically distinct compounds must not merge through suffix matching."""
    mdma = detect("Rats self-administered MDMA and methamphetamine.")
    assert {"drug_mdma", "drug_methamphetamine"} <= mdma.keys()
    assert "drug_amphetamine" not in mdma and "drug_mda" not in mdma
    cno = detect("Mice received clozapine-N-oxide.")
    assert "drug_cno" in cno and "drug_clozapine" not in cno
    esketamine = detect("Mice received esketamine.")
    assert "drug_esketamine" in esketamine and "drug_ketamine" not in esketamine


def test_operant_schedule_does_not_establish_drug_self_administration():
    """Injected cocaine plus food-reward training is not drug self-administration."""
    result = detect("Mice received cocaine intraperitoneally. They completed fixed-ratio operant training and a progressive-ratio breakpoint test for food rewards.")
    assert "drug_cocaine" in result and "selfadmin" not in result
    assert "selfadmin" in detect("Rats self-administered methamphetamine during daily sessions.")


@pytest.mark.parametrize("sentence", [
    "Cocaine affects dopamine release.",
    "Previous studies administered cocaine to rats.",
    "Smith et al. administered cocaine to rats.",
    "Future studies could administer cocaine to rats.",
    "No cocaine was administered.",
    "Cocaine was not administered.",
    "Cocaine was excluded from the protocol.",
    "Cocaine concentrations were measured after saline administration.",
    "Cocaine was measured in plasma after injection of saline.",
    "The DOI:10.1234/example was provided with the published method.",
    "Animals received saline and we measured MDA as a marker of lipid peroxidation.",
    "PCP was mentioned in the background discussion.",
    "An infusion over 3 s was followed by the Methamphetamine and high temperatures page header.",
])
def test_drug_background_exclusion_and_measurement_are_not_administration(sentence):
    """A mentioned or assayed compound is not automatically an intervention."""
    assert not any(d["category"] == "drugs" for d in detect(sentence).values())


@pytest.mark.parametrize("sentence", [
    "Cocaine (10 mg/kg) was administered before testing.",
    "Cocaine hydrochloride was dissolved in saline before use.",
    "Animals underwent cocaine self-administration.",
])
def test_drug_procedure_after_name_is_detected(sentence):
    assert "drug_cocaine" in detect(sentence)


def test_pdf_line_wraps_keep_procedure_context_and_complete_evidence():
    """A line ending between action and compound must not erase its identity."""
    result = detect("Animals received\ncocaine (2.5 mg/kg) before testing.\nWe recorded dopamine signals\nusing fiber photometry.")
    assert result["drug_cocaine"]["evidence"] == "Animals received cocaine (2.5 mg/kg) before testing."
    assert result["signal_dopamine"]["evidence"] == "We recorded dopamine signals using fiber photometry."


def test_neural_signals_and_instruments_are_separate():
    """Electrode identity does not silently substitute for a recorded signal."""
    result = detect("We recorded action potentials and local field potentials using Neuropixels probes.")
    assert {"signal_spikes", "signal_lfp", "tool_neuropixels"} <= result.keys()
    assert result["signal_spikes"]["category"] == "signals"
    assert result["tool_neuropixels"]["category"] == "techniques_tools"
    tool_only = detect("Neuropixels probes were inserted into the cortex.")
    assert "tool_neuropixels" in tool_only
    assert not {"signal_spikes", "signal_lfp", "signal_calcium"} & tool_only.keys()


def test_dopamine_photometry_is_not_calcium_imaging():
    """dLight reports dopamine; photometry alone does not identify its analyte."""
    result = detect("We recorded dopamine signals using dLight1.3 and fiber photometry.")
    assert {"signal_dopamine", "tool_dlight", "tool_fiber_photometry"} <= result.keys()
    assert not {"calcium", "signal_calcium", "tool_gcamp"} & result.keys()


def test_two_photon_structure_does_not_imply_calcium():
    result = detect("We imaged dendritic spine morphology using two-photon microscopy.")
    assert "tool_two_photon" in result
    assert not {"calcium", "signal_calcium"} & result.keys()
    calcium = detect("We imaged calcium transients with GCaMP6f using two-photon microscopy.")
    assert {"signal_calcium", "tool_gcamp", "tool_two_photon"} <= calcium.keys()


@pytest.mark.parametrize("technique,ident", [
    ("tetrodes", "tetrodes"), ("silicon probes", "silicon_probe"),
    ("patch-clamp", "patch_clamp"), ("whole-cell recordings", "whole_cell"),
    ("multielectrode arrays", "mea"), ("one-photon microscopy", "one_photon"),
    ("miniscopes", "miniscope"), ("fast-scan cyclic voltammetry", "fscv"),
    ("fiber photometry", "fiber_photometry"), ("voltage-sensitive dyes", "voltage_dye"),
])
def test_explicit_tools_are_individually_visible(technique, ident):
    result = detect(f"We recorded neural activity using {technique}.")
    assert "tool_" + ident in result


def test_optical_voltage_is_not_automatically_extracellular_electrophysiology():
    result = detect("We imaged membrane voltage using ASAP4 and two-photon microscopy.")
    assert {"signal_voltage", "tool_voltage_indicator", "tool_two_photon"} <= result.keys()
    assert "ephys" not in result and "calcium" not in result


def test_chemical_assay_tool_is_not_automatically_pharmacokinetics():
    result = detect("We measured dopamine concentrations in brain tissue by HPLC.")
    assert {"signal_dopamine", "tool_hplc"} <= result.keys()
    assert "pk" not in result
    levels = detect("Brain concentrations of MDMA were determined using chromatography.")
    assert "pk" in levels


def test_gas_chromatography_is_not_liquid_chromatography():
    result = detect("We measured samples using gas chromatography-mass spectrometry (GC-MS).")
    assert {"tool_gcms", "tool_mass_spec"} <= result.keys()
    assert "tool_lcms" not in result


def test_tool_background_and_negation_do_not_generate_specific_chips():
    result = detect("Previous studies recorded spikes using tetrodes. No Neuropixels probes were used.")
    assert not {"tool_tetrodes", "tool_neuropixels", "signal_spikes"} & result.keys()


@pytest.mark.parametrize("sentence,ident", [
    ("Dopamine signals were not recorded.", "signal_dopamine"),
    ("Calcium imaging not performed.", "signal_calcium"),
    ("Neuropixels probes were not implanted.", "tool_neuropixels"),
])
def test_explicitly_omitted_recordings_are_not_positive_evidence(sentence, ident):
    assert ident not in detect(sentence)


def test_detail_categories_survive_pipeline_and_matrix(library, tmp_path):
    """Check the public method payload using a temporary, explicitly indexed file."""
    from doi_hunter_web import catalog, fulltext, insight

    item = catalog.create_item({"title": "Synthetic instrument study", "abstract": ""})
    path = tmp_path / "synthetic-methods.txt"
    path.write_text("Methods\nMice received cocaine. We recorded action potentials using Neuropixels probes.\nResults\nActivity varied.", encoding="utf-8")
    catalog.register_file(item["id"], path)
    fulltext.index_item(item["id"])
    result = insight.methods()
    matrix = {m["id"]: m for m in result["matrix"]}
    assert matrix["drug_cocaine"]["category"] == "drugs"
    assert matrix["signal_spikes"]["category"] == "signals"
    assert matrix["tool_neuropixels"]["category"] == "techniques_tools"
    assert len(result["papers"][0]["pipeline"]) == 5


def test_drug_title_alone_does_not_generate_administration(library):
    from doi_hunter_web import catalog, insight

    catalog.create_item({"title": "Cocaine administration affects neurons", "abstract": ""})
    assert not any(d["category"] == "drugs" for p in insight.methods()["papers"] for d in p["detected"])
