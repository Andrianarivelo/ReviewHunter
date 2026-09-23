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
import html
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
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

MIN_VERIFIED_TITLE = 15      # shorter titles ("Introduction") are too generic to verify by containment
# Journal banner lines that precede the real title on many first pages (IEEE, Elsevier, Springer...).
_BOILERPLATE_LINE_RE = re.compile(
    r"^(received|accepted|revised|published|date of|digital object identifier|doi|available online|"
    r"contents lists|journal homepage|copyright|©|\(c\)|https?://|www\.|vol(ume)?\.?\s*\d|issn|"
    r"research article|original article|article|open access|citation:)", re.I)


_GREEK_NAMES = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "μ": "mu", "κ": "kappa", "λ": "lambda", "ω": "omega"}


def _plain_title(value: str) -> str:
    """Remove registry presentation markup without adding tag names to identity."""
    value = html.unescape(value or "")
    value = re.sub(r"</(?:i|b|em|strong)>\s*(?=[A-Za-z])", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


def _compact(s: str) -> str:
    """Lower-case alphanumerics only (accents folded), so line breaks and hyphenation do not matter."""
    s = unicodedata.normalize("NFKD", _plain_title(s)).lower()
    for letter, name in _GREEK_NAMES.items():
        s = s.replace(letter, name)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _title_in_text(title: str, text: str) -> bool:
    """True when a candidate title is literally printed in the PDF text (verification, not guessing)."""
    t = _compact(title)
    return len(t) >= MIN_VERIFIED_TITLE and t in _compact(text)


def _verified_title_position(payload: dict, text: str, *, glyph_fallback: bool = False,
                             title_guesses: list[str] | None = None) -> int:
    """Locate a title exactly, or tolerate isolated PDF font codes with two authors.

    The fallback is only used for a DOI extracted from the document. Every title
    letter must match in sequence. Only hyphen positions may contain an erroneous
    capital glyph, and a Greek symbol may appear as an explicit /C-number code.
    Two different author surnames must also occur in the first-page header area.
    """
    title = _plain_title(payload.get("title") or "")
    if _title_in_text(title, text):
        return _compact(text).find(_compact(title))
    if not glyph_fallback or len(_compact(title)) < 60:
        return -1
    if any(letter.lower() in _GREEK_NAMES for letter in title) and not any(
            _compact(guess) == _compact(title) for guess in title_guesses or []):
        # /C227 alone cannot establish which Greek letter was printed. An
        # agreeing embedded title must establish its identity independently.
        return -1
    header = text[:6000]
    surnames = {_compact(person.get("lastName") or "") for person in payload.get("creators", [])}
    if sum(len(name) >= 4 and name in _compact(header) for name in surnames) < 2:
        return -1
    pieces = []
    for character in unicodedata.normalize("NFKD", title):
        if character.lower() in _GREEK_NAMES:
            name = _GREEK_NAMES[character.lower()]
            pieces.append(r"(?:(?i:" + name + r")|C\d{1,4})")
        elif character in "-\u2010\u2011\u2012\u2013\u2212":
            pieces.append(r"[A-Z]?")
        elif character.isascii() and character.isalnum():
            pieces.append("[" + character.lower() + character.upper() + "]" if character.isalpha() else character)
    normalized = unicodedata.normalize("NFKD", _plain_title(header))
    for letter, name in _GREEK_NAMES.items():
        normalized = normalized.replace(letter, name).replace(letter.upper(), name)
    normalized = re.sub(r"[^A-Za-z0-9]", "", normalized)
    match = re.search("".join(pieces), normalized)
    return match.start() if match else -1


def _pdf_title(value: object) -> str:
    """Reject common export placeholders before treating embedded text as a title."""
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(title) < 8 or len(title) > 1000 or re.search(
        r"\.(pdf|docx?)$|^(untitled|microsoft word|main|document|full.?text|manuscript)$|^https?://|^[a-z]{1,6}[_-]\d+\s+\d+\.{2}\d+$",
        title, re.I,
    ):
        return ""
    return title


def _xmp_fields(reader) -> dict:
    """Read bounded RDF directly, including legacy attribute-encoded DC fields.

    Some older PDFs store dc:title as an attribute; pypdf's property accessor
    assumes an element and raises AttributeError. Explicit namespaces here avoid
    both that failure and treating related-paper DOIs as the document's own DOI.
    """
    root_ref = getattr(reader, "trailer", {}).get("/Root")
    if root_ref is None:
        return {}
    stream_ref = root_ref.get_object().get("/Metadata")
    if stream_ref is None:
        return {}
    raw = stream_ref.get_object().get_data()
    if len(raw) > 2 * 1024 * 1024 or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw.replace(b"\x00", b""), re.I):
        raise ValueError("XMP packet exceeds limits or contains an unsupported entity declaration")
    root = ET.fromstring(raw)
    dc = "{http://purl.org/dc/elements/1.1/}"
    rdf_li = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li"
    fields = {"titles": [], "creators": [], "identifiers": [], "prism": []}
    for node in root.iter():
        for key, value in [(node.tag, node.text or ""), *node.attrib.items()]:
            if key in (dc + "title", dc + "creator", dc + "identifier"):
                values = [value.strip()] if value.strip() else []
                if key == node.tag:
                    leaves = list(node.iter(rdf_li))
                    leaves.sort(key=lambda leaf: leaf.get("{http://www.w3.org/XML/1998/namespace}lang") != "x-default")
                    values.extend("".join(leaf.itertext()).strip() for leaf in leaves)
                field = {dc + "title": "titles", dc + "creator": "creators", dc + "identifier": "identifiers"}[key]
                fields[field].extend(value for value in values if value)
            elif key.startswith("{http://prismstandard.org/namespaces/basic/") and key.endswith("}doi"):
                fields["prism"].append(value)
    return fields


def _before_references(text: str) -> str:
    """Exclude bibliography entries from identifier candidates and title guesses."""
    return re.split(r"(?im)^\s*(?:\d+[.\s]+)?(?:references|bibliography|literature cited)\s*[:.]?\s*$",
                    text, maxsplit=1)[0]


def extract_pdf_evidence(path: str | Path) -> dict:
    """Read local PDF identity evidence without contacting a metadata service.

    Embedded title/author fields are distinguished from unverified text guesses.
    ``doi`` is set only for an unambiguous, explicit document Info/XMP identifier;
    DOIs printed in the first pages remain candidates for title verification by
    the caller. Failure of one page or XMP packet does not discard other evidence.
    Password-protected documents return a warning without guessing their identity.
    """
    from pypdf import PdfReader

    result = {"title": "", "creators": [], "doi": "", "identifiers": [],
              "title_guesses": [], "text": "", "first_page_text": "",
              "metadata_identifiers": [], "metadata_sources": [], "warnings": [], "encrypted": False}
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ValueError(f"PDF could not be parsed: {exc}") from exc
    result["encrypted"] = bool(reader.is_encrypted)
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                result["warnings"].append("PDF requires a password; metadata could not be read")
                return result
        except Exception as exc:
            result["warnings"].append(f"PDF decryption failed: {exc}")
            return result

    # Trust explicit identifier fields only. A Subject or description can contain
    # the DOI of a cited article, so those values are merely resolver candidates.
    try:
        meta = reader.metadata or {}
    except Exception as exc:
        meta = {}
        result["warnings"].append(f"PDF document metadata could not be read: {exc}")
    title = _pdf_title(meta.get("/Title"))
    if title:
        result["title"] = title
        result["metadata_sources"].append("info:Title")
    author = str(meta.get("/Author") or "").strip()
    if author:
        # Semicolon, newline and 'and' are clear list separators. Preserve a
        # comma in 'Family, Given' because splitting that would invent authors.
        names = re.split(r"\s*(?:;|\n|\s+and\s+)\s*", author)
        # Publishers also commonly encode 'Given Family, Given Family'.
        comma_names = [name.strip() for name in author.split(",")]
        if len(names) == 1 and len(comma_names) > 1 and all(len(n.split()) >= 2 for n in comma_names):
            names = comma_names
        result["creators"] = catalog.clean_creators(names)
        result["metadata_sources"].append("info:Author")
    for key, value in meta.items():
        if re.sub(r"[^a-z]", "", str(key).lower()) in {"doi", "prismdoi", "identifier", "dcidentifier"}:
            found = identifiers.extract_identifiers(str(value))
            if found:
                result["metadata_identifiers"].extend(found)
                result["metadata_sources"].append(f"info:{str(key).lstrip('/')}")

    try:
        xmp = _xmp_fields(reader)
        xmp_title = next((candidate for value in xmp.get("titles", []) if (candidate := _pdf_title(value))), "")
        if xmp_title:
            if title and _compact(title) != _compact(xmp_title):
                result["warnings"].append("PDF Info and XMP titles differ; XMP title preferred")
            result["title"] = xmp_title
            result["metadata_sources"].append("xmp:dc_title")
        if xmp.get("creators"):
            result["creators"] = catalog.clean_creators(xmp["creators"])
            result["metadata_sources"].append("xmp:dc_creator")
        for field, label in (("identifiers", "xmp:dc_identifier"), ("prism", "xmp:prism_doi")):
            for value in xmp.get(field, []):
                found = identifiers.extract_identifiers(value)
                if found:
                    result["metadata_identifiers"].extend(found)
                    result["metadata_sources"].append(label)
    except Exception as exc:
        result["warnings"].append(f"PDF XMP metadata could not be read: {exc}")

    # Keep page positions stable even if extraction fails, so page two can never
    # be mistaken for the first-page header by a downstream identity check.
    first_pages = []
    try:
        pages = reader.pages[:2]
    except Exception as exc:
        raise ValueError(f"PDF pages could not be read: {exc}") from exc
    for index, page in enumerate(pages):
        try:
            first_pages.append(identifiers.repair_broken_dois(page.extract_text() or "")[:MAX_CHARS])
        except Exception as exc:
            first_pages.append("")
            result["warnings"].append(f"PDF page {index + 1} text could not be read: {exc}")
    result["first_page_text"] = first_pages[0] if first_pages else ""
    result["text"] = "\n".join(first_pages)[:MAX_CHARS]
    if not result["text"].strip():
        result["warnings"].append("PDF has no extractable text on its first two pages")
    result["metadata_identifiers"] = list(dict.fromkeys(result["metadata_identifiers"]))
    result["metadata_sources"] = list(dict.fromkeys(result["metadata_sources"]))
    own_dois = [value for kind, value in result["metadata_identifiers"] if kind == "doi"]
    if len(own_dois) == 1:
        result["doi"] = own_dois[0]
    elif len(own_dois) > 1:
        result["warnings"].append("Conflicting embedded DOI identifiers; identity requires verification")
    blob = "\n".join(str(v) for v in meta.values() if v)
    candidates = identifiers.extract_identifiers(_before_references(blob) + "\n" + _before_references(result["text"]))
    # OUP download watermarks append a numeric article ID after their DOI route.
    # This adds a lookup candidate only; acceptance still requires title evidence.
    oup = re.findall(r"https?://academic\.oup\.com/[^\s]*/doi/(10\.1093/[^/\s]+/[^/\s]+)/\d+(?=[\s?#]|$)",
                     _before_references(result["text"]), re.I)
    candidates = [("doi", value.lower()) for value in oup] + candidates
    result["identifiers"] = list(dict.fromkeys(result["metadata_identifiers"] + candidates))

    guesses = [result["title"]] if result["title"] else []
    lines = [ln.strip() for ln in _before_references(result["first_page_text"]).splitlines()]
    lines = [ln for ln in lines if len(ln) > 3 and not _BOILERPLATE_LINE_RE.match(ln)]
    if lines:
        block = []
        for line in lines[:4]:
            if block and (line.isupper() or re.match(r"^(abstract|keywords|introduction)\b", line, re.I)
                          or re.match(r"^\d[\s,]", line)):
                break
            block.append(line)
        if block:
            guesses.append(" ".join(block))
    result["title_guesses"] = list(dict.fromkeys(guesses))
    return result


def _pdf_identifiers(path: str) -> tuple[list[tuple[str, str]], list[str], str]:
    """Compatibility tuple for verified metadata resolution of existing attachments."""
    evidence = extract_pdf_evidence(path)
    return evidence["identifiers"], evidence["title_guesses"], evidence["text"]


def _similar(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower())  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def _is_work(payload: Optional[dict]) -> bool:
    """A citable work, not a container record.

    CrossRef also registers DOIs for whole journals ("INTERNATIONAL JOURNAL OF ...", type 'journal',
    no authors). Their title is printed in every page banner, so title verification alone would
    accept them: records of the generic 'document' type without any creator are rejected.
    """
    if not payload or not payload.get("title"):
        return False
    return not (catalog.normalize_type(payload.get("item_type")) == "document" and not payload.get("creators"))


def _find_verified_metadata(item: dict, pdf_path: str) -> tuple[Optional[dict], str, list[str]]:
    """Find metadata for a PDF, accepting a record only when the PDF itself confirms it.

    Order: (1) identifiers printed in the PDF, accepted if the resolved title is printed in the
    PDF before the bibliography (a DOI near the top can still be a cited reference);
    (2) CrossRef bibliographic search on the page-1 text and on title guesses, accepted if the
    candidate title is printed in the PDF or is near-identical (>= TITLE_MATCH_THRESHOLD) to a guess.
    Returns (payload or None, human-readable source, title guesses tried).
    """
    from .metadata import search_crossref

    ids, guesses, page_text = _pdf_identifiers(pdf_path)
    page_text = _before_references(page_text)
    printed: list[tuple[int, dict, str]] = []      # (position of the title in the PDF, payload, source)
    for kind, value in ids[:6]:
        payload = identifiers.resolve_doi(value) if kind == "doi" else identifiers.resolve_arxiv(value)
        if not _is_work(payload):
            continue
        position = _verified_title_position(payload, page_text, glyph_fallback=kind == "doi", title_guesses=guesses)
        if position >= 0:
            payload = {**payload, "title": _plain_title(payload["title"])}
            printed.append((position, payload, f"{kind}:{value}"))
    # Several DOIs can be verified (a letter quoting the article it answers, an issue bundle):
    # the paper itself is the one whose title is printed first.
    if printed:
        _pos, payload, source = min(printed, key=lambda t: t[0])
        return payload, source, guesses

    # The item's current title may come from the file name ("... Methods. IEEE.pdf"): also a guess.
    item_title = re.sub(r"\.pdf$", "", (item.get("title") or "").strip(), flags=re.I)
    # A filename/browser title is a search hint, not independent identity evidence.
    queries = [re.sub(r"\s+", " ", page_text[:600])] + guesses + [item_title]
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
            close = not page_text.strip() and any(_similar(title, g) >= TITLE_MATCH_THRESHOLD for g in guesses)
            if verified or close:
                payload = identifiers.resolve_doi(doi)
                if _is_work(payload):
                    payload = {**payload, "title": _plain_title(payload["title"])}
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
    from .pdf_ingest import _identify, reconcile_existing
    payload, evidence, source = _identify(pdf["path"], item)
    if not payload:
        return {"ok": False, "reason": "no identifier or confident title match found", "title_guesses": evidence.get("title_guesses", [])}

    # A verified orphan belongs on its existing reference, preserving notes and files.
    return reconcile_existing(item_id, payload, source or "PDF embedded metadata")
