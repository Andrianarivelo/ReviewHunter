# Method details: compounds, recorded signals and instruments

The methodology map retains five canonical stages. Specific chips add a second,
independent grouping: `drugs` and `interventions` within Manipulations, plus
`signals` and `techniques_tools` within Measurements. Other entries retain their
stage as the category. Categories are present on detected entries and matrix
columns. Every chip retains the complete matching source sentence, match text,
section and confidence label. A chip is a rule-based candidate for inspection,
not a verified assertion about the study.

`doi_hunter_web/method_details.py` contains the editable drug, signal and tool
tables. Entries use explicit names, bounded aliases and stable identifiers.
New aliases should have positive and adversarial examples in
`tests/test_method_details.py` before being added.

## Interpretation and guardrails

- Drug names require a treatment, preparation or self-administration cue in the
  same clause. A title, background mention, cited procedure, planned experiment,
  explicit exclusion, or measured drug concentration alone does not qualify.
  These chips identify compounds mentioned in procedures; they do not establish
  dose, route, receptor mechanism, or the compound responsible for each signal.
- Methamphetamine, amphetamine, MDA and MDMA are distinct identities. Acronyms
  receive the same context requirement; a bibliographic DOI does not qualify as
  the compound DOI. No chemical or pharmacological similarity merging occurs.
- A signal requires explicit measurement language. A method or instrument alone
  does not establish its measured signal. Signal, sensor and instrument chips
  can coexist when the source explicitly supports each of them.
- A fixed-ratio or progressive-ratio schedule does not establish drug
  self-administration. The Self-administration chip requires explicit wording;
  any named compound remains a separate chip.
- Photometry, miniscopes and two-photon microscopy do not imply calcium imaging.
  Neuropixels does not, by itself, establish that both spikes and LFP were used.
  HPLC and mass spectrometry do not, by themselves, establish pharmacokinetics.
- Existing section, negation and repeated-body-mention checks remain active.
  Heuristics can miss paraphrases, split PDF lines, implicit procedures and
  uncommon aliases. They cannot reliably resolve every attribution or determine
  experimental chronology. Missing chips are not evidence of missing methods.

## Primary sources informing these distinctions

| Source | Relevant distinction |
| --- | --- |
| [Steinmetz et al., Neuropixels 2.0 (2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8244810/) | A probe is recording hardware; recorded signal use must be established from each paper. |
| [Patriarchi et al., dLight dopamine indicators (2018)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6287765/) | Optical indicators can report dopamine rather than calcium. |
| [Kim et al., multisite fiber photometry (2016)](https://www.nature.com/articles/nmeth.3770) | Fiber photometry is an optical recording technique; the indicator defines what is reported. |
| [Chen et al., GCaMP6 (2013)](https://www.nature.com/articles/nature12354) | GCaMP is a calcium indicator, with signal transduction distinct from direct voltage recording. |
| [St-Pierre et al., ASAP1 (2014)](https://www.nature.com/articles/nn.3709) | Genetically encoded voltage indicators report membrane potential optically. |
| [Lee et al., CaMKII and structural spine plasticity (2009)](https://www.nature.com/articles/nature07842) | Two-photon microscopy can support molecular and structural observations. |
| [Dalley et al., comparative self-administration (2007)](https://www.nature.com/articles/1301220) | Amphetamine, methamphetamine and MDMA are separate interventions within a study. |
| [Aarde et al., MDMA self-administration (2017)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5688002/) | Drug identity in the experimental procedure must be separated from compounds discussed in background/comparisons. |

The taxonomy is a curated discovery vocabulary, not a comprehensive ontology or
a pharmacological advice system. Its neuroscience coverage includes behavioral
drug studies, optical sensors, electrical recordings and chemical measurements.
The frozen five-paper benchmark scores its original label universe; newly added
detail labels are exported separately and are not counted as validated recall.
