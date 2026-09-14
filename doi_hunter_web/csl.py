"""CSL-JSON conversion (the lingua franca of citation processors).

* ``item_to_csl``: library item -> CSL-JSON (consumed by citeproc-js in the web UI
  and the Word add-in, and by the CSL-JSON export).
* ``csl_to_item``: CSL-JSON -> create payload for ``catalog.create_item``
  (used by imports, the browser connector and DOI content negotiation).

Mapping follows the official Zotero <-> CSL mappings
(https://aurimasv.github.io/z2csl/typeMap.xml) for the fields we store.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import catalog

# Zotero creator type -> CSL name variable
_CREATOR_TO_CSL = {
    "author": "author", "editor": "editor", "translator": "translator", "seriesEditor": "collection-editor",
    "bookAuthor": "container-author", "reviewedAuthor": "reviewed-author", "recipient": "recipient",
    "director": "director", "programmer": "author", "inventor": "author", "presenter": "author",
    "contributor": "contributor", "producer": "producer", "scriptwriter": "script-writer",
    "castMember": "performer", "attorneyAgent": "contributor", "commenter": "commenter",
}
_CSL_TO_CREATOR = {v: k for k, v in _CREATOR_TO_CSL.items() if k not in ("programmer", "inventor", "presenter")}

# CSL type -> Zotero type
_CSL_TO_TYPE = {spec["csl"]: key for key, spec in catalog.ITEM_TYPES.items()}
_CSL_TO_TYPE.update({
    "article": "preprint", "posted-content": "preprint", "proceedings-article": "conferencePaper",
    "book-chapter": "bookSection", "chapter": "bookSection", "journal-article": "journalArticle",
    "dissertation": "thesis", "monograph": "book", "edited-book": "book", "reference-entry": "encyclopediaArticle",
    "entry-dictionary": "encyclopediaArticle", "standard": "report", "legislation": "document",
    "legal_case": "document", "map": "document", "graphic": "document", "interview": "document",
    "broadcast": "videoRecording", "song": "document", "review": "journalArticle", "review-book": "journalArticle",
    "post": "blogPost", "component": "document", "peer-review": "journalArticle", "report-component": "report",
})


def _date_parts(date: Optional[str], year: Optional[int]) -> Optional[dict]:
    """'2021-05-03' / 'May 2021' / 2021 -> CSL {"date-parts": [[2021, 5, 3]]}."""
    s = str(date or "").strip()
    m = re.match(r"^(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", s)
    if m:
        parts = [int(p) for p in m.groups() if p]
        return {"date-parts": [parts]}
    months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                          "nov", "dec"], start=1)}
    ym = re.search(r"(\d{4})", s)
    if ym:
        mon = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", s, flags=re.I)
        parts = [int(ym.group(1))]
        if mon:
            parts.append(months[mon.group(1).lower()])
        return {"date-parts": [parts]}
    if s:
        return {"literal": s}
    if year:
        return {"date-parts": [[int(year)]]}
    return None


def item_to_csl(item: dict) -> dict[str, Any]:
    t = catalog.normalize_type(item.get("item_type"))
    fields = item.get("fields") or {}
    csl: dict[str, Any] = {
        "id": str(item.get("id")),
        "type": catalog.ITEM_TYPES[t]["csl"],
        "title": item.get("title") or "",
    }
    for c in item.get("creators") or catalog.clean_creators(item.get("authors") or []):
        var = _CREATOR_TO_CSL.get(c.get("creatorType") or "author", "author")
        name = {"literal": c["name"]} if c.get("name") else {"family": c.get("lastName") or "",
                                                            "given": c.get("firstName") or ""}
        csl.setdefault(var, []).append(name)

    def put(key: str, value: Any) -> None:
        if value not in (None, "", []):
            csl[key] = value

    put("container-title", item.get("journal"))
    put("publisher", item.get("publisher"))
    put("publisher-place", item.get("place"))
    put("volume", item.get("volume"))
    put("issue", item.get("issue"))
    put("page", item.get("pages"))
    put("number-of-pages", item.get("num_pages"))
    put("edition", item.get("edition"))
    put("collection-title", item.get("series"))
    put("abstract", item.get("abstract"))
    put("DOI", item.get("doi"))
    put("URL", item.get("url"))
    put("ISSN", item.get("issn"))
    isbn = item.get("isbn") or []
    put("ISBN", ", ".join(isbn) if isinstance(isbn, list) else isbn)
    put("language", item.get("language"))
    put("title-short", item.get("short_title"))
    put("PMID", item.get("pmid"))
    put("citation-key", item.get("citation_key"))
    put("note", item.get("extra"))
    put("event-title", fields.get("conferenceName") or fields.get("meetingName"))
    put("genre", fields.get("thesisType") or fields.get("reportType") or fields.get("websiteType")
        or fields.get("presentationType") or fields.get("manuscriptType") or fields.get("letterType"))
    put("number", fields.get("reportNumber") or fields.get("patentNumber") or fields.get("archiveID"))
    put("version", fields.get("versionNumber"))
    put("container-title-short", fields.get("journalAbbreviation"))
    put("archive", fields.get("archive"))
    put("archive_location", fields.get("archiveLocation"))
    put("call-number", fields.get("callNumber"))
    put("section", fields.get("section"))
    put("medium", fields.get("medium"))
    put("dimensions", fields.get("runningTime"))
    issued = _date_parts(item.get("date"), item.get("year"))
    if issued:
        csl["issued"] = issued
    accessed = _date_parts(item.get("access_date"), None)
    if accessed and item.get("access_date"):
        csl["accessed"] = accessed
    return csl


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _citation_key(csl: dict) -> Optional[str]:
    """Keep an explicit citation key (or a key-like string id, e.g. from Better BibTeX exports)."""
    candidate = csl.get("citation-key") or csl.get("id")
    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][\w:.-]*", candidate):
        return candidate
    return None


def csl_to_item(csl: dict) -> dict[str, Any]:
    """CSL-JSON (also CrossRef/DataCite content-negotiation output) -> create payload."""
    ctype = str(csl.get("type") or "article-journal")
    item_type = _CSL_TO_TYPE.get(ctype, "document")
    creators = []
    for var, ztype in [("author", "author"), ("editor", "editor"), ("translator", "translator"),
                       ("container-author", "bookAuthor"), ("collection-editor", "seriesEditor"),
                       ("reviewed-author", "reviewedAuthor"), ("recipient", "recipient"),
                       ("director", "director"), ("contributor", "contributor")]:
        for n in csl.get(var) or []:
            if not isinstance(n, dict):
                continue
            if n.get("literal") or (n.get("name") and not n.get("family")):
                creators.append({"creatorType": ztype, "name": n.get("literal") or n.get("name")})
            elif n.get("family") or n.get("given"):
                creators.append({"creatorType": ztype, "lastName": n.get("family") or "",
                                 "firstName": n.get("given") or ""})
    if item_type in ("computerProgram",):
        for c in creators:
            if c["creatorType"] == "author":
                c["creatorType"] = "programmer"

    issued = csl.get("issued") or csl.get("published-print") or csl.get("published-online") or {}
    date = ""
    year = None
    if isinstance(issued, dict):
        parts = (issued.get("date-parts") or [[]])[0] or []
        parts = [p for p in parts if p not in (None, "")]
        if parts:
            year = int(parts[0])
            date = "-".join(f"{int(p):02d}" if i else str(int(p)) for i, p in enumerate(parts))
        elif issued.get("literal") or issued.get("raw"):
            date = str(issued.get("literal") or issued.get("raw"))
            year = catalog.year_from_date(date)

    fields: dict[str, Any] = {}
    genre = csl.get("genre")
    if genre:
        key = {"thesis": "thesisType", "report": "reportType", "webpage": "websiteType",
               "presentation": "presentationType", "manuscript": "manuscriptType"}.get(item_type)
        if key:
            fields[key] = genre
    if csl.get("event-title") or csl.get("event"):
        ev = csl.get("event-title") or csl.get("event")
        fields["conferenceName"] = ev if isinstance(ev, str) else (ev or {}).get("name", "")
    if csl.get("number"):
        key = {"report": "reportNumber", "patent": "patentNumber", "preprint": "archiveID"}.get(item_type)
        if key:
            fields[key] = str(csl["number"])
    if csl.get("container-title-short") or csl.get("short-container-title"):
        fields["journalAbbreviation"] = _first(csl.get("container-title-short") or csl.get("short-container-title"))

    isbn = csl.get("ISBN")
    isbn_list = [p.strip() for p in re.split(r"[,;\s]+", " ".join(isbn) if isinstance(isbn, list) else str(isbn or ""))
                 if p.strip()]
    abstract = csl.get("abstract") or ""
    abstract = re.sub(r"</?jats:[^>]+>", " ", abstract)
    abstract = re.sub(r"<[^>]+>", " ", abstract)
    abstract = re.sub(r"\s+", " ", abstract).strip()
    subjects = csl.get("subject") or csl.get("categories") or []
    if isinstance(subjects, str):
        subjects = [s.strip() for s in subjects.split(",") if s.strip()]
    keywords = csl.get("keyword") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in re.split(r"[,;]", keywords) if k.strip()]

    return {
        "item_type": item_type,
        "title": re.sub(r"\s+", " ", str(_first(csl.get("title")) or "Untitled")).strip(),
        "creators": creators,
        "journal": _first(csl.get("container-title")) or "",
        "publisher": csl.get("publisher") or "",
        "place": csl.get("publisher-place") or "",
        "volume": str(csl.get("volume") or ""),
        "issue": str(csl.get("issue") or ""),
        "pages": str(csl.get("page") or ""),
        "num_pages": str(csl.get("number-of-pages") or ""),
        "edition": str(csl.get("edition") or ""),
        "series": _first(csl.get("collection-title")) or "",
        "abstract": abstract,
        "doi": catalog.normalize_doi(csl.get("DOI")) or None,
        "url": csl.get("URL") or "",
        "issn": _first(csl.get("ISSN")) or "",
        "isbn": isbn_list,
        "language": csl.get("language") or "",
        "short_title": _first(csl.get("title-short") or csl.get("short-title")) or "",
        "pmid": str(csl.get("PMID") or ""),
        "citation_key": _citation_key(csl),
        "extra": csl.get("note") or "",
        "date": date,
        "year": year,
        "subjects": list(subjects),
        "keywords": list(keywords),
        "fields": fields,
    }
