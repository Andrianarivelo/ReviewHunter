"""Conservative directional findings and contextual comparisons of source text.

This module describes reported observations. It does not estimate effect sizes,
test significance, pool outcomes or identify statistical outliers. Comparison
keys deliberately retain drug, species, anatomical and experimental qualifiers.
"""
from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from collections.abc import Callable

MAX_FINDINGS = 8
MAX_RESULTS_CHARACTERS = 80_000
DIRECTIONS = ("increase", "decrease", "no_detected_change", "mixed", "unspecified")
_OUTCOMES = (
    ("core temperature", r"(?:core body|core) temperature"),
    ("rectal temperature", r"rectal temperature"),
    ("colonic temperature", r"colonic temperature"),
    ("brain temperature", r"brain temperature"),
    ("body temperature", r"body temperature"),
    ("self-administration", r"(?:self[- ]administration|IVSA|drug intake|MDMA intake)"),
    ("locomotor activity", r"locomotor activity"),
    ("motor activity", r"motor activity"),
    ("dopamine release", r"(?:extracellular )?dopamine(?: (?:release|efflux|levels?|concentrations?))?"),
    ("serotonin release", r"(?:serotonin|5[- ]HT) (?:release|efflux|increases?|levels?|concentrations?)"),
    ("neurotoxicity", r"neurotoxicity|neurotoxic effects?"),
    ("sleep duration", r"sleep[- ]times?"),
    ("brain ethanol concentration", r"brain ethanol concentrations?"),
    ("metabolic rate", r"metabolic rate|MR"),
    ("evaporative water loss", r"evaporative water loss|EWL"),
    ("ICSS reward threshold", r"(?:intracranial self[- ]stimulation|ICSS|reward) thresholds?"),
)
_OUTCOME_PATTERNS = [(label, re.compile(r"\b(?:" + pattern + r")\b", re.I)) for label, pattern in _OUTCOMES]
_DIRECTION = re.compile(r"\b(?P<up>increas(?:e[ds]?|ing)|higher|elevat(?:e[ds]?|ion)|enhanc(?:e[ds]?|ement)|rais(?:e[ds]?|ing)|rise|rose)\b|"
                        r"\b(?P<down>decreas(?:e[ds]?|ing)|lower(?:ed)?|reduc(?:e[ds]?|tion)|diminish(?:ed)?|fall|fell|drop|depression)\b", re.I)
_AIM = re.compile(r"\b(?:aim(?:ed|s)?|hypothes\w*|objective|purpose|whether|we (?:examined|investigated|tested|sought)|to determine|designed to)\b|^\s*To (?:assess|evaluate|measure|test|examine|investigate|compare)\b", re.I)
_BACKGROUND = re.compile(r"\b(?:previous|prior|earlier) (?:stud(?:y|ies)|work|research|reports?|evidence|findings)\b|\b(?:has|have) been shown|\bet al\b|\b(?:may|might|could|would|should|suggest\w*|appears?)\b", re.I)
_ASSOCIATION = re.compile(r"\b(?:regression|correlat\w*|associated with|predictors?)\b", re.I)
_RESULT_CUE = re.compile(r"\b(?:increased|decreased|reduced|enhanced|elevated|lowered|higher|lower|diminished|caused|produced|elicited|induced|showed|observed|found|led to|did not|does not|unchanged|unaffected|failed to)\b", re.I)
_SPECIES = (("rats", r"\brats?\b"), ("mice", r"\b(?:mice|mouse)\b"), ("humans", r"\bhuman(?:s| participants| subjects)?\b"), ("monkeys", r"\b(?:monkeys?|macaques?)\b"))
_REGIONS = (("nucleus accumbens", r"\b(?:nucleus accumbens|NAc)\b"), ("striatum", r"\bstriat(?:um|al)\b"),
            ("caudate-putamen", r"\bcaudate[- ]putamen\b"), ("hippocampus", r"\bhippocamp\w*\b"), ("cortex", r"\b(?:cortex|cortical)\b"))
_TEMPERATURE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:[–-]\s*\d+(?:\.\d+)?\s*)?(?:°\s*C|/C14\s*C)\b", re.I)
_DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:[–-]\s*\d+(?:\.\d+)?\s*)?(?:mg/kg|g/kg|µg/kg|ug/kg)(?:/infusion)?\b", re.I)
_ROUTES = (("intravenous", r"\b(?:intravenous|iv)\b|\bi\.v\."), ("intraperitoneal", r"\bintraperitoneal\b|\bi\.p\.|\bIP\b"), ("oral", r"\b(?:oral|gavage)\b"))


def _plain(text: str) -> str:
    """Normalize a matching copy; callers retain the complete original sentence."""
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _drugs(text: str) -> list[str]:
    """Identify explicit compound names without inferring administration."""
    from .method_details import DRUGS
    return sorted({label for _, label, pattern in DRUGS if re.search(r"\b(?:" + pattern + r")\b", text, re.I)
                   and not (label.startswith("DOI") and re.search(r"\bDOI\s*:?\s*10\.", text, re.I))})


def _context(sentence: str, title: str) -> dict:
    """Keep explicit local conditions and mark title-only species attribution."""
    species = [label for label, pattern in _SPECIES if re.search(pattern, sentence, re.I)]
    species_source = "sentence"
    if not species:
        species = [label for label, pattern in _SPECIES if re.search(pattern, title, re.I)]
        species_source = "title" if species else "unavailable"
    temperature_matches = [m for m in _TEMPERATURE.finditer(sentence) if re.search(
        r"\b(?:at|under|temperature|temperatures|T\s*a)\b[^.;]{0,30}$", sentence[max(0, m.start() - 50):m.start()], re.I)]
    dose_matches = list(_DOSE.finditer(sentence))
    temperatures = sorted({re.sub(r"\s+", "", m.group(0)).replace("/C14", "°") for m in temperature_matches})
    doses = sorted({re.sub(r"\s+", "", m.group(0)) for m in dose_matches})
    ambiguous_conditions = any(re.search(r"\d\s*(?:,|or|and)\s*$", sentence[max(0, m.start() - 15):m.start()])
                               for m in [*temperature_matches, *dose_matches])
    comparators = sorted({m.group(1).lower() for m in re.finditer(
        r"\b(?:compared (?:with|to)|relative to|versus|than)\s+(?:the\s+)?(baseline|saline(?:[- ]treated)? controls?|vehicle(?:[- ]treated)? controls?|untreated controls?)\b", sentence, re.I)})
    conditions = []
    for phrase in ("quiet rest", "social interaction", "warm environment", "cold-trained", "hot-trained", "during acquisition", "during withdrawal"):
        if phrase in sentence.lower():
            conditions.append(phrase)
    conditions.extend(m.group(0).lower() for m in re.finditer(
        r"\b(?:acute|chronic|single|repeated|intermittent|continuous|naive|experienced|dependent|withdrawn|withdrawal|"
        r"pregnant|non[- ]?pregnant|awake|ana?estheti[sz]ed|fasted|fed|night|daytime|day)\b", sentence, re.I))
    return {"drugs": _drugs(sentence), "species": sorted(species), "species_source": species_source,
            "regions": sorted(label for label, pattern in _REGIONS if re.search(pattern, sentence, re.I)),
            "temperatures": temperatures, "doses": doses, "routes": sorted(label for label, pattern in _ROUTES if re.search(pattern, sentence, re.I)),
            "conditions": sorted(set(conditions)), "comparators": comparators, "ambiguous_conditions": ambiguous_conditions,
            "strains": sorted(m.group(0).lower() for m in re.finditer(r"\b(?:Wistar|Sprague[- ]Dawley|Long[- ]Evans|Fischer ?344|C57BL/?6)\b", sentence, re.I)),
            "sex": sorted({m.group(0).lower().removesuffix("s") if m.group(0).lower() != "both sexes" else "both sexes" for m in re.finditer(r"\b(?:males?|females?|both sexes)\b", sentence, re.I)}),
            "time_windows": sorted({m.group(0).lower() for m in re.finditer(r"\b(?:within|after|over|during) (?:the )?(?:first )?\d+(?:[-–]\d+)?\s*(?:min(?:utes)?|h(?:ours)?|days?|weeks?)\b", sentence, re.I)})}


def _factor(clause: str, outcome_start: int, drugs: list[str]) -> tuple[str, bool]:
    """Use only explicit causal subjects; passive change retains an unknown factor."""
    lead = clause[:outcome_start]
    actions = list(re.finditer(r"\b(?:increas\w*|decreas\w*|reduc\w*|enhanc\w*|caused|produced|elicited|induced|led to|did not)\b", lead, re.I))
    action = actions[-1] if actions else None
    subject = lead[:action.start()] if action else ""
    subject = re.split(r"\b(?:and|but|whereas)\b|;", subject, flags=re.I)[-1]
    for drug in drugs:
        # The canonical display name may include a parenthesized alias.
        if drug.split(" (")[0].lower() in subject.lower():
            if re.search(r"\bdoses? of\s+" + re.escape(drug.split(" (")[0]), subject, re.I):
                return drug + " dose", True
            if re.search(r"\bwithdrawal\b", subject, re.I):
                return drug + " withdrawal", True
            if re.search(r"\bself[- ]administration\b", subject, re.I):
                return drug + " self-administration", True
            return drug, True
    if re.search(r"\b(?:ambient|room) temperature|\b(?:heat|high T\s*A)\b", subject, re.I):
        return "ambient temperature", True
    return "Reported condition", False


class Findings(list):
    """List-compatible result collection with an honest display-limit flag."""
    def __init__(self, values=(), *, capped=False):
        super().__init__(values)
        self.capped = capped


def _direction_at(clause: str, start: int, end: int, hits: list) -> str | None:
    """Require a directional predicate attached to this endpoint, not proximity."""
    before, after = clause[:start], clause[end:]
    previous = [right for left, right, _ in hits if right <= start]
    if previous and re.search(r"\b(?:during|following|after|before|marker for)\b", clause[max(previous):start], re.I):
        return None
    modifiers = r"(?:(?:was|were|is|are|also|significantly|substantially|markedly|rapidly|initially|generally)\s+)*"
    nominal = r"(?:increases?|decreases?|reduction|elevation|fall|rise)"
    if re.search(r"\b(?:increased|decreased|reduced|enhanced)\s+(?:the|an?|any)\s+" + nominal + r"\s+in\s*$", before, re.I):
        return "unspecified"
    if re.search(nominal + r"\s+in\s*$", before, re.I) and re.match(r"\s*(?:was|were)\s+(?:increased|decreased|reduced|enhanced)\b", after, re.I):
        return "unspecified"
    if re.match(r"\s*(?:showed|had)\s+(?:a\s+)?biphasic\b", after, re.I):
        return "mixed"
    null_after = re.match(r"\s*" + modifiers + r"(?:unchanged|unaffected|did not (?:change|alter))\b", after, re.I)
    null_before = re.search(r"\b(?:did|does|do) not (?:alter|affect|change|modify)\s+(?:the\s+)?$", before, re.I)
    if null_after or null_before:
        return "no_detected_change"
    if re.search(r"\b(?:did|does) not (?:promote|increase|decrease|reduce|induce)\s+(?:[\w-]+\s+){0,2}$", before, re.I):
        return "unspecified"
    # A directly attached following predicate outranks a shared preceding one:
    # 'temperature increased and dopamine decreased' has two directions.
    cues = sorted(_DIRECTION.finditer(clause), key=lambda cue: (cue.start() < end, abs(cue.start() - end)))
    for cue in cues:
        if cue.end() <= start:
            gap = clause[cue.end():start]
            # Enumerated outcomes may share one predicate, but words such as
            # 'during', 'marker' or 'associated' block borrowing that direction.
            for left, right, _ in reversed(hits):
                if cue.end() <= left and right <= start:
                    gap = gap[:left - cue.end()] + " " * (right - left) + gap[right - cue.end():]
            gap = re.sub(r"\b(?:in|of|the|a|an|and|significant|substantial|marked|rapid|dose-dependent)\b|[,\s]", "", gap, flags=re.I)
            if gap or cue.group().lower() in ("higher", "lower"):
                continue
        elif cue.start() >= end:
            gap = clause[end:cue.start()]
            if not re.fullmatch(r"\s*(?:in (?:individual )?(?:rats|mice|animals)\s+)?" + modifiers, gap, re.I):
                continue
            following = [(left, right) for left, right, _ in hits if left >= cue.end()]
            if following and not re.search(r"\b(?:was|were|is|are)\b", gap, re.I):
                next_start = min(left for left, _ in following)
                if re.fullmatch(r"\s*(?:(?:the|a|an|significantly|substantially)\s+)*", clause[cue.end():next_start], re.I):
                    continue  # This endpoint is the subject causing another to change.
        else:
            continue
        preceding = clause[max(0, cue.start() - 65):cue.start()]
        preceding = re.split(r"\b(?:and|but|whereas)\b|[;.]", preceding, flags=re.I)[-1]
        preceding = re.sub(r"\bnot only\b", "", preceding, flags=re.I)
        if re.search(r"\b(?:no|not|never|failed to|fails to|without|protection against)\b", preceding, re.I):
            return "unspecified"
        later = clause[max(end, cue.end()):]
        if re.search(r"\b(?:other|it|them)\b", later, re.I) and any(other.lastgroup != cue.lastgroup for other in _DIRECTION.finditer(later)):
            return "mixed"
        return "increase" if cue.lastgroup == "up" else "decrease"
    return None


def extract_findings(title: str, abstract: str, results: str, *, splitter: Callable[[str], list[str]]) -> list[dict]:
    """Extract bounded observations, keeping each outcome/condition in its clause.

    Explicit contrast clauses are processed separately, so an increase in one
    condition cannot supply the direction of an outcome in another. Difficult
    negation is retained as unspecified instead of inverted or forced to zero.
    """
    findings, seen = [], set()
    for source, body in (("abstract", abstract), ("results", results[:MAX_RESULTS_CHARACTERS])):
        for original in splitter(body or ""):
            sentence = _plain(original)
            if _AIM.search(sentence) or _BACKGROUND.search(sentence) or _ASSOCIATION.search(sentence) or not _RESULT_CUE.search(sentence):
                continue
            # Do not split conjunctions inside outcome lists; contrast words
            # establish explicit independent conditions and retain full evidence.
            for clause in re.split(r"\s*(?:;|\bwhereas\b|\bbut\b)\s*", sentence, flags=re.I):
                context = _context(clause, title)
                hits = sorted([(m.start(), m.end(), label) for label, rx in _OUTCOME_PATTERNS for m in rx.finditer(clause)])
                for start, end, outcome in hits:
                    direction = _direction_at(clause, start, end, hits)
                    if direction is None:
                        continue
                    factor, explicit_factor = _factor(clause, start, context["drugs"])
                    key = (factor, outcome, direction, tuple(context["temperatures"]), sentence)
                    if key in seen:
                        continue
                    seen.add(key)
                    # Both identity and experimental conditions must be present
                    # before cross-paper counting is even considered.
                    eligible = bool(explicit_factor and len(context["drugs"]) == 1 and len(context["species"]) == 1
                                    and len(context["temperatures"]) == 1 and len(context["doses"]) == 1
                                    and context["comparators"] and not context["ambiguous_conditions"] and len(context["strains"]) <= 1
                                    and len(context["sex"]) <= 1
                                    and len(context["routes"]) == 1
                                    and direction != "unspecified")
                    findings.append({"id": f"finding-{len(findings) + 1}", "factor": factor, "outcome": outcome,
                                     "direction": direction, "evidence": original, "source": source,
                                     "clause": clause, "context": context, "comparison_eligible": eligible,
                                     "interpretation": "Reported direction only; no effect-size or significance inference."})
                    if len(findings) > MAX_FINDINGS:
                        return Findings(findings[:MAX_FINDINGS], capped=True)
    return Findings(findings)


def compare_findings(papers: list[dict]) -> list[dict]:
    """Count one direction per compatible paper, never treating repetition as votes."""
    groups = defaultdict(list)
    for paper in papers:
        if paper.get("findings_capped"):
            continue  # An undisplayed contradictory finding could change its vote.
        for finding in paper.get("findings", []):
            if not finding.get("comparison_eligible"):
                continue
            c = finding["context"]
            context_key = tuple(tuple(c.get(field, [])) for field in ("drugs", "species", "regions", "temperatures", "doses", "routes", "conditions", "comparators", "strains", "sex", "time_windows"))
            groups[(finding["factor"], finding["outcome"], context_key)].append({**finding, "paper_id": paper["id"], "paper_title": paper["title"]})
    comparisons = []
    for (factor, outcome, _), findings in groups.items():
        per_paper = defaultdict(set)
        for finding in findings:
            per_paper[finding["paper_id"]].add(finding["direction"])
        if len(per_paper) < 2:
            continue
        counts = Counter(next(iter(values)) if len(values) == 1 else "mixed" for values in per_paper.values())
        largest = max(counts.values())
        minority = [direction for direction, count in counts.items() if count < largest]
        comparisons.append({"factor": factor, "outcome": outcome, "context": findings[0]["context"],
                            "paper_count": len(per_paper), "direction_counts": dict(counts), "findings": findings,
                            "minority_directions": minority,
                            "interpretation": "Descriptive agreement among matching reported contexts, not a meta-analysis. Minority directions are not statistical outliers; verify protocols and comparators."})
    return sorted(comparisons, key=lambda c: (-c["paper_count"], c["factor"], c["outcome"]))


def related_outcomes(papers: list[dict]) -> list[dict]:
    """Expose shared endpoints across experiments without implying comparability."""
    groups = defaultdict(list)
    for paper in papers:
        for finding in paper.get("findings", []):
            groups[finding["outcome"]].append({**finding, "paper_id": paper["id"], "paper_title": paper["title"]})
    related = []
    capped_ids = {paper["id"] for paper in papers if paper.get("findings_capped")}
    for outcome, findings in groups.items():
        per_paper = defaultdict(set)
        for finding in findings:
            per_paper[finding["paper_id"]].add(finding["direction"])
        if len(per_paper) < 2:
            continue
        fields = ("drugs", "species", "regions", "temperatures", "doses", "routes", "conditions", "comparators", "strains", "sex", "time_windows")
        contexts = {(f["factor"], tuple(tuple(f["context"].get(field, [])) for field in fields)) for f in findings}
        all_complete = all(f.get("comparison_eligible") and f["paper_id"] not in capped_ids for f in findings)
        if all_complete and len(contexts) == 1:
            continue  # Already represented by the compatible-comparison group.
        counts = Counter(next(iter(values)) if len(values) == 1 else "mixed" for values in per_paper.values())
        related.append({"outcome": outcome, "paper_count": len(per_paper), "direction_counts": dict(counts),
                        "findings": findings, "comparison_level": "different_contexts" if all_complete else "incomplete_contexts",
                        "interpretation": "Shared outcome across different experiments; directions are not directly comparable." if all_complete else
                        "Shared outcome with incomplete or different reported contexts; directions are not directly comparable."})
    return sorted(related, key=lambda group: (-group["paper_count"], group["outcome"]))
