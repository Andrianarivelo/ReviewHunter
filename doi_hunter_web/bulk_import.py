"""Bounded, cancellable imports with explicit identity and per-record outcomes.

Only the worker writes references. Source paths are read, never moved or renamed;
uploaded files live in a job-owned temporary directory until the job finishes.
"""
from __future__ import annotations

import copy
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from . import catalog, db, library, metadata, pdf_ingest

MAX_ENTRIES = 500
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
JOB_TTL_SECONDS = 3600
MAX_ACTIVE_JOBS = 2
MAX_RETAINED_JOBS = 30
SQLITE_RETRY_DELAYS = (0.25, 0.5, 1.0)  # Three retries after the initial attempt.
SUPPORTED_SUFFIXES = {".pdf", ".csv", ".docx", ".bib", ".bibtex", ".ris", ".json", ".txt", ".tsv"}
METADATA_FIELDS = {"title", "doi", "authors", "creators", "year", "date", "journal", "publisher", "volume",
                   "issue", "pages", "abstract", "url", "language", "issn", "isbn", "pmid", "arxiv_id",
                   "item_type", "subjects", "keywords", "edition", "series", "place", "num_pages"}
TERMINAL = {"completed", "cancelled", "failed"}
_jobs: dict[str, dict] = {}
_lock = threading.RLock()


class BulkInputError(ValueError):
    """A rejected batch has no partially accepted references."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _title_key(value: str) -> str:
    """Normalize typography, without fuzzy similarity or dropping title words."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def _compatible(candidate: dict, supplied: dict) -> bool:
    """An explicit year or DOI disagreement invalidates a title-only match."""
    for key in ("year", "doi"):
        a, b = candidate.get(key), supplied.get(key)
        if a and b and str(a).casefold() != str(b).casefold():
            return False
    creators_a = catalog.clean_creators(candidate.get("creators") or candidate.get("authors") or [])
    creators_b = catalog.clean_creators(supplied.get("creators") or supplied.get("authors") or [])
    if creators_a and creators_b:
        family_a = creators_a[0].get("lastName") or creators_a[0].get("name")
        family_b = creators_b[0].get("lastName") or creators_b[0].get("name")
        if family_a and family_b and _title_key(family_a) != _title_key(family_b):
            return False
    return True


def _citation_matches(candidate: dict, citation: str) -> bool:
    """Corroborate a full citation using its title, year, and first author.

    A complete substantial title must end at citation punctuation, not halfway
    through a longer title. Author/year evidence must occur outside that title.
    This supports common APA and Vancouver references without parsing every style.
    """
    words = _title_key(candidate.get("title") or "").split()
    if len(words) < 5 or len(" ".join(words)) < 28 or not candidate.get("year"):
        return False
    creators = catalog.clean_creators(candidate.get("creators") or candidate.get("authors") or [])
    if not creators:
        return False
    surname = _title_key(creators[0].get("lastName") or creators[0].get("name") or "")
    if not surname:
        return False
    normalized = unicodedata.normalize("NFKC", citation).casefold()
    title_pattern = r"(?<!\w)" + r"[^\w]+".join(re.escape(word) for word in words) + r"(?!\w)"
    match = re.search(title_pattern, normalized)
    if not match:
        return False
    after = normalized[match.end():].lstrip()
    if after and after[0] not in ".?!:;,\"'”’)]":
        return False
    surrounding = normalized[:match.start()] + " " + normalized[match.end():]
    years = set(re.findall(r"\b(?:18|19|20)\d{2}\b", surrounding))
    if years != {str(candidate["year"])}:
        return False
    return f" {surname} " in f" {_title_key(surrounding)} "


def parse_sources(text: str, paths: list[tuple[Path, str]], *, folder_path: str = "",
                  recursive: bool = False) -> tuple[list[dict], list[str]]:
    """Validate every source before admitting a batch; do not truncate large input."""
    from .bulk_sources import parse_bulk_source

    entries, warnings = [], []
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise BulkInputError("Pasted text exceeds 2 MB.", 413)
    sources = list(paths)
    if folder_path:
        root = Path(folder_path).expanduser().resolve()
        if not root.is_dir():
            raise BulkInputError("The selected folder does not exist.")
        for path in (root.rglob("*") if recursive else root.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                warnings.append(f"Skipped linked source: {path.name}")
                continue
            sources.append((path, str(path.relative_to(root))))
            if len(sources) > MAX_ENTRIES:
                raise BulkInputError("A batch can contain at most 500 source files.", 413)
    if len(sources) > MAX_ENTRIES:
        raise BulkInputError("A batch can contain at most 500 source files.", 413)

    def append_parsed(parsed: dict, label: str) -> None:
        if parsed.get("errors"):
            raise BulkInputError(f"{label}: {'; '.join(parsed['errors'])}")
        warnings.extend(f"{label}: {w}" for w in parsed.get("warnings", []))
        for entry in parsed.get("entries", []):
            entries.append({**entry, "source": f"{label}: {entry.get('source', '')}".rstrip(": ")})
        if len(entries) > MAX_ENTRIES:
            raise BulkInputError("A batch can contain at most 500 references; split this input.", 413)

    if text.strip():
        append_parsed(parse_bulk_source(text), "Pasted text")
    total_bytes = len(text.encode("utf-8"))
    for path, label in sorted(sources, key=lambda pair: pair[1].casefold()):
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise BulkInputError(f"Unsupported file: {label}. Save Word documents as .docx.")
        size = path.stat().st_size
        limit = MAX_PDF_BYTES if suffix == ".pdf" else MAX_DOCUMENT_BYTES
        if size > limit:
            raise BulkInputError(f"{label} exceeds the {limit // (1024 * 1024)} MB file limit.", 413)
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise BulkInputError("Combined source size exceeds 1 GB.", 413)
        if suffix == ".pdf":
            entries.append({"query": label, "metadata": {}, "source": label, "pdf_path": str(path), "filename": Path(label).name})
        else:
            append_parsed(parse_bulk_source(filename=label, data=path.read_bytes()), label)
        if len(entries) > MAX_ENTRIES:
            raise BulkInputError("A batch can contain at most 500 references; split this input.", 413)
    if not entries:
        raise BulkInputError("No references or supported source files were found.")
    return entries, warnings


def _resolve(entry: dict, auto_search: bool) -> tuple[dict | None, dict]:
    """Resolve exact identifiers or uniquely corroborated titles, never first hits."""
    supplied = {key: value for key, value in (entry.get("metadata") or {}).items() if key in METADATA_FIELDS}
    query = entry.get("query", "").strip()
    doi = catalog.normalize_doi(supplied.get("doi") or (query if metadata.looks_like_doi(query) else ""))
    if doi:
        supplied["doi"] = doi
        row = db.get_conn().execute("SELECT id, deleted_at FROM items WHERE lower(doi)=?", (doi.lower(),)).fetchone()
        if row and row["deleted_at"]:
            return None, {"status": "needs_review", "error": "This DOI is in Trash. Restore it before importing."}
        existing = catalog.get_item(row["id"]) if row else None
        fetched = metadata.fetch_doi(doi) if auto_search else None
        if fetched and catalog.normalize_doi(fetched.get("doi")) != doi:
            return None, {"status": "needs_review", "error": "The registry returned a different DOI."}
        # Supplied bibliographic fields remain authoritative; registry values fill gaps.
        payload = {**(fetched or {}), **{k: v for k, v in supplied.items() if v not in (None, "", [])}}
        if existing or payload.get("title"):
            return payload, {"metadata_status": "verified" if fetched else "supplied",
                             "warnings": [] if fetched or not auto_search else ["DOI metadata lookup returned no record; supplied metadata was retained."]}
        return None, {"status": "not_found", "error": "No metadata was found for this DOI."}
    if not auto_search:
        if supplied.get("title"):
            return supplied, {"metadata_status": "supplied"}
        return None, {"status": "needs_review", "error": "Metadata search is disabled and this entry has no supplied title."}
    wanted_title = supplied.get("title") or query
    candidates = metadata.search_crossref(wanted_title, rows=8)
    exact = [c for c in candidates if _compatible(c, supplied)
             and (_title_key(c.get("title") or "") == _title_key(wanted_title)
                  or (not supplied.get("title") and _citation_matches(c, query)))]
    # Multiple works with the same title are ambiguous, even if one ranks first.
    unique = {catalog.normalize_doi(c.get("doi")) or repr(c): c for c in exact}
    if len(unique) == 1:
        candidate = next(iter(unique.values()))
        return {**candidate, **{k: v for k, v in supplied.items() if v not in (None, "", [])}}, {"metadata_status": "verified"}
    if supplied.get("title"):
        return supplied, {"metadata_status": "supplied", "warnings": ["No unique exact registry match; imported the supplied metadata without assigning a suggested DOI."]}
    return None, {"status": "needs_review" if candidates else "not_found",
                  "error": "No unique exact title match. Supply a DOI or structured citation metadata.",
                  "candidates": [{k: c.get(k) for k in ("title", "doi", "year")} for c in candidates[:3]]}


def process_entry(entry: dict, *, collection_id: int | None, auto_search: bool, auto_download: bool) -> dict:
    """Import one source and retain a saved reference even when its PDF search fails."""
    links = [collection_id] if collection_id is not None else []
    if entry.get("pdf_path"):
        path = Path(entry["pdf_path"])
        if path.stat().st_size > MAX_PDF_BYTES:
            raise BulkInputError("PDF grew beyond the 200 MB limit after validation.")
        result = pdf_ingest.ingest_pdf(path.read_bytes(), filename=entry.get("filename") or path.name, collections=links)
        return {"status": "pdf", "item_id": result["item_id"], "title": result.get("title"),
                "created": not result.get("existing", False), "matched": bool(result.get("existing")),
                "pdf": True, "pdf_status": "attached", "metadata_status": result.get("metadata_status"),
                "warnings": result.get("warnings", [])}
    payload, details = _resolve(entry, auto_search)
    if payload is None:
        return details
    payload = {**payload, "collections": links, "source_query": entry.get("query", ""), "status": "metadata_only"}
    # Match a live title only when the PDF identity matcher regards it as unambiguous.
    with pdf_ingest._IMPORT_LOCK, db.tx():
        existing = pdf_ingest.find_existing(payload, {})
        if existing:
            same_doi = bool(payload.get("doi") and catalog.normalize_doi(existing.get("doi")) == catalog.normalize_doi(payload["doi"]))
            if not same_doi and not _compatible(existing, payload):
                existing = None
        if existing:
            catalog._fill_missing(existing["id"], payload, catalog.clean_creators(payload.get("creators") or payload.get("authors") or []))
            if payload.get("doi") and not existing.get("doi"):
                catalog.patch_item(existing["id"], {"field_values": {"DOI": payload["doi"]}})
            catalog._attach_links(existing["id"], payload)
            item = catalog.get_item(existing["id"])
            matched = True
        else:
            # Recheck trash for identifiers discovered by title search, without restoring it.
            doi = catalog.normalize_doi(payload.get("doi"))
            trashed = db.get_conn().execute("SELECT id FROM items WHERE doi=? AND deleted_at IS NOT NULL", (doi,)).fetchone() if doi else None
            if trashed:
                return {"status": "needs_review", "error": "The matching reference is in Trash. Restore it before importing."}
            item = catalog.create_item(payload)
            matched = bool(item.get("_existing"))
    result = {"status": "matched" if matched else "created", "item_id": item["id"],
              "title": item.get("title"), "created": not matched, "matched": matched,
              "pdf": bool(item.get("file_path") and Path(item["file_path"]).is_file()), **details}
    result["pdf_status"] = "existing" if result["pdf"] else "not_requested"
    if auto_download and not result["pdf"]:
        if not item.get("doi") and item.get("item_type") != "book":
            result.update(pdf_status="not_found", error="Reference saved; PDF search requires a DOI.")
        else:
            try:
                downloaded = library.download_item(item["id"])
                found = bool(downloaded.get("file_path") and Path(downloaded["file_path"]).is_file())
                result.update(pdf=found, pdf_status="downloaded" if found else "not_found")
                if not found:
                    result["error"] = "Reference saved; " + (downloaded.get("error_msg") or "no PDF was found.")
            except Exception as exc:
                if _sqlite_contention(exc):
                    # The reference transaction has committed before PDF lookup.
                    # Carry that known outcome through a retry so a newly saved
                    # citation is not relabelled as an older library reference.
                    exc.bulk_saved_result = dict(result)
                    raise
                result.update(pdf_status="failed", error=f"Reference saved; PDF download failed: {exc}")
    return result


def _sqlite_contention(exc: Exception) -> bool:
    """Retry SQLite lock contention only, including extended BUSY_SNAPSHOT codes."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    # Older Python builds and test-generated errors may not expose SQLite codes.
    message = str(exc).lower()
    return any(message == text or message.startswith(text + ":")
               for text in ("database is locked", "database table is locked", "database schema is locked", "database is busy"))


def _process_with_retry(entry: dict, options: dict, *, on_retry=None, cancel_event=None) -> dict:
    """Recheck canonical identity after a transient concurrent indexing write.

    SQLite's busy timeout cannot repair a stale WAL read snapshot. A fresh
    transaction can. Canonical PDF ingestion and DOI/title matching make a retry
    safe even if the previous attempt committed before reporting its failure.
    Other I/O, parsing, constraint, and network errors are never retried here.
    """
    saved_result = None
    for attempt in range(len(SQLITE_RETRY_DELAYS) + 1):
        try:
            result = process_entry(entry, **options)
            if saved_result and result.get("item_id") == saved_result.get("item_id") and saved_result.get("created"):
                result.update(created=True, matched=False)
                if result.get("status") == "matched":
                    result["status"] = "created"
            result["retry_count"] = attempt
            return result
        except sqlite3.OperationalError as exc:
            known_save = getattr(exc, "bulk_saved_result", None)
            if known_save and (saved_result is None or known_save.get("created")):
                saved_result = known_save
            if not _sqlite_contention(exc) or attempt >= len(SQLITE_RETRY_DELAYS):
                if saved_result and _sqlite_contention(exc):
                    # Retry exhaustion affects PDF retrieval, not the reference
                    # already saved. Preserve its identity and make the failure
                    # visible without asking the user to re-import it.
                    return {**saved_result, "retry_count": attempt, "pdf_status": "failed",
                            "error": f"Reference saved; PDF retrieval stopped after database contention: {exc}"}
                raise
            # Normal catalog/ingest transactions already roll back. Also release
            # an unfinished transaction before waiting, without resetting other
            # threads' connections or the shared schema migration cache.
            connection = db.get_conn()
            if connection.in_transaction:
                connection.rollback()
            delay = SQLITE_RETRY_DELAYS[attempt]
            if on_retry:
                on_retry(attempt + 1)
            if cancel_event is not None:
                # A cancellation wakes backoff promptly. Finish this entry's
                # bounded retries, then the worker cancels untouched entries.
                cancel_event.wait(delay)
            else:
                time.sleep(delay)


def _prune() -> None:
    """Expire completed progress records without touching stored library files."""
    now = time.time()
    for key, job in list(_jobs.items()):
        if job["status"] in TERMINAL and now - job.get("finished_at", now) > JOB_TTL_SECONDS:
            del _jobs[key]
    finished = sorted((job.get("finished_at", now), key) for key, job in _jobs.items() if job["status"] in TERMINAL)
    for _, key in finished[:max(0, len(_jobs) - MAX_RETAINED_JOBS)]:
        del _jobs[key]


def snapshot(job_id: str) -> dict | None:
    """Return JSON-safe progress without exposing events or temporary file paths."""
    with _lock:
        _prune()
        job = _jobs.get(job_id)
        if not job:
            return None
        return copy.deepcopy({k: v for k, v in job.items() if not k.startswith("_")})


def cancel(job_id: str) -> dict | None:
    """Finish the current reference, then cancel each untouched entry."""
    with _lock:
        job = _jobs.get(job_id)
        if job and job["status"] not in TERMINAL:
            job["_cancel"].set()
            job["status"] = "cancelling"
    return snapshot(job_id)


def _run(job_id: str) -> None:
    """Use this worker's SQLite connection only; always release staged uploads."""
    job = _jobs[job_id]
    try:
        with _lock:
            if not job["_cancel"].is_set():
                job["status"] = "running"
        for index, entry in enumerate(job["_entries"]):
            with _lock:
                if job["_cancel"].is_set():
                    for record in job["records"][index:]:
                        record["status"] = "cancelled"
                    break
                job["current"] = index + 1
                job["records"][index]["status"] = "running"
            try:
                def report_retry(attempt: int) -> None:
                    """Expose retries without converting a running record to failed."""
                    with _lock:
                        job["records"][index]["retry_count"] = attempt

                outcome = _process_with_retry(entry, job["_options"], on_retry=report_retry,
                                              cancel_event=job["_cancel"])
            except Exception as exc:
                outcome = {"status": "failed", "error": str(exc)}
            with _lock:
                job["records"][index].update(outcome)
                job["completed"] += 1
        with _lock:
            job["status"] = "cancelled" if job["_cancel"].is_set() else "completed"
    except Exception as exc:
        with _lock:
            job.update(status="failed", error=str(exc))
    finally:
        # reset_connections closes only this thread's connection, never a request's.
        db.reset_connections()
        if job.get("_temp_dir"):
            shutil.rmtree(job["_temp_dir"], ignore_errors=True)
        with _lock:
            job["current"] = None
            job["finished_at"] = time.time()
            job.pop("_entries", None)


def start_job(entries: list[dict], *, collection_id: int | None = None,
              new_collection_name: str = "", auto_search: bool = True,
              auto_download: bool = True, warnings: list[str] | None = None,
              temp_dir: str | None = None) -> dict:
    """Capture and validate the destination once, before any background writes."""
    if not 1 <= len(entries) <= MAX_ENTRIES:
        raise BulkInputError("A batch must contain between 1 and 500 references.", 413)
    name = new_collection_name.strip()
    if collection_id is not None and name:
        raise BulkInputError("Choose an existing collection or a new name, not both.")
    if len(name) > 200:
        raise BulkInputError("Collection names are limited to 200 characters.")
    with _lock:
        _prune()
        if sum(j["status"] not in TERMINAL for j in _jobs.values()) >= MAX_ACTIVE_JOBS:
            raise BulkInputError("Two imports are already active. Wait or cancel one.", 429)
        collection = None
        if collection_id is not None:
            row = db.get_conn().execute("SELECT id, name FROM collections WHERE id=?", (collection_id,)).fetchone()
            if not row:
                raise BulkInputError("The destination collection no longer exists.")
            collection = dict(row)
        elif name:
            collection = library.create_collection(name)
            collection_id = collection["id"]
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {"job_id": job_id, "status": "queued", "total": len(entries),
                         "completed": 0, "current": None, "collection": collection,
                         "warnings": warnings or [], "created_at": time.time(),
                         "records": [{"index": i + 1, "query": e.get("query", ""), "source": e.get("source", ""),
                                      "status": "queued", "item_id": None, "pdf": False, "warnings": []}
                                     for i, e in enumerate(entries)],
                         "_entries": entries, "_temp_dir": temp_dir, "_cancel": threading.Event(),
                         "_options": {"collection_id": collection_id, "auto_search": auto_search,
                                      "auto_download": auto_download}}
        threading.Thread(target=_run, args=(job_id,), name=f"bulk-import-{job_id[:8]}", daemon=True).start()
        return snapshot(job_id)
