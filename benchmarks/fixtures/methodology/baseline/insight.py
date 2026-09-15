"""Insight maps over a set of papers: concepts, words, goals and methodology.

All four maps take the same *scope* (whole library, one collection with its subcollections, an
author, a tag) and read the text ReviewHunter already holds for each item: title, abstract, tags,
keywords / subjects, and the full text of indexed PDFs (``fulltext_fts``).

    concepts(scope)  co-occurrence graph of terms (tags + keywords + phrases extracted from titles and
                     abstracts), with communities (clusters) so the map shows themes, not a ring of dots.
    words(scope)     word map: frequency of words and two-word phrases in abstracts or full texts, with
                     a "distinctiveness" score against the rest of the library when the scope is a subset.
    goals(scope)     goal map: the sentence in which each paper states its aim, its goal type, and the
                     factor -> outcome pairs it announces ("effect of X on Y", "whether X alters Y"),
                     assembled into a bipartite map with the papers on the links.
    methods(scope)   methodology map: for every paper, the methods detected in its Methods section
                     (species, design, manipulations, measurements, analyses) ordered as a pipeline, the
                     sentence that proves each detection, reporting checks (sex, n, randomisation,
                     blinding, ethics, statistics, power, pre-registration, data availability) and, for the
                     whole scope, a papers x methods matrix and a list of gaps.

Everything is rule-based and shows its evidence. It does not hallucinate: a method or a goal appears
only when a sentence of the paper matches, and that sentence is returned. Limits are stated in the
payloads (``limits`` keys) so the UI can say what was not analysed (no full text, no Methods section).
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from itertools import combinations
from typing import Iterable, Optional

from . import catalog
from .db import get_conn
from .method_taxonomy import METHODS, REPORTING_CHECKS, STAGE_ORDER, STAGES

log = logging.getLogger("doihunter.insight")

# ============================================================================ PARAMETERS
MAX_ITEMS = 2000                 # scope size guard
MAX_TERMS_PER_ITEM = 16          # concept terms kept per paper
MAX_CONCEPT_NODES = 80
MAX_WORDS = 150
MAX_GOAL_SENTENCES = 3           # aim sentences kept per paper
EVIDENCE_CHARS = 240             # length of an evidence sentence in payloads
METHODS_SECTION_MIN_CHARS = 600  # shorter "Methods" sections are treated as not found
CLUSTER_ITERATIONS = 12          # label propagation rounds
# ============================================================================

STOPWORDS = set("""
a about above after again against all also although among an and any are as at be because been before being below
between both but by can could did do does doing done down during each either few for from further had has have having
he her here hers him his how however i if in into is it its itself just less may me might more most much must my nor
not of off on once only or other our ours out over own per same she should since so some such than that the their them
then there these they this those through thus to too toward under until up upon us very via was we were what when where
whether which while who whom whose why will with within without would yet you your
study studies present paper article results result data show shown showed suggest suggests suggested indicate indicates
indicated using used use based effect effects level levels group groups significant significantly compared control
controls analysis analyses method methods approach findings finding may also however although whereas respectively
et al fig figure table one two three first second three increase increased increases decrease decreased decreases
higher lower high low large small total number rate rates mean means following respectively various different
""".split())

_WORD_RX = re.compile(r"[A-Za-z][A-Za-z0-9\-']{1,}")
_SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
_ABBREV_RX = re.compile(r"\b(?:e\.g|i\.e|et al|Fig|Figs|vs|approx|ca|cf|Dr|Prof|No|St)\.$")
_HEADING_RX = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s*\|?\s*)?(?P<h>abstract|introduction|background|materials? and methods?|methods?|"
    r"experimental procedures?|experimental (?:design|methods?)|subjects and methods|patients and methods|"
    r"results?(?: and discussion)?|discussion|conclusions?|references|acknowledg?e?ments?|supplementary (?:material|information))\s*:?\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------- scope & text

def scope_items(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
                include_sub: bool = True) -> list[dict]:
    """Items of a scope, with abstracts and tag names."""
    items = catalog.query_items(collection_id=collection_id, include_subcollections=include_sub, author=author,
                                tags=[tag] if tag else None, limit=MAX_ITEMS, compact=False)
    for it in items:
        it["tag_names"] = [t["name"] if isinstance(t, dict) else str(t) for t in (it.get("tags") or [])]
    return items


def scope_label(collection_id: Optional[int], author: Optional[str], tag: Optional[str]) -> str:
    parts = []
    if collection_id is not None:
        col = next((c for c in catalog.list_collections() if c["id"] == collection_id), None)
        parts.append(col["name"] if col else "Collection")
    if author:
        parts.append(author)
    if tag:
        parts.append(f"#{tag}")
    return " / ".join(parts) if parts else "Whole library"


def fulltext_of(item_id: int) -> str:
    rows = get_conn().execute("SELECT content FROM fulltext_fts WHERE item_id=? ORDER BY attachment_id", (item_id,)).fetchall()
    return "\n".join(r[0] for r in rows if r[0])


def _json_list(value) -> list[str]:
    import json

    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    try:
        parsed = json.loads(value)
        return [str(v) for v in parsed if v] if isinstance(parsed, list) else [str(value)]
    except (ValueError, TypeError):
        return [str(value)]


def sentences(text: str) -> list[str]:
    """Sentence split that survives 'et al.', 'Fig.' and PDF line breaks."""
    text = re.sub(r"-\n(?=[a-z])", "", text)          # hyphenation at line end
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    out: list[str] = []
    buf = ""
    for piece in _SENT_SPLIT_RX.split(text):
        if buf and _ABBREV_RX.search(buf):
            buf = f"{buf} {piece}"
            continue
        if buf:
            out.append(buf)
        buf = piece
    if buf:
        out.append(buf)
    return [s.strip() for s in out if len(s.strip()) > 2]


def split_sections(text: str) -> dict[str, str]:
    """Split a paper's text on its headings. Keys: abstract, introduction, methods, results, discussion, references."""
    if not text:
        return {}
    canon = {
        "abstract": "abstract", "introduction": "introduction", "background": "introduction",
        "results": "results", "results and discussion": "results", "discussion": "discussion",
        "conclusion": "discussion", "conclusions": "discussion", "references": "references",
        "acknowledgements": "references", "acknowledgments": "references", "acknowledgement": "references",
        "supplementary material": "references", "supplementary information": "references",
    }
    sections: dict[str, list[str]] = defaultdict(list)
    current = "front"
    for line in text.splitlines():
        m = _HEADING_RX.match(line)
        if m and len(line.strip()) <= 60:
            head = m.group("h").lower()
            key = canon.get(head)
            if key is None:
                key = "methods" if ("method" in head or "procedure" in head or "experimental" in head) else head
            current = key
            continue
        sections[current].append(line)
    out = {k: "\n".join(v).strip() for k, v in sections.items()}
    if "methods" in out and len(out["methods"]) < METHODS_SECTION_MIN_CHARS:
        out.pop("methods")
    return out


def _evidence(sentence: str) -> str:
    s = re.sub(r"\s+", " ", sentence).strip()
    return s if len(s) <= EVIDENCE_CHARS else s[:EVIDENCE_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _item_brief(it: dict) -> dict:
    return {"id": it["id"], "title": it.get("title") or "(untitled)", "year": it.get("year"),
            "authors": it.get("authors") or [], "journal": it.get("journal") or it.get("publisher") or ""}


def _texts(items: list[dict], with_fulltext: bool) -> dict[int, dict]:
    """Per item: title, abstract, tags, terms, fulltext, sections."""
    out = {}
    for it in items:
        ft = fulltext_of(it["id"]) if with_fulltext else ""
        out[it["id"]] = {
            "title": it.get("title") or "",
            "abstract": it.get("abstract") or "",
            "tags": it.get("tag_names") or [],
            "keywords": [*_json_list(it.get("keywords")), *_json_list(it.get("subjects"))],
            "fulltext": ft,
            "sections": split_sections(ft) if ft else {},
        }
    return out


# ---------------------------------------------------------------------------- concepts

def _tokens(text: str) -> list[str]:
    return [w.lower().strip("-'") for w in _WORD_RX.findall(text or "")]


def _phrases(text: str, max_n: int = 3) -> Counter:
    """Candidate 1-3 word phrases of non-stopword tokens (no stopword at either end, no digits)."""
    toks = _tokens(text)
    out: Counter = Counter()
    for n in range(1, max_n + 1):
        for i in range(len(toks) - n + 1):
            gram = toks[i:i + n]
            if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or any(len(t) < 3 or t.isdigit() for t in gram):
                continue
            if n > 1 and any(t in STOPWORDS for t in gram[1:-1]) and n == 2:
                continue
            out[" ".join(gram)] += 1
    return out


def _singular(word: str) -> str:
    """Cheap English singulariser so 'temperatures' and 'temperature' are one concept."""
    w = word
    for suf, repl in (("ies", "y"), ("sses", "ss"), ("shes", "sh"), ("ches", "ch"), ("xes", "x")):
        if w.endswith(suf) and len(w) > len(suf) + 1:
            return w[: -len(suf)] + repl
    if w.endswith("s") and not w.endswith("ss") and not w.endswith("us") and len(w) > 3:
        return w[:-1]
    return w


def _key(label: str) -> str:
    """Normalised concept key: lower-case, collapsed spaces, each word singularised."""
    norm = re.sub(r"\s+", " ", label.strip().casefold())
    return " ".join(_singular(w) for w in norm.split(" ")) if norm else norm


def concepts(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
             limit: int = MAX_CONCEPT_NODES, focus: Optional[str] = None) -> dict:
    """Concept co-occurrence graph with clusters."""
    items = scope_items(collection_id, author, tag)
    texts = _texts(items, with_fulltext=False)
    limit = max(10, min(limit, 160))

    # Curated terms (author / user tags and keywords) are always kept and never dropped as sub-phrases.
    curated: set[str] = set()
    for it in items:
        for raw in [*texts[it["id"]]["tags"], *texts[it["id"]]["keywords"]]:
            curated.add(_key(raw))

    # 1. candidate terms per item: tags + keywords always; phrases from title + abstract by score.
    per_item: dict[int, dict[str, str]] = {}
    df: Counter = Counter()
    for it in items:
        t = texts[it["id"]]
        labels: dict[str, str] = {}
        for raw in [*t["tags"], *t["keywords"]]:
            k = _key(raw)
            if len(k) > 1 and k not in STOPWORDS:
                labels.setdefault(k, raw.strip())
        title_phr = _phrases(t["title"])
        abs_phr = _phrases(t["abstract"])
        scored: list[tuple[float, str]] = []
        for phrase, c in (title_phr + abs_phr).items():
            n_words = phrase.count(" ") + 1
            in_title = phrase in title_phr
            if c < 2 and not in_title:
                continue
            score = c * (1.0 + 0.6 * (n_words - 1)) + (2.5 if in_title else 0)
            scored.append((score, phrase))
        scored.sort(reverse=True)
        for _s, phrase in scored:
            if len(labels) >= MAX_TERMS_PER_ITEM:
                break
            k = _key(phrase)
            if k in labels:
                continue
            labels.setdefault(k, phrase)
        # Drop a single-word term already contained in a kept multiword phrase (temperature vs ambient temperature).
        multiword_parts = {part for key in labels if " " in key for part in key.split(" ")}
        per_item[it["id"]] = {k: v for k, v in labels.items() if " " in k or k not in multiword_parts}
        df.update(per_item[it["id"]].keys())

    # keep phrases seen in >= 2 papers, tags/keywords always (they are curated), unless the scope has a single paper
    curated: set[str] = set()
    for it in items:
        for raw in [*texts[it["id"]]["tags"], *texts[it["id"]]["keywords"]]:
            curated.add(_key(raw))
    single = len(items) <= 1
    kept_terms = {k for k, c in df.items() if c >= 2 or k in curated or single}
    for iid in per_item:
        per_item[iid] = {k: v for k, v in per_item[iid].items() if k in kept_terms}

    # 2. graph
    counts: Counter = Counter()
    edges: Counter = Counter()
    term_items: dict[str, list[dict]] = defaultdict(list)
    label_by_key: dict[str, str] = {}
    for it in items:
        terms = per_item.get(it["id"], {})
        for k, label in terms.items():
            counts[k] += 1
            term_items[k].append(_item_brief(it))
            label_by_key.setdefault(k, label)
        for a, b in combinations(sorted(terms), 2):
            edges[(a, b)] += 1
    selected = [k for k, _c in counts.most_common(limit)]
    focus_key = _key(focus or "")
    if focus_key and focus_key in counts:
        neigh = Counter()
        for (a, b), w in edges.items():
            if a == focus_key:
                neigh[b] += w
            elif b == focus_key:
                neigh[a] += w
        selected = [focus_key, *[k for k, _w in neigh.most_common(limit - 1)]]
    sel = set(selected)

    # 3. communities: label propagation, deterministic (nodes visited by decreasing count)
    adj: dict[str, dict[str, int]] = defaultdict(dict)
    for (a, b), w in edges.items():
        if a in sel and b in sel:
            adj[a][b] = w
            adj[b][a] = w
    community = {k: i for i, k in enumerate(selected)}
    for _ in range(CLUSTER_ITERATIONS):
        changed = False
        for k in selected:
            if not adj[k]:
                continue
            votes: Counter = Counter()
            for other, w in adj[k].items():
                votes[community[other]] += w * counts[other]
            best = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if best != community[k]:
                community[k] = best
                changed = True
        if not changed:
            break
    clusters: dict[int, list[str]] = defaultdict(list)
    for k in selected:
        clusters[community[k]].append(k)
    cluster_payload = []
    remap: dict[int, int] = {}
    for new_id, (cid, members) in enumerate(sorted(clusters.items(), key=lambda kv: (-sum(counts[m] for m in kv[1]), kv[0]))):
        remap[cid] = new_id
        members.sort(key=lambda m: (-counts[m], m))
        cluster_payload.append({"id": new_id, "label": label_by_key[members[0]], "size": len(members),
                               "terms": [label_by_key[m] for m in members[:8]],
                               "items": len({i["id"] for m in members for i in term_items[m]})})

    nodes = []
    for k in selected:
        related = sorted(((label_by_key[o], w) for o, w in adj[k].items()), key=lambda t: (-t[1], t[0]))[:8]
        nodes.append({"id": k, "label": label_by_key[k], "count": counts[k], "cluster": remap[community[k]],
                      "curated": k in curated, "focused": k == focus_key,
                      "items": term_items[k][:12], "related": [{"label": l, "weight": w} for l, w in related]})
    edge_payload = sorted(({"source": a, "target": b, "weight": w} for (a, b), w in edges.items() if a in sel and b in sel),
                          key=lambda e: -e["weight"])[:400]
    unique = [label_by_key[k] for k, c in counts.items() if c == 1 and k in curated][:20]
    return {
        "scope": {"label": scope_label(collection_id, author, tag), "items": len(items), "collection_id": collection_id,
                  "author": author, "tag": tag, "focus": focus or ""},
        "nodes": nodes, "edges": edge_payload, "clusters": cluster_payload, "total_terms": len(counts),
        "insights": {"unique_terms": unique,
                     "isolated_items": [_item_brief(it) for it in items if not per_item.get(it["id"])][:10]},
        "limits": {"no_abstract": sum(1 for it in items if not (it.get("abstract") or "").strip())},
    }


# ---------------------------------------------------------------------------- words

def words(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
          source: str = "abstract", limit: int = MAX_WORDS) -> dict:
    """Word map (unigrams + bigrams) with per-word items and distinctiveness against the rest of the library."""
    items = scope_items(collection_id, author, tag)
    use_full = source == "fulltext"
    texts = _texts(items, with_fulltext=use_full)
    limit = max(20, min(limit, 400))

    def text_of(iid: int) -> str:
        t = texts[iid]
        body = t["fulltext"] if use_full and t["fulltext"] else t["abstract"]
        if use_full and t["sections"].get("references"):
            body = body.replace(t["sections"]["references"], " ")
        return f"{t['title']}. {body}"

    total: Counter = Counter()
    per_word_items: dict[str, set[int]] = defaultdict(set)
    per_item_counts: dict[int, Counter] = {}
    for it in items:
        c = _phrases(text_of(it["id"]), max_n=2)
        per_item_counts[it["id"]] = c
        total.update(c)
        for w in c:
            per_word_items[w].add(it["id"])
    # drop a bigram's unigrams when the bigram carries most of their occurrences? keep both; UI can filter by length.
    scoped_ids = {it["id"] for it in items}
    rest_counts: Counter = Counter()
    subset = collection_id is not None or author or tag
    if subset:
        others = [it for it in catalog.query_items(limit=MAX_ITEMS, compact=False) if it["id"] not in scoped_ids]
        for o in others:
            rest_counts.update(_phrases(f"{o.get('title') or ''}. {o.get('abstract') or ''}", max_n=2))
    n_scope = max(1, sum(total.values()))
    n_rest = max(1, sum(rest_counts.values()))
    out = []
    for w, c in total.most_common(limit * 2):
        if c < 2 and len(items) > 1:
            continue
        distinct = math.log(((c + 1) / n_scope) / ((rest_counts.get(w, 0) + 1) / n_rest)) if subset else 0.0
        out.append({"word": w, "count": c, "items": sorted(per_word_items[w]), "n_items": len(per_word_items[w]),
                    "distinct": round(distinct, 2), "bigram": " " in w})
        if len(out) >= limit:
            break
    briefs = {it["id"]: _item_brief(it) for it in items}
    return {
        "scope": {"label": scope_label(collection_id, author, tag), "items": len(items), "collection_id": collection_id,
                  "author": author, "tag": tag, "source": "fulltext" if use_full else "abstract"},
        "words": out, "items": briefs,
        "limits": {"no_fulltext": sum(1 for it in items if use_full and not texts[it["id"]]["fulltext"]),
                   "no_abstract": sum(1 for it in items if not texts[it["id"]]["abstract"].strip())},
    }


# ---------------------------------------------------------------------------- goals

_AIM_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:we |authors )?(?:tested|test|examined|examine) (?:the hypothesis|whether|if)\b|\bhypothesi[sz]e[ds]?\b|\bwhether\b", re.I), "test"),
    (re.compile(r"\b(?:effects?|influence|impact|consequences?) of\b|\bmodulat(?:e|es|ed|ion)\b|\balter(?:s|ed)?\b", re.I), "effect"),
    (re.compile(r"\bmechanism(?:s)?\b|\bmediat(?:e|es|ed|ion)\b|\brole of\b|\bunderlying\b|\bcontribut(?:e|es|ion) of\b", re.I), "mechanism"),
    (re.compile(r"\bcompar(?:e|ed|ing|ison)\b|\bversus\b|\bdifferences? between\b|\brelative to\b", re.I), "compare"),
    (re.compile(r"\b(?:we )?(?:develop(?:ed)?|propose[ds]?|present|introduce[ds]?|validate[ds]?|design(?:ed)?) (?:a |an |the )?(?:novel |new )?(?:method|model|approach|framework|tool|protocol|assay|paradigm|algorithm|pipeline)\b", re.I), "develop"),
    (re.compile(r"\bsystematic(?:ally)? review(?:ed)?\b|\bmeta-?analys[ie]s\b|\bwe review\b|\bthis review\b|\boverview of\b", re.I), "review"),
    (re.compile(r"\bcharacteri[sz]e[ds]?\b|\bdescribe[ds]?\b|\binvestigat(?:e|ed|es|ion)\b|\bexamin(?:e|ed|es)\b|\bexplor(?:e|ed|es)\b|\bdetermin(?:e|ed|es)\b|\bassess(?:ed|es)?\b|\bevaluat(?:e|ed|es)\b|\bquantif(?:y|ied|ies)\b|\bmeasur(?:e|ed|es)\b|\bidentif(?:y|ied|ies)\b", re.I), "describe"),
]
_AIM_SENTENCE_RX = re.compile(
    r"\b(?:aim(?:ed|s)?|objectives?|purpose|goal|the present (?:study|experiments?|work|paper)|this (?:study|paper|work|article|experiment)|"
    r"here,? we|in this (?:paper|study|work|article|report)|we (?:investigated|examined|tested|determined|explored|assessed|evaluated|"
    r"characteri[sz]ed|studied|sought|hypothesi[sz]ed|asked|compared|measured|quantified|examine|investigate|test|report|show|"
    r"demonstrate|propose|develop|present)|to (?:determine|investigate|examine|test|assess|evaluate|characteri[sz]e|explore|elucidate|"
    r"identify|quantify|establish|clarify|understand|compare) )", re.I)
_PAIR_RXS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:effects?|influence|impact|consequences?|contribution) of (?P<x>[^,.;]{3,90}?) (?:on|upon) (?P<y>[^,.;]{3,90}?)(?=[,.;:]|\s+(?:in|was|were|during|under|using|among|of|at|across|remains?|is|are)\b)", re.I), "effect"),
    (re.compile(r"\b(?:role|involvement) of (?P<x>[^,.;]{3,70}?) in (?P<y>[^,.;]{3,90}?)(?=[,.;:]|\s+(?:in|was|were|during|under|using|among|of|at|remains?|is|are)\b)", re.I), "mechanism"),
    (re.compile(r"\bwhether (?P<x>[^,.;]{3,90}?) (?:alters?|altered|affects?|affected|changes?|changed|modulates?|modulated|increases?|increased|"
                r"decreases?|decreased|reduces?|reduced|enhances?|enhanced|influences?|influenced|impairs?|impaired|facilitates?|facilitated|"
                r"promotes?|promoted|induces?|induced|predicts?|predicted|drives?|mediates?|contributes? to|is (?:required|necessary|sufficient) for) (?P<y>[^,.;]{3,90}?)(?=[,.;:]|\s+(?:in|under|during|at|among|of|using|when|after|following)\b)", re.I), "test"),
    (re.compile(r"\b(?:relationship|relation|association|link|interaction) between (?P<x>[^,.;]{3,70}?) and (?P<y>[^,.;]{3,70}?)(?=[,.;:]|\s+(?:in|was|were|among|of|at|remains?|is|are)\b)", re.I), "describe"),
    (re.compile(r"\b(?P<x>[A-Z]?[^,.;]{3,60}?) (?:as a (?:predictor|marker|determinant) of|predicts?) (?P<y>[^,.;]{3,80}?)(?=[,.;:]|\s+(?:in|was|were|among|of|at)\b)", re.I), "describe"),
]
_TRIM_RX = re.compile(r"^(?:the |a |an |such |these |those |this |that |its |their |our |whether )+", re.I)


def _clean_phrase(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" \"'()")
    s = _TRIM_RX.sub("", s)
    words_ = s.split(" ")
    if len(words_) > 8:
        s = " ".join(words_[-8:])
    return s.strip().rstrip(",;:").lower()


def _goal_type(sentence: str) -> str:
    for rx, kind in _AIM_CUES:
        if rx.search(sentence):
            return kind
    return "describe"


def goals(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None) -> dict:
    """Goal map: aim sentences, goal types and factor -> outcome pairs per paper, aggregated into a bipartite map."""
    items = scope_items(collection_id, author, tag)
    texts = _texts(items, with_fulltext=True)
    papers = []
    factors: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    links: dict[tuple[str, str], dict] = {}
    type_counts: Counter = Counter()
    for it in items:
        t = texts[it["id"]]
        pool = [t["title"]] + sentences(t["abstract"])
        intro = t["sections"].get("introduction", "")
        if intro:
            pool += sentences(intro)[-8:]  # aims usually close an introduction
        elif t["fulltext"] and not t["abstract"]:
            pool += sentences(t["fulltext"][:6000])
        aims = [s for s in pool[1:] if _AIM_SENTENCE_RX.search(s)]
        confidence = "high" if aims else ("low" if len(pool) > 1 else "none")
        if not aims and len(pool) > 2:
            aims = [pool[2]]
        aims = aims[:MAX_GOAL_SENTENCES]
        kinds = [_goal_type(s) for s in aims]
        main_type = kinds[0] if kinds else "unknown"
        type_counts[main_type] += 1
        pairs = []
        for s in [t["title"], *aims]:
            for rx, kind in _PAIR_RXS:
                for m in rx.finditer(s):
                    x, y = _clean_phrase(m.group("x")), _clean_phrase(m.group("y"))
                    if len(x) < 3 or len(y) < 3 or x == y or len(x) > 90 or len(y) > 90:
                        continue
                    pairs.append({"factor": x, "outcome": y, "kind": kind, "evidence": _evidence(s)})
        seen_pairs = set()
        for p in pairs:
            key = (p["factor"], p["outcome"])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            factors.setdefault(p["factor"], {"id": p["factor"], "label": p["factor"], "items": set()})["items"].add(it["id"])
            outcomes.setdefault(p["outcome"], {"id": p["outcome"], "label": p["outcome"], "items": set()})["items"].add(it["id"])
            link = links.setdefault(key, {"factor": p["factor"], "outcome": p["outcome"], "items": [], "kind": p["kind"]})
            link["items"].append({"id": it["id"], "title": it.get("title") or "", "evidence": p["evidence"]})
        papers.append({**_item_brief(it), "goal_type": main_type, "confidence": confidence,
                       "aims": [{"sentence": _evidence(s), "kind": k} for s, k in zip(aims, kinds)],
                       "pairs": [{k: v for k, v in p.items() if k != "evidence"} for p in pairs][:6]})

    def _nodes(d: dict) -> list[dict]:
        return sorted(({"id": v["id"], "label": v["label"], "count": len(v["items"]), "items": sorted(v["items"])} for v in d.values()),
                      key=lambda n: (-n["count"], n["label"]))

    return {
        "scope": {"label": scope_label(collection_id, author, tag), "items": len(items), "collection_id": collection_id,
                  "author": author, "tag": tag},
        "papers": papers, "factors": _nodes(factors), "outcomes": _nodes(outcomes),
        "links": sorted(links.values(), key=lambda l: (-len(l["items"]), l["factor"])),
        "goal_types": [{"kind": k, "count": c} for k, c in type_counts.most_common()],
        "insights": {"single_paper_outcomes": [o["label"] for o in _nodes(outcomes) if o["count"] == 1][:12],
                     "no_aim_found": [p["title"] for p in papers if p["confidence"] != "high"][:10]},
        "limits": {"no_abstract": sum(1 for it in items if not texts[it["id"]]["abstract"].strip())},
    }


# ---------------------------------------------------------------------------- methods

def _detect_methods(text: str, confidence: str, seen: dict[str, dict], scale: int, min_count: int = 1) -> None:
    """Run every taxonomy regex over ``text``; keep the strongest hit per method (with its sentence).

    ``min_count`` guards against passing mentions: a rat paper that cites a monkey study once should
    not be tagged "Non-human primates". Author-chosen text (abstract, methods section) uses min_count 1;
    the body of the paper (introduction + discussion included) requires the method to appear at least twice.
    """
    if not text:
        return
    for m in METHODS:
        hits = m["regex"].findall(text)
        count = len(hits)
        if count < min_count:
            continue
        hit = m["regex"].search(text)
        if not hit:
            continue
        start = hit.start()
        s0 = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
        s1_candidates = [p for p in (text.find(".", hit.end()), text.find("\n", hit.end())) if p != -1]
        s1 = min(s1_candidates) + 1 if s1_candidates else min(len(text), hit.end() + 200)
        sentence = text[s0:s1]
        entry = seen.get(m["id"])
        rank = {"high": 3, "medium": 2, "low": 1}
        if entry is None or rank[confidence] > rank[entry["confidence"]]:
            seen[m["id"]] = {"id": m["id"], "label": m["label"], "stage": m["stage"], "confidence": confidence,
                             "count": count, "position": round(start / max(1, scale), 4), "evidence": _evidence(sentence),
                             "match": hit.group(0)}
        elif entry is not None and rank[confidence] == rank[entry["confidence"]]:
            entry["count"] = max(entry["count"], count)


def _reporting(text: str) -> list[dict]:
    out = []
    for chk in REPORTING_CHECKS:
        hit = chk["regex"].search(text or "")
        ev = ""
        if hit:
            s0 = max(text.rfind(".", 0, hit.start()), text.rfind("\n", 0, hit.start())) + 1
            s1 = text.find(".", hit.end())
            ev = _evidence(text[s0:(s1 + 1 if s1 != -1 else hit.end() + 160)])
        out.append({"id": chk["id"], "label": chk["label"], "found": bool(hit), "evidence": ev})
    return out


def methods(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None) -> dict:
    """Methodology map: per-paper pipelines with evidence, reporting checks, matrix and gaps for the scope."""
    items = scope_items(collection_id, author, tag)
    texts = _texts(items, with_fulltext=True)
    papers = []
    method_items: dict[str, list[int]] = defaultdict(list)
    stage_items: dict[str, set[int]] = defaultdict(set)
    check_totals: Counter = Counter()
    for it in items:
        t = texts[it["id"]]
        seen: dict[str, dict] = {}
        body = t["fulltext"]
        if t["sections"].get("references"):
            body = body.replace(t["sections"]["references"], " ")
        methods_text = t["sections"].get("methods", "")
        source = "methods section" if methods_text else ("full text" if body else ("abstract" if t["abstract"] else "none"))
        # Author-chosen text (abstract, methods section) counts from one mention; the whole body needs two,
        # so a method named once in the introduction or discussion of another study is not attributed here.
        _detect_methods(f"{t['title']}. {t['abstract']}", "low", seen, max(1, len(t["abstract"]) + 40), min_count=1)
        if body:
            _detect_methods(body, "medium", seen, max(1, len(body)), min_count=2)
        if methods_text:
            _detect_methods(methods_text, "high", seen, max(1, len(methods_text)), min_count=1)
        detected = sorted(seen.values(), key=lambda d: (STAGE_ORDER[d["stage"]], d["position"], -d["count"]))
        pipeline = [{"stage": sid, "label": slabel, "methods": [d for d in detected if d["stage"] == sid]} for sid, slabel in STAGES]
        for d in detected:
            method_items[d["id"]].append(it["id"])
            stage_items[d["stage"]].add(it["id"])
        checks = _reporting(body or t["abstract"])
        for c in checks:
            if c["found"]:
                check_totals[c["id"]] += 1
        papers.append({**_item_brief(it), "source": source, "detected": detected, "pipeline": pipeline, "checks": checks,
                       "missing_stages": [slabel for sid, slabel in STAGES if not any(d["stage"] == sid for d in detected)]})

    n = len(items)
    matrix_methods = sorted(method_items.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    matrix = [{"id": mid, "label": next(m["label"] for m in METHODS if m["id"] == mid),
               "stage": next(m["stage"] for m in METHODS if m["id"] == mid), "items": ids, "share": round(len(ids) / max(1, n), 2)}
              for mid, ids in matrix_methods]
    rare = [m for m in matrix if n >= 3 and len(m["items"]) == 1]
    common = [m for m in matrix if n >= 2 and len(m["items"]) == n]
    absent_stage = [slabel for sid, slabel in STAGES if n and not stage_items.get(sid)]
    subjects_used = {m["label"] for m in matrix if m["stage"] == "subjects"}
    gaps = []
    if n:
        if not {"Human participants"} & subjects_used and subjects_used:
            gaps.append("No human study in this scope: every paper uses animal, cell or computational models.")
        if subjects_used and len(subjects_used) == 1:
            gaps.append(f"A single model system ({next(iter(subjects_used))}): cross-species generalisation is untested.")
        female = check_totals.get("female", 0)
        if female < n:
            gaps.append(f"Female subjects mentioned in {female} of {n} papers: sex as a biological variable is under-covered.")
        for cid, label in (("power", "power analysis / sample-size justification"), ("prereg", "pre-registration"),
                           ("blind", "blinding"), ("data", "data or code availability"), ("random", "randomisation")):
            c = check_totals.get(cid, 0)
            if c < n:
                gaps.append(f"{label[0].upper()}{label[1:]} reported in {c} of {n} papers.")
        for slabel in absent_stage:
            gaps.append(f"No {slabel.lower()} detected in any paper (or the text is not indexed).")
        if rare:
            gaps.append("Used by one paper only: " + ", ".join(m["label"] for m in rare[:8]) + ".")
    return {
        "scope": {"label": scope_label(collection_id, author, tag), "items": n, "collection_id": collection_id, "author": author, "tag": tag},
        "stages": [{"id": sid, "label": slabel, "items": len(stage_items.get(sid, ()))} for sid, slabel in STAGES],
        "papers": papers, "matrix": matrix, "checks": [{"id": c["id"], "label": c["label"], "count": check_totals.get(c["id"], 0)} for c in REPORTING_CHECKS],
        "insights": {"gaps": gaps, "common": [m["label"] for m in common][:12], "rare": [m["label"] for m in rare][:12]},
        "limits": {"no_fulltext": sum(1 for p in papers if p["source"] in ("abstract", "none")),
                   "no_methods_section": sum(1 for p in papers if p["source"] == "full text")},
    }
