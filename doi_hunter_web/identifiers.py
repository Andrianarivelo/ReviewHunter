"""Add-by-identifier (Zotero's magic wand): DOI, ISBN, PMID, arXiv ID.

Every resolver returns a create payload (see ``catalog.create_item``) or ``None``.

Sources (all public, no API key):
    DOI   CrossRef REST, then doi.org content negotiation (covers DataCite, mEDRA...)
    ISBN  Open Library Books API, then Google Books
    PMID  NCBI Literature Citation Exporter (CSL-JSON)
    arXiv export.arxiv.org Atom API
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from . import catalog
from .csl import csl_to_item

log = logging.getLogger("doihunter.identifiers")

UA = {"User-Agent": "ReviewHunter/2.0 (reference manager)"}

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)
ARXIV_RE = re.compile(r"(?:arxiv[:\s/]*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
                      re.I)
BARE_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
PMID_RE = re.compile(r"^(?:pmid[:\s]*)?(\d{5,9})$", re.I)
ISBN_RE = re.compile(r"^(?:isbn[:\s]*)?((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx])$", re.I)


def clean_doi(doi: str) -> str:
    doi = catalog.normalize_doi(doi)
    return doi.rstrip(".,;)]}")


def isbn_valid(isbn: str) -> bool:
    digits = re.sub(r"[^0-9Xx]", "", isbn).upper()
    if len(digits) == 10:
        total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(digits))
        return total % 11 == 0
    if len(digits) == 13 and digits.isdigit():
        total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(digits))
        return total % 10 == 0
    return False


def classify(text: str) -> tuple[str, str]:
    """Return (kind, value) with kind in doi | arxiv | pmid | isbn | title."""
    s = (text or "").strip()
    m = DOI_RE.search(s)
    if m:
        return "doi", clean_doi(m.group(1))
    m = ARXIV_RE.search(s)
    if m:
        return "arxiv", m.group(1)
    if BARE_ARXIV_RE.match(s):
        return "arxiv", s
    m = ISBN_RE.match(s)
    if m and isbn_valid(m.group(1)):
        return "isbn", re.sub(r"[^0-9Xx]", "", m.group(1)).upper()
    m = PMID_RE.match(s)
    if m:
        return "pmid", m.group(1)
    return "title", s


def extract_identifiers(text: str) -> list[tuple[str, str]]:
    """All identifiers in a blob of text (dedup, order preserved)."""
    found: list[tuple[str, str]] = []
    seen = set()
    for m in DOI_RE.finditer(text or ""):
        key = ("doi", clean_doi(m.group(1)))
        if key not in seen:
            seen.add(key)
            found.append(key)
    for m in ARXIV_RE.finditer(text or ""):
        key = ("arxiv", m.group(1))
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


# ----------------------------------------------------------------------------- DOI

def resolve_doi(doi: str) -> Optional[dict]:
    doi = clean_doi(doi)
    # 1) CrossRef works endpoint (rich: abstract, subjects).
    try:
        r = requests.get(f"https://api.crossref.org/works/{requests.utils.quote(doi, safe='/')}",
                         headers={**UA, "Accept": "application/json"}, timeout=20)
        if r.status_code == 200:
            work = r.json().get("message", {})
            payload = _crossref_work_to_item(work)
            if payload:
                return payload
    except requests.RequestException as e:
        log.info(f"crossref failed for {doi}: {e}")
    # 2) Content negotiation (DataCite, mEDRA, JaLC... any registration agency).
    try:
        r = requests.get(f"https://doi.org/{doi}", headers={**UA, "Accept": "application/vnd.citationstyles.csl+json"},
                         timeout=20, allow_redirects=True)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            item = csl_to_item(r.json())
            item["doi"] = doi
            return item
    except (requests.RequestException, ValueError) as e:
        log.info(f"content negotiation failed for {doi}: {e}")
    return None


def _crossref_work_to_item(work: dict) -> Optional[dict]:
    if not work:
        return None
    csl = dict(work)
    # CrossRef uses CSL-like keys but different type names and list-valued titles.
    type_map = {"journal-article": "article-journal", "proceedings-article": "paper-conference",
                "book-chapter": "chapter", "posted-content": "article", "dissertation": "thesis",
                "report": "report", "book": "book", "monograph": "book", "edited-book": "book",
                "reference-entry": "entry-encyclopedia", "dataset": "dataset", "standard": "report"}
    csl["type"] = type_map.get(work.get("type"), work.get("type") or "article-journal")
    csl["issued"] = work.get("issued") or work.get("published-print") or work.get("published-online")
    if work.get("event"):
        csl["event-title"] = work["event"].get("name")
    item = csl_to_item(csl)
    if work.get("link"):
        pdfs = [l["URL"] for l in work["link"] if "pdf" in (l.get("content-type") or "")]
        if pdfs:
            item["_pdf_url"] = pdfs[0]
    if not item.get("url") and item.get("doi"):
        item["url"] = f"https://doi.org/{item['doi']}"
    return item


# ----------------------------------------------------------------------------- ISBN

def resolve_isbn(isbn: str) -> Optional[dict]:
    isbn = re.sub(r"[^0-9Xx]", "", isbn).upper()
    try:
        r = requests.get("https://openlibrary.org/api/books", params={"bibkeys": f"ISBN:{isbn}", "format": "json",
                                                                       "jscmd": "data"}, headers=UA, timeout=15)
        data = r.json().get(f"ISBN:{isbn}") if r.ok else None
        if data:
            creators = []
            for a in data.get("authors", []):
                c = catalog.parse_creator_string(a.get("name", ""), "author")
                if c not in creators:  # Open Library sometimes lists the same author record twice
                    creators.append(c)
            date = data.get("publish_date", "")
            return {
                "item_type": "book", "title": data.get("title", "Untitled") + (f": {data['subtitle']}" if data.get("subtitle") else ""),
                "creators": creators, "publisher": ", ".join(p.get("name", "") for p in data.get("publishers", [])),
                "place": ", ".join(p.get("name", "") for p in data.get("publish_places", [])),
                "num_pages": str(data.get("number_of_pages") or ""), "date": date,
                "year": catalog.year_from_date(date), "isbn": [isbn], "url": data.get("url", ""),
                "subjects": [s.get("name") for s in data.get("subjects", [])[:10] if s.get("name")],
            }
    except (requests.RequestException, ValueError) as e:
        log.info(f"openlibrary failed for {isbn}: {e}")
    try:
        r = requests.get("https://www.googleapis.com/books/v1/volumes", params={"q": f"isbn:{isbn}"}, headers=UA,
                         timeout=15)
        vols = r.json().get("items") if r.ok else None
        if vols:
            v = vols[0].get("volumeInfo", {})
            date = v.get("publishedDate", "")
            return {
                "item_type": "book", "title": v.get("title", "Untitled") + (f": {v['subtitle']}" if v.get("subtitle") else ""),
                "creators": [catalog.parse_creator_string(a, "author") for a in v.get("authors", [])],
                "publisher": v.get("publisher", ""), "num_pages": str(v.get("pageCount") or ""), "date": date,
                "year": catalog.year_from_date(date), "isbn": [isbn], "abstract": v.get("description", ""),
                "language": v.get("language", ""), "subjects": v.get("categories", []),
            }
    except (requests.RequestException, ValueError) as e:
        log.info(f"google books failed for {isbn}: {e}")
    return None


# ----------------------------------------------------------------------------- PMID

def resolve_pmid(pmid: str) -> Optional[dict]:
    try:
        r = requests.get("https://api.ncbi.nlm.nih.gov/lit/ctxp/v1/pubmed/", params={"format": "csl", "id": pmid},
                         headers=UA, timeout=20)
        if r.ok and r.text.strip().startswith(("{", "[")):
            data = r.json()
            data = data[0] if isinstance(data, list) else data
            item = csl_to_item(data)
            item["pmid"] = pmid
            item["citation_key"] = None
            if item.get("doi"):
                richer = resolve_doi(item["doi"])
                if richer:
                    richer["pmid"] = pmid
                    return richer
            return item
    except (requests.RequestException, ValueError) as e:
        log.info(f"pubmed failed for {pmid}: {e}")
    return None


# ----------------------------------------------------------------------------- arXiv

def resolve_arxiv(arxiv_id: str) -> Optional[dict]:
    arxiv_id = re.sub(r"^arxiv:", "", arxiv_id.strip(), flags=re.I)
    try:
        r = requests.get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id}, headers=UA, timeout=(10, 45))
        if not r.ok:
            return None
        ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(r.text)
        entry = root.find("a:entry", ns)
        if entry is None or entry.find("a:title", ns) is None:
            return None
        text = lambda path: re.sub(r"\s+", " ", (entry.findtext(path, default="", namespaces=ns) or "")).strip()  # noqa: E731
        published = text("a:published")[:10]
        doi = entry.findtext("arxiv:doi", default="", namespaces=ns)
        bare_id = re.sub(r"v\d+$", "", arxiv_id)
        item = {
            "item_type": "preprint", "title": text("a:title"),
            "creators": [catalog.parse_creator_string(a.findtext("a:name", default="", namespaces=ns), "author")
                         for a in entry.findall("a:author", ns)],
            "abstract": text("a:summary"), "date": published, "year": catalog.year_from_date(published),
            "publisher": "arXiv", "url": f"https://arxiv.org/abs/{bare_id}", "arxiv_id": bare_id,
            "doi": catalog.normalize_doi(doi) or f"10.48550/arxiv.{bare_id}".lower(),
            "fields": {"archiveID": f"arXiv:{bare_id}"},
            "subjects": [c.get("term") for c in entry.findall("a:category", ns) if c.get("term")],
            "_pdf_url": f"https://arxiv.org/pdf/{bare_id}",
        }
        journal_ref = entry.findtext("arxiv:journal_ref", default="", namespaces=ns)
        if journal_ref:
            item["extra"] = f"Journal reference: {journal_ref.strip()}"
        return item
    except (requests.RequestException, ET.ParseError) as e:
        log.info(f"arxiv failed for {arxiv_id}: {e}")
    # Fallback: every arXiv paper has a DataCite DOI (10.48550/arXiv.<id>), resolvable by content negotiation.
    bare_id = re.sub(r"v\d+$", "", arxiv_id)
    item = resolve_doi(f"10.48550/arXiv.{bare_id}")
    if item:
        item.update({"item_type": "preprint", "publisher": item.get("publisher") or "arXiv", "arxiv_id": bare_id,
                     "url": f"https://arxiv.org/abs/{bare_id}", "_pdf_url": f"https://arxiv.org/pdf/{bare_id}"})
        item.setdefault("fields", {})["archiveID"] = f"arXiv:{bare_id}"
    return item


def resolve(text: str) -> Optional[dict]:
    kind, value = classify(text)
    if kind == "doi":
        return resolve_doi(value)
    if kind == "arxiv":
        return resolve_arxiv(value)
    if kind == "isbn":
        return resolve_isbn(value)
    if kind == "pmid":
        return resolve_pmid(value)
    return None
