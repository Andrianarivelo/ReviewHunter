"""Insight maps over a set of papers: concepts, words, goals and methodology.

All four maps take the same *scope* (whole library, a collection union with its subcollections, an
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
GOAL_TEXT_CHARS = 60_000         # intro/aim scan budget per paper, disclosed in the payload
EVIDENCE_CHARS = 240             # length of an evidence sentence in payloads
METHODS_SECTION_MIN_CHARS = 40   # accept concise, explicitly headed methods sections
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
measure measured measuring receive received receiving examine examined examining perform performed performing
conduct conducted conducting investigate investigated investigating evaluate evaluated evaluating assess assessed assessing
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

def selected_collection_ids(collection_id: Optional[int] = None,
                            collection_ids: Optional[list[int]] = None) -> list[int]:
    """Normalize the requested union; an explicitly supplied list overrides the legacy scalar."""
    values = collection_ids if collection_ids is not None else ([collection_id] if collection_id is not None else [])
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Collection IDs must be positive integers")
    return sorted(set(values))


class ScopedItems(list):
    """An ordinary item list retaining whether the SQL query omitted further matches."""

    def __init__(self, items: list[dict], *, capped: bool = False):
        super().__init__(items)
        self.capped = capped


def _scope_predicate(collection_id: Optional[int], author: Optional[str], tag: Optional[str],
                     include_sub: bool, collection_ids: Optional[list[int]]) -> tuple[str, list]:
    """Build one parameterized predicate for both selected papers and their true complement."""
    selected = selected_collection_ids(collection_id, collection_ids)
    conditions = ["i.deleted_at IS NULL"]
    parameters: list = []
    if selected:
        known = {collection["id"] for collection in catalog.list_collections()}
        missing = set(selected) - known
        if missing:
            raise LookupError("Unknown collection IDs: " + ", ".join(map(str, sorted(missing))))
        expanded = sorted({descendant for cid in selected for descendant in
                           (catalog.collection_descendants(cid) if include_sub else [cid])})
        marks = ",".join("?" for _ in expanded)
        conditions.append(f"EXISTS (SELECT 1 FROM item_collections ic WHERE ic.item_id=i.id AND ic.collection_id IN ({marks}))")
        parameters.extend(expanded)
    if author:
        conditions.append("(i.authors LIKE ? OR i.creators LIKE ?)")
        parameters.extend([f"%{author}%", f"%{author}%"])
    if tag:
        conditions.append("EXISTS (SELECT 1 FROM item_tags it JOIN tags t ON t.id=it.tag_id "
                          "WHERE it.item_id=i.id AND t.name=? COLLATE NOCASE)")
        parameters.append(tag)
    return " AND ".join(conditions), parameters


def scope_items(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
                include_sub: bool = True, *, collection_ids: Optional[list[int]] = None) -> list[dict]:
    """Return the collection union intersected with author/tag filters, capped after deduplication."""
    predicate, parameters = _scope_predicate(collection_id, author, tag, include_sub, collection_ids)
    # EXISTS deduplicates before the global limit, rather than capping each collection independently.
    rows = get_conn().execute("SELECT i.* FROM items i WHERE " + predicate +
                              " ORDER BY i.added_at DESC, i.id DESC LIMIT ?", [*parameters, MAX_ITEMS + 1]).fetchall()
    items = ScopedItems(catalog.decorate_many(catalog.rows_to_dicts(rows[:MAX_ITEMS]), compact=False), capped=len(rows) > MAX_ITEMS)
    for it in items:
        it["tag_names"] = [t["name"] if isinstance(t, dict) else str(t) for t in (it.get("tags") or [])]
    return items


def _comparison_items(collection_id: Optional[int], author: Optional[str], tag: Optional[str],
                      include_sub: bool, collection_ids: Optional[list[int]]) -> ScopedItems:
    """Exclude the complete selected scope before capping comparison papers, including unanalysed members."""
    predicate, parameters = _scope_predicate(collection_id, author, tag, include_sub, collection_ids)
    rows = get_conn().execute("SELECT i.* FROM items i WHERE i.deleted_at IS NULL AND i.id NOT IN "
                              "(SELECT i.id FROM items i WHERE " + predicate + ") "
                              "ORDER BY i.added_at DESC, i.id DESC LIMIT ?", [*parameters, MAX_ITEMS + 1]).fetchall()
    return ScopedItems(catalog.decorate_many(catalog.rows_to_dicts(rows[:MAX_ITEMS]), compact=False), capped=len(rows) > MAX_ITEMS)


def scope_label(collection_id: Optional[int], author: Optional[str], tag: Optional[str], *,
                collection_ids: Optional[list[int]] = None) -> str:
    """Name every selected collection and the intersecting metadata filters."""
    parts = []
    selected = selected_collection_ids(collection_id, collection_ids)
    if selected:
        names = {c["id"]: c["name"] for c in catalog.list_collections()}
        parts.append(" + ".join(names.get(cid, f"Collection {cid}") for cid in selected))
    if author:
        parts.append(author)
    if tag:
        parts.append(f"#{tag}")
    return " / ".join(parts) if parts else "Whole library"


def fulltext_of(item_id: int, max_chars: Optional[int] = None) -> str:
    """Read indexed text, with an optional bounded prefix for introduction/aim analysis."""
    selection = "substr(content, 1, ?)" if max_chars else "content"
    parameters = (max_chars, item_id) if max_chars else (item_id,)
    rows = get_conn().execute(f"SELECT {selection} FROM fulltext_fts WHERE item_id=? ORDER BY attachment_id", parameters)
    chunks = []
    remaining = max_chars
    for row in rows:
        if row[0]:
            chunk = row[0] if remaining is None else row[0][:remaining]
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk) + 1
                if remaining <= 0:
                    break
    return "\n".join(chunks)


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
    return s  # Keep complete evidence, including qualifiers and negation.


def _item_brief(it: dict) -> dict:
    return {"id": it["id"], "title": it.get("title") or "(untitled)", "year": it.get("year"),
            "authors": it.get("authors") or [], "journal": it.get("journal") or it.get("publisher") or ""}


def _texts(items: list[dict], with_fulltext: bool, max_fulltext_chars: Optional[int] = None) -> dict[int, dict]:
    """Per item: title, abstract, tags, terms, fulltext, sections."""
    out = {}
    for it in items:
        ft = fulltext_of(it["id"], max_fulltext_chars) if with_fulltext else ""
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
    out: Counter = Counter()
    # A phrase must exist within one clause rather than joining unrelated sentences or list entries.
    for clause in re.split(r"[.!?;:,\n]+", text or ""):
        toks = _tokens(clause)
        for n in range(1, max_n + 1):
            for i in range(len(toks) - n + 1):
                gram = toks[i:i + n]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or any(len(t) < 3 or t.isdigit() for t in gram):
                    continue
                out[" ".join(gram)] += 1
    return out


def _singular(word: str) -> str:
    """Cheap English singulariser so 'temperatures' and 'temperature' are one concept."""
    w = word
    if w in {"analysis", "hypothesis", "synthesis", "basis", "species", "diabetes", "stress", "mice"}:
        return w
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


def _term_evidence(iid: int, term: str, text: dict, *, normalized: bool = False,
                   source: Optional[str] = None) -> dict:
    """Return the actual sentence or curated metadata value supporting a term."""
    key = _key(term) if normalized else term.lower()
    for field in ([source] if source else ["title", "abstract", "tags", "keywords"]):
        value = text.get(field) or ""
        candidates = value if isinstance(value, list) else sentences(value)
        for sentence in candidates:
            haystack = _key(sentence) if normalized else " ".join(_tokens(sentence))
            if key in haystack or term.casefold() in sentence.casefold():
                return {"id": iid, "source": field, "sentence": sentence}
    # Curated labels need not occur in prose. This value is the source metadata, not an invented sentence.
    return {"id": iid, "source": "metadata", "sentence": term}


def concepts(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
             limit: int = MAX_CONCEPT_NODES, focus: Optional[str] = None, include_sub: bool = True,
             collection_ids: Optional[list[int]] = None) -> dict:
    """Concept co-occurrence graph with evidence and descriptive scope analytics."""
    from .concept_analysis import analyse

    items = scope_items(collection_id, author, tag, include_sub, collection_ids=collection_ids)
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
        per_item[it["id"]] = {k: v for k, v in labels.items() if k in curated or " " in k or k not in multiword_parts}
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
                      "evidence": [_term_evidence(i["id"], per_item[i["id"]][k], texts[i["id"]], normalized=True)
                                   for i in term_items[k]],
                      "items": term_items[k], "related": [{"label": l, "weight": w} for l, w in related]})
    all_selected_edges = sorted(({"source": a, "target": b, "weight": w} for (a, b), w in edges.items() if a in sel and b in sel),
                                key=lambda e: (-e["weight"], e["source"], e["target"]))
    edge_payload = all_selected_edges[:400]
    analytics = analyse(nodes, all_selected_edges, [_item_brief(it) for it in items], total_terms=len(counts),
                        displayed_edges=len(edge_payload), scope_capped=getattr(items, "capped", False))
    unique = [label_by_key[k] for k, c in counts.items() if c == 1 and k in curated][:20]
    return {
        "scope": {"label": scope_label(collection_id, author, tag, collection_ids=collection_ids), "items": len(items), "collection_id": collection_id,
                  "collection_ids": selected_collection_ids(collection_id, collection_ids),
                  "author": author, "tag": tag, "focus": focus or "", "include_sub": include_sub},
        "nodes": nodes, "edges": edge_payload, "clusters": cluster_payload, "total_terms": len(counts),
        "analytics": analytics,
        "insights": {"unique_terms": unique,
                     "isolated_items": [_item_brief(it) for it in items if not per_item.get(it["id"])][:10]},
        "limits": {"no_abstract": sum(1 for it in items if not (it.get("abstract") or "").strip()),
                   "scope_capped": getattr(items, "capped", False), "max_items": MAX_ITEMS},
    }


# ---------------------------------------------------------------------------- words

def words(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
          source: str = "abstract", limit: int = MAX_WORDS, include_sub: bool = True,
          collection_ids: Optional[list[int]] = None) -> dict:
    """Map source terms with lexical themes and separate frequent, rare and local-outlier lists."""
    from .word_concepts import categories, classify_term, semantics, term_flags

    items = scope_items(collection_id, author, tag, include_sub, collection_ids=collection_ids)
    use_full = source == "fulltext"
    texts = _texts(items, with_fulltext=use_full)
    limit = max(20, min(limit, 400))

    def text_of(iid: int) -> str:
        t = texts[iid]
        body = t["fulltext"] if use_full else t["abstract"]
        if use_full and t["sections"].get("references"):
            body = body.replace(t["sections"]["references"], " ")
        return body

    total: Counter = Counter()
    per_word_items: dict[str, set[int]] = defaultdict(set)
    for it in items:
        c = _phrases(text_of(it["id"]), max_n=2)
        total.update(c)
        for w in c:
            per_word_items[w].add(it["id"])
    # drop a bigram's unigrams when the bigram carries most of their occurrences? keep both; UI can filter by length.
    rest_counts: Counter = Counter()
    rest_word_items: Counter = Counter()
    others = []
    comparison_capped = False
    subset = bool(selected_collection_ids(collection_id, collection_ids) or author or tag)
    if subset:
        others = _comparison_items(collection_id, author, tag, include_sub, collection_ids)
        comparison_capped = others.capped
        texts.update(_texts(others, with_fulltext=use_full))
        for o in others:
            counts = _phrases(text_of(o["id"]), max_n=2)
            rest_counts.update(counts)
            rest_word_items.update(counts.keys())
    n_scope = max(1, sum(total.values()))
    n_rest = max(1, sum(rest_counts.values()))
    comparison_available = bool(subset and rest_counts)
    scores = {}
    flags = {}
    for w, c in total.items():
        # Smoothed log odds of term occurrence versus every other candidate term.
        other_c = rest_counts.get(w, 0)
        distinct = (math.log((c + 0.5) / (n_scope - c + 0.5)) -
                    math.log((other_c + 0.5) / (n_rest - other_c + 0.5))) if comparison_available else 0.0
        scores[w] = distinct
        flags[w] = term_flags(c, len(per_word_items[w]), other_c, rest_word_items[w], distinct, comparison_available)

    # Select each view from the entire vocabulary. In particular, singleton terms
    # must survive independently of the frequent list's cap rather than disappearing
    # before the user chooses the rare-terms view.
    ranked = sorted(total, key=lambda word: (-total[word], word))
    frequent = [word for word in ranked if total[word] >= 2 or len(items) == 1]
    # Single words first: an incidental one-off word pair must not crowd a genuinely
    # single-occurrence word out of the rare list. Ties are alphabetical and stable.
    rare = sorted((word for word in total if flags[word]["rare"]), key=lambda word: (" " in word, word))
    unusual = sorted((word for word in total if flags[word]["unusual"]),
                     key=lambda word: (-scores[word], -len(per_word_items[word]), -total[word], word))
    payloads = {}
    for w in dict.fromkeys(frequent[:limit] + rare[:limit] + unusual[:limit]):
        payloads[w] = {"word": w, "count": total[w], "items": sorted(per_word_items[w]), "n_items": len(per_word_items[w]),
                    "evidence": [_term_evidence(iid, w, texts[iid], source="fulltext" if use_full else "abstract")
                                 for iid in sorted(per_word_items[w])],
                    "distinct": round(scores[w], 2), "bigram": " " in w,
                    **classify_term(w), **flags[w]}
    briefs = {it["id"]: _item_brief(it) for it in items}
    return {
        "scope": {"label": scope_label(collection_id, author, tag, collection_ids=collection_ids), "items": len(items), "collection_id": collection_id,
                  "collection_ids": selected_collection_ids(collection_id, collection_ids),
                  "author": author, "tag": tag, "source": "fulltext" if use_full else "abstract", "include_sub": include_sub,
                  "comparison_available": comparison_available, "comparison_items": len(others),
                  "comparison_items_with_text": sum(bool(text_of(it["id"]).strip()) for it in others)},
        "words": [payloads[w] for w in frequent[:limit]],
        "rare_words": [payloads[w] for w in rare[:limit]],
        "outlier_words": [payloads[w] for w in unusual[:limit]],
        "items": briefs, "concept_categories": categories(), "semantics": semantics(),
        "candidate_counts": {"frequent": len(frequent), "rare": len(rare), "unusual": len(unusual), "total": len(total)},
        "limits": {"scope_capped": getattr(items, "capped", False), "max_items": MAX_ITEMS,
                   "comparison_capped": comparison_capped,
                   "list_limit": limit, "frequent_capped": len(frequent) > limit,
                   "rare_capped": len(rare) > limit, "outlier_capped": len(unusual) > limit,
                   "no_fulltext": sum(1 for it in items if use_full and not texts[it["id"]]["fulltext"]),
                   "no_abstract": sum(1 for it in items if not texts[it["id"]]["abstract"].strip())},
    }


# ---------------------------------------------------------------------------- goals

def _clean_phrase(s: str) -> str:
    """Compatibility helper for validated, source-derived goal noun phrases."""
    from .goal_extraction import clean_phrase
    return clean_phrase(s)


def _goal_type(sentence: str) -> str:
    """Compatibility helper for explicit-aim classification."""
    from .goal_extraction import goal_type
    return goal_type(sentence)


def goals(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
          include_sub: bool = True, collection_ids: Optional[list[int]] = None) -> dict:
    """Represent every paper's research focus, with optional source-backed relationships."""
    from .goal_extraction import extract_goal
    from .result_extraction import MAX_FINDINGS, compare_findings, extract_findings, related_outcomes

    items = scope_items(collection_id, author, tag, include_sub, collection_ids=collection_ids)
    texts = _texts(items, with_fulltext=True, max_fulltext_chars=GOAL_TEXT_CHARS)
    papers = []
    factors: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    links: dict[tuple[str, str], dict] = {}
    type_counts: Counter = Counter()
    theme_counts: Counter = Counter()
    theme_labels = {}
    for item in items:
        text = texts[item["id"]]
        extracted = extract_goal(text["title"], text["abstract"], text["sections"].get("introduction", ""),
                                 text["fulltext"], splitter=sentences, max_aims=MAX_GOAL_SENTENCES)
        paper = {**_item_brief(item), **extracted}
        findings = extract_findings(text["title"], text["abstract"], text["sections"].get("results", ""), splitter=sentences)
        paper["findings"] = [{**finding, "paper_id": item["id"]} for finding in findings]
        paper["findings_capped"] = findings.capped
        papers.append(paper)
        type_counts[paper["goal_type"]] += 1
        for theme in paper["themes"]:
            theme_counts[theme["id"]] += 1
            theme_labels[theme["id"]] = theme["label"]
        for pair in paper["pairs"]:
            key = (pair["factor"], pair["outcome"])
            factors.setdefault(key[0], {"id": key[0], "label": key[0], "items": set()})["items"].add(item["id"])
            outcomes.setdefault(key[1], {"id": key[1], "label": key[1], "items": set()})["items"].add(item["id"])
            link = links.setdefault(key, {"factor": key[0], "outcome": key[1], "items": [], "kind": pair["kind"]})
            link["items"].append({"id": item["id"], "title": item.get("title") or "", "evidence": pair["evidence"],
                                  "source": pair["source"], "confidence": pair["confidence"],
                                  "negated": pair["negated"], "relation_label": pair["relation_label"]})

    for link in links.values():
        polarities = {member["negated"] for member in link["items"]}
        labels = {member["relation_label"] for member in link["items"]}
        link["negated"] = polarities == {True}
        link["relation_label"] = next(iter(labels)) if len(labels) == 1 else "Mixed statements"

    def nodes(values: dict) -> list[dict]:
        """Aggregate only validated relationships, leaving unpaired papers in the atlas."""
        return sorted(({"id": value["id"], "label": value["label"], "count": len(value["items"]),
                        "items": sorted(value["items"])} for value in values.values()),
                      key=lambda node: (-node["count"], node["label"]))

    return {
        "scope": {"label": scope_label(collection_id, author, tag, collection_ids=collection_ids), "items": len(items),
                  "collection_id": collection_id, "collection_ids": selected_collection_ids(collection_id, collection_ids),
                  "author": author, "tag": tag, "include_sub": include_sub},
        "papers": papers, "factors": nodes(factors), "outcomes": nodes(outcomes),
        "result_comparisons": compare_findings(papers),
        "related_outcomes": related_outcomes(papers),
        "links": sorted(links.values(), key=lambda link: (-len(link["items"]), link["factor"], link["outcome"])),
        "goal_types": [{"kind": kind, "count": count} for kind, count in type_counts.most_common()],
        "themes": [{"id": key, "label": theme_labels[key], "count": count} for key, count in theme_counts.most_common()],
        "coverage": {"papers": len(papers), "explicit_aim": sum(p["focus_status"] == "explicit_aim" for p in papers),
                     "title_focus": sum(p["focus_status"] == "title_focus" for p in papers),
                     "unavailable": sum(p["focus_status"] == "unavailable" for p in papers),
                     "with_findings": sum(bool(p["findings"]) for p in papers),
                     "with_relationships": sum(bool(p["pairs"]) for p in papers)},
        "insights": {"single_paper_outcomes": [node["label"] for node in nodes(outcomes) if node["count"] == 1],
                     "no_aim_found": [paper["title"] for paper in papers if paper["focus_status"] != "explicit_aim"]},
        "limits": {"scope_capped": getattr(items, "capped", False), "max_items": MAX_ITEMS,
                   "no_abstract": sum(not texts[item["id"]]["abstract"].strip() for item in items),
                   "fulltext_scan_chars": GOAL_TEXT_CHARS,
                   "findings_per_paper": MAX_FINDINGS,
                   "findings_capped": sum(p["findings_capped"] for p in papers),
                   "result_interpretation": "Reported directions are extracted from abstracts and detected Results sections within the indexed-text scan. Compatible comparisons require matching stated drugs, species, temperatures, doses, routes, comparators and remaining detected context; papers reaching the finding limit are excluded. Shared-outcome groups span different experiments and are not direct comparisons. Counts are descriptive; minority directions are not statistical outliers and missing extraction is not evidence of no effect.",
                   "interpretation": "Explicit aims come from abstracts, introductions and unheaded introductory text within the first 60000 indexed characters; explicit retrospective statements of study purpose may also come from Discussion or Conclusion sections. A title-derived focus is labelled separately and is not an invented aim. Relationships are stated links, not proof of causation; papers without a valid pair remain in the atlas."},
    }


# ---------------------------------------------------------------------------- methods

_SEQUENCE_RX = re.compile(r"\b(?:first|then|subsequently|after training|on day \d+|finally)\b", re.I)
_NEGATION_RX = re.compile(r"\b(?:not|never|without|no|neither)\b", re.I)
_NULL_RESULT_RX = re.compile(
    r"\b(?:no|not|without)\s+(?:(?:statistically|significant(?:ly)?|any|a|an|detectable|measurable)\s+)*"
    r"(?:differences?|effects?|changes?|associations?|correlations?|evidence of an? effect)\b", re.I)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Keep raw offsets while treating wrapped PDF lines as sentence whitespace."""
    spans, start = [], 0
    for boundary in _SENT_SPLIT_RX.finditer(text):
        if _ABBREV_RX.search(text[max(start, boundary.start() - 16):boundary.start()]):
            continue
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))
    return spans


def _sentence_at(text: str, start: int, end: int, spans: Optional[list[tuple[int, int]]] = None) -> str:
    """Find full evidence across PDF line wraps, retaining decimals and qualifiers."""
    for left, right in spans if spans is not None else _sentence_spans(text):
        if left <= start < right:
            return re.sub(r"\s+", " ", text[left:right]).strip()
    return re.sub(r"\s+", " ", text[start:end]).strip()


def _negated(text: str, hit: re.Match) -> bool:
    """Reject local absence, preserving subjects and assays named in null results.

    This is a conservative lexical check, not grammatical parsing. A null result
    such as 'no significant differences in rats' describes a result, and must
    not remove the animals or the assay that produced it. Prefix negation in
    'non-randomized' still denies the corresponding design/reporting cue.
    """
    before = re.split(r"[.;,:\n]|\b(?:but|whereas)\b", text[max(0, hit.start() - 70):hit.start()])[-1]
    if re.search(r"\b(?:non|un)[-\s]?$", before, re.I):
        return True
    # Remove only the negated result noun phrase, retaining any separate denial.
    before = _NULL_RESULT_RX.sub("", before)
    after = text[hit.end():hit.end() + 65]
    return bool(_NEGATION_RX.search(before) or re.match(
        r"\s+(?:(?:was|were|is|are)\s+)?(?:not|never)\s+"
        r"(?:(?:actually|routinely|systematically)\s+)?(?:used|performed|conducted|employed|applied|included|"
        r"recruited|required|obtained|sought|provided|reported|recorded|measured|monitored|imaged|implanted|inserted|"
        r"implemented|undertaken|available|carried out)\b|"
        r"\s+(?:was|were)\s+(?:omitted|excluded)\b", after, re.I))


def _detect_methods(text: str, confidence: str, seen: dict[str, dict], scale: int, min_count: int = 1) -> None:
    """Run every taxonomy regex over ``text``; keep the strongest hit per method (with its sentence).

    ``min_count`` guards against passing mentions: a rat paper that cites a monkey study once should
    not be tagged "Non-human primates". Author-chosen text (abstract, methods section) uses min_count 1;
    the body of the paper (introduction + discussion included) requires the method to appear at least twice.
    """
    if not text:
        return
    from .method_details import detail_has_context

    # PDF layout line endings are not grammatical boundaries. Match an
    # ephemeral normalized view, leaving indexed text and metadata untouched.
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    spans = _sentence_spans(text)
    for m in METHODS:
        hits = [hit for hit in m["regex"].finditer(text) if not _negated(text, hit)
                and (not m.get("context_required") or detail_has_context(
                    _sentence_at(text, hit.start(), hit.end(), spans), hit.group(0), m["category"], confidence))]
        count = len(hits)
        if count < min_count:
            continue
        hit = hits[0] if hits else None
        if not hit:
            continue
        start = hit.start()
        sentence = _sentence_at(text, start, hit.end(), spans)
        sequence = _SEQUENCE_RX.search(sentence)
        entry = seen.get(m["id"])
        rank = {"high": 3, "medium": 2, "low": 1}
        if entry is None or rank[confidence] > rank[entry["confidence"]]:
            seen[m["id"]] = {"id": m["id"], "label": m["label"], "stage": m["stage"], "confidence": confidence,
                             "category": m.get("category", m["stage"]),
                             "count": count, "position": round(start / max(1, scale), 4), "evidence": _evidence(sentence),
                             "source": {"high": "methods section", "medium": "full text", "low": "abstract"}[confidence],
                             "sequence_cue": sequence.group(0) if sequence else "",
                             "match": hit.group(0)}
        elif entry is not None and rank[confidence] == rank[entry["confidence"]]:
            entry["count"] = max(entry["count"], count)


def _reporting(text: str) -> list[dict]:
    """Separate detected reporting from explicit absence and unavailable evidence."""
    out = []
    spans = _sentence_spans(text or "")
    for chk in REPORTING_CHECKS:
        candidates = list(chk["regex"].finditer(text or ""))
        hit = next((h for h in candidates if not _negated(text, h)), None)
        evidence_hit = hit or (candidates[0] if candidates else None)
        ev = ""
        if evidence_hit:
            ev = _sentence_at(text, evidence_hit.start(), evidence_hit.end(), spans)
        out.append({"id": chk["id"], "label": chk["label"], "found": bool(hit), "evidence": ev,
                    "status": "detected" if hit else ("explicitly_absent" if candidates else
                              ("not_detected" if (text or "").strip() else "unavailable"))})
    return out


def methods(collection_id: Optional[int] = None, author: Optional[str] = None, tag: Optional[str] = None,
            include_sub: bool = True, collection_ids: Optional[list[int]] = None) -> dict:
    """Methodology map: per-paper pipelines with evidence, reporting checks, matrix and gaps for the scope."""
    items = scope_items(collection_id, author, tag, include_sub, collection_ids=collection_ids)
    texts = _texts(items, with_fulltext=True)
    papers = []
    method_items: dict[str, list[int]] = defaultdict(list)
    stage_items: dict[str, set[int]] = defaultdict(set)
    check_totals: Counter = Counter()
    check_absent: Counter = Counter()
    for it in items:
        t = texts[it["id"]]
        seen: dict[str, dict] = {}
        body = t["fulltext"]
        if t["sections"].get("references"):
            body = body.replace(t["sections"]["references"], " ")
        methods_text = t["sections"].get("methods", "")
        source = "methods section" if methods_text else ("full text" if body.strip() else ("abstract" if t["abstract"].strip() else "none"))
        # Author-chosen text (abstract, methods section) counts from one mention; the whole body needs two,
        # so a method named once in the introduction or discussion of another study is not attributed here.
        _detect_methods(t["abstract"], "low", seen, max(1, len(t["abstract"])), min_count=1)
        if body and not methods_text:
            _detect_methods(body, "medium", seen, max(1, len(body)), min_count=2)
        if methods_text:
            _detect_methods(methods_text, "high", seen, max(1, len(methods_text)), min_count=1)
        detected = sorted(seen.values(), key=lambda d: (STAGE_ORDER[d["stage"]], d["position"], -d["count"]))
        pipeline = [{"stage": sid, "label": slabel, "methods": [d for d in detected if d["stage"] == sid]} for sid, slabel in STAGES]
        for d in detected:
            method_items[d["id"]].append(it["id"])
            stage_items[d["stage"]].add(it["id"])
        checks = _reporting(body if body.strip() else t["abstract"])
        for c in checks:
            if c["found"]:
                check_totals[c["id"]] += 1
            if c["status"] == "explicitly_absent":
                check_absent[c["id"]] += 1
        papers.append({**_item_brief(it), "source": source, "detected": detected, "pipeline": pipeline, "checks": checks,
                       "missing_stages": [slabel for sid, slabel in STAGES if not any(d["stage"] == sid for d in detected)]})

    n = len(items)
    assessed = sum(1 for p in papers if p["source"] != "none")
    taxonomy = {m["id"]: m for m in METHODS}
    matrix_methods = sorted(method_items.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    matrix = [{"id": mid, "label": taxonomy[mid]["label"], "stage": taxonomy[mid]["stage"],
               "category": taxonomy[mid].get("category", taxonomy[mid]["stage"]),
               "items": ids, "share": round(len(ids) / max(1, n), 2)}
              for mid, ids in matrix_methods]
    rare = [m for m in matrix if n >= 3 and len(m["items"]) == 1]
    common = [m for m in matrix if n >= 2 and len(m["items"]) == n]
    absent_stage = [slabel for sid, slabel in STAGES if n and not stage_items.get(sid)]
    subjects_used = {m["label"] for m in matrix if m["stage"] == "subjects"}
    gaps = []
    if n:
        if not {"Human participants"} & subjects_used and subjects_used:
            gaps.append("Human participants were not detected in the available text; absence of a mention does not establish that a study excluded humans.")
        if subjects_used and len(subjects_used) == 1:
            gaps.append(f"Only one model category detected ({next(iter(subjects_used))}); verify model diversity in the source papers.")
        female = check_totals.get("female", 0)
        if female < n:
            gaps.append(f"Female subjects detected in {female} of {n} papers; missing mentions do not establish exclusion.")
        for cid, label in (("power", "power analysis / sample-size justification"), ("prereg", "pre-registration"),
                           ("blind", "blinding"), ("data", "data or code availability"), ("random", "randomisation")):
            c = check_totals.get(cid, 0)
            if c < n:
                gaps.append(f"{label[0].upper()}{label[1:]} detected in {c} of {n} papers' available text.")
        for slabel in absent_stage:
            gaps.append(f"No {slabel.lower()} detected in any paper (or the text is not indexed).")
        if rare:
            gaps.append("Detected in one paper only: " + ", ".join(m["label"] for m in rare[:8]) + ".")
    return {
        "scope": {"label": scope_label(collection_id, author, tag, collection_ids=collection_ids), "items": n,
                  "collection_id": collection_id, "collection_ids": selected_collection_ids(collection_id, collection_ids),
                  "author": author, "tag": tag, "include_sub": include_sub},
        "stages": [{"id": sid, "label": slabel, "items": len(stage_items.get(sid, ()))} for sid, slabel in STAGES],
        "papers": papers, "matrix": matrix, "checks": [{"id": c["id"], "label": c["label"], "count": check_totals.get(c["id"], 0),
            "assessed": assessed, "unavailable": n - assessed, "explicitly_absent": check_absent.get(c["id"], 0)} for c in REPORTING_CHECKS],
        "insights": {"gaps": gaps, "common": [m["label"] for m in common][:12], "rare": [m["label"] for m in rare][:12]},
        "limits": {"scope_capped": getattr(items, "capped", False), "max_items": MAX_ITEMS,
                   "no_text": n - assessed,
                   "no_fulltext": sum(1 for p in papers if p["source"] in ("abstract", "none")),
                   "no_methods_section": sum(1 for p in papers if p["source"] != "methods section"),
                   "no_abstract": sum(1 for it in items if not texts[it["id"]]["abstract"].strip()),
                   "interpretation": "Not detected does not mean not performed. Pipeline stage order is canonical; explicit sequence cues do not establish a complete chronology."},
    }
