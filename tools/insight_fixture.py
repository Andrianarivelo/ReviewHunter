"""Create a disposable, explicitly synthetic insight-map demonstration library.

Run this script before ``ui_snapshot.py --insights``. The generated library stays
beneath the requested Temp/claude directory; an existing database is never edited.
The text attachments exercise the production full-text indexing path without
implying that these invented teaching examples are published research.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "claude"

# Scientific phrases are intentionally repeated across documents to exercise
# frequency, distinctiveness, co-occurrence, and conservative method detection.
PAPERS = [
    {
        "title": "Ambient temperature and stimulant self-administration",
        "authors": ["Example, Alice"],
        "abstract": "We examined the effect of ambient temperature on methamphetamine self-administration. Male rats self-administered methamphetamine under a progressive ratio schedule. Ambient temperatures increased self-administration.",
        "keywords": ["ambient temperature", "self-administration"],
        "methods": "Male Wistar rats (n = 24) were randomly assigned to treatment groups. Methamphetamine was administered intravenously at 0.1 mg/kg. Rats performed operant self-administration under a progressive ratio schedule. Body temperature was measured by telemetry. A two-way ANOVA compared temperature groups. The animal ethics committee approved the study.",
        "collection": "parent",
    },
    {
        "title": "Ambient temperatures and locomotor activity",
        "authors": ["Example, Alice", "Example, Bob"],
        "abstract": "We investigated whether ambient temperature alters locomotor activity. Male and female rats received cocaine. Ambient temperature and body temperature were measured using telemetry.",
        "keywords": ["ambient temperatures", "locomotor activity"],
        "methods": "Male and female Sprague-Dawley rats (n = 32) received cocaine intraperitoneally at 10 mg/kg. Locomotor activity was measured in an open field. Core temperature was recorded by telemetry. Investigators were blinded to treatment. Repeated measures ANOVA was used for analysis.",
        "collection": "parent",
    },
    {
        "title": "Temperature effects on dopamine release",
        "authors": ["Example, Carol"],
        "abstract": "Our aim was to measure the effect of ambient temperature on dopamine release. Mice received amphetamine. Dopamine release was measured using microdialysis and high performance liquid chromatography.",
        "keywords": ["ambient temperature", "dopamine release"],
        "methods": "Male mice (n = 18) were assigned to amphetamine treatment. Microdialysis probes measured dopamine release. High performance liquid chromatography quantified dopamine. First, mice were trained in an operant chamber. Subsequently, dopamine was sampled after drug administration. Linear regression related temperature to dopamine concentration.",
        "collection": "child",
    },
    {
        "title": "Human working memory and visual attention",
        "authors": ["Example, Dana"],
        "abstract": "We compared working memory and visual attention in human participants. Electroencephalography measured event-related potentials during visual attention and working memory tasks.",
        "keywords": ["working memory", "visual attention"],
        "methods": "Human participants (n = 40) provided informed consent. Electroencephalography recorded event-related potentials. Participants completed a working memory task. A power analysis determined sample size. Data are available in an open repository. A mixed effects model assessed performance.",
        "collection": None,
    },
]


# Richer examples deliberately stress long pipelines, named recording hardware,
# drug identities and word-cloud frequency/category differences. They are invented
# interface fixtures, not findings or annotations of published papers.
DETAIL_PAPERS = [
    {
        "title": "Synthetic study: cocaine and population spike dynamics",
        "authors": ["Synthetic, Elena", "Synthetic, Farid"],
        "abstract": "We tested whether cocaine alters dopamine signals and neural activity during reward seeking. Cocaine changed spike firing and local field potentials in this synthetic example. Neural activity and dopamine signals were measured during reward seeking. Dopamine signals and reward seeking were linked to cocaine treatment. Alliesthesia is a single-occurrence exploratory term.",
        "keywords": ["dopamine signals", "neural activity", "reward seeking", "cocaine"],
        "methods": "Male and female mice (n = 24) were randomly assigned to cocaine treatment or saline control in a within-subject design. Mice received cocaine intraperitoneally at 10 mg/kg. First, mice were trained in operant chambers under a fixed-ratio schedule. Then, we recorded action potentials and local field potentials using Neuropixels probes. We recorded dopamine signals using dLight1.3 and fiber photometry. An overhead video camera recorded locomotor activity during the open-field task. Kilosort sorted spikes and principal component analysis reduced neural population activity. A mixed effects model and permutation tests assessed neural activity. The ethics committee approved the study. Investigators were blinded and a power analysis determined sample size. Data and code are available in a synthetic repository.",
        "collection": "child", "tag": "neural-recording",
    },
    {
        "title": "Synthetic study: methamphetamine and tetrode reward recordings",
        "authors": ["Synthetic, Elena"],
        "abstract": "We examined the effect of methamphetamine on reward seeking and neural activity. Methamphetamine self-administration was paired with spike firing and local field potentials. Neural activity and reward seeking were recorded in rats. Dopamine signals accompany reward seeking in this synthetic example.",
        "keywords": ["neural activity", "reward seeking", "methamphetamine"],
        "methods": "Male rats (n = 18) self-administered methamphetamine intravenously at 0.05 mg/kg per infusion. Rats performed a progressive-ratio task in operant chambers. Tetrodes recorded action potentials and local field potentials during reward seeking. Spike sorting isolated single-unit activity. Fourier analysis measured spectral power. Video tracking measured locomotor activity. A repeated-measures ANOVA compared sessions.",
        "collection": "child", "tag": "neural-recording",
    },
    {
        "title": "Synthetic study: visual calcium transients during learning",
        "authors": ["Synthetic, Grace"],
        "abstract": "We investigated whether visual learning changes calcium transients in visual cortex. Calcium transients and neural activity were recorded using two-photon microscopy. Visual learning and calcium transients increased across synthetic training sessions. Visual cortex responses supported visual learning.",
        "keywords": ["visual cortex", "calcium transients", "visual learning"],
        "methods": "Transgenic mice (n = 16) expressed GCaMP6f in visual cortex. We imaged calcium transients using two-photon microscopy and GCaMP6f. Mice performed a visual discrimination task across repeated sessions. DeepLabCut video tracking quantified movement. A linear regression related calcium transients to behavioral performance. The ethics committee approved animal procedures.",
        "collection": None, "tag": "neural-recording",
    },
    {
        "title": "Synthetic study: dLight dopamine signals and MDMA",
        "authors": ["Synthetic, Farid"],
        "abstract": "Our aim was to measure the effect of MDMA on dopamine signals and locomotor activity. Dopamine signals were measured with fiber photometry during MDMA treatment. MDMA changed dopamine signals and locomotor activity in the synthetic demonstration.",
        "keywords": ["dopamine signals", "MDMA", "locomotor activity"],
        "methods": "Female mice (n = 20) received MDMA intraperitoneally at 5 mg/kg. We recorded dopamine signals using dLight1.3 and fiber photometry. An overhead video camera recorded locomotor activity. Body temperature was recorded using telemetry. Investigators were blinded to treatment. Bootstrap resampling estimated confidence intervals for dopamine signals.",
        "collection": "parent", "tag": "temperature",
    },
    {
        "title": "Synthetic study: thermal hysteresis and stimulant intake",
        "authors": ["Synthetic, Helena"],
        "abstract": "We examined the effect of ambient temperature on stimulant intake. Thermal hysteresis distinguishes the heating and cooling sessions in this synthetic example. Ambient temperature and thermal hysteresis were related to stimulant intake. Ambient temperature changed stimulant intake during heating.",
        "keywords": ["ambient temperature", "thermal hysteresis", "stimulant intake"],
        "methods": "Male rats (n = 22) received methamphetamine intraperitoneally at 2 mg/kg. Ambient temperature was varied across heating and cooling sessions in a counterbalanced design. Rats performed an operant task. Core temperature was measured by telemetry. A dose-response experiment tested graded doses. Repeated-measures ANOVA assessed intake.",
        "collection": "parent", "tag": "temperature",
    },
    {
        "title": "Synthetic study: thermal hysteresis and cocaine behavior",
        "authors": ["Synthetic, Helena"],
        "abstract": "We tested whether thermal hysteresis alters cocaine reward seeking. Ambient temperature and thermal hysteresis influenced locomotor activity. Ambient temperature was controlled during cocaine treatment. Reward seeking and locomotor activity were quantified in this synthetic example.",
        "keywords": ["thermal hysteresis", "ambient temperature", "reward seeking"],
        "methods": "Male and female rats (n = 28) received cocaine at 5 mg/kg by intraperitoneal injection. Rats performed conditioned place preference and open-field tests at two ambient temperatures. Core temperature was recorded by telemetry. The experiment used a factorial design. A mixed effects model quantified the temperature interaction.",
        "collection": "parent", "tag": "temperature",
    },
    {
        "title": "Synthetic study: human attention and scalp EEG",
        "authors": ["Synthetic, Ibrahim"],
        "abstract": "We compared visual attention and working memory in human participants. Visual attention was associated with EEG oscillations and working memory performance. Working memory and visual attention were assessed during a cognitive task. Mental chronometry describes a single exploratory analysis.",
        "keywords": ["working memory", "visual attention", "EEG oscillations"],
        "methods": "Human participants (n = 48) performed an n-back working memory task. EEG recorded event-related potentials during visual attention. Eye tracking measured pupil diameter. Wavelet analysis quantified spectral power. A preregistered power analysis determined sample size. Data and code are available in a synthetic repository.",
        "collection": None, "tag": "cognition",
    },
    {
        "title": "Synthetic study: neural networks and visual decoding",
        "authors": ["Synthetic, Jun"],
        "abstract": "We developed a neural network model for decoding visual cortex activity. Neural activity and visual cortex responses were used to predict visual attention. Cross-validation quantified decoding accuracy in this synthetic model. Persistent homology appears once as an exploratory descriptor.",
        "keywords": ["neural activity", "visual cortex", "decoding"],
        "methods": "A computational model simulated visual cortex activity. A neural network classified visual stimuli using cross-validation. Principal component analysis reduced dimensionality. Bootstrap resampling estimated confidence intervals. No drug administration was performed. Model code is available in a synthetic repository.",
        "collection": None, "tag": "computing",
    },
]


def goal_fixture_papers() -> list[dict]:
    """Create 12 synthetic cases with known focus provenance and overlapping collection scopes."""
    papers = copy.deepcopy([*DETAIL_PAPERS, *PAPERS])
    papers[0].update({
        "title": "MDMA fails to elicit reward facilitation during intracranial self-stimulation",
        "abstract": "Adult rats received MDMA. No reward facilitation was observed in this synthetic example.",
        "methods": "Male rats (n = 20) received MDMA intraperitoneally at 5 mg/kg. Intracranial self-stimulation (ICSS) thresholds were measured using a rate-frequency procedure. Electrodes delivered electrical stimulation to the medial forebrain bundle. A repeated-measures ANOVA compared reward thresholds.",
        "keywords": ["MDMA", "intracranial self-stimulation"],
    })
    papers[1]["abstract"] = "The effects of ambient temperature on methamphetamine self-administration were investigated. Male rats received methamphetamine. Drug intake was recorded at two ambient temperatures."
    papers[2].update({"title": "Visual calcium transients during learning", "abstract": "Calcium activity was recorded in visual cortex. Mice completed visual training sessions.", "collection": "sibling"})
    papers[3].update({"title": "Synthetic study: contextual analysis of MDMA behavior", "abstract": "We examined the effect of ambient temperature on whether it was modified by the chamber. MDMA was administered to mice."})
    papers[4].update({
        "title": "Effects of ambient temperature and dose on stimulant intake",
        "abstract": "We examined the effect of ambient temperature on stimulant intake and compared three dose groups. Thermal responses were quantified in this synthetic factorial experiment.",
        "methods": "Male rats (n = 30) received methamphetamine. Rats were assigned to three groups maintained at 20, 25 or 30 degrees C. Separate groups received methamphetamine doses of 0.5, 1 or 2 mg/kg. Core temperature was measured by telemetry and drug intake was measured in operant chambers. A two-way ANOVA compared the temperature and dose groups.",
    })
    papers[5].update({"title": "", "abstract": "", "methods": "", "keywords": [], "no_fulltext": True, "collection": None})
    for index in (6, 7, 11):
        papers[index]["collection"] = "sibling"
    return papers


def result_fixture_papers() -> list[dict]:
    """Create exact directional comparisons while keeping missing findings explicit."""
    papers = goal_fixture_papers()
    for index, direction in enumerate(("increased", "increased", "increased", "decreased", "increased")):
        temperature = 30 if index == 4 else 22
        finding = f"MDMA {direction} body temperature in rats at {temperature}\u00b0C after 5 mg/kg intraperitoneal administration compared with saline controls."
        papers[index].update({
            "title": f"Synthetic MDMA thermoregulation experiment {index + 1} in rats",
            "abstract": "We examined the effect of MDMA on body temperature in rats. " + finding,
            "results": finding,
            "methods": f"Male rats received MDMA at 5 mg/kg by intraperitoneal administration at {temperature} degrees C. Core temperature was recorded by telemetry. A factorial design and repeated-measures ANOVA were used.",
            "keywords": ["general", "MDMA", "body temperature", "ambient temperature", "thermoregulation",
                         "ambient temperature-dependent modulation of mesolimbic dopamine reward signalling"],
        })
    for index in (6, 7):
        finding = "Cocaine did not alter locomotor activity in rats at 22\u00b0C after 5 mg/kg intraperitoneal administration compared with saline controls."
        papers[index].update({
            "title": f"Synthetic cocaine locomotion experiment {index + 1} in rats",
            "abstract": "We examined the effect of cocaine on locomotor activity. " + finding,
            "results": finding,
            "keywords": ["general", "cocaine", "locomotor activity", "ambient temperature"],
            "methods": "Male rats received cocaine at 5 mg/kg intraperitoneally. Locomotor activity was recorded using video tracking.",
        })
    return papers


def create_fixture(destination: Path, *, detailed: bool = False, goals: bool = False, results: bool = False) -> dict:
    """Seed a new temporary library and return reproducible scope identifiers."""
    destination = destination.resolve()
    if not destination.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError(f"Fixture must stay beneath {TEMP_ROOT}")
    if (destination / "library.db").exists():
        raise ValueError("Refusing to change an existing database; choose a new fixture path")
    destination.mkdir(parents=True, exist_ok=True)
    os.environ["REVIEWHUNTER_HOME"] = str(destination)
    for name in ("REVIEWHUNTER_PAPERS_DIR", "DOIHUNTER_PAPERS_DIR"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(ROOT))
    from doi_hunter_web import catalog, db, fulltext, library

    db.reset_connections()
    parent = library.create_collection("Temperature_addiction (synthetic)")
    child = library.create_collection("Dopamine (synthetic)", parent_id=parent["id"])
    collections = {"parent": parent["id"], "child": child["id"]}
    if goals or results:
        collections["sibling"] = library.create_collection("Visual cognition (synthetic)")["id"]
    papers = result_fixture_papers() if results else goal_fixture_papers() if goals else [*DETAIL_PAPERS, *PAPERS] if detailed else PAPERS
    ids = []
    parent_items, descendant_items, sibling_items = [], [], []
    for index, paper in enumerate(papers, 1):
        item_id = library.upsert_item({
            "title": paper["title"], "authors": paper["authors"],
            "abstract": paper["abstract"], "keywords": paper["keywords"],
            "year": 2020 + index % 6, "status": "metadata", "source_query": "synthetic UI fixture",
        })
        path = destination / f"synthetic-paper-{index}.txt"
        result_text = paper.get("results", "Synthetic results are provided only to test the interface.")
        text = f"{paper['title']}\nAbstract\n{paper['abstract']}\nIntroduction\nA monkey study is cited once for context.\nMaterials and methods\n{paper['methods']}\nResults\n{result_text}\nReferences\n"
        if not paper.get("no_fulltext"):
            path.write_text(text, encoding="utf-8")
            catalog.register_file(item_id, path, title="Synthetic Methods-section text")
            if fulltext.index_item(item_id) < 100:
                raise RuntimeError("Synthetic document was not indexed")
        if paper["collection"]:
            library.assign_to_collection(item_id, collections[paper["collection"]])
            if paper["collection"] == "sibling":
                sibling_items.append(item_id)
            else:
                descendant_items.append(item_id)
            if paper["collection"] == "parent":
                parent_items.append(item_id)
        catalog.add_tag(item_id, paper.get("tag") or ("temperature" if paper["collection"] else "cognition"))
        if detailed and index == 1:
            catalog.add_tag(item_id, "detail-stress")
        ids.append(item_id)
    result = {"home": str(destination), "synthetic": True, "items": ids, "collections": collections,
              "titles": [paper["title"] for paper in papers], "detailed": detailed, "goals": goals, "results": results,
              "parent_items": parent_items, "descendant_items": descendant_items, "port": 8775}
    if detailed and not goals:
        result["detail_expectations"] = {
            "first_paper": ids[0], "stress_tag": "detail-stress", "single_occurrence_word": "alliesthesia", "subset_word": "hysteresis",
            "method_ids": ["drug_cocaine", "drug_methamphetamine", "drug_mdma", "tool_neuropixels", "tool_tetrodes",
                           "tool_two_photon", "tool_fiber_photometry", "tool_gcamp", "tool_dlight",
                           "signal_spikes", "signal_lfp", "signal_calcium", "signal_dopamine"],
            "paper_expectations": [
                {"id": ids[0], "present": ["drug_cocaine", "tool_neuropixels", "signal_spikes", "signal_lfp", "signal_dopamine"],
                 "absent": ["signal_calcium", "selfadmin"]},
                {"id": ids[1], "present": ["drug_methamphetamine", "tool_tetrodes", "selfadmin"], "absent": []},
                {"id": ids[2], "present": ["tool_two_photon", "tool_gcamp", "signal_calcium"], "absent": ["signal_dopamine"]},
                {"id": ids[3], "present": ["drug_mdma", "tool_dlight", "tool_fiber_photometry", "signal_dopamine"], "absent": ["signal_calcium"]},
            ],
        }
    if goals or results:
        result["sibling_items"] = sibling_items
        result["union_items"] = sorted(set(descendant_items + sibling_items))
        result["goal_expectations"] = {"negative": ids[0], "passive": ids[1], "title_focus": ids[2],
            "malformed": ids[3], "groups": ids[4], "unavailable": ids[5],
            "statuses": {str(ids[0]): "title_focus", str(ids[1]): "explicit_aim", str(ids[2]): "title_focus",
                         str(ids[3]): "explicit_aim", str(ids[5]): "unavailable"}}
    if results:
        result.pop("goal_expectations", None)
        result["result_expectations"] = {"increase_ids": ids[:3], "decrease_id": ids[3],
            "different_context_id": ids[4], "missing_id": ids[5], "unchanged_ids": ids[6:8],
            "comparison_ids": ids[:4], "directions": {"increase": 3, "decrease": 1},
            "long_concept": "ambient temperature-dependent modulation of mesolimbic dopamine reward signalling"}
    (destination / "fixture.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    db.reset_connections()
    return result


def main() -> None:
    """Create a new unique fixture unless a safe, unused destination is provided."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--detailed", action="store_true", help="Use 12 synthetic papers with detailed methods and rare words")
    parser.add_argument("--goals", action="store_true", help="Use 12 synthetic goal-provenance and multi-collection cases")
    parser.add_argument("--results", action="store_true", help="Use 12 synthetic concepts and directional outcome comparisons")
    args = parser.parse_args()
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    destination = args.home or Path(tempfile.mkdtemp(prefix="insight-ui-", dir=TEMP_ROOT))
    print(json.dumps(create_fixture(destination, detailed=args.detailed, goals=args.goals, results=args.results), indent=2))


if __name__ == "__main__":
    main()
