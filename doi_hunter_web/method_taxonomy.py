"""Methodology taxonomy used by the Methodology map (insight.py).

Each entry is one recognisable method, with the stage of a study it belongs to and the regular
expressions that detect it in a paper's text. Detection is evidence-based: the sentence that matched
is returned with every hit, so the researcher can verify it. Patterns are word-bounded, case-insensitive
and deliberately conservative (a false "optogenetics" hurts more than a missed one).

Stages (canonical order of a study pipeline):
    subjects      who / what was studied (species, cells, humans, models)
    design        how the study is organised (randomised, longitudinal, dose-response, review...)
    manipulation  what was done to the subjects (drugs, lesions, stimulation, environment, training)
    measurement   what was measured (behaviour, physiology, recordings, molecular assays, imaging)
    analysis      how the data were analysed (statistics, modelling, machine learning)

Reporting checks (ARRIVE / CONSORT style) flag what a paper states about sex, sample size,
randomisation, blinding, ethics, statistics and data availability.
"""
from __future__ import annotations

import re

STAGES: list[tuple[str, str]] = [
    ("subjects", "Subjects & models"),
    ("design", "Study design"),
    ("manipulation", "Manipulations"),
    ("measurement", "Measurements"),
    ("analysis", "Analyses"),
]

# (id, label, stage, [patterns])
_RAW: list[tuple[str, str, str, list[str]]] = [
    # ---------------- subjects & models
    ("human", "Human participants", "subjects", [r"\bparticipants\b", r"\bhealthy (?:volunteers|adults|subjects)\b", r"\bpatients\b", r"\bhuman (?:subjects|volunteers|participants)\b"]),
    ("nhp", "Non-human primates", "subjects", [r"\b(?:rhesus|macaques?|marmosets?|non-?human primates?)\b"]),
    ("rat", "Rats", "subjects", [r"\brats?\b", r"\b(?:sprague[- ]dawley|wistar|long[- ]evans|lister hooded)\b"]),
    ("mouse", "Mice", "subjects", [r"\bmice\b", r"\bmouse\b", r"\bc57bl/?6\b"]),
    ("zebrafish", "Zebrafish", "subjects", [r"\bzebrafish\b", r"\bdanio rerio\b"]),
    ("drosophila", "Drosophila", "subjects", [r"\bdrosophila\b", r"\bfruit fl(?:y|ies)\b"]),
    ("celegans", "C. elegans", "subjects", [r"\bc\.? ?elegans\b", r"\bcaenorhabditis\b"]),
    ("cells", "Cell culture / slices", "subjects", [r"\bcell (?:culture|lines?)\b", r"\bcultured (?:cells|neurons)\b", r"\b(?:hek|hela|sh-sy5y|pc12)\b", r"\bbrain slices?\b", r"\borganoids?\b"]),
    ("insilico", "Computational model / simulation", "subjects", [r"\bin silico\b", r"\bcomputational model(?:s|ling)?\b", r"\bsimulations?\b", r"\bneural network models?\b"]),
    ("transgenic", "Transgenic / knockout animals", "subjects", [r"\bknock-?outs?\b", r"\bknock-?ins?\b", r"\btransgenic\b", r"\bcre[- ]?(?:driver|line|recombinase)\b", r"\b[a-z0-9]+-/-\b"]),
    # ---------------- design
    ("rct", "Randomised controlled trial", "design", [r"\brandomi[sz]ed controlled\b", r"\brandomi[sz]ed,? (?:double|single)-blind\b"]),
    ("crossover", "Cross-over design", "design", [r"\bcross-?over (?:design|study|trial)\b"]),
    ("longitudinal", "Longitudinal / repeated sessions", "design", [r"\blongitudinal\b", r"\brepeated[- ]measures\b", r"\bdaily sessions?\b", r"\bfollow-?up\b"]),
    ("crosssectional", "Cross-sectional", "design", [r"\bcross-?sectional\b"]),
    ("casecontrol", "Case-control / cohort", "design", [r"\bcase-?control\b", r"\bcohort (?:study|design)\b", r"\bprospective\b", r"\bretrospective\b"]),
    ("withinsubject", "Within-subject design", "design", [r"\bwithin-?subjects?\b", r"\bcounterbalanced\b", r"\bin a counter-?balanced order\b"]),
    ("betweensubject", "Between-groups design", "design", [r"\bbetween-?(?:subjects?|groups?)\b", r"\bassigned to (?:one of )?(?:two|three|four|\d+) groups\b", r"\bseparate groups of\b"]),
    ("doseresponse", "Dose-response", "design", [r"\bdose[- ]?(?:response|effect)\b", r"\b(?:graded|increasing|ascending) doses\b"]),
    ("review", "Systematic review / meta-analysis", "design", [r"\bsystematic review\b", r"\bmeta-?analys[ie]s\b", r"\bprisma\b"]),
    ("survey", "Survey / questionnaire", "design", [r"\bquestionnaires?\b", r"\bsurvey(?:s|ed)?\b", r"\bself-?report\b"]),
    ("timeseries", "Time course", "design", [r"\btime[- ]course\b", r"\bevery \d+ ?(?:min|minutes|h|hours|days)\b", r"\bat \d+,? \d+,? (?:and )?\d+ ?(?:min|h|days)\b"]),
    # ---------------- manipulations
    ("drug_systemic", "Systemic drug administration", "manipulation", [r"\bintraperitoneal(?:ly)?\b", r"\bi\.p\.(?!\w)", r"\bsubcutaneous(?:ly)?\b", r"\bs\.c\.(?!\w)", r"\boral(?:ly)? (?:administered|administration|gavage)\b", r"\bp\.o\.(?!\w)", r"\binjections? of\b"]),
    ("drug_iv", "Intravenous drug delivery", "manipulation", [r"\bintravenous(?:ly)?\b", r"\bi\.v\.\b", r"\bjugular (?:vein|catheter)\b"]),
    ("selfadmin", "Drug self-administration", "manipulation", [r"\bself-?administ(?:ration|ered|er)\b", r"\bfixed[- ]ratio\b", r"\bprogressive[- ]ratio\b", r"\bFR ?\d\b", r"\bbreak-?points?\b"]),
    ("intracranial", "Intracranial / local infusion", "manipulation", [r"\bintracranial\b", r"\bmicroinjection\b", r"\bintracerebroventricular\b", r"\bi\.c\.v\.\b", r"\bcannula(?:e|s)?\b"]),
    ("lesion", "Lesion", "manipulation", [r"\blesion(?:s|ed)?\b", r"\bablation\b", r"\b6-?ohda\b"]),
    ("optogenetics", "Optogenetics", "manipulation", [r"\boptogenetic(?:s|ally)?\b", r"\bchannelrhodopsin\b", r"\bchr2\b", r"\bhalorhodopsin\b", r"\barchaerhodopsin\b"]),
    ("chemogenetics", "Chemogenetics (DREADDs)", "manipulation", [r"\bchemogenetic(?:s|ally)?\b", r"\bdreadds?\b", r"\bclozapine[- ]n[- ]oxide\b", r"\bhM[34]D[qi]\b"]),
    ("stimulation", "Brain stimulation", "manipulation", [r"\bdeep brain stimulation\b", r"\btranscranial (?:magnetic|direct current)\b", r"\btms\b", r"\btdcs\b", r"\belectrical stimulation\b"]),
    ("genetic_manip", "Genetic manipulation (viral, CRISPR, siRNA)", "manipulation", [r"\bcrispr\b", r"\bsi ?rna\b", r"\bsh ?rna\b", r"\baav\d?\b", r"\bviral vectors?\b", r"\blentivir(?:us|al)\b"]),
    ("environment", "Environmental manipulation", "manipulation", [r"\bambient temperatures?\b", r"\benvironmental enrichment\b", r"\bsocial isolation\b", r"\bhousing conditions?\b", r"\blight[- ]dark cycle\b", r"\bfood (?:restriction|deprivation|restricted)\b"]),
    ("stress", "Stress paradigm", "manipulation", [r"\brestraint stress\b", r"\bchronic (?:mild|unpredictable) stress\b", r"\bsocial defeat\b", r"\bfoot-?shock\b", r"\bforced swim\b"]),
    ("training", "Behavioural training / conditioning", "manipulation", [r"\btrained to\b", r"\bconditioning\b", r"\bacquisition (?:phase|sessions?)\b", r"\bextinction\b", r"\breinstatement\b", r"\boperant\b"]),
    ("task", "Cognitive / behavioural task", "manipulation", [r"\bstroop\b", r"\bn-?back\b", r"\bgo/no-?go\b", r"\bdecision-?making task\b", r"\bworking memory task\b", r"\battention task\b"]),
    ("diet", "Diet / nutrition", "manipulation", [r"\bhigh-?fat diet\b", r"\bdietary\b", r"\bsucrose (?:solution|pellets?)\b", r"\bad libitum\b"]),
    # ---------------- measurements
    ("locomotor", "Locomotor activity", "measurement", [r"\blocomotor (?:activity|response)\b", r"\bopen[- ]field\b", r"\bambulat(?:ion|ory)\b", r"\bbeam breaks?\b", r"\bphotobeams?\b"]),
    ("temperature", "Body temperature / thermoregulation", "measurement", [r"\bbody temperatures?\b", r"\bcore temperatures?\b", r"\bhyperthermi[ac]\b", r"\bhypothermi[ac]\b", r"\brectal temperatures?\b", r"\btelemetr(?:y|ic)\b"]),
    ("cpp", "Conditioned place preference", "measurement", [r"\bconditioned place (?:preference|aversion)\b", r"\bcpp\b"]),
    ("anxiety_tests", "Anxiety / mood tests", "measurement", [r"\belevated plus maze\b", r"\blight[- ]dark box\b", r"\bsucrose preference\b", r"\btail suspension\b", r"\bmarble burying\b"]),
    ("memory_tests", "Learning & memory tests", "measurement", [r"\bmorris water maze\b", r"\bradial arm maze\b", r"\bnovel object recognition\b", r"\bfear conditioning\b", r"\by-?maze\b", r"\bt-?maze\b"]),
    ("intake", "Drug intake / infusions", "measurement", [r"\bnumber of infusions\b", r"\bdrug intake\b", r"\bresponses? per session\b", r"\blever press(?:es|ing)?\b", r"\bnose[- ]pokes?\b"]),
    ("pk", "Pharmacokinetics / blood levels", "measurement", [r"\bplasma (?:levels?|concentrations?)\b", r"\bblood (?:levels?|concentrations?|samples?)\b", r"\bpharmacokinetic(?:s)?\b", r"\bserum\b", r"\bhplc\b", r"\bmass spectrometry\b", r"\blc-?ms\b"]),
    ("ephys", "Electrophysiology", "measurement", [r"\belectrophysiolog(?:y|ical)\b", r"\bpatch[- ]clamp\b", r"\bwhole-?cell\b", r"\bsingle[- ]unit\b", r"\bmulti-?unit\b", r"\bfield potentials?\b", r"\blfp\b", r"\bspike (?:sorting|trains?)\b", r"\btetrodes?\b", r"\bsilicon probes?\b"]),
    ("eeg", "EEG / MEG", "measurement", [r"\beeg\b", r"\bmeg\b", r"\bevent-?related potentials?\b", r"\berps?\b"]),
    ("fmri", "fMRI / PET / structural MRI", "measurement", [r"\bfmri\b", r"\bfunctional magnetic resonance\b", r"\bbold\b", r"\bpositron emission\b", r"\bpet\b", r"\bdiffusion tensor\b", r"\bvoxel-?based\b"]),
    ("calcium", "Calcium imaging / photometry", "measurement", [r"\bcalcium imaging\b", r"\bgcamp\d*\b", r"\bfiber photometry\b", r"\btwo-?photon\b", r"\bminiscope\b"]),
    ("microdialysis", "Microdialysis / voltammetry", "measurement", [r"\bmicrodialysis\b", r"\bvoltammetry\b", r"\bfscv\b"]),
    ("histology", "Histology / immunohistochemistry", "measurement", [r"\bimmunohistochem(?:istry|ical)\b", r"\bimmunofluorescen(?:ce|t)\b", r"\bhistolog(?:y|ical)\b", r"\bc-?fos\b", r"\bnissl\b", r"\bcresyl violet\b"]),
    ("molecular", "Molecular assays (PCR, western blot, ELISA)", "measurement", [r"\bwestern blot(?:ting)?\b", r"\b(?:q|rt-)?pcr\b", r"\belisa\b", r"\bimmunoblot\b", r"\bin situ hybridi[sz]ation\b"]),
    ("omics", "Omics (RNA-seq, proteomics)", "measurement", [r"\brna-?seq\b", r"\bsingle-?cell\b", r"\btranscriptom(?:e|ic|ics)\b", r"\bproteom(?:e|ic|ics)\b", r"\bmetabolom(?:e|ic|ics)\b", r"\bgenome-?wide\b"]),
    ("hormones", "Hormonal / physiological markers", "measurement", [r"\bcorticosterone\b", r"\bcortisol\b", r"\bheart rate\b", r"\bblood pressure\b", r"\bglucose\b"]),
    ("eyetracking", "Eye tracking / pupillometry", "measurement", [r"\beye[- ]tracking\b", r"\bpupil(?:lometry| diameter)\b", r"\bsaccades?\b"]),
    ("clinical_scales", "Clinical scales / ratings", "measurement", [r"\bhamilton\b", r"\bbeck depression\b", r"\bvisual analogue scale\b", r"\bclinical global impression\b", r"\bdsm-?[45iv]+\b"]),
    ("video", "Video tracking / observation", "measurement", [r"\bvideo[- ]?(?:track(?:ed|ing)|record(?:ed|ing))\b", r"\bethovision\b", r"\banymaze\b", r"\bdeeplabcut\b"]),
    # ---------------- analyses
    ("anova", "ANOVA", "analysis", [r"\banovas?\b", r"\banalys[ie]s of variance\b", r"\bpost[- ]hoc\b", r"\btukey\b", r"\bbonferroni\b", r"\bnewman-?keuls\b", r"\bdunnett\b"]),
    ("ttest", "t-test / non-parametric tests", "analysis", [r"\bt-?tests?\b", r"\bmann-?whitney\b", r"\bwilcoxon\b", r"\bkruskal-?wallis\b", r"\bchi-?square\b"]),
    ("regression", "Regression / correlation", "analysis", [r"\bregression\b", r"\bcorrelation(?:s|al)?\b", r"\bpearson\b", r"\bspearman\b"]),
    ("mixed", "Mixed / hierarchical models", "analysis", [r"\bmixed[- ](?:effects? )?models?\b", r"\bhierarchical (?:linear )?models?\b", r"\bmultilevel\b", r"\bgeneral(?:i[sz]ed)? linear (?:mixed )?models?\b", r"\bglmm?\b"]),
    ("bayes", "Bayesian analysis", "analysis", [r"\bbayesian\b", r"\bbayes factors?\b", r"\bposterior (?:distribution|density|probabilit(?:y|ies)|predictive)\b", r"\bmcmc\b"]),
    ("survival", "Survival / time-to-event", "analysis", [r"\bkaplan-?meier\b", r"\bcox (?:proportional|regression)\b", r"\bsurvival analysis\b", r"\bhazard ratios?\b"]),
    ("ml", "Machine learning / classification", "analysis", [r"\bmachine learning\b", r"\bdeep learning\b", r"\bsupport vector\b", r"\brandom forests?\b", r"\bclassif(?:ier|ication)\b", r"\bcross-?validation\b", r"\bneural networks?\b"]),
    ("dimred", "Dimensionality reduction / clustering", "analysis", [r"\bprincipal components?\b", r"\bpca\b", r"\bt-?sne\b", r"\bumap\b", r"\bfactor analysis\b", r"\bclustering\b"]),
    ("signal", "Spectral / time-frequency analysis", "analysis", [r"\bspectral (?:analysis|power)\b", r"\bfourier\b", r"\bwavelet\b", r"\bcoherence\b", r"\bpower spectr(?:um|a|al)\b"]),
    ("decoding", "Decoding / encoding models", "analysis", [r"\bdecod(?:ing|er|ed)\b", r"\bencoding models?\b", r"\breceptive fields?\b", r"\btuning curves?\b"]),
    ("effectsize", "Effect sizes / confidence intervals", "analysis", [r"\beffect sizes?\b", r"\bcohen'?s d\b", r"\bconfidence intervals?\b", r"\b95% ci\b", r"\bpartial eta\b"]),
    ("modelfit", "Computational / mathematical modelling", "analysis", [r"\bmodel fitting\b", r"\bparameter estimation\b", r"\breinforcement learning models?\b", r"\bdrift[- ]diffusion\b", r"\bmaximum likelihood\b"]),
]

# Specific assay names avoid attributing a broad technique from an ambiguous acronym.
_RAW.extend([
    ("guinea_pig", "Guinea pigs", "subjects", [r"\bguinea pigs?\b"]),
    ("rabbit", "Rabbits", "subjects", [r"\brabbits?\b"]),
    ("ipsc", "Induced pluripotent stem cells", "subjects", [r"\binduced pluripotent stem cells?\b", r"\biPSCs?\b"]),
    ("patient_derived", "Patient-derived models", "subjects", [r"\bpatient-derived (?:cells?|organoids?|xenografts?)\b"]),
    ("factorial", "Factorial design", "design", [r"\bfactorial (?:design|experiment)\b"]),
    ("matched", "Matched groups", "design", [r"\b(?:age|sex|propensity)-matched\b", r"\bmatched (?:pairs?|controls?)\b"]),
    ("pilot", "Pilot / feasibility study", "design", [r"\b(?:pilot|feasibility) (?:study|trial)\b"]),
    ("yoked", "Yoked control", "design", [r"\byoked (?:controls?|group|animals?)\b"]),
    ("sham", "Sham control", "design", [r"\bsham (?:surgery|operated|stimulation|controls?)\b"]),
    ("washout", "Washout period", "design", [r"\bwash[- ]?out (?:period|interval)\b"]),
    ("sleep_deprivation", "Sleep deprivation", "manipulation", [r"\bsleep deprivation\b", r"\bsleep-deprived\b"]),
    ("exercise", "Exercise intervention", "manipulation", [r"\btreadmill (?:training|exercise)\b", r"\bvoluntary wheel running\b"]),
    ("antagonist", "Receptor antagonist intervention", "manipulation", [r"\b(?:administered|injected|treated with) [^.;]{0,50}\bantagonist\b", r"\bantagonist pretreatment\b"]),
    ("microbiome", "Microbiota manipulation", "manipulation", [r"\b(?:faecal|fecal) microbiota transplan\w*\b", r"\bantibiotic depletion\b"]),
    ("anesthesia", "Anaesthesia", "manipulation", [r"\bana?estheti[sz]ed\b", r"\bisoflurane ana?esthesia\b"]),
    ("gonadectomy", "Gonadectomy / hormone replacement", "manipulation", [r"\bovariectom\w*\b", r"\bgonadectom\w*\b", r"\bhormone replacement\b"]),
    ("social_behavior", "Social interaction tests", "measurement", [r"\bsocial interaction test\b", r"\bthree-chamber (?:test|assay)\b"]),
    ("rotarod", "Rotarod motor coordination", "measurement", [r"\brotarod\b"]),
    ("pain", "Nociception assays", "measurement", [r"\b(?:hot-plate|tail-flick|von Frey) (?:test|assay|filaments?)\b"]),
    ("startle", "Startle / prepulse inhibition", "measurement", [r"\bprepulse inhibition\b", r"\bacoustic startle\b"]),
    ("discounting", "Delay discounting", "measurement", [r"\bdelay[- ]discounting (?:task|test|procedure)\b"]),
    ("attention5", "Five-choice serial reaction time", "measurement", [r"\bfive-choice serial reaction\b", r"\b5-CSRTT\b"]),
    ("binding", "Receptor binding assay", "measurement", [r"\bradioligand binding\b", r"\breceptor binding assay\b"]),
    ("flow", "Flow cytometry", "measurement", [r"\bflow cytometr\w*\b", r"\bfluorescence-activated cell sorting\b"]),
    ("electron", "Electron microscopy", "measurement", [r"\belectron microscop\w*\b"]),
    ("confocal", "Confocal microscopy", "measurement", [r"\bconfocal microscop\w*\b"]),
    ("respirometry", "Respirometry / calorimetry", "measurement", [r"\brespirometr\w*\b", r"\bindirect calorimetry\b"]),
    ("permutation", "Permutation / randomisation tests", "analysis", [r"\bpermutation tests?\b", r"\brandomi[sz]ation tests?\b"]),
    ("bootstrap", "Bootstrap resampling", "analysis", [r"\bbootstrap(?:ping|ped)?\b"]),
    ("fdr", "False discovery rate correction", "analysis", [r"\bfalse discovery rate\b", r"\bbenjamini[- ]hochberg\b"]),
    ("mediation", "Mediation analysis", "analysis", [r"\bmediation analys[ie]s\b"]),
    ("network", "Network / graph analysis", "analysis", [r"\bgraph theor(?:y|etical)\b", r"\bnetwork centrality\b"]),
    ("changepoint", "Change-point analysis", "analysis", [r"\bchange[- ]point (?:analysis|detection)\b"]),
    ("equivalence", "Equivalence testing", "analysis", [r"\bequivalence tests?\b", r"\btwo one-sided tests?\b"]),
])

# Tighten inherited broad cues that otherwise confuse routine housing or equipment with experiments.
for _index, (_mid, _label, _stage, _patterns) in enumerate(_RAW):
    if _mid == "pk":
        _patterns = [p for p in _patterns if p != r"\bserum\b"]
    elif _mid == "intracranial":
        _patterns = [r"\bintracranial (?:infusion|injection|administration)\b", r"\bmicroinjection\b",
                     r"\bintracerebroventricular\b"]
    elif _mid == "diet":
        _patterns = [r"\bhigh[- ]fat diet\b", r"\bdietary (?:intervention|supplementation|restriction)\b"]
    elif _mid == "stimulation":
        _patterns = [*_patterns, r"\bintracranial self[- ]stimulation\b", r"\bICSS\b"]
    elif _mid == "nhp":
        _patterns = [*_patterns, r"\bmonkeys?\b"]
    _RAW[_index] = (_mid, _label, _stage, _patterns)

METHODS: list[dict] = [
    {"id": mid, "label": label, "stage": stage, "regex": re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)}
    for mid, label, stage, patterns in _RAW
]
METHOD_BY_ID: dict[str, dict] = {m["id"]: m for m in METHODS}
STAGE_ORDER: dict[str, int] = {sid: i for i, (sid, _label) in enumerate(STAGES)}

# Reporting checks: (id, label, regex, what a hit means)
REPORTING_CHECKS: list[dict] = [
    {"id": "sex", "label": "Sex of subjects stated", "regex": re.compile(r"\b(?:male|female|both sexes|women|men)\b", re.I)},
    {"id": "female", "label": "Female subjects included", "regex": re.compile(r"\b(?:females?|women)\b", re.I)},
    {"id": "n", "label": "Sample size stated", "regex": re.compile(r"\b[nN]\s*=\s*\d+\b|\b\d+\s+(?:rats|mice|animals|participants|subjects|patients|volunteers|monkeys)\b", re.I)},
    {"id": "random", "label": "Randomisation mentioned", "regex": re.compile(r"\brandom(?:ly|i[sz]ed|i[sz]ation|ised|ized)\b", re.I)},
    {"id": "blind", "label": "Blinding mentioned", "regex": re.compile(r"\bblind(?:ed|ing)?\b", re.I)},
    {"id": "ethics", "label": "Ethics review / approval mentioned", "regex": re.compile(r"\b(?:ethics|ethical) (?:committee|approval|review)\b|\binstitutional (?:animal care|review board)\b|\bIACUC\b|\b(?:animal care and use|research ethics) committee\b", re.I)},
    {"id": "stats", "label": "Statistical test named", "regex": re.compile(r"\banovas?\b|\banalys[ie]s of variance\b|\bt-?tests?\b|\bregression\b|\bmixed[- ](?:effects? )?models?\b|\bchi-?square\b|\bmann-?whitney\b|\bwilcoxon\b|\bkruskal-?wallis\b|\bfisher'?s exact\b|\bpermutation tests?\b", re.I)},
    {"id": "power", "label": "Power analysis / sample size justification", "regex": re.compile(r"\bpower (?:analysis|calculation)\b|\bsample size (?:was )?(?:calculated|estimated|determined|justified)\b|\bg\*power\b", re.I)},
    {"id": "prereg", "label": "Pre-registration", "regex": re.compile(r"\bpre-?registered\b|\bpre-?registration\b|\bosf\.io\b|\bclinicaltrials\.gov\b|\bnct\d{8}\b", re.I)},
    {"id": "data", "label": "Data or code availability", "regex": re.compile(r"\bdata (?:are |is )?available\b|\bcode (?:is |are )?available\b|\bgithub\.com\b|\bzenodo\b|\bfigshare\b|\bdryad\b|\bupon (?:reasonable )?request\b", re.I)},
    {"id": "dose", "label": "Doses / concentrations stated", "regex": re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg/kg|µg|ug|mg|µM|uM|nM|mM)\b", re.I)},
]
