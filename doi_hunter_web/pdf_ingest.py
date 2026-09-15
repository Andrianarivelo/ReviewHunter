"""Identify PDF bytes before creating references, and keep uploads on their canonical item.

PDF content is never rewritten. Embedded metadata and verified registry records supply
identity; filenames and browser metadata are hints, never sufficient proof of a DOI.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import tempfile
import threading
import unicodedata
from pathlib import Path

from . import catalog, fulltext
from .db import get_conn, rows_to_dicts, tx

MAX_PDF_BYTES = 200 * 1024 * 1024
MIN_MATCH_TITLE = 24
MAX_ABSTRACT_CHARS = 8000
_IMPORT_LOCK = threading.RLock()  # Serialize matching + storage within the local app.
log = logging.getLogger("doihunter.pdf_ingest")


def _same_doi(a: dict, b: dict) -> bool:
    """Return whether both records carry the same normalized, nonempty DOI."""
    doi = catalog.normalize_doi(a.get("doi"))
    return bool(doi and doi == catalog.normalize_doi(b.get("doi")))


def _conflicts(a: dict, b: dict) -> bool:
    """Different explicit DOIs must never be merged by a similar title."""
    return bool(a.get("doi") and b.get("doi") and not _same_doi(a, b))


def _placeholder(item: dict) -> bool:
    """Only an unclassified PDF shell can be absorbed into another reference automatically."""
    return item.get("item_type") == "document" and not item.get("doi") and not item.get("creators")


def find_existing(payload: dict, evidence: dict, *, exclude_id: int | None = None) -> dict | None:
    """Match a live reference by own DOI or unambiguous, fully normalized title.

    The first-page containment fallback also recognizes an existing reference when the
    registry is offline and the PDF lacks an embedded title. Bibliographies are excluded
    by the evidence reader. Conflicting identifiers/years prevent a title-only merge.
    """
    rows = rows_to_dicts(get_conn().execute("SELECT * FROM items WHERE deleted_at IS NULL").fetchall())
    rows = [r for r in rows if r["id"] != exclude_id]
    doi = catalog.normalize_doi(payload.get("doi"))
    if doi:
        own = next((r for r in rows if catalog.normalize_doi(r.get("doi")) == doi), None)
        if own:
            return catalog.get_item(own["id"])
    title = fulltext._compact(payload.get("title") or "")
    first_page = fulltext._before_references(evidence.get("first_page_text") or "")
    candidates = []
    for row in rows:
        if _conflicts(row, payload):
            continue
        if row.get("year") and payload.get("year") and str(row["year"]) != str(payload["year"]):
            continue
        other = fulltext._compact(row.get("title") or "")
        exact = len(title) >= MIN_MATCH_TITLE and title == other
        printed = not title and len(other) >= MIN_MATCH_TITLE and fulltext._title_in_text(row["title"], first_page)
        if exact or printed:
            candidates.append(row)
    return catalog.get_item(candidates[0]["id"]) if len(candidates) == 1 else None


def _enrich(item_id: int, payload: dict) -> None:
    """Fill missing fields while preserving the user's populated bibliographic values."""
    item = catalog.get_item(item_id, full=False)
    if not item or _conflicts(item, payload):
        raise ValueError("PDF identifies a different DOI than the selected reference")
    patch = {}
    for key in ("title", "creators", "year", "journal", "publisher", "volume", "issue", "pages", "abstract", "url", "language"):
        if payload.get(key) and (not item.get(key) or (key == "title" and _placeholder(item))):
            patch[key] = payload[key]
    previous_abstract = item.get("abstract") or ""
    if (previous_abstract.lstrip().startswith("•") and previous_abstract.count("•") >= 2
            and len(payload.get("abstract") or "") > len(previous_abstract)):
        # Publisher highlights were historically scraped into the abstract field.
        # Preserve them as a note before installing the PDF's explicitly labelled abstract.
        catalog.create_note(item_id, "<p><strong>Publisher highlights (previous abstract)</strong></p><p>"
                            + html.escape(previous_abstract) + "</p>")
        patch["abstract"] = payload["abstract"]
    if payload.get("item_type") and _placeholder(item):
        patch["item_type"] = payload["item_type"]
    fields = {}
    for key, field in (("doi", "DOI"), ("date", "date"), ("issn", "ISSN")):
        if payload.get(key) and not item.get(key):
            fields[field] = payload[key]
    if fields:
        patch["field_values"] = fields
    if patch:
        catalog.patch_item(item_id, patch)


def _existing_bytes(data: bytes) -> dict | None:
    """Repeated imports reuse the exact stored file even if no metadata can be resolved."""
    digest = hashlib.sha256(data).digest()
    rows = get_conn().execute(
        "SELECT a.* FROM attachments a JOIN items i ON i.id=a.item_id "
        "WHERE i.deleted_at IS NULL AND a.mime='application/pdf' AND a.size=?", (len(data),)).fetchall()
    for row in rows:
        path = Path(row["path"] or "")
        try:
            if path.is_file() and hashlib.sha256(path.read_bytes()).digest() == digest:
                return dict(row)
        except OSError:
            continue
    return None


def _identify(path: str, hints: dict) -> tuple[dict, dict, str]:
    """Read local evidence and enrich only metadata verified against the PDF itself."""
    evidence = fulltext.extract_pdf_evidence(path)
    if evidence.get("encrypted") and not evidence.get("text") and not evidence.get("title"):
        raise ValueError("This PDF is password-protected; unlock it before importing")
    payload = {k: evidence[k] for k in ("title", "creators", "doi", "year", "date", "journal", "publisher", "abstract")
               if evidence.get(k)}
    if evidence.get("text", "").strip() and payload.get("title") and not fulltext._title_in_text(payload["title"], evidence["text"]):
        # Producer templates can leave a previous paper's Info/XMP behind.
        payload = {}
        evidence.setdefault("warnings", []).append("Embedded title was not printed in the PDF; stale metadata ignored")
    # Page metadata can help the resolver search, but must not assign arbitrary identity.
    verified, source, _ = fulltext._find_verified_metadata({"title": hints.get("title") or payload.get("title") or ""}, path)
    if verified:
        payload.update({k: v for k, v in verified.items() if v not in (None, "", [], {}) and not k.startswith("_")})
    elif evidence.get("text", "").strip() and payload.get("doi") and not payload.get("title"):
        # An isolated embedded DOI can be stale too; text-bearing PDFs must confirm a title.
        payload.pop("doi", None)
    if not payload.get("abstract"):
        abstract = extract_abstract(evidence.get("text") or "")
        if abstract:
            payload["abstract"] = abstract
    if payload.get("doi"):
        payload["doi"] = catalog.normalize_doi(payload["doi"])
    return payload, evidence, source


def extract_abstract(text: str) -> str:
    """Extract an explicitly delimited abstract, never guess where body prose ends."""
    text = unicodedata.normalize("NFKC", text)
    start = re.search(r"(?im)^\s*abstract\s*[:.-]?\s*\n", text)
    if not start:
        return ""
    remainder = text[start.end():]
    end = re.search(r"(?im)^\s*(?:\d+[.)]?\s*)?(?:introduction|background|keywords|key words)\s*[:.-]?\s*(?:\n|$)", remainder)
    if not end or not 80 <= end.start() <= MAX_ABSTRACT_CHARS:
        return ""
    paragraph = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", remainder[:end.start()])
    return re.sub(r"\s+", " ", paragraph).strip()


def ingest_pdf(data: bytes, filename: str = "file.pdf", *, item_id: int | None = None,
               hints: dict | None = None, source_url: str | None = None,
               collections: list[int] | None = None, tags: list[str] | None = None,
               notes: list[str] | None = None, title: str | None = None) -> dict:
    """Validate, identify, match, enrich, and attach a PDF; return its canonical item ID.

    The identification happens before any new item is written. Unknown metadata remains
    partial, and explicit attachment targets are preserved unless a verified placeholder
    belongs to an existing reference. Temporary extraction files are always removed.
    """
    if not data or data[:5] != b"%PDF-":
        raise ValueError("The response is not a PDF (it may be a login or error page)")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds the 200 MB import limit")
    target = catalog.get_item(item_id) if item_id is not None else None
    if item_id is not None and (not target or target.get("deleted_at")):
        raise ValueError("Selected reference is missing or in the trash")
    with tempfile.TemporaryDirectory(prefix="reviewhunter-pdf-") as tmp:
        path = Path(tmp) / "input.pdf"
        path.write_bytes(data)
        try:
            payload, evidence, source = _identify(str(path), hints or {})
        except (ValueError, OSError):
            raise
        except Exception as exc:
            raise ValueError(f"Could not read PDF: {exc}") from exc

    with _IMPORT_LOCK, tx():
        existing = find_existing(payload, evidence)
        identical = _existing_bytes(data)
        if not existing and identical:
            existing = catalog.get_item(identical["item_id"])
        if target and _conflicts(target, payload):
            raise ValueError("PDF identifies a different DOI than the selected reference")
        if existing and target and existing["id"] != target["id"] and not _placeholder(target):
            raise ValueError(f"This PDF belongs to existing reference #{existing['id']}")
        chosen = existing or target
        reused = chosen is not None
        if chosen:
            _enrich(chosen["id"], payload)
        else:
            fallback = Path(filename).stem.replace("_", " ").strip() or "Unidentified PDF"
            chosen = catalog.create_item({**payload, "title": payload.get("title") or fallback,
                "item_type": payload.get("item_type") or "document", "source_query": "pdf-import"})
        canonical_id = chosen["id"]
        catalog._attach_links(canonical_id, {"collections": collections or [], "tags": tags or [], "notes": notes or []})
        merged_from = None
        if target and target["id"] != canonical_id and _placeholder(target):
            from .duplicates import merge
            merge(canonical_id, [target["id"]])
            merged_from = target["id"]
        att = next((a for a in catalog.list_attachments(canonical_id)
                    if a.get("exists") and a.get("mime") == "application/pdf" and Path(a["path"]).read_bytes() == data), None)
        if not att:
            att = catalog.store_file_bytes(canonical_id, data, "file.pdf", title=title, url=source_url)
        get_conn().execute("UPDATE items SET status='downloaded', error_msg=NULL WHERE id=?", (canonical_id,))
        catalog.rename_files_from_metadata(canonical_id)
        att = catalog.get_attachment(att["id"])
        final = catalog.get_item(canonical_id)
    warnings = list(evidence.get("warnings") or [])
    if not source:
        warnings.append("Registry metadata could not be verified; only available PDF/library fields were retained")
    from .library import _index_fulltext_async
    _index_fulltext_async(canonical_id)
    return {"ok": True, "item_id": canonical_id, "title": final["title"], "existing": reused,
            "attachment": att, "metadata_status": "verified" if source else "partial",
            "source": source or "PDF embedded metadata / existing library", "warnings": warnings,
            "merged_from": merged_from}


def reconcile_existing(item_id: int, payload: dict, source: str) -> dict:
    """Repair an already-imported orphan without copying or rewriting its PDF bytes."""
    from .duplicates import merge
    item = catalog.get_item(item_id)
    with _IMPORT_LOCK, tx():
        owner = find_existing(payload, {}, exclude_id=item_id)
        if owner and _placeholder(item):
            _enrich(owner["id"], payload)
            merge(owner["id"], [item_id])
            canonical_id = owner["id"]
        elif owner:
            return {"ok": False, "reason": "duplicate", "duplicate_of": owner["id"], "source": source}
        else:
            _enrich(item_id, payload)
            canonical_id = item_id
        catalog.rename_files_from_metadata(canonical_id)
        get_conn().execute("UPDATE items SET status='downloaded', error_msg=NULL WHERE id=?", (canonical_id,))
    return {"ok": True, "item_id": canonical_id, "merged_from": item_id if canonical_id != item_id else None,
            "source": source, "applied_fields": sorted(k for k, v in payload.items() if v)}
