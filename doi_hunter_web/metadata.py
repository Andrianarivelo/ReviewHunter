"""CrossRef client + heuristic categorization. Embeddings are optional.

Identifying the polite-pool mailto via REVIEWHUNTER_MAILTO env var bumps our
CrossRef rate-limit tier; if unset we still work, just in the public pool.
"""
import os
import re
import time
import unicodedata
from collections import Counter
from html import unescape
from typing import Optional
from urllib.parse import quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

CROSSREF_API = "https://api.crossref.org"
PUBMED_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Trimmed English stopword list — avoids pulling NLTK just for keyword extraction.
_STOP = set("""
a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing down during
each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no
nor not now of off on once only or other our ours ourselves out over own same
she should so some such than that the their theirs them themselves then there
these they this those through to too under until up very was we were what when
where which while who whom why will with would you your yours yourself yourselves
based using approach approaches study studies analysis paper review novel new
toward towards via we our its their from also however thus
""".split())

JATS_RE = re.compile(r"</?jats:[^>]+>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _mailto() -> Optional[str]:
    return os.environ.get("REVIEWHUNTER_MAILTO") or os.environ.get("DOIHUNTER_MAILTO") or None


def _headers() -> dict:
    ua = "ReviewHunter/0.1"
    m = _mailto()
    if m:
        ua += f" (mailto:{m})"
    return {"User-Agent": ua, "Accept": "application/json"}


def _ascii_lower(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]+", " ", s.lower()).strip()


def _clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = unescape(s)
    s = JATS_RE.sub(" ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = WHITESPACE_RE.sub(" ", s).strip()
    return s


def _trim_doi(s: str) -> str:
    doi = (s or "").strip().strip("<>\"'")
    doi = re.sub(r"[\s?#].*$", "", doi)
    return doi.rstrip(".,;:)]}").lower()


def extract_doi(text: str) -> Optional[str]:
    """Return a normalized DOI from raw DOI text, DOI URLs, or embedded text."""
    raw = unquote((text or "").strip())
    if not raw:
        return None

    lowered = raw.lower()
    for prefix in ("doi:", "doi "):
        if lowered.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break

    parsed = urlparse(raw)
    if parsed.netloc and parsed.netloc.lower() in {"doi.org", "dx.doi.org"}:
        doi = _trim_doi(parsed.path.lstrip("/"))
        return doi if DOI_RE.match(doi) else None

    m = DOI_RE.search(raw)
    return _trim_doi(m.group(1)) if m else None


def looks_like_url(s: str) -> bool:
    return bool(URL_RE.match((s or "").strip()))


def _meta_content(soup: BeautifulSoup, names: tuple[str, ...]) -> str:
    wanted = {n.lower() for n in names}
    for tag in soup.find_all("meta"):
        key = (tag.get("name") or tag.get("property") or "").strip().lower()
        if key in wanted:
            content = (tag.get("content") or "").strip()
            if content:
                return content
    return ""


def fetch_url_metadata(url: str) -> Optional[dict]:
    """Resolve a publisher/landing-page URL to CrossRef metadata when possible."""
    if not looks_like_url(url):
        return None

    doi = extract_doi(url)
    if doi:
        return fetch_doi(doi)

    try:
        headers = {**_headers(), "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code >= 400:
            return None
        content_type = (r.headers.get("content-type") or "").lower()
        if content_type and "html" not in content_type and "xml" not in content_type:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    doi = extract_doi(_meta_content(soup, (
        "citation_doi",
        "dc.identifier",
        "dc.identifier.doi",
        "dc.doi",
        "prism.doi",
        "doi",
    )))
    if doi:
        meta = fetch_doi(doi)
        if meta:
            return meta

    title = _meta_content(soup, ("citation_title", "og:title", "twitter:title"))
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if title:
        candidates = search_crossref(_clean_text(title), rows=1)
        if candidates:
            return candidates[0]
    return None


def _query_tokens(query: str) -> list[str]:
    return [
        t
        for t in WORD_RE.findall(_ascii_lower(query))
        if t not in _STOP and len(t) > 2
    ]


def _contains_token(text: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def strip_exact_quotes(query: str) -> str:
    q = (query or "").strip()
    if len(q) >= 2 and q[0] == q[-1] and q[0] in {'"', "'"}:
        return q[1:-1].strip()
    return q


def is_exact_query(query: str) -> bool:
    q = (query or "").strip()
    return len(q) >= 2 and q[0] == q[-1] and q[0] in {'"', "'"}


def exact_phrase_matches(item: dict, query: str) -> bool:
    phrase = _ascii_lower(strip_exact_quotes(query))
    if not phrase:
        return True
    haystack = _ascii_lower(" ".join([
        item.get("title") or "",
        item.get("abstract") or "",
        item.get("journal") or "",
        item.get("publisher") or "",
        item.get("doi") or "",
        " ".join(item.get("authors") or []),
        " ".join(item.get("subjects") or []),
        " ".join(item.get("keywords") or []),
    ]))
    return phrase in haystack


def filter_exact_results(results: list[dict], query: str) -> list[dict]:
    return [r for r in results if exact_phrase_matches(r, query)]


def title_contains_all_words(item: dict, query: str) -> bool:
    tokens = _query_tokens(strip_exact_quotes(query))
    if not tokens:
        return True
    title = _ascii_lower(item.get("title") or "")
    return all(_contains_token(title, token) for token in tokens)


def filter_title_contains_results(results: list[dict], query: str) -> list[dict]:
    return [r for r in results if title_contains_all_words(r, query)]


def relevance_score(item: dict, query: str, raw_score: float | int | None = None) -> float:
    """Stable local relevance score used to order discovery results."""
    clean_query = strip_exact_quotes(query)
    q_norm = _ascii_lower(clean_query)
    tokens = _query_tokens(clean_query)
    if not q_norm or not tokens:
        return float(raw_score or 0)

    title = _ascii_lower(item.get("title") or "")
    abstract = _ascii_lower(item.get("abstract") or "")
    journal = _ascii_lower(item.get("journal") or item.get("publisher") or "")
    authors = _ascii_lower(" ".join(item.get("authors") or []))
    subjects = _ascii_lower(" ".join(item.get("subjects") or []))
    doi = _ascii_lower(item.get("doi") or "")

    score = 0.0
    if raw_score:
        # CrossRef scores are not normalized; log-like damping keeps them useful
        # without letting them overpower exact title/query matches.
        try:
            score += min(24.0, float(raw_score) ** 0.5)
        except (TypeError, ValueError):
            pass

    if q_norm and q_norm in title:
        score += 60
    elif q_norm and q_norm in abstract:
        score += 18
    if q_norm and q_norm in journal:
        score += 8
    if q_norm and q_norm in authors:
        score += 8
    if q_norm and q_norm in doi:
        score += 80

    matched_any = 0
    matched_title = 0
    for token in tokens:
        found = False
        if _contains_token(title, token):
            score += 10
            matched_title += 1
            found = True
        if _contains_token(abstract, token):
            score += 2
            found = True
        if _contains_token(subjects, token):
            score += 4
            found = True
        if _contains_token(journal, token):
            score += 2
            found = True
        if _contains_token(authors, token):
            score += 2
            found = True
        if found:
            matched_any += 1

    coverage = matched_any / max(1, len(tokens))
    title_coverage = matched_title / max(1, len(tokens))
    score += coverage * 22 + title_coverage * 24
    return round(score, 4)


def sort_results(results: list[dict], query: str, result_sort: str = "pertinence") -> list[dict]:
    result_sort = (result_sort or "pertinence").lower()
    out = []
    for item in results:
        item = dict(item)
        if "relevance_score" not in item:
            item["relevance_score"] = relevance_score(item, query)
        out.append(item)
    if result_sort in {"pertinence", "relevance", "best"}:
        return sorted(out, key=lambda x: (x.get("relevance_score") or 0, x.get("year") or 0), reverse=True)
    if result_sort == "newest":
        return sorted(out, key=lambda x: (x.get("year") or 0, x.get("relevance_score") or 0), reverse=True)
    if result_sort == "oldest":
        return sorted(out, key=lambda x: (x.get("year") or 9999, -(x.get("relevance_score") or 0)))
    if result_sort == "title":
        return sorted(out, key=lambda x: (x.get("title") or "").casefold())
    return out


def _normalize_work(work: dict, *, query: str = "") -> dict:
    """Map a CrossRef work object to our DB shape."""
    title_list = work.get("title") or []
    title = _clean_text(title_list[0]) if title_list else ""

    authors = []
    for a in work.get("author") or []:
        family = a.get("family") or ""
        given = a.get("given") or ""
        if family and given:
            authors.append(f"{family}, {given}")
        elif family:
            authors.append(family)
        elif a.get("name"):
            authors.append(a["name"])

    issued = work.get("issued", {}).get("date-parts") or [[None]]
    year = issued[0][0] if issued and issued[0] else None
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None

    container = work.get("container-title") or []
    journal = container[0] if container else ""

    item = {
        "doi": (work.get("DOI") or "").lower() or None,
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "publisher": work.get("publisher") or "",
        "volume": work.get("volume") or "",
        "issue": work.get("issue") or "",
        "pages": work.get("page") or "",
        "abstract": _clean_text(work.get("abstract")),
        "subjects": list(work.get("subject") or []),
        "url": work.get("URL") or "",
        "landing_url": work.get("URL") or "",
    }
    for link in work.get("link") or []:
        url = link.get("URL") or link.get("url") or ""
        content_type = (link.get("content-type") or link.get("content_type") or "").lower()
        if url and ("pdf" in content_type or url.lower().split("?", 1)[0].endswith(".pdf")):
            item["pdf_url"] = url
            break
    item["relevance_score"] = relevance_score(item, query, work.get("score"))
    return item


def _pubmed_author_name(name: str) -> str:
    parts = (name or "").split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]+", parts[-1]):
        return f"{' '.join(parts[:-1])}, {parts[-1]}"
    return name or ""


def _normalize_pubmed_summary(rec: dict) -> dict:
    doi = None
    for aid in rec.get("articleids") or []:
        if aid.get("idtype") == "doi":
            doi = (aid.get("value") or "").lower() or None
            break
    title = _clean_text(rec.get("title"))
    authors = [_pubmed_author_name(a.get("name", "")) for a in rec.get("authors") or []]
    authors = [a for a in authors if a]
    pubdate = rec.get("pubdate") or rec.get("epubdate") or ""
    year_match = re.search(r"\b(18|19|20)\d{2}\b", pubdate)
    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "year": int(year_match.group(0)) if year_match else None,
        "journal": rec.get("fulljournalname") or rec.get("source") or "",
        "publisher": "PubMed",
        "volume": rec.get("volume") or "",
        "issue": rec.get("issue") or "",
        "pages": rec.get("pages") or "",
        "abstract": "",
        "subjects": list(rec.get("pubtype") or []),
        "pmid": rec.get("uid"),
    }


def search_pubmed_author(query: str, rows: int = 25, offset: int = 0) -> Optional[dict]:
    """Strict PubMed author search. Returns None when PubMed is unavailable."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": rows,
        "retstart": offset,
        "sort": "pub date",
        "tool": "ReviewHunter",
    }
    if _mailto():
        params["email"] = _mailto()
    try:
        r = requests.get(f"{PUBMED_API}/esearch.fcgi", params=params, headers=_headers(), timeout=20)
        if r.status_code == 429:
            time.sleep(0.4)
            r = requests.get(f"{PUBMED_API}/esearch.fcgi", params=params, headers=_headers(), timeout=20)
        r.raise_for_status()
        result = r.json().get("esearchresult", {})
        ids = result.get("idlist") or []
        total = int(result.get("count") or 0)
        if not ids:
            return {"results": [], "total": total, "source": "PubMed"}

        sparams = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
            "tool": "ReviewHunter",
        }
        if _mailto():
            sparams["email"] = _mailto()
        sr = requests.get(f"{PUBMED_API}/esummary.fcgi", params=sparams, headers=_headers(), timeout=20)
        if sr.status_code == 429:
            time.sleep(0.4)
            sr = requests.get(f"{PUBMED_API}/esummary.fcgi", params=sparams, headers=_headers(), timeout=20)
        sr.raise_for_status()
        data = sr.json().get("result", {})
        results = [_normalize_pubmed_summary(data[pmid]) for pmid in ids if pmid in data]
        return {"results": results, "total": total, "source": "PubMed"}
    except Exception:
        return None


def _parse_author_query(query: str) -> tuple[str, list[str]]:
    q = _ascii_lower(query)
    if not q:
        return "", []
    if "," in query:
        left, right = query.split(",", 1)
        family = _ascii_lower(left)
        given = _ascii_lower(right).split()
    else:
        parts = q.split()
        if len(parts) == 1:
            return parts[0], []
        family = parts[-1]
        given = parts[:-1]
    return family, given


def _initials(words: list[str]) -> str:
    return "".join(w[0] for w in words if w)


def _author_matches_query(author: dict, family: str, given_query: list[str]) -> bool:
    afamily = _ascii_lower(author.get("family") or author.get("name") or "")
    if afamily != family:
        return False
    if not given_query:
        return True

    agiven_words = _ascii_lower(author.get("given") or "").split()
    if not agiven_words:
        return False

    query_initials = _initials(given_query)
    author_initials = _initials(agiven_words)
    for i, qword in enumerate(given_query):
        if len(qword) == 1:
            if i >= len(author_initials) or author_initials[i] != qword:
                return False
            continue
        if not any(
            len(gword) > 1 and (gword == qword or gword.startswith(qword) or qword.startswith(gword))
            for gword in agiven_words
        ):
            return False
    return not query_initials or author_initials.startswith(query_initials[: len(author_initials)])


def search_crossref_author_exact(query: str, rows: int = 25, offset: int = 0, result_sort: str = "newest") -> dict:
    """CrossRef fallback that only keeps works with the requested person in authors."""
    family, given = _parse_author_query(query)
    if not family:
        return {"results": [], "total": 0, "source": "CrossRef exact author"}

    scan_limit = int(
        os.environ.get("REVIEWHUNTER_AUTHOR_SCAN_LIMIT")
        or os.environ.get("DOIHUNTER_AUTHOR_SCAN_LIMIT", "1000")
        or "1000"
    )
    page_size = 100
    filtered: list[dict] = []
    seen: set[str] = set()
    raw_total = 0
    scanned = 0

    while scanned < scan_limit:
        params = {
            "query.author": family,
            "rows": min(page_size, scan_limit - scanned),
            "offset": scanned,
            "sort": "published",
            "order": "desc",
        }
        if _mailto():
            params["mailto"] = _mailto()
        try:
            r = requests.get(f"{CROSSREF_API}/works", params=params, headers=_headers(), timeout=20)
            r.raise_for_status()
            msg = r.json().get("message", {})
        except Exception:
            break

        raw_total = int(msg.get("total-results", 0) or 0)
        items = msg.get("items", [])
        if not items:
            break
        scanned += len(items)

        for w in items:
            if not any(_author_matches_query(a, family, given) for a in w.get("author") or []):
                continue
            key = (w.get("DOI") or (w.get("title") or [""])[0]).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            filtered.append(_normalize_work(w, query=query))

        if scanned >= raw_total:
            break

    filtered = sort_results(filtered, query, result_sort)
    return {
        "results": filtered[offset: offset + rows],
        "total": len(filtered),
        "source": "CrossRef exact author",
    }


def search_crossref(query: str, rows: int = 8) -> list[dict]:
    """Return up to `rows` CrossRef matches for a free-text query (title-ish)."""
    return search_paged(query, rows=rows, mode="bibliographic", result_sort="pertinence")["results"]


def search_paged(query: str, rows: int = 25, offset: int = 0,
                 mode: str = "bibliographic", result_sort: str = "pertinence",
                 exact: bool = False, title_contains: bool = False) -> dict:
    """Paged CrossRef search.

    mode: 'bibliographic' (title/author/venue), 'author' (query.author —
    returns a researcher's whole catalogue), or 'title'. Author searches are
    sorted newest-first which is what users expect when bulk-importing a
    researcher's body of work.

    Returns ``{"results": [...], "total": int}``.
    """
    exact = exact or is_exact_query(query)
    clean_query = strip_exact_quotes(query) if exact else query

    if mode == "author":
        pubmed = search_pubmed_author(clean_query, rows=rows, offset=offset)
        if pubmed and pubmed.get("total", 0) > 0:
            pubmed["results"] = sort_results(pubmed["results"], clean_query, result_sort)
            return pubmed
        return search_crossref_author_exact(clean_query, rows=rows, offset=offset, result_sort=result_sort)

    def apply_query_params(params: dict, page_rows: int, page_offset: int) -> dict:
        params.update({"rows": page_rows, "offset": page_offset})
        if mode == "title":
            params["query.title"] = clean_query
        else:
            params["query.bibliographic"] = clean_query
        if result_sort == "newest":
            params["sort"] = "published"
            params["order"] = "desc"
        elif result_sort == "oldest":
            params["sort"] = "published"
            params["order"] = "asc"
        if _mailto():
            params["mailto"] = _mailto()
        return params

    if exact or title_contains:
        scan_limit = int(
            os.environ.get("REVIEWHUNTER_FILTER_SCAN_LIMIT")
            or os.environ.get("REVIEWHUNTER_EXACT_SCAN_LIMIT")
            or os.environ.get("DOIHUNTER_EXACT_SCAN_LIMIT", "500")
            or "500"
        )
        scan_limit = max(rows, min(scan_limit, 1000))
        page_size = 100
        filtered: list[dict] = []
        raw_total = 0
        scanned = 0
        try:
            while scanned < scan_limit and len(filtered) < offset + rows:
                params = apply_query_params({}, min(page_size, scan_limit - scanned), scanned)
                r = requests.get(f"{CROSSREF_API}/works", params=params, headers=_headers(), timeout=20)
                r.raise_for_status()
                msg = r.json().get("message", {})
                raw_total = int(msg.get("total-results", 0) or 0)
                items = msg.get("items", [])
                if not items:
                    break
                scanned += len(items)
                page_results = [
                    _normalize_work(w, query=clean_query)
                    for w in items
                    if (w.get("title") or [""])[0]
                ]
                if exact:
                    page_results = filter_exact_results(page_results, clean_query)
                if title_contains:
                    page_results = filter_title_contains_results(page_results, clean_query)
                filtered.extend(page_results)
                if scanned >= raw_total:
                    break
            filtered = sort_results(filtered, clean_query, result_sort)
            if exact and title_contains:
                source = "CrossRef exact wording + title contains"
            elif title_contains:
                source = "CrossRef title contains"
            else:
                source = "CrossRef exact wording"
            return {
                "results": filtered[offset: offset + rows],
                "total": len(filtered),
                "source": source,
                "scanned": min(scanned, raw_total or scanned),
            }
        except Exception:
            source = "CrossRef title contains" if title_contains else "CrossRef exact wording"
            return {"results": [], "total": 0, "source": source}

    params: dict = {"rows": rows, "offset": offset}
    if mode == "title":
        params["query.title"] = clean_query
    else:
        params["query.bibliographic"] = clean_query
    if result_sort == "newest":
        params["sort"] = "published"
        params["order"] = "desc"
    elif result_sort == "oldest":
        params["sort"] = "published"
        params["order"] = "asc"
    if _mailto():
        params["mailto"] = _mailto()
    try:
        r = requests.get(f"{CROSSREF_API}/works", params=params, headers=_headers(), timeout=20)
        r.raise_for_status()
        msg = r.json().get("message", {})
        items = msg.get("items", [])
        results = [_normalize_work(w, query=clean_query) for w in items if (w.get("title") or [""])[0]]
        return {
            "results": sort_results(results, clean_query, result_sort),
            "total": int(msg.get("total-results", 0) or 0),
        }
    except Exception:
        return {"results": [], "total": 0}


def fetch_doi(doi: str) -> Optional[dict]:
    """Fetch a single work by DOI. Returns normalized dict or None."""
    doi = extract_doi(doi) or doi.strip().lower()
    if doi.startswith("doi.org/"):
        doi = doi[len("doi.org/"):]
    try:
        r = requests.get(f"{CROSSREF_API}/works/{quote(doi, safe='/')}",
                         headers=_headers(), timeout=20)
        if r.status_code != 200:
            return None
        return _normalize_work(r.json().get("message", {}))
    except Exception:
        return None


def looks_like_doi(s: str) -> bool:
    return extract_doi(s) is not None


# ---------- Categorization ----------

def extract_keywords(title: str, abstract: str = "", limit: int = 8) -> list[str]:
    """Lightweight TF-style keyword extraction. Bigrams + unigrams, stopword-filtered."""
    text = f"{title} {abstract}".lower()
    tokens = [t for t in WORD_RE.findall(text) if t not in _STOP and len(t) > 3]
    if not tokens:
        return []
    counts = Counter(tokens)
    bigrams = Counter()
    for a, b in zip(tokens, tokens[1:]):
        if a in _STOP or b in _STOP:
            continue
        bigrams[f"{a} {b}"] += 1
    # Bigrams are weighted slightly more — they're more meaningful.
    scored = [(w, c * 1.5) for w, c in bigrams.most_common(20) if c > 1]
    scored += [(w, float(c)) for w, c in counts.most_common(30)]
    seen, out = set(), []
    for w, _ in sorted(scored, key=lambda x: -x[1]):
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def suggest_collections(item: dict, existing_collections: list[dict]) -> list[int]:
    """Suggest collection ids for an item based on subject/keyword overlap with names."""
    subjects = {s.lower() for s in item.get("subjects") or []}
    keywords = {k.lower() for k in item.get("keywords") or []}
    text = " ".join([
        (item.get("title") or "").lower(),
        (item.get("abstract") or "").lower(),
    ])
    matches = []
    for c in existing_collections:
        name = (c.get("name") or "").lower()
        if not name or name in {"all papers", "starred", "unfiled"}:
            continue
        if name in subjects or any(name in s for s in subjects):
            matches.append(c["id"])
            continue
        if name in keywords or any(name in k for k in keywords):
            matches.append(c["id"])
            continue
        if name in text:
            matches.append(c["id"])
    return matches
