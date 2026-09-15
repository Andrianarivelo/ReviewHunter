"""Specific intervention, signal and instrument vocabulary for evidence maps.

The tables are deliberately independent: an instrument does not establish which
signal was measured, and a drug name does not establish administration. Context
guards below complement the shared negation and section checks in insight.py.
See docs/method-detail-taxonomy.md for source references and interpretation.
"""
from __future__ import annotations

import re

# Editable vocabulary: stable identifier, displayed label, explicit name/alias.
# Word boundaries keep amphetamine distinct from methamphetamine and MDMA.
DRUGS = [
    ("cocaine", "Cocaine", r"cocaine"),
    ("amphetamine", "Amphetamine", r"(?:d[- ]|l[- ]|dl[- ])?amphetamine"),
    ("methamphetamine", "Methamphetamine", r"methamphetamine|METH"),
    ("mdma", "MDMA", r"MDMA|(?:3,?4[- ])?methylenedioxymethamphetamine"),
    ("mda", "MDA", r"MDA|(?:3,?4[- ])?methylenedioxyamphetamine"),
    ("mdpv", "MDPV", r"MDPV|methylenedioxypyrovalerone"),
    ("mephedrone", "Mephedrone", r"mephedrone|4[- ]MMC"),
    ("alpha_pvp", "Alpha-PVP", r"(?:alpha|α)[- ]?PVP|(?:alpha|α)[- ]pyrrolidinopentiophenone"),
    ("methylphenidate", "Methylphenidate", r"methylphenidate"),
    ("modafinil", "Modafinil", r"modafinil"),
    ("nicotine", "Nicotine", r"nicotine"),
    ("ethanol", "Ethanol", r"ethanol"),
    ("caffeine", "Caffeine", r"caffeine"),
    ("thc", "THC", r"THC|(?:delta[- ]?9[- ]?)?tetrahydrocannabinol"),
    ("cbd", "Cannabidiol", r"cannabidiol|CBD"),
    ("morphine", "Morphine", r"morphine"),
    ("heroin", "Heroin", r"heroin|diacetylmorphine"),
    ("fentanyl", "Fentanyl", r"fentanyl"),
    ("oxycodone", "Oxycodone", r"oxycodone"),
    ("buprenorphine", "Buprenorphine", r"buprenorphine"),
    ("naloxone", "Naloxone", r"naloxone"),
    ("naltrexone", "Naltrexone", r"naltrexone"),
    ("ketamine", "Ketamine", r"ketamine"),
    ("esketamine", "Esketamine", r"esketamine"),
    ("pcp", "Phencyclidine", r"phencyclidine|PCP"),
    ("mk801", "MK-801 (dizocilpine)", r"MK[- ]?801|dizocilpine"),
    ("psilocybin", "Psilocybin", r"psilocybin"),
    ("psilocin", "Psilocin", r"psilocin"),
    ("lsd", "LSD", r"LSD|lysergic acid diethylamide"),
    ("dmt", "DMT", r"DMT|(?:N,N[- ])?dimethyltryptamine"),
    ("doi", "DOI (5-HT2 agonist)", r"(?:R[- ])?DOI|(?:2,?5[- ])?dimethoxy[- ]4[- ]iodoamphetamine"),
    ("fluoxetine", "Fluoxetine", r"fluoxetine"),
    ("sertraline", "Sertraline", r"sertraline"),
    ("citalopram", "Citalopram", r"citalopram"),
    ("escitalopram", "Escitalopram", r"escitalopram"),
    ("desipramine", "Desipramine", r"desipramine"),
    ("bupropion", "Bupropion", r"bupropion"),
    ("haloperidol", "Haloperidol", r"haloperidol"),
    ("clozapine", "Clozapine", r"clozapine(?![- ]N[- ]oxide)"),
    ("diazepam", "Diazepam", r"diazepam"),
    ("midazolam", "Midazolam", r"midazolam"),
    ("lorazepam", "Lorazepam", r"lorazepam"),
    ("muscimol", "Muscimol", r"muscimol"),
    ("bicuculline", "Bicuculline", r"bicuculline"),
    ("gabazine", "Gabazine", r"gabazine|SR[- ]?95531"),
    ("baclofen", "Baclofen", r"baclofen"),
    ("ttx", "Tetrodotoxin", r"tetrodotoxin|TTX"),
    ("cnqx", "CNQX", r"CNQX"),
    ("nbqx", "NBQX", r"NBQX"),
    ("ap5", "AP5 / APV", r"(?:D[- ])?AP[- ]?5|(?:D[- ])?APV"),
    ("sch23390", "SCH-23390", r"SCH[- ]?23390"),
    ("raclopride", "Raclopride", r"raclopride"),
    ("quinpirole", "Quinpirole", r"quinpirole"),
    ("apomorphine", "Apomorphine", r"apomorphine"),
    ("ldopa", "L-DOPA", r"L[- ]DOPA|levodopa"),
    ("propranolol", "Propranolol", r"propranolol"),
    ("clonidine", "Clonidine", r"clonidine"),
    ("scopolamine", "Scopolamine", r"scopolamine"),
    ("atropine", "Atropine", r"atropine"),
    ("cno", "Clozapine-N-oxide (CNO)", r"clozapine[- ]N[- ]oxide|CNO"),
    ("dcz", "Deschloroclozapine (DCZ)", r"deschloroclozapine|DCZ"),
    ("isoflurane", "Isoflurane", r"isoflurane"),
    ("xylazine", "Xylazine", r"xylazine"),
    ("urethane", "Urethane", r"urethane"),
    ("pentobarbital", "Pentobarbital", r"pentobarbital|pentobarbitone"),
    ("propofol", "Propofol", r"propofol"),
]

# Explicit signal terms only. Neither an instrument nor an endogenous chemical
# named in background automatically establishes a measured signal.
SIGNALS = [
    ("spikes", "Action potentials / spikes", r"action potentials?|spike trains?|spiking activity|single[- ]unit activity|multi[- ]unit activity"),
    ("lfp", "Local field potentials (LFP)", r"local field potentials?|LFPs?"),
    ("calcium", "Calcium activity", r"calcium (?:signals?|transients?|activity|dynamics|imaging)|Ca2\+ (?:signals?|transients?|imaging)"),
    ("voltage", "Membrane voltage", r"membrane (?:voltage|potentials?)|voltage (?:signals?|transients?|imaging)|subthreshold (?:voltage|potentials?)"),
    ("synaptic_current", "Synaptic currents (EPSC / IPSC)", r"(?:[ms]?EPSC|[ms]?IPSC)s?|(?:excitatory|inhibitory) postsynaptic currents?"),
    ("synaptic_potential", "Synaptic potentials (EPSP / IPSP)", r"(?:EPSP|IPSP)s?|(?:excitatory|inhibitory) postsynaptic potentials?"),
    ("dopamine", "Dopamine dynamics", r"dopamine (?:signals?|release|transients?|levels?|concentrations?|dynamics)"),
    ("glutamate", "Glutamate dynamics", r"glutamate (?:signals?|release|transients?|levels?|concentrations?|dynamics)"),
    ("gaba", "GABA dynamics", r"GABA (?:signals?|release|transients?|levels?|concentrations?|dynamics)"),
    ("serotonin", "Serotonin dynamics", r"(?:serotonin|5[- ]HT) (?:signals?|release|transients?|levels?|concentrations?|dynamics)"),
    ("acetylcholine", "Acetylcholine dynamics", r"acetylcholine (?:signals?|release|transients?|levels?|concentrations?|dynamics)"),
    ("norepinephrine", "Noradrenaline dynamics", r"(?:noradrenaline|norepinephrine) (?:signals?|release|transients?|levels?|concentrations?|dynamics)"),
    ("eeg", "EEG signals", r"EEG (?:signals?|recordings?|activity|power)|electroencephalogra(?:m|phy|phic)"),
    ("meg", "MEG signals", r"MEG (?:signals?|recordings?|activity)|magnetoencephalogra(?:m|phy|phic)"),
    ("ecog", "Electrocorticography (ECoG)", r"ECoG|electrocorticogra(?:m|phy|phic)"),
]

TOOLS = [
    ("neuropixels", "Neuropixels probes", r"Neuropixels?(?:\s*[12]\.0)?(?: probes?)?"),
    ("tetrodes", "Tetrodes", r"tetrodes?"),
    ("silicon_probe", "Silicon probes", r"silicon (?:probes?|electrodes?)"),
    ("microwires", "Microwire electrodes", r"microwire (?:electrodes?|arrays?)"),
    ("utah_array", "Utah electrode array", r"Utah (?:electrode )?arrays?"),
    ("mea", "Multielectrode arrays", r"multi[- ]?electrode arrays?|MEA (?:recordings?|systems?|plates?)|HD[- ]MEA"),
    ("patch_clamp", "Patch-clamp", r"patch[- ]clamp(?:ed|ing)?"),
    ("whole_cell", "Whole-cell recordings", r"whole[- ]cell (?:recordings?|patch|voltage|current)"),
    ("cell_attached", "Cell-attached recordings", r"cell[- ]attached (?:recordings?|patch)"),
    ("voltage_clamp", "Voltage clamp", r"voltage[- ]clamp(?:ed|ing)?"),
    ("current_clamp", "Current clamp", r"current[- ]clamp(?:ed|ing)?"),
    ("two_photon", "Two-photon microscopy", r"(?:two|2)[- ]photon (?:[\w-]+\s+){0,3}(?:imag(?:ing|ed)|microscop\w*)"),
    ("one_photon", "One-photon microscopy", r"(?:one|single|1)[- ]photon (?:[\w-]+\s+){0,3}(?:imag(?:ing|ed)|microscop\w*)"),
    ("three_photon", "Three-photon microscopy", r"(?:three|3)[- ]photon (?:[\w-]+\s+){0,3}(?:imag(?:ing|ed)|microscop\w*)"),
    ("miniscope", "Miniscope / miniature microscope", r"miniscopes?|miniatur(?:e|ized) (?:fluorescence )?microscop\w*"),
    ("fiber_photometry", "Fiber photometry", r"fib(?:er|re) photometry"),
    ("widefield", "Widefield imaging", r"wide[- ]?field (?:[\w-]+\s+){0,2}imag(?:ing|ed)"),
    ("light_sheet", "Light-sheet microscopy", r"light[- ]sheet (?:microscop\w*|imag(?:ing|ed))"),
    ("grin_lens", "GRIN lens / microendoscope", r"GRIN lens(?:es)?|gradient[- ]index lens(?:es)?|microendoscop\w*"),
    ("fscv", "Fast-scan cyclic voltammetry", r"fast[- ]scan cyclic voltammetry|FSCV"),
    ("microdialysis", "Microdialysis", r"microdialysis"),
    ("amperometry", "Amperometry", r"amperometr(?:y|ic)"),
    ("hplc", "HPLC", r"HPLC|high[- ]performance liquid chromatography"),
    ("lcms", "LC-MS", r"LC[- /]MS(?:/MS)?|liquid chromatography[-– ]mass spectrometry"),
    ("gcms", "GC-MS", r"GC[-– /]MS(?:/MS)?|gas chromatography[-– ]mass spectrometry"),
    ("mass_spec", "Mass spectrometry", r"mass spectrometry"),
    ("gcamp", "GCaMP indicator", r"(?:j)?GCaMP\d*[a-z]*"),
    ("rcamp", "RCaMP / R-GECO indicator", r"(?:j)?RCaMP\d*[a-z]*|(?:j)?R[- ]GECO\d*(?:\.\d+)?[a-z]*"),
    ("dlight", "dLight dopamine sensor", r"dLight\d*(?:\.\d+)?[a-z]*"),
    ("grab_da", "GRAB dopamine sensor", r"GRAB[- _]?(?:DA|dopamine)\d*[a-z]*|GRABDA\d*[a-z]*"),
    ("iglusnfr", "iGluSnFR glutamate sensor", r"iGluSnFR\d*[a-z]*"),
    ("gabasic", "GABA sensor", r"iGABASnFR\d*[a-z]*|GRAB[- _]?GABA\d*[a-z]*"),
    ("voltage_indicator", "Genetically encoded voltage indicator", r"genetically encoded voltage indicators?|GEVIs?|ASAP[1-9][a-z0-9.]*|Voltron[1-9]?|QuasAr[1-9]?|Archon[1-9]?"),
    ("voltage_dye", "Voltage-sensitive dye", r"voltage[- ]sensitive dyes?|voltage[- ]sensitive dye imaging"),
]

_PAST_STUDY = re.compile(r"\b(?:previous(?:ly)?|prior|earlier|published|other) (?:\w+\s+){0,3}(?:studies|study|reports?|work|research)|\b(?:et al|has been shown|have been shown|literature|reviewed|cited|according to)\b", re.I)
_FUTURE = re.compile(r"\b(?:future|could|would|might|propos(?:e|ed)|plan(?:ned)? to)\b", re.I)
_ACTION = r"(?:administer(?:ed|ing|ation)?|inject(?:ed|ing|ion)?|infus(?:ed|ing|ion)|receiv(?:ed|ing)|treat(?:ed|ment)|expos(?:ed|ure)|self[- ]administ(?:ered|ration)|bath[- ]appl(?:ied|ication)|perfus(?:ed|ion)|ana?estheti[sz]ed)"
_DRUG_BEFORE = re.compile(r"\b" + _ACTION + r"\b[^.;!?]{0,95}$", re.I)
_DRUG_AFTER = re.compile(r"^\s*(?:hydrochloride|sulfate|tartrate)?\s*(?:\([^)]{0,80}\)\s*)?"
                         r"(?:(?:was|were)\s+(?:" + _ACTION + r"|dissolved)|self[- ]administration|administration|pretreatment|infusions?)\b", re.I)
_MEASUREMENT = re.compile(r"\b(?:record(?:ed|ing|ings)?|measur(?:ed|ing|ement)|monitor(?:ed|ing)|imag(?:ed|ing)|detect(?:ed|ion)|acqui(?:red|sition)|quantif(?:ied|ication)|sampled|estimated|extracted)\b", re.I)


def detail_has_context(sentence: str, matched: str, category: str, confidence: str) -> bool:
    """Require local procedure evidence and exclude obvious citations/planned work.

    Drugs need a treatment action in the same clause. Signals need a measurement
    action, while instruments in an identified Methods section can be named as
    equipment without an additional verb. The function never links a drug to a
    specific recorded signal and never infers a signal from its sensor/tool.
    """
    if _PAST_STUDY.search(sentence) or _FUTURE.search(sentence):
        return False
    start = sentence.casefold().find(matched.casefold())
    if start < 0:
        return False
    before, after = sentence[:start], sentence[start + len(matched):]
    if category == "drugs":
        # A DOI identifier in prose is not the serotonergic research compound.
        if matched.upper() == "DOI" and re.match(r"\s*:?\s*10\.", after):
            return False
        procedure = _DRUG_BEFORE.search(before)
        # Stop an earlier treatment verb from claiming an analyte mentioned in
        # a later measurement clause ('received saline and measured MDA').
        if procedure and (_MEASUREMENT.search(procedure.group(0)) or re.search(r"\bfollowed by\b", procedure.group(0), re.I)):
            procedure = None
        return bool(procedure or _DRUG_AFTER.search(after))
    if category == "signals":
        return bool(_MEASUREMENT.search(sentence))
    return confidence == "high" or bool(_MEASUREMENT.search(sentence) or re.search(r"\b(?:used|implanted|inserted|expressed)\b", sentence, re.I))


def detail_methods() -> list[dict]:
    """Compile the small declarative tables into the shared taxonomy contract."""
    out = []
    for prefix, stage, category, rows in (("drug", "manipulation", "drugs", DRUGS),
                                          ("signal", "measurement", "signals", SIGNALS),
                                          ("tool", "measurement", "techniques_tools", TOOLS)):
        for ident, label, pattern in rows:
            out.append({"id": f"{prefix}_{ident}", "label": label, "stage": stage,
                        "category": category, "context_required": True,
                        "regex": re.compile(r"\b(?:" + pattern + r")\b", re.I)})
    return out
