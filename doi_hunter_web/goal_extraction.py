"""Conservative, source-backed research-focus extraction for the goal atlas.

Every paper receives a focus entry. An explicit aim and a title-derived focus
are different evidence types and remain labelled accordingly. Relationships are
optional: a readable source sentence is preferable to an invented noun fragment.
"""
from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Callable


MAX_PHRASE_WORDS = 20
MAX_PHRASE_CHARACTERS = 180
MAX_RELATIONSHIPS = 8
MAX_THEMES = 8
_ACTION = r"(?:investigat(?:e|ed|es)|examin(?:e|ed|es)|determin(?:e|ed|es)|assess(?:ed|es)?|evaluat(?:e|ed|es)|explor(?:e|ed|es)|test(?:ed|s)?|compar(?:e|ed|es)|characteri[sz](?:e|ed|es)|measur(?:e|ed|es)|quantif(?:y|ied|ies)|understand|elucidat(?:e|ed|es))"
_RELATION_VERB = r"(?:alters?|altered|affects?|affected|influences?|influenced|changes?|changed|modulates?|modulated|increases?|increased|decreases?|decreased|reduces?|reduced|enhances?|enhanced|facilitates?|facilitated|impairs?|impaired|promotes?|promoted|induces?|induced|causes?|caused|predicts?|predicted|drives?|mediates?)"
_AIM_PATTERNS = (
    re.compile(r"\b(?:objectives?|aims?|purpose)\s*:", re.I),
    re.compile(r"\b(?:our|the|this) (?:primary |main )?(?:aim|objective|purpose|hypothesis)\b", re.I),
    re.compile(r"\b(?:we|authors)\s+(?:here |also |further |therefore |specifically )?(?:" + _ACTION + r"|aim(?:ed)?|sought|hypothesi[sz](?:e|ed)|develop(?:ed)?|propos(?:e|ed))\b", re.I),
    re.compile(r"\b(?:this|the present|the current|our) (?:study|experiment|work|paper)\s+(?:(?:was|is|were)\s+)?(?:designed|aim(?:ed|s)?|sought|" + _ACTION + r")\b", re.I),
    re.compile(r"\b(?:was|were)\s+(?:investigated|examined|assessed|evaluated|explored|compared)\b", re.I),
    re.compile(r"^\s*(?:To|In order to)\s+" + _ACTION + r"\s+(?:whether|how|if)\b", re.I),
    re.compile(r"\b(?:being developed as a model|allowed us)\s+to\s+" + _ACTION + r"\b", re.I),
)
_BACKGROUND = re.compile(r"\b(?:previous|earlier|prior) (?:studies|work|research)|\b(?:others|previous authors) (?:examined|investigated)|\b(?:future studies|remains? to be|should be investigated)\b", re.I)
_COORDINATED_ACTION = re.compile(
    r"\s+(?:and|but|while|whereas)\s+(?:(?:also|then|further|separately)\s+)?"
    r"(?:(?:we|the authors|this study)\s+(?:also\s+)?" + _ACTION +
    r"|compared|examined|investigated|assessed|evaluated|explored|tested|characteri[sz]ed|measured|quantified|determined)\b", re.I)
_TYPES = (
    ("test", r"\bhypothesi[sz]\w*|\bhypothes[ie]s\b|\bwhether\b"),
    ("mechanism", r"\bmechanisms?\b|\bunderlying\b|\bresponsible for\b|\bmediat\w*"),
    ("compare", r"\bcompar\w*|\bversus\b|\bdifferences? between\b"),
    ("develop", r"\b(?:develop\w*|propos\w*|validat\w*)\b.*\b(?:method|model|approach|tool|algorithm)\b"),
    ("review", r"\breview\w*|\bmeta[- ]analys\w*"),
    ("effect", r"\beffects?\b|\binfluence\w*|\bimpact\b|\balter\w*|\baffect\w*"),
)
_THEMES = (
    ("temperature", "Temperature", r"\b(?:temperatures?|thermal|heat|thermoregulat\w*|hypertherm\w*|hypotherm\w*)\b"),
    ("self_administration", "Self-administration", r"\b(?:self[- ]?administration|IVSA|drug intake)\b"),
    ("neurotoxicity", "Neurotoxicity", r"\bneurotoxic\w*\b"),
    ("dopamine", "Dopamine", r"\b(?:dopamine|dopaminergic)\b"),
    ("serotonin", "Serotonin", r"\b(?:serotonin|serotonergic|5-HT)\b"),
    ("cognition", "Cognition & behaviour", r"\b(?:memory|attention|cognition|behavio\w*|locomot\w*|conditioning|learning)\b"),
    ("mdma", "MDMA", r"\b(?:MDMA|methylenedioxymethamphetamine)\b"),
    ("methamphetamine", "Methamphetamine", r"\bmethamphetamine\b"),
    ("cocaine", "Cocaine", r"\bcocaine\b"),
    ("ethanol", "Ethanol", r"\b(?:ethanol|alcohol)\b"),
    ("heroin", "Heroin", r"\bheroin\b"),
    ("physiology", "Physiology", r"\b(?:physiolog\w*|metabol\w*|homeostas\w*|heat loss|heat production)\b"),
)


def normalized(text: str) -> str:
    """Normalise only a matching copy; the source evidence remains unchanged."""
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\b3,4[- ]methylenedioxymethamphetamine\s*\(MDMA\)", "MDMA", text, flags=re.I)
    text = re.sub(r"\be\s+ffects?\b", lambda match: "effects" if match.group().endswith("s") else "effect", text, flags=re.I)
    text = re.sub(r"\bT\s*A\b", "ambient temperature", text)
    text = re.sub(r"\b(MDMA|methamphetamine|cocaine|heroin)\s+IVSA\b", r"\1 intravenous self-administration", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def clean_phrase(value: str) -> str:
    """Keep complete noun phrases, remove explicit trailing assay context, reject clauses."""
    value = normalized(value).strip(" \"'().,;:")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"^(?:(?:the|a|an|such|these|those|this|that|its|their|our)\s+)+", "", value, flags=re.I)
    # Coordinated research actions begin another clause, not another outcome noun.
    # Keep ordinary noun coordination such as 'memory and test performance'.
    value = _COORDINATED_ACTION.split(value, maxsplit=1)[0]
    value = re.split(r"\s+(?:using|by using|measured (?:by|with)|assessed (?:by|with)|as measured|by means of|"
                     r"in (?:the |freely moving )?(?:rats?|mice|humans?|participants|patients)|"
                     r"when (?:exposed|administered)|under conditions|at a dose|on (?:fixed|progressive)|"
                     r"was investigated|were investigated)\b", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"\bmeth self[- ]administration\b", "methamphetamine self-administration", value, flags=re.I)
    value = re.sub(r"\bself administration\b", "self-administration", value, flags=re.I)
    value = re.sub(r"\blocomo(?:tion|tor activity)\b", "locomotor activity", value, flags=re.I)
    value = re.sub(r"\btemperatures\b", "temperature", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" ,;:")
    if (len(value) < 3 or len(value) > MAX_PHRASE_CHARACTERS or len(value.split()) > MAX_PHRASE_WORDS or
            "," in value or
            re.search(r"\b(?:is|are|was|were|does|did|has|have|had|will|would|could|should|constitutes|cause|causes|affects|influences|facilitates)\b", value, re.I) or
            re.match(r"(?:whether|how|if|we|it|they|to|and|or|in|on|of|for|with|modified|increased|decreased)\b", value, re.I)):
        return ""
    return value.lower()


def goal_type(sentence: str) -> str:
    """Classify an explicit aim conservatively; unclassified aims remain descriptive."""
    for kind, pattern in _TYPES:
        if re.search(pattern, sentence, re.I):
            return kind
    return "describe"


def is_explicit_aim(sentence: str) -> bool:
    """Require purpose or investigation language, not merely a mention of this study."""
    candidate = normalized(sentence)
    if _BACKGROUND.search(candidate) and not re.search(r"\b(?:we|this study|present study)\b", candidate, re.I):
        return False
    return any(pattern.search(candidate) for pattern in _AIM_PATTERNS)


def extract_pairs(sentence: str, source: str) -> list[dict]:
    """Recognise explicit relation constructions and preserve the full source sentence."""
    candidate = normalized(sentence).rstrip(" .!?")
    # Parenthetical doses and abbreviations can contain periods that are not clause
    # boundaries. Known MDMA aliases were resolved above before removing these spans.
    candidate = re.sub(r"\([^)]*\)", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate)
    matches: list[tuple[str, str, str]] = []

    def collect(pattern: str, kind: str = "effect", text: str = candidate) -> None:
        """Collect named factor/outcome captures without guessing missing arguments."""
        for match in re.finditer(pattern, text, re.I):
            matches.append((match.group("x"), match.group("y"), kind))

    collect(r"\b(?:effects?|influence|impact|consequences?) of (?P<x>[^.;:!?]+?) (?:on|upon) (?P<y>[^.;:!?]+)")
    collect(r"\b(?:role|involvement) of (?P<x>[^.;:!?]+?) in (?P<y>[^.;:!?]+)", "mechanism")
    collect(r"\b(?:how|whether|if) (?P<x>[^;:!?]+?)\s+" + _RELATION_VERB + r"\s+(?P<y>[^;:!?]+)", "test")
    collect(r"\b(?:how|whether|if) (?P<y>[^;:!?]+?)\s+(?:is|are)\s+(?:modified|affected|altered|changed)\s+(?:by|with)\s+(?P<x>[^;:!?]+)", "test")
    collect(r"\bmechanisms? responsible for (?P<y>[^.;:!?]+?) following (?P<x>[^.;:!?]+)", "mechanism")
    relationship = re.search(r"\b(?:relationship|association|interaction) between (.+?)(?:\s+(?:was|were)\s+|$)", candidate, re.I)
    if relationship:
        parts = [part.strip(" ,") for part in re.split(r",\s*(?:and\s+)?|\s+and\s+", relationship.group(1)) if part.strip(" ,")]
        if 2 <= len(parts) <= 4:
            matches.append((parts[0], " and ".join(parts[1:]), "describe"))
    if source == "title":
        collect(r"^(?P<x>.+?) dependence of (?P<y>.+)$", "describe")
        collect(r"^(?P<y>[^:;]+?) effects? of (?P<x>[^:;]+)(?:[:;]|$)")
        collect(r"^.+? and (?P<y>.+?):\s*effects? of (?P<x>.+)$")
        collect(r"^(?P<x>.+?)\s+(?:has|have)\s+(?:differential\s+)?effects? on (?P<y>.+)$")
        # A noun such as 'changes' can resemble a verb. Try each boundary instead
        # of accepting the first split in 'Small changes in X cause changes in Y'.
        for boundary in re.finditer(r"\s+(?:" + _RELATION_VERB + r"|fails? to elicit)\s+", candidate, re.I):
            matches.append((candidate[:boundary.start()], candidate[boundary.end():], "effect"))
        collect(r"^(?P<y>.+?) following (?P<x>.+)$", "describe")
    out = []
    seen = set()
    for raw_factor, raw_outcome, kind in matches:
        factor, outcome = clean_phrase(raw_factor), clean_phrase(raw_outcome)
        if not factor or not outcome or factor == outcome or (factor, outcome) in seen:
            continue
        seen.add((factor, outcome))
        negated = source == "title" and bool(re.search(r"\b(?:fails? to|does not|did not|no effect)\b", candidate, re.I))
        out.append({"factor": factor, "outcome": outcome, "kind": kind, "evidence": sentence,
                    "source": source, "confidence": "title_derived" if source == "title" else "explicit",
                    "negated": negated,
                    "relation_label": "No effect reported" if negated else "Title-stated relationship" if source == "title" else "Research question",
                    "interpretation": "Stated relationship, not a demonstrated causal effect."})
    return out


def extract_goal(title: str, abstract: str, introduction: str = "", fallback_text: str = "",
                 *, splitter: Callable[[str], list[str]], max_aims: int = 3) -> dict:
    """Produce one traceable focus for every paper, with optional validated relations."""
    candidates = [(sentence, "abstract") for sentence in splitter(abstract or "")]
    candidates += [(sentence, "introduction") for sentence in splitter(introduction or "")[-12:]]
    if not introduction and not abstract and fallback_text:
        candidates += [(sentence, "fulltext") for sentence in splitter(fallback_text[:6000])]
    aims = []
    seen = set()
    for sentence, source in candidates:
        key = normalized(sentence).lower()
        if key in seen or not is_explicit_aim(sentence):
            continue
        seen.add(key)
        aims.append({"sentence": sentence, "kind": goal_type(sentence), "source": source, "confidence": "high"})
        if len(aims) >= max_aims:
            break
    if aims:
        focus, source, status, label = aims[0]["sentence"], aims[0]["source"], "explicit_aim", "Explicit aim"
    elif (title or "").strip() and not re.fullmatch(r"\(?untitled\)?", title.strip(), re.I):
        focus, source, status, label = title.strip(), "title", "title_focus", "From title"
    else:
        focus, source, status, label = "", "", "unavailable", "Text unavailable"
    pairs = []
    used = set()
    for sentence, sentence_source in [(aim["sentence"], aim["source"]) for aim in aims] + [(title or "", "title")]:
        for pair in extract_pairs(sentence, sentence_source):
            key = (pair["factor"], pair["outcome"])
            if key not in used:
                pairs.append(pair)
                used.add(key)
    themes = []
    for key, theme_label, pattern in _THEMES:
        for text, theme_source in [(focus, source), (title or "", "title")]:
            match = re.search(pattern, text, re.I)
            if match:
                themes.append({"id": key, "label": theme_label, "match": match.group(), "source": theme_source})
                break
    return {"focus": focus, "focus_source": source, "focus_status": status, "focus_label": label,
            "goal_type": aims[0]["kind"] if aims else "unknown", "confidence": "high" if aims else "low" if focus else "none",
            "aims": aims, "pairs": pairs[:MAX_RELATIONSHIPS], "themes": themes[:MAX_THEMES]}
