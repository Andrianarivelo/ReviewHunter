"""Full-text indexing and 'Retrieve Metadata for PDF'.

* ``index_attachment`` extracts text from PDFs (pypdf) and HTML snapshots (BeautifulSoup)
  into the FTS5 table ``fulltext_fts`` so the "Everything" search mode finds words
  inside documents, exactly like Zotero's full-text index.
* ``retrieve_metadata`` looks for a DOI / arXiv id inside a PDF (document info + first
  pages) and fills the parent item; if none is found it tries a CrossRef title match
  with a strict similarity threshold so wrong metadata is never silently applied.
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional

from . import catalog, identifiers
from .db import get_conn

log = logging.getLogger("doihunter.fulltext")

MAX_PAGES = 300              # Zotero default indexes up to 100 pages; we allow more but cap work.
MAX_CHARS = 2_000_000
TITLE_MATCH_THRESHOLD = 0.88  # SequenceMatcher ratio required to accept a CrossRef title match.


def extract_pdf_text(path: str | Path, max_pages: int = MAX_PAGES) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        if i >= max_pages or total > MAX_CHARS:
            break
        try:
            txt = page.extract_text() or ""
        except Exception:  # corrupt page: keep going
            txt = ""
        chunks.append(txt)
        total += len(txt)
    return "\n".join(chunks)[:MAX_CHARS]


def extract_html_text(path: str | Path) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8", errors="ignore"), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ")[:MAX_CHARS]


def index_attachment(att: dict) -> int:
    """Index one attachment. Returns number of characters indexed (0 if nothing)."""
    path = att.get("path")
    if not path or not Path(path).exists():
        return 0
    ext = Path(path).suffix.lower()
    try:
        if ext == ".pdf":
            text = extract_pdf_text(path)
        elif ext in (".html", ".htm"):
            text = extract_html_text(path)
        elif ext in (".txt", ".md"):
            text = Path(path).read_text(encoding="utf-8", errors="ignore")[:MAX_CHARS]
        else:
            return 0
    except Exception as e:
        log.warning(f"text extraction failed for {path}: {e}")
        return 0
    conn = get_conn()
    conn.execute("DELETE FROM fulltext_fts WHERE attachment_id=?", (att["id"],))
    if text.strip():
        conn.execute("INSERT INTO fulltext_fts (content, attachment_id, item_id) VALUES (?, ?, ?)",
                     (text, att["id"], att.get("item_id")))
    conn.execute("UPDATE attachments SET fulltext_indexed=1 WHERE id=?", (att["id"],))
    return len(text)


def index_item(item_id: int, *, force: bool = False) -> int:
    total = 0
    for att in catalog.list_attachments(item_id):
        if att["kind"] in ("file", "snapshot") and (force or not att.get("fulltext_indexed")):
            total += index_attachment(att)
    return total


def reindex_all(*, force: bool = False) -> dict:
    rows = get_conn().execute(
        "SELECT * FROM attachments WHERE kind IN ('file','snapshot')" + ("" if force else " AND fulltext_indexed=0")
    ).fetchall()
    chars = 0
    for r in rows:
        chars += index_attachment(dict(r))
    return {"attachments": len(rows), "characters": chars}


def fulltext_stats() -> dict:
    conn = get_conn()
    return {
        "indexed": conn.execute("SELECT COUNT(*) FROM attachments WHERE fulltext_indexed=1").fetchone()[0],
        "total": conn.execute("SELECT COUNT(*) FROM attachments WHERE kind IN ('file','snapshot')").fetchone()[0],
    }


# ----------------------------------------------------------------------------- metadata retrieval

HEADER_CHARS = 1500          # a DOI printed in the first 1500 characters of page 1 belongs to the paper itself
MIN_VERIFIED_TITLE = 15      # shorter titles ("Introduction") are too generic to verify by containment
# Journal banner lines that precede the real title on many first pages (IEEE, Elsevier, Springer...).
_BOILERPLATE_LINE_RE = re.compile(
    r"^(received|accepted|revised|published|date of|digital object identifier|doi|available online|"
    r"contents lists|journal homepage|copyright|©|\(c\)|https?://|www\.|vol(ume)?\.?\s*\d|issn|"
    r"research article|original article|article|open access|citation:)", re.I)


def _compact(s: str) -> str:
    """Lower-case alphanumerics only (accents folded), so line breaks and hyphenation do not matter."""
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _title_in_text(title: str, text: str) -> bool:
    """True when a candidate title is literally printed in the PDF text (verification, not guessing)."""
    t = _compact(title)
    return len(t) >= MIN_VERIFIED_TITLE and t in _compact(text)


def _pdf_identifiers(path: str) -> tuple[list[tuple[str, str]], list[str], str]:
    """Read a PDF's first pages.

    Returns (identifiers in document info + first pages, title guesses, text of the first two pages).
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    meta = reader.metadata or {}
    blob = " ".join(str(v) for v in meta.values() if v)
    first_pages = []
    for page in reader.pages[:3]:
        try:
            first_pages.append(page.extract_text() or "")
        except Exception:
            pass
    ids = identifiers.extract_identifiers(blob + "\n" + "\n".join(first_pages))
    page_text = identifiers.repair_broken_dois("\n".join(first_pages[:2]))

    guesses: list[str] = []
    meta_title = str(meta.get("/Title") or "").strip()
    if len(meta_title) >= 12 and not re.search(r"\.(pdf|docx?)$|^untitled|^microsoft word", meta_title, re.I):
        guesses.append(meta_title)
    # Heuristic: first non-banner block of page 1, joining the lines of a title that wraps (up to 4 lines).
    lines = [ln.strip() for ln in (first_pages[0] if first_pages else "").splitlines()]
    lines = [ln for ln in lines if len(ln) > 3 and not _BOILERPLATE_LINE_RE.match(ln)]
    if lines:
        block = []
        for ln in lines[:4]:
            if block and (re.search(r"\d", ln) or ln.isupper()):  # author lines: affiliations digits / CAPITALS
                break
            block.append(ln)
        guesses.append(" ".join(block))
    return ids, guesses, page_text


def _similar(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower())  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def _find_verified_metadata(item: dict, pdf_path: str) -> tuple[Optional[dict], str, list[str]]:
    """Find metadata for a PDF, accepting a record only when the PDF itself confirms it.

    Order: (1) identifiers printed in the PDF, accepted if the resolved title is printed in the
    PDF or the DOI sits in the page-1 header (otherwise it is probably a cited reference);
    (2) CrossRef bibliographic search on the page-1 text and on title guesses, accepted if the
    candidate title is printed in the PDF or is near-identical (>= TITLE_MATCH_THRESHOLD) to a guess.
    Returns (payload or None, human-readable source, title guesses tried).
    """
    from .metadata import search_crossref

    ids, guesses, page_text = _pdf_identifiers(pdf_path)
    header = page_text[:HEADER_CHARS].lower()
    for kind, value in ids[:6]:
        payload = identifiers.resolve_doi(value) if kind == "doi" else identifiers.resolve_arxiv(value)
        if not payload:
            continue
        if _title_in_text(payload.get("title", ""), page_text) or value.lower() in header.replace(" ", ""):
            return payload, f"{kind}:{value}", guesses

    # The item's current title may come from the file name ("... Methods. IEEE.pdf"): also a guess.
    item_title = re.sub(r"\.pdf$", "", (item.get("title") or "").strip(), flags=re.I)
    if item_title and item_title.lower() != "untitled":
        guesses.append(item_title)
    queries = [re.sub(r"\s+", " ", page_text[:600])] + guesses
    seen: set[str] = set()
    for query in queries:
        if len(query) < MIN_VERIFIED_TITLE:
            continue
        try:
            candidates = search_crossref(query, rows=5)
        except Exception as e:  # network errors must not break the UI action
            log.info(f"crossref search failed: {e}")
            continue
        for cand in candidates:
            doi, title = cand.get("doi"), cand.get("title", "")
            if not doi or doi in seen:
                continue
            seen.add(doi)
            verified = _title_in_text(title, page_text)
            close = any(_similar(title, g) >= TITLE_MATCH_THRESHOLD for g in guesses)
            if verified or close:
                payload = identifiers.resolve_doi(doi)
                if payload:
                    how = "title printed in PDF" if verified else "title match"
                    return payload, f"{how}: {title}", guesses
    return None, "", guesses


def retrieve_metadata(item_id: int) -> dict:
    """Fill an item from identifiers found in its PDF. Returns {ok, source, applied_fields}."""
    item = catalog.get_item(item_id)
    if not item:
        raise ValueError("item not found")
    pdf = next((a for a in item["attachments"] if a.get("path") and a["path"].lower().endswith(".pdf")
                and a.get("exists")), None)
    if not pdf:
        return {"ok": False, "reason": "no PDF attachment"}
    payload, source, guesses = _find_verified_metadata(item, pdf["path"])
    if not payload:
        return {"ok": False, "reason": "no identifier or confident title match found", "title_guesses": guesses}

    # DOI collision: another live item already owns this DOI -> report instead of corrupting.
    doi = payload.get("doi")
    if doi:
        owner = get_conn().execute("SELECT id FROM items WHERE doi=? AND id != ?", (doi, item_id)).fetchone()
        if owner:
            return {"ok": False, "reason": "duplicate", "duplicate_of": owner[0], "source": source}

    patch = {
        "item_type": payload.get("item_type"),
        "creators": payload.get("creators") or [],
        "title": payload.get("title"),
        "field_values": {
            "DOI": doi or "", "date": payload.get("date") or "",
            "publicationTitle": payload.get("journal") or "", "volume": payload.get("volume") or "",
            "issue": payload.get("issue") or "", "pages": payload.get("pages") or "",
            "abstractNote": catalog.clean_abstract(payload.get("abstract")), "url": payload.get("url") or "",
            "ISSN": payload.get("issn") or "", "publisher": payload.get("publisher") or "",
        },
    }
    # Only keep field keys valid for the target type, else the value is silently dropped.
    valid = set(catalog.ITEM_TYPES[catalog.normalize_type(payload.get("item_type"))]["fields"])
    patch["field_values"] = {k: v for k, v in patch["field_values"].items() if k in valid and v}
    catalog.patch_item(item_id, patch)
    get_conn().execute("UPDATE items SET status='downloaded', error_msg=NULL WHERE id=? AND file_path IS NOT NULL",
                       (item_id,))
    catalog.regenerate_citation_key(item_id)
    return {"ok": True, "source": source, "applied_fields": sorted(patch["field_values"])}
