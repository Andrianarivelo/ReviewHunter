"""Catalog v2: the Zotero-parity item model.

This module owns everything a reference manager needs beyond downloading:

* item types and their fields (single source of truth, served to the UI),
* structured creators (author / editor / translator ...),
* full item create / patch, trash, restore, permanent delete,
* tags, child and standalone notes, attachments, related items,
* nested collections helpers,
* saved searches and the advanced item query used by every list view,
* citation keys (Better BibTeX style ``smith2021deep``).

Storage conventions
-------------------
Frequently used fields have real columns (``title``, ``journal``, ``publisher`` ...).
Type-specific fields that only a few types use (``conferenceName``, ``thesisType`` ...)
live in the JSON ``fields`` column. ``ITEM_TYPES`` below maps each UI field key to
its storage, so the rest of the code never guesses.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import papers_dir
from .db import get_conn, row_to_dict, rows_to_dicts, tx

# ======================================================================================
# Item types and fields
# ======================================================================================

# field key -> (label, storage). storage is a column name or "fields.<key>".
FIELD_DEFS: dict[str, tuple[str, str]] = {
    "title": ("Title", "title"),
    "abstractNote": ("Abstract", "abstract"),
    "publicationTitle": ("Publication", "journal"),
    "bookTitle": ("Book Title", "journal"),
    "proceedingsTitle": ("Proceedings Title", "journal"),
    "websiteTitle": ("Website Title", "journal"),
    "blogTitle": ("Blog Title", "journal"),
    "encyclopediaTitle": ("Encyclopedia Title", "journal"),
    "conferenceName": ("Conference Name", "fields.conferenceName"),
    "publisher": ("Publisher", "publisher"),
    "university": ("University", "publisher"),
    "institution": ("Institution", "publisher"),
    "repository": ("Repository", "publisher"),
    "label": ("Label", "publisher"),
    "company": ("Company", "publisher"),
    "place": ("Place", "place"),
    "volume": ("Volume", "volume"),
    "issue": ("Issue", "issue"),
    "pages": ("Pages", "pages"),
    "numPages": ("# of Pages", "num_pages"),
    "edition": ("Edition", "edition"),
    "series": ("Series", "series"),
    "date": ("Date", "date"),
    "language": ("Language", "language"),
    "DOI": ("DOI", "doi"),
    "ISSN": ("ISSN", "issn"),
    "ISBN": ("ISBN", "isbn"),
    "shortTitle": ("Short Title", "short_title"),
    "url": ("URL", "url"),
    "accessDate": ("Accessed", "access_date"),
    "extra": ("Extra", "extra"),
    "PMID": ("PMID", "pmid"),
    "arXivID": ("arXiv ID", "arxiv_id"),
    "citationKey": ("Citation Key", "citation_key"),
    "thesisType": ("Type", "fields.thesisType"),
    "reportNumber": ("Report Number", "fields.reportNumber"),
    "reportType": ("Report Type", "fields.reportType"),
    "archiveID": ("Archive ID", "fields.archiveID"),
    "websiteType": ("Website Type", "fields.websiteType"),
    "patentNumber": ("Patent Number", "fields.patentNumber"),
    "country": ("Country", "fields.country"),
    "versionNumber": ("Version", "fields.versionNumber"),
    "programmingLanguage": ("Prog. Language", "fields.programmingLanguage"),
    "presentationType": ("Type", "fields.presentationType"),
    "meetingName": ("Meeting Name", "fields.meetingName"),
    "section": ("Section", "fields.section"),
    "manuscriptType": ("Type", "fields.manuscriptType"),
    "letterType": ("Type", "fields.letterType"),
    "medium": ("Medium", "fields.medium"),
    "runningTime": ("Running Time", "fields.runningTime"),
    "archive": ("Archive", "fields.archive"),
    "archiveLocation": ("Loc. in Archive", "fields.archiveLocation"),
    "callNumber": ("Call Number", "fields.callNumber"),
    "rights": ("Rights", "fields.rights"),
    "journalAbbreviation": ("Journal Abbr", "fields.journalAbbreviation"),
}

_COMMON_TAIL = ["language", "shortTitle", "url", "accessDate", "archive", "archiveLocation", "callNumber",
                "rights", "extra", "citationKey"]

# type -> (label, CSL type, creator types, ordered field keys)
ITEM_TYPES: dict[str, dict[str, Any]] = {
    "journalArticle": {
        "label": "Journal Article", "csl": "article-journal",
        "creators": ["author", "editor", "translator", "reviewedAuthor"],
        "fields": ["title", "abstractNote", "publicationTitle", "volume", "issue", "pages", "date", "series",
                   "journalAbbreviation", "DOI", "ISSN", "PMID", *_COMMON_TAIL],
    },
    "book": {
        "label": "Book", "csl": "book",
        "creators": ["author", "editor", "translator", "seriesEditor"],
        "fields": ["title", "abstractNote", "series", "volume", "numPages", "edition", "place", "publisher",
                   "date", "ISBN", "DOI", *_COMMON_TAIL],
    },
    "bookSection": {
        "label": "Book Section", "csl": "chapter",
        "creators": ["author", "editor", "bookAuthor", "translator", "seriesEditor"],
        "fields": ["title", "abstractNote", "bookTitle", "series", "volume", "edition", "place", "publisher",
                   "date", "pages", "ISBN", "DOI", *_COMMON_TAIL],
    },
    "conferencePaper": {
        "label": "Conference Paper", "csl": "paper-conference",
        "creators": ["author", "editor", "translator", "seriesEditor"],
        "fields": ["title", "abstractNote", "date", "proceedingsTitle", "conferenceName", "place", "publisher",
                   "volume", "pages", "series", "DOI", "ISBN", *_COMMON_TAIL],
    },
    "preprint": {
        "label": "Preprint", "csl": "article",
        "creators": ["author", "editor", "translator"],
        "fields": ["title", "abstractNote", "repository", "archiveID", "place", "date", "series", "DOI",
                   "arXivID", *_COMMON_TAIL],
    },
    "thesis": {
        "label": "Thesis", "csl": "thesis",
        "creators": ["author", "contributor"],
        "fields": ["title", "abstractNote", "thesisType", "university", "place", "date", "numPages", "DOI",
                   *_COMMON_TAIL],
    },
    "report": {
        "label": "Report", "csl": "report",
        "creators": ["author", "editor", "seriesEditor", "translator", "contributor"],
        "fields": ["title", "abstractNote", "reportNumber", "reportType", "series", "place", "institution",
                   "date", "pages", "DOI", *_COMMON_TAIL],
    },
    "webpage": {
        "label": "Web Page", "csl": "webpage",
        "creators": ["author", "contributor", "translator"],
        "fields": ["title", "abstractNote", "websiteTitle", "websiteType", "date", *_COMMON_TAIL],
    },
    "blogPost": {
        "label": "Blog Post", "csl": "post-weblog",
        "creators": ["author", "commenter", "contributor"],
        "fields": ["title", "abstractNote", "blogTitle", "websiteType", "date", *_COMMON_TAIL],
    },
    "magazineArticle": {
        "label": "Magazine Article", "csl": "article-magazine",
        "creators": ["author", "contributor", "translator"],
        "fields": ["title", "abstractNote", "publicationTitle", "volume", "issue", "date", "pages", "ISSN",
                   *_COMMON_TAIL],
    },
    "newspaperArticle": {
        "label": "Newspaper Article", "csl": "article-newspaper",
        "creators": ["author", "contributor", "translator"],
        "fields": ["title", "abstractNote", "publicationTitle", "place", "edition", "date", "section", "pages",
                   "ISSN", *_COMMON_TAIL],
    },
    "encyclopediaArticle": {
        "label": "Encyclopedia Article", "csl": "entry-encyclopedia",
        "creators": ["author", "editor", "translator"],
        "fields": ["title", "abstractNote", "encyclopediaTitle", "series", "volume", "edition", "place",
                   "publisher", "date", "pages", "ISBN", *_COMMON_TAIL],
    },
    "dataset": {
        "label": "Dataset", "csl": "dataset",
        "creators": ["author", "contributor"],
        "fields": ["title", "abstractNote", "repository", "place", "versionNumber", "date", "DOI", *_COMMON_TAIL],
    },
    "computerProgram": {
        "label": "Software", "csl": "software",
        "creators": ["programmer", "contributor"],
        "fields": ["title", "abstractNote", "series", "versionNumber", "date", "place", "company",
                   "programmingLanguage", "ISBN", *_COMMON_TAIL],
    },
    "patent": {
        "label": "Patent", "csl": "patent",
        "creators": ["inventor", "attorneyAgent", "contributor"],
        "fields": ["title", "abstractNote", "place", "country", "patentNumber", "date", *_COMMON_TAIL],
    },
    "presentation": {
        "label": "Presentation", "csl": "speech",
        "creators": ["presenter", "contributor"],
        "fields": ["title", "abstractNote", "presentationType", "date", "place", "meetingName", *_COMMON_TAIL],
    },
    "manuscript": {
        "label": "Manuscript", "csl": "manuscript",
        "creators": ["author", "contributor", "translator"],
        "fields": ["title", "abstractNote", "manuscriptType", "place", "date", "numPages", *_COMMON_TAIL],
    },
    "letter": {
        "label": "Letter", "csl": "personal_communication",
        "creators": ["author", "recipient", "contributor"],
        "fields": ["title", "abstractNote", "letterType", "date", *_COMMON_TAIL],
    },
    "videoRecording": {
        "label": "Video Recording", "csl": "motion_picture",
        "creators": ["director", "castMember", "contributor", "producer", "scriptwriter"],
        "fields": ["title", "abstractNote", "medium", "series", "volume", "place", "label", "date",
                   "runningTime", *_COMMON_TAIL],
    },
    "document": {
        "label": "Document", "csl": "document",
        "creators": ["author", "contributor", "editor", "translator", "reviewedAuthor"],
        "fields": ["title", "abstractNote", "publisher", "date", *_COMMON_TAIL],
    },
}

CREATOR_LABELS = {
    "author": "Author", "editor": "Editor", "translator": "Translator", "reviewedAuthor": "Reviewed Author",
    "seriesEditor": "Series Editor", "bookAuthor": "Book Author", "contributor": "Contributor",
    "commenter": "Commenter", "programmer": "Programmer", "inventor": "Inventor",
    "attorneyAgent": "Attorney/Agent", "presenter": "Presenter", "recipient": "Recipient",
    "director": "Director", "castMember": "Cast Member", "producer": "Producer", "scriptwriter": "Scriptwriter",
}

# Legacy ReviewHunter types
_TYPE_ALIASES = {"paper": "journalArticle", "article": "journalArticle", None: "journalArticle", "": "journalArticle"}


def normalize_type(item_type: Optional[str]) -> str:
    t = _TYPE_ALIASES.get(item_type, item_type)
    return t if t in ITEM_TYPES else "document"


def item_types_payload() -> dict:
    """Everything the UI needs to render the item editor for any type."""
    return {
        "types": [
            {
                "id": key,
                "label": spec["label"],
                "creators": [{"id": c, "label": CREATOR_LABELS.get(c, c)} for c in spec["creators"]],
                "fields": [{"id": f, "label": FIELD_DEFS[f][0]} for f in spec["fields"]],
            }
            for key, spec in ITEM_TYPES.items()
        ],
        "creator_labels": CREATOR_LABELS,
    }


# ======================================================================================
# Creators
# ======================================================================================

def creator_display(c: dict) -> str:
    if c.get("name"):
        return str(c["name"])
    last, first = (c.get("lastName") or "").strip(), (c.get("firstName") or "").strip()
    return f"{last}, {first}" if last and first else (last or first)


def parse_creator_string(s: str, creator_type: str = "author") -> dict:
    """'Smith, John' / 'John Smith' / 'World Health Organization' -> creator dict."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return {"creatorType": creator_type, "name": ""}
    if "," in s:
        last, first = [p.strip() for p in s.split(",", 1)]
        return {"creatorType": creator_type, "lastName": last, "firstName": first}
    parts = s.split(" ")
    if len(parts) == 1:
        return {"creatorType": creator_type, "name": s}
    return {"creatorType": creator_type, "lastName": parts[-1], "firstName": " ".join(parts[:-1])}


def clean_creators(creators: Iterable[Any]) -> list[dict]:
    out: list[dict] = []
    for c in creators or []:
        if isinstance(c, str):
            c = parse_creator_string(c)
        if not isinstance(c, dict):
            continue
        ctype = c.get("creatorType") or "author"
        if c.get("name") and not (c.get("lastName") or c.get("firstName")):
            name = str(c["name"]).strip()
            if name:
                out.append({"creatorType": ctype, "name": name})
            continue
        last, first = str(c.get("lastName") or "").strip(), str(c.get("firstName") or "").strip()
        if last or first:
            out.append({"creatorType": ctype, "lastName": last, "firstName": first})
    return out


def authors_mirror(creators: list[dict]) -> list[str]:
    """Legacy `authors` column: display strings of author-like creators (falls back to all)."""
    main = [c for c in creators if c.get("creatorType") in ("author", "programmer", "inventor", "presenter",
                                                             "director")]
    return [creator_display(c) for c in (main or creators) if creator_display(c)]


# ======================================================================================
# Citation keys
# ======================================================================================

_KEY_STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "from", "at", "by", "is", "are"}


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")


def make_citation_key(item: dict, conn: Optional[sqlite3.Connection] = None) -> str:
    """Better-BibTeX-like key: lastname + year + first significant title word, unique."""
    creators = item.get("creators") or []
    last = ""
    if creators:
        c = creators[0]
        last = c.get("lastName") or c.get("name") or ""
    elif item.get("authors"):
        last = str(item["authors"][0]).split(",")[0]
    last = re.sub(r"[^a-z]", "", _ascii(last).lower()) or "anon"
    year = str(item.get("year") or "")
    if not year and item.get("date"):
        m = re.search(r"(\d{4})", str(item["date"]))
        year = m.group(1) if m else ""
    words = [w for w in re.findall(r"[a-z0-9]+", _ascii(item.get("title") or "").lower()) if w not in _KEY_STOP]
    base = f"{last}{year}{words[0] if words else ''}"
    conn = conn or get_conn()
    key, suffix = base, 0
    while conn.execute(
        "SELECT 1 FROM items WHERE citation_key=? AND id != ?", (key, item.get("id") or -1)
    ).fetchone():
        key = base + chr(ord("a") + suffix) if suffix < 26 else f"{base}{suffix}"
        suffix += 1
    return key


# ======================================================================================
# Serialization helpers
# ======================================================================================

_YEAR_RE = re.compile(r"(1[5-9]\d{2}|20\d{2}|21\d{2})")


def year_from_date(date: Optional[str]) -> Optional[int]:
    m = _YEAR_RE.search(str(date or ""))
    return int(m.group(1)) if m else None


def _json(v: Any) -> Optional[str]:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def decorate(item: dict, *, full: bool = False) -> dict:
    """Add computed/linked data to an item dict."""
    conn = get_conn()
    iid = item["id"]
    item["item_type"] = normalize_type(item.get("item_type"))
    item["type_label"] = ITEM_TYPES[item["item_type"]]["label"]
    if not item.get("creators") and item.get("authors"):
        item["creators"] = clean_creators(item["authors"])
    item["tags"] = [
        dict(r) for r in conn.execute(
            "SELECT t.id, t.name, t.color, t.type FROM tags t JOIN item_tags it ON it.tag_id=t.id "
            "WHERE it.item_id=? ORDER BY t.name COLLATE NOCASE", (iid,)
        ).fetchall()
    ]
    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM notes WHERE item_id=? AND deleted_at IS NULL),"
        "       (SELECT COUNT(*) FROM attachments WHERE item_id=?)", (iid, iid)
    ).fetchone()
    item["note_count"], item["attachment_count"] = counts[0], counts[1]
    if full:
        item["collections"] = [
            r[0] for r in conn.execute("SELECT collection_id FROM item_collections WHERE item_id=?", (iid,))
        ]
        item["notes_list"] = list_notes(item_id=iid)
        item["attachments"] = list_attachments(iid)
        item["related"] = [
            {"id": r["id"], "title": r["title"], "year": r["year"], "item_type": normalize_type(r["item_type"])}
            for r in conn.execute(
                "SELECT i.id, i.title, i.year, i.item_type FROM related_items r JOIN items i ON i.id=r.related_id "
                "WHERE r.item_id=? AND i.deleted_at IS NULL", (iid,)
            ).fetchall()
        ]
        # Flat field map keyed by Zotero field ids, convenient for the editor.
        item["field_values"] = get_field_values(item)
    return item


def get_field_values(item: dict) -> dict[str, Any]:
    fields = item.get("fields") or {}
    out: dict[str, Any] = {}
    spec = ITEM_TYPES[normalize_type(item.get("item_type"))]
    for key in spec["fields"]:
        storage = FIELD_DEFS[key][1]
        if storage.startswith("fields."):
            out[key] = fields.get(storage[7:], "")
        elif storage == "isbn":
            isbn = item.get("isbn") or []
            out[key] = ", ".join(isbn) if isinstance(isbn, list) else str(isbn)
        else:
            v = item.get(storage)
            out[key] = "" if v is None else v
    return out


# ======================================================================================
# Item CRUD
# ======================================================================================

def get_item(item_id: int, *, full: bool = True) -> Optional[dict]:
    row = get_conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    item = row_to_dict(row)
    return decorate(item, full=full) if item else None


def _apply_field_values(item_type: str, field_values: dict[str, Any], existing_fields: dict) -> dict[str, Any]:
    """Map Zotero field ids -> column updates. Unknown keys are ignored."""
    cols: dict[str, Any] = {}
    fields = dict(existing_fields or {})
    for key, value in (field_values or {}).items():
        if key not in FIELD_DEFS:
            continue
        storage = FIELD_DEFS[key][1]
        value = value.strip() if isinstance(value, str) else value
        if storage.startswith("fields."):
            if value in (None, ""):
                fields.pop(storage[7:], None)
            else:
                fields[storage[7:]] = value
        elif storage == "isbn":
            parts = [p.strip() for p in re.split(r"[,;\s]+", str(value or "")) if p.strip()]
            cols["isbn"] = _json(parts)
        elif storage == "doi":
            doi = normalize_doi(value)
            cols["doi"] = doi or None
        elif storage == "date":
            cols["date"] = value or None
            cols["year"] = year_from_date(value)
        else:
            cols[storage] = value if value != "" else None
    cols["fields"] = _json(fields) if fields else None
    return cols


def normalize_doi(value: Any) -> str:
    s = str(value or "").strip()
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.lower()


# A section heading glued to the text by HTML tag stripping: "AbstractSubstance use..." or
# "Abstract: We..." (ScienceDirect, Springer and Wiley pages render <h2>Abstract</h2><p>...</p>).
_ABSTRACT_HEADING_RE = re.compile(r"^\s*(?:abstract|summary)(?:\s*[:.\-]\s*|\s+|(?=[A-Z]))(?=\S)", re.I)


def clean_abstract(value: Any) -> str:
    """Remove a leading 'Abstract'/'Summary' heading and collapse whitespace.

    Only the heading word is removed and only when it is followed by the start of the
    text (a capital letter, punctuation or whitespace), so a sentence such as
    "Abstraction is..." is left intact.
    """
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if re.match(r"^(?i:abstract|summary)[a-z]", s) or re.match(r"^(ABSTRACT|SUMMARY)[A-Z]", s):
        return s  # "Abstraction...", "ABSTRACTION...": a real word, not a glued heading.
    return _ABSTRACT_HEADING_RE.sub("", s, count=1)


def create_item(data: dict, *, dedupe_doi: bool = True) -> dict:
    """Create an item from a mixed payload.

    Accepts legacy keys (``authors``, ``journal``, ``year``...) *and* v2 keys
    (``creators``, ``field_values``, ``tags``, ``collections``, ``notes``).
    If ``dedupe_doi`` and the DOI already exists, the existing item is updated
    (missing fields filled) and returned with ``_existing=True``.
    """
    conn = get_conn()
    if data.get("abstract"):
        data = {**data, "abstract": clean_abstract(data["abstract"])}
    item_type = normalize_type(data.get("item_type"))
    creators = clean_creators(data.get("creators") or data.get("authors") or [])
    doi = normalize_doi(data.get("doi") or (data.get("field_values") or {}).get("DOI")) or None

    with tx():
        row = None
        if dedupe_doi and doi:
            row = conn.execute("SELECT id, deleted_at FROM items WHERE doi=?", (doi,)).fetchone()
        if dedupe_doi and not row and data.get("arxiv_id"):
            row = conn.execute("SELECT id, deleted_at FROM items WHERE arxiv_id=?", (data["arxiv_id"],)).fetchone()
        if dedupe_doi and not row and data.get("isbn") and item_type == "book":
            for isbn in data["isbn"]:
                row = conn.execute("SELECT id, deleted_at FROM items WHERE isbn LIKE ? AND item_type='book'",
                                   (f'%"{isbn}"%',)).fetchone()
                if row:
                    break
        if row:
            iid = row["id"]
            if row["deleted_at"]:
                conn.execute("UPDATE items SET deleted_at=NULL WHERE id=?", (iid,))
            _fill_missing(iid, data, creators)
            _attach_links(iid, data)
            item = get_item(iid)
            item["_existing"] = True
            return item

        cols: dict[str, Any] = {
            "item_type": item_type,
            "title": (data.get("title") or (data.get("field_values") or {}).get("title") or "Untitled").strip(),
            "creators": _json(creators),
            "authors": _json(authors_mirror(creators)),
            "doi": doi,
        }
        for key in ("journal", "publisher", "volume", "issue", "pages", "abstract", "url", "access_date",
                    "language", "short_title", "extra", "issn", "series", "edition", "place", "num_pages",
                    "pmid", "arxiv_id", "status", "source_query", "extension", "file_path", "file_size",
                    "error_msg", "citation_key"):
            if data.get(key) not in (None, ""):
                cols[key] = data[key]
        for key in ("subjects", "keywords", "isbn", "mirrors"):
            if data.get(key):
                cols[key] = _json(list(data[key]))
        if data.get("date"):
            cols["date"] = str(data["date"])
        if data.get("year"):
            cols["year"] = int(data["year"])
            cols.setdefault("date", str(data["year"]))
        elif cols.get("date"):
            cols["year"] = year_from_date(cols["date"])
        if data.get("fields"):
            cols["fields"] = _json(data["fields"])
        if data.get("field_values"):
            cols.update({k: v for k, v in _apply_field_values(item_type, data["field_values"],
                                                              data.get("fields") or {}).items()
                         if v is not None})
            cols["title"] = cols.get("title") or "Untitled"
        cols.setdefault("status", "metadata_only")

        names = list(cols)
        cur = conn.execute(
            f"INSERT INTO items ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
            [cols[n] for n in names],
        )
        iid = cur.lastrowid
        if not cols.get("citation_key"):
            key = make_citation_key({**data, "id": iid, "creators": creators, "year": cols.get("year"),
                                     "title": cols["title"]}, conn)
            conn.execute("UPDATE items SET citation_key=? WHERE id=?", (key, iid))
        _attach_links(iid, data)
    return get_item(iid)


def _fill_missing(iid: int, data: dict, creators: list[dict]) -> None:
    """Merge incoming metadata into an existing item without overwriting user edits."""
    conn = get_conn()
    row = row_to_dict(conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone())
    updates: dict[str, Any] = {}
    for key in ("journal", "publisher", "volume", "issue", "pages", "abstract", "url", "language", "issn",
                "pmid", "arxiv_id", "date", "short_title"):
        if not row.get(key) and data.get(key):
            updates[key] = data[key]
    if not row.get("year") and data.get("year"):
        updates["year"] = int(data["year"])
    if not row.get("creators") and creators:
        updates["creators"] = _json(creators)
        updates["authors"] = _json(authors_mirror(creators))
    if updates:
        conn.execute(
            f"UPDATE items SET {', '.join(f'{k}=?' for k in updates)}, updated_at=datetime('now') WHERE id=?",
            [*updates.values(), iid],
        )


def _attach_links(iid: int, data: dict) -> None:
    """Tags / collections / notes / related supplied with a create payload."""
    for name in data.get("tags") or []:
        add_tag(iid, name if isinstance(name, str) else name.get("name", ""))
    for cid in data.get("collections") or []:
        get_conn().execute("INSERT OR IGNORE INTO item_collections (item_id, collection_id) VALUES (?, ?)",
                           (iid, int(cid)))
    for note in data.get("notes") or []:
        html = note if isinstance(note, str) else note.get("html") or note.get("note") or ""
        if html.strip():
            create_note(iid, html)


def patch_item(item_id: int, patch: dict) -> Optional[dict]:
    """Partial update. Accepts `field_values`, `creators`, `item_type` and legacy columns."""
    conn = get_conn()
    row = row_to_dict(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
    if not row:
        return None
    updates: dict[str, Any] = {}
    item_type = normalize_type(patch.get("item_type") or row.get("item_type"))
    if patch.get("item_type"):
        updates["item_type"] = item_type
    if "field_values" in patch:
        updates.update(_apply_field_values(item_type, patch["field_values"], row.get("fields") or {}))
        if "title" in updates and not updates["title"]:
            updates["title"] = "Untitled"
    if "creators" in patch:
        creators = clean_creators(patch["creators"])
        updates["creators"] = _json(creators)
        updates["authors"] = _json(authors_mirror(creators))
    elif "authors" in patch:
        creators = clean_creators(patch["authors"])
        updates["creators"] = _json(creators)
        updates["authors"] = _json(authors_mirror(creators))
    for key in ("title", "journal", "abstract", "starred", "status", "extension", "url", "extra",
                "citation_key", "publisher", "volume", "issue", "pages", "language"):
        if key in patch and patch[key] is not None:
            val = patch[key]
            if key == "starred":
                val = 1 if val else 0
            updates[key] = val
    if "year" in patch and patch["year"] is not None:
        updates["year"] = int(patch["year"])
        updates.setdefault("date", str(patch["year"]))
    for key in ("keywords", "subjects"):
        if key in patch and patch[key] is not None:
            updates[key] = _json(list(patch[key]))
    if not updates:
        return get_item(item_id)
    # Better BibTeX behaviour: an automatically generated key follows the metadata (author, year, title)
    # until the user types a custom key, which is then kept forever.
    key_inputs = {"title", "creators", "authors", "date", "year"}
    user_key = "citation_key" in updates or "citationKey" in (patch.get("field_values") or {})
    if not user_key and key_inputs & set(updates) and row.get("citation_key"):
        was_auto = row["citation_key"] == make_citation_key(row, conn) or re.fullmatch(
            re.escape(make_citation_key(row, conn)) + r"[a-z]?", row["citation_key"] or "")
        if was_auto:
            future = {**row, **{k: v for k, v in updates.items() if k not in ("creators", "authors")}, "id": item_id}
            if "creators" in updates:
                future["creators"] = json.loads(updates["creators"]) if updates["creators"] else []
            if "year" in updates:
                future["year"] = updates["year"]
            updates["citation_key"] = make_citation_key(future, conn)
    try:
        conn.execute(
            f"UPDATE items SET {', '.join(f'{k}=?' for k in updates)}, updated_at=datetime('now') WHERE id=?",
            [*updates.values(), item_id],
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"another item already uses this DOI ({e})") from e
    return get_item(item_id)


def regenerate_citation_key(item_id: int) -> Optional[dict]:
    item = get_item(item_id, full=False)
    if not item:
        return None
    key = make_citation_key(item)
    get_conn().execute("UPDATE items SET citation_key=? WHERE id=?", (key, item_id))
    return get_item(item_id)


# ---------------------------- Trash ----------------------------

def trash_items(item_ids: list[int]) -> int:
    with tx() as conn:
        n = 0
        for iid in item_ids:
            n += conn.execute(
                "UPDATE items SET deleted_at=datetime('now') WHERE id=? AND deleted_at IS NULL", (iid,)
            ).rowcount
    return n


def restore_items(item_ids: list[int]) -> int:
    with tx() as conn:
        return sum(conn.execute("UPDATE items SET deleted_at=NULL WHERE id=?", (iid,)).rowcount for iid in item_ids)


def delete_items_permanently(item_ids: list[int], *, delete_files: bool = True) -> int:
    """Hard delete (trash emptying). Files inside the managed papers folder are removed."""
    conn = get_conn()
    managed_root = str(papers_dir().resolve()).casefold()
    n = 0
    for iid in item_ids:
        paths = [r[0] for r in conn.execute("SELECT path FROM attachments WHERE item_id=? AND path IS NOT NULL",
                                            (iid,))]
        legacy = conn.execute("SELECT file_path FROM items WHERE id=?", (iid,)).fetchone()
        if legacy and legacy[0]:
            paths.append(legacy[0])
        conn.execute("DELETE FROM fulltext_fts WHERE item_id=?", (iid,))
        n += conn.execute("DELETE FROM items WHERE id=?", (iid,)).rowcount
        if delete_files:
            for p in set(paths):
                try:
                    rp = Path(p).resolve()
                    # Only delete files we manage; never touch user files elsewhere on disk.
                    if str(rp).casefold().startswith(managed_root) and rp.is_file():
                        rp.unlink()
                except OSError:
                    pass
    return n


def empty_trash() -> int:
    ids = [r[0] for r in get_conn().execute("SELECT id FROM items WHERE deleted_at IS NOT NULL")]
    get_conn().execute("DELETE FROM notes WHERE deleted_at IS NOT NULL")
    return delete_items_permanently(ids)


# ======================================================================================
# Tags
# ======================================================================================

def list_tags(*, item_ids: Optional[list[int]] = None) -> list[dict]:
    conn = get_conn()
    if item_ids is not None:
        if not item_ids:
            return []
        marks = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            f"SELECT t.id, t.name, t.color, t.type, COUNT(it.item_id) AS count FROM tags t "
            f"JOIN item_tags it ON it.tag_id=t.id WHERE it.item_id IN ({marks}) "
            f"GROUP BY t.id ORDER BY (t.color IS NULL), t.name COLLATE NOCASE", item_ids
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT t.id, t.name, t.color, t.type, COUNT(i.id) AS count FROM tags t "
            "LEFT JOIN item_tags it ON it.tag_id=t.id "
            "LEFT JOIN items i ON i.id=it.item_id AND i.deleted_at IS NULL "
            "GROUP BY t.id ORDER BY (t.color IS NULL), t.name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def _tag_id(name: str, *, create: bool = True, tag_type: int = 0) -> Optional[int]:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return None
    conn = get_conn()
    row = conn.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    return conn.execute("INSERT INTO tags (name, type) VALUES (?, ?)", (name, tag_type)).lastrowid


def add_tag(item_id: int, name: str, *, tag_type: int = 0) -> Optional[int]:
    tid = _tag_id(name, tag_type=tag_type)
    if tid:
        get_conn().execute("INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)", (item_id, tid))
    return tid


def remove_tag(item_id: int, name: str) -> None:
    tid = _tag_id(name, create=False)
    if tid:
        get_conn().execute("DELETE FROM item_tags WHERE item_id=? AND tag_id=?", (item_id, tid))


def bulk_tags(item_ids: list[int], add: list[str], remove: list[str]) -> dict:
    with tx():
        for iid in item_ids:
            for name in add or []:
                add_tag(iid, name)
            for name in remove or []:
                remove_tag(iid, name)
    return {"items": len(item_ids), "added": len(add or []), "removed": len(remove or [])}


def update_tag(tag_id: int, *, name: Optional[str] = None, color: Optional[str] = None,
               clear_color: bool = False) -> Optional[dict]:
    conn = get_conn()
    if name is not None and name.strip():
        other = conn.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE AND id != ?",
                             (name.strip(), tag_id)).fetchone()
        if other:
            # Renaming onto an existing tag merges them (Zotero behaviour).
            with tx():
                conn.execute("INSERT OR IGNORE INTO item_tags (item_id, tag_id) "
                             "SELECT item_id, ? FROM item_tags WHERE tag_id=?", (other[0], tag_id))
                conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
            tag_id = other[0]
        else:
            conn.execute("UPDATE tags SET name=? WHERE id=?", (name.strip(), tag_id))
    if clear_color:
        conn.execute("UPDATE tags SET color=NULL WHERE id=?", (tag_id,))
    elif color:
        conn.execute("UPDATE tags SET color=? WHERE id=?", (color, tag_id))
    row = conn.execute("SELECT id, name, color, type FROM tags WHERE id=?", (tag_id,)).fetchone()
    return dict(row) if row else None


def delete_tag(tag_id: int) -> None:
    get_conn().execute("DELETE FROM tags WHERE id=?", (tag_id,))


def delete_automatic_tags() -> int:
    return get_conn().execute("DELETE FROM tags WHERE type=1").rowcount


# ======================================================================================
# Notes
# ======================================================================================

_ALLOWED_TAGS = {"p", "br", "b", "strong", "i", "em", "u", "s", "sub", "sup", "h1", "h2", "h3", "ul", "ol", "li",
                 "blockquote", "pre", "code", "a", "span", "div", "hr", "table", "tr", "td", "th", "thead",
                 "tbody", "mark"}


def sanitize_html(html: str) -> str:
    """Conservative allow-list sanitizer (notes are rendered with innerHTML in the UI)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(True):
        if tag.name in ("script", "style", "iframe", "object", "embed", "link", "meta"):
            tag.decompose()
            continue
        if tag.name not in _ALLOWED_TAGS:
            tag.unwrap()
            continue
        attrs = {}
        if tag.name == "a" and tag.get("href", "").startswith(("http://", "https://", "mailto:", "/")):
            attrs["href"] = tag["href"]
            attrs["target"] = "_blank"
            attrs["rel"] = "noopener"
        style = tag.get("style", "")
        m = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", style)
        if m:
            attrs["style"] = f"background-color: {m.group(1)}"
        if tag.get("data-annotation-id") and str(tag["data-annotation-id"]).isdigit():
            attrs["data-annotation-id"] = tag["data-annotation-id"]
        tag.attrs = attrs
    return str(soup)


def note_title(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    # Title = text of the first non-empty block; inline tags (<b>, <i>, <a>) stay part of the same line.
    blocks = ("p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "td")
    for el in soup.find_all(blocks):
        text = re.sub(r"\s+", " ", el.get_text(" ")).strip()
        if text:
            return text[:120]
    text = re.sub(r"[ \t]+", " ", soup.get_text("\n"))
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:120] or "Untitled note"


def _note_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["title"] = note_title(d.get("html") or "")
    return d


def list_notes(*, item_id: Optional[int] = None, standalone: bool = False, trash: bool = False) -> list[dict]:
    conn = get_conn()
    where = ["deleted_at IS NOT NULL" if trash else "deleted_at IS NULL"]
    params: list[Any] = []
    if item_id is not None:
        where.append("item_id=?")
        params.append(item_id)
    elif standalone:
        where.append("item_id IS NULL")
    rows = conn.execute(
        f"SELECT * FROM notes WHERE {' AND '.join(where)} ORDER BY updated_at DESC", params
    ).fetchall()
    return [_note_dict(r) for r in rows]


def get_note(note_id: int) -> Optional[dict]:
    row = get_conn().execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    return _note_dict(row) if row else None


def create_note(item_id: Optional[int], html: str) -> dict:
    nid = get_conn().execute("INSERT INTO notes (item_id, html) VALUES (?, ?)",
                             (item_id, sanitize_html(html))).lastrowid
    return get_note(nid)


def update_note(note_id: int, html: str) -> Optional[dict]:
    get_conn().execute("UPDATE notes SET html=?, updated_at=datetime('now') WHERE id=?",
                       (sanitize_html(html), note_id))
    return get_note(note_id)


def delete_note(note_id: int, *, permanent: bool = False) -> None:
    if permanent:
        get_conn().execute("DELETE FROM notes WHERE id=?", (note_id,))
    else:
        get_conn().execute("UPDATE notes SET deleted_at=datetime('now') WHERE id=?", (note_id,))


def restore_note(note_id: int) -> None:
    get_conn().execute("UPDATE notes SET deleted_at=NULL WHERE id=?", (note_id,))


# ======================================================================================
# Attachments
# ======================================================================================

MIME_BY_EXT = {
    "pdf": "application/pdf", "epub": "application/epub+zip", "html": "text/html", "htm": "text/html",
    "txt": "text/plain", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword", "mobi": "application/x-mobipocket-ebook", "djvu": "image/vnd.djvu",
    "csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def list_attachments(item_id: int) -> list[dict]:
    rows = get_conn().execute(
        "SELECT a.*, (SELECT COUNT(*) FROM annotations n WHERE n.attachment_id=a.id) AS annotation_count "
        "FROM attachments a WHERE a.item_id=? ORDER BY (a.mime='application/pdf') DESC, a.added_at", (item_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["exists"] = bool(d.get("path")) and Path(d["path"]).exists()
        out.append(d)
    return out


def get_attachment(att_id: int) -> Optional[dict]:
    row = get_conn().execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
    return dict(row) if row else None


def sync_primary_file(item_id: int) -> None:
    """Keep items.file_path (legacy, used by downloads/exports) pointing at the first file attachment."""
    conn = get_conn()
    row = conn.execute(
        "SELECT path, size FROM attachments WHERE item_id=? AND kind IN ('file','snapshot') AND path IS NOT NULL "
        "ORDER BY (mime='application/pdf') DESC, added_at LIMIT 1", (item_id,)
    ).fetchone()
    if row:
        conn.execute("UPDATE items SET file_path=?, file_size=?, status=CASE WHEN status IN "
                     "('downloading') THEN status ELSE 'downloaded' END WHERE id=?", (row[0], row[1], item_id))
    else:
        conn.execute("UPDATE items SET file_path=NULL, file_size=NULL, status=CASE WHEN status='downloaded' "
                     "THEN 'metadata_only' ELSE status END WHERE id=?", (item_id,))


def register_file(item_id: int, path: str | Path, *, title: Optional[str] = None, kind: str = "file",
                  url: Optional[str] = None) -> dict:
    """Record an existing file on disk as an attachment (no copy)."""
    p = Path(path)
    conn = get_conn()
    existing = conn.execute("SELECT id FROM attachments WHERE item_id=? AND path=?", (item_id, str(p))).fetchone()
    ext = p.suffix.lstrip(".").lower()
    size = p.stat().st_size if p.exists() else None
    if existing:
        conn.execute("UPDATE attachments SET size=?, fulltext_indexed=0 WHERE id=?", (size, existing[0]))
        att_id = existing[0]
    else:
        default_title = {"pdf": "Full Text PDF", "html": "Snapshot", "htm": "Snapshot"}.get(ext, p.name)
        att_id = conn.execute(
            "INSERT INTO attachments (item_id, kind, title, path, url, mime, size) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, kind, title or default_title, str(p), url, MIME_BY_EXT.get(ext, "application/octet-stream"),
             size),
        ).lastrowid
    sync_primary_file(item_id)
    return get_attachment(att_id)


def store_file_bytes(item_id: int, data: bytes, filename: str, *, title: Optional[str] = None,
                     kind: str = "file", url: Optional[str] = None) -> dict:
    """Write uploaded bytes into the papers folder (renamed from metadata) and attach them."""
    from .library import build_filename  # local import: library imports catalog

    item = get_item(item_id, full=False)
    if not item:
        raise ValueError("item not found")
    ext = Path(filename).suffix.lstrip(".").lower() or "pdf"
    target = papers_dir() / build_filename({**item, "extension": ext})
    if target.exists():
        if target.read_bytes() == data:
            return register_file(item_id, target, title=title, kind=kind, url=url)
        stem = target.stem
        i = 2
        while target.exists():
            target = target.with_name(f"{stem} ({i}).{ext}")
            i += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    return register_file(item_id, target, title=title, kind=kind, url=url)


def add_link_attachment(item_id: int, url: str, title: Optional[str] = None) -> dict:
    att_id = get_conn().execute(
        "INSERT INTO attachments (item_id, kind, title, url, mime) VALUES (?, 'link', ?, ?, 'text/html')",
        (item_id, title or url, url),
    ).lastrowid
    return get_attachment(att_id)


def rename_attachment(att_id: int, title: str) -> Optional[dict]:
    get_conn().execute("UPDATE attachments SET title=? WHERE id=?", (title.strip(), att_id))
    return get_attachment(att_id)


def delete_attachment(att_id: int, *, delete_file: bool = True) -> None:
    att = get_attachment(att_id)
    if not att:
        return
    conn = get_conn()
    conn.execute("DELETE FROM fulltext_fts WHERE attachment_id=?", (att_id,))
    conn.execute("DELETE FROM attachments WHERE id=?", (att_id,))
    if delete_file and att.get("path"):
        managed_root = str(papers_dir().resolve()).casefold()
        try:
            rp = Path(att["path"]).resolve()
            if str(rp).casefold().startswith(managed_root) and rp.is_file():
                rp.unlink()
        except OSError:
            pass
    if att.get("item_id"):
        sync_primary_file(att["item_id"])


def rename_files_from_metadata(item_id: int) -> dict:
    """Zotero 'Rename File from Parent Metadata' for every file attachment of an item."""
    from .library import build_filename

    item = get_item(item_id, full=False)
    renamed = 0
    for att in list_attachments(item_id):
        if att["kind"] != "file" or not att.get("path"):
            continue
        src = Path(att["path"])
        if not src.exists():
            continue
        target = src.with_name(build_filename({**item, "extension": src.suffix.lstrip(".")}))
        if target == src:
            continue
        i = 2
        base = target
        while target.exists():
            target = base.with_name(f"{base.stem} ({i}){base.suffix}")
            i += 1
        shutil.move(str(src), str(target))
        get_conn().execute("UPDATE attachments SET path=? WHERE id=?", (str(target), att["id"]))
        renamed += 1
    sync_primary_file(item_id)
    return {"renamed": renamed}


# ======================================================================================
# Annotations
# ======================================================================================

def list_annotations(att_id: int) -> list[dict]:
    rows = get_conn().execute(
        "SELECT * FROM annotations WHERE attachment_id=? ORDER BY page, sort_index, id", (att_id,)
    ).fetchall()
    return rows_to_dicts(rows)


def create_annotation(att_id: int, data: dict) -> dict:
    ann_id = get_conn().execute(
        "INSERT INTO annotations (attachment_id, type, color, page, text, comment, position, sort_index) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (att_id, data.get("type") or "highlight", data.get("color") or "#ffd400", int(data.get("page") or 1),
         data.get("text"), data.get("comment"), _json(data.get("position") or {}), data.get("sort_index")),
    ).lastrowid
    return row_to_dict(get_conn().execute("SELECT * FROM annotations WHERE id=?", (ann_id,)).fetchone())


def update_annotation(ann_id: int, data: dict) -> Optional[dict]:
    updates = {k: data[k] for k in ("color", "comment", "text", "type") if k in data}
    if "position" in data:
        updates["position"] = _json(data["position"])
    if updates:
        get_conn().execute(
            f"UPDATE annotations SET {', '.join(f'{k}=?' for k in updates)}, updated_at=datetime('now') WHERE id=?",
            [*updates.values(), ann_id],
        )
    return row_to_dict(get_conn().execute("SELECT * FROM annotations WHERE id=?", (ann_id,)).fetchone())


def delete_annotation(ann_id: int) -> None:
    get_conn().execute("DELETE FROM annotations WHERE id=?", (ann_id,))


def annotations_to_note(att_id: int) -> Optional[dict]:
    """Zotero 'Add Note from Annotations': quoted highlights + comments with page numbers."""
    import html as _html

    att = get_attachment(att_id)
    if not att or not att.get("item_id"):
        return None
    item = get_item(att["item_id"], full=False)
    anns = list_annotations(att_id)
    parts = [f"<h2>Annotations</h2><p><i>{_html.escape(item.get('title') or '')}</i></p>"]
    for a in anns:
        chunk = ""
        if a.get("text"):
            chunk += (f'<blockquote><span style="background-color: {_html.escape(a.get("color") or "#ffd400")}">'
                      f'"{_html.escape(a["text"])}"</span> (p. {a["page"]})</blockquote>')
        if a.get("comment"):
            chunk += f"<p>{_html.escape(a['comment'])}" + ("" if a.get("text") else f" (p. {a['page']})") + "</p>"
        parts.append(chunk)
    return create_note(att["item_id"], "".join(parts))


# ======================================================================================
# Related items
# ======================================================================================

def relate(item_id: int, other_id: int) -> None:
    if item_id == other_id:
        return
    with tx() as conn:
        conn.execute("INSERT OR IGNORE INTO related_items (item_id, related_id) VALUES (?, ?)", (item_id, other_id))
        conn.execute("INSERT OR IGNORE INTO related_items (item_id, related_id) VALUES (?, ?)", (other_id, item_id))


def unrelate(item_id: int, other_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM related_items WHERE (item_id=? AND related_id=?) OR (item_id=? AND related_id=?)",
                     (item_id, other_id, other_id, item_id))


# ======================================================================================
# Collections (nested)
# ======================================================================================

def list_collections() -> list[dict]:
    rows = get_conn().execute(
        "SELECT c.id, c.name, c.parent_id, c.color, c.icon, c.sort_order, "
        "  (SELECT COUNT(*) FROM item_collections ic JOIN items i ON i.id=ic.item_id "
        "   WHERE ic.collection_id=c.id AND i.deleted_at IS NULL) AS count "
        "FROM collections c ORDER BY c.sort_order, c.name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def collection_descendants(cid: int) -> list[int]:
    rows = get_conn().execute(
        "WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT c.id FROM collections c JOIN sub ON c.parent_id=sub.id) "
        "SELECT id FROM sub", (cid,)
    ).fetchall()
    return [r[0] for r in rows]


def move_collection(cid: int, parent_id: Optional[int]) -> None:
    if parent_id is not None and parent_id in collection_descendants(cid):
        raise ValueError("cannot move a collection inside itself")
    get_conn().execute("UPDATE collections SET parent_id=? WHERE id=?", (parent_id, cid))


# ======================================================================================
# Advanced search
# ======================================================================================

# condition field -> SQL builder. Each returns (sql, params) evaluated per item alias `i`.
_TEXT_COLUMNS = {
    "title": "i.title", "publicationTitle": "i.journal", "publisher": "i.publisher", "abstract": "i.abstract",
    "doi": "i.doi", "url": "i.url", "extra": "i.extra", "citationKey": "i.citation_key", "creator": "i.creators",
    "itemType": "i.item_type", "year": "CAST(i.year AS TEXT)", "language": "i.language", "volume": "i.volume",
}


def _condition_sql(cond: dict) -> tuple[str, list[Any]]:
    field = cond.get("field") or "title"
    op = cond.get("op") or "contains"
    value = str(cond.get("value") or "")
    if field == "tag":
        inner = "SELECT it.item_id FROM item_tags it JOIN tags t ON t.id=it.tag_id WHERE "
        if op in ("is", "isNot"):
            sql = f"i.id {'NOT ' if op == 'isNot' else ''}IN ({inner} t.name=? COLLATE NOCASE)"
        else:
            sql = f"i.id {'NOT ' if op == 'doesNotContain' else ''}IN ({inner} t.name LIKE ?)"
            value = f"%{value}%"
        return sql, [value]
    if field == "collection":
        ids = collection_descendants(int(value)) if value.isdigit() else []
        marks = ",".join("?" for _ in ids) or "NULL"
        neg = "NOT " if op in ("isNot", "doesNotContain") else ""
        return f"i.id {neg}IN (SELECT item_id FROM item_collections WHERE collection_id IN ({marks}))", ids
    if field == "note":
        neg = "NOT " if op == "doesNotContain" else ""
        return f"i.id {neg}IN (SELECT item_id FROM notes WHERE deleted_at IS NULL AND html LIKE ?)", [f"%{value}%"]
    if field == "fulltext":
        neg = "NOT " if op == "doesNotContain" else ""
        return (f"i.id {neg}IN (SELECT CAST(item_id AS INTEGER) FROM fulltext_fts WHERE fulltext_fts MATCH ?)",
                [_fts_phrase(value)])
    if field == "dateAdded":
        cmp = {"isBefore": "<", "isAfter": ">", "is": "="}.get(op, ">")
        return f"date(i.added_at) {cmp} date(?)", [value]
    if field == "hasAttachment":
        return ("i.id IN (SELECT item_id FROM attachments WHERE kind != 'link')" if value in ("1", "true", "yes")
                else "i.id NOT IN (SELECT item_id FROM attachments WHERE kind != 'link')"), []
    if field == "starred":
        return ("i.starred=1" if value in ("1", "true", "yes") else "COALESCE(i.starred,0)=0"), []
    if field == "impactFactor":
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "0", []
        # "at least" / "at most" (>=, <=) rather than strict, so a threshold search includes the boundary.
        cmp = {"isLessThan": "<=", "isGreaterThan": ">="}.get(op, "=")
        return f"i.impact_factor {cmp} ?", [num]
    col = _TEXT_COLUMNS.get(field, "i.title")
    if field == "year" and op in ("isLessThan", "isGreaterThan"):
        return f"i.year {'<' if op == 'isLessThan' else '>'} ?", [int(value or 0)]
    if op == "is":
        return f"COALESCE({col},'') = ? COLLATE NOCASE", [value]
    if op == "isNot":
        return f"COALESCE({col},'') != ? COLLATE NOCASE", [value]
    if op == "doesNotContain":
        return f"COALESCE({col},'') NOT LIKE ?", [f"%{value}%"]
    if op == "beginsWith":
        return f"COALESCE({col},'') LIKE ?", [f"{value}%"]
    return f"COALESCE({col},'') LIKE ?", [f"%{value}%"]


def _fts_phrase(q: str) -> str:
    tokens = [t for t in re.findall(r"\w+", q or "", flags=re.UNICODE) if t]
    return " ".join(f'"{t}"*' for t in tokens) or '""'


def query_items(
    *,
    collection_id: Optional[int] = None,
    include_subcollections: bool = True,
    unfiled: bool = False,
    starred: bool = False,
    trash: bool = False,
    status: Optional[str] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    tags: Optional[list[str]] = None,
    item_type: Optional[str] = None,
    q: Optional[str] = None,
    mode: str = "titleCreatorYear",          # titleCreatorYear | fields | everything
    conditions: Optional[list[dict]] = None,
    match: str = "all",
    saved_search_id: Optional[int] = None,
    has_notes: bool = False,
    sort: str = "added",
    direction: str = "desc",
    limit: int = 5000,
    offset: int = 0,
    compact: bool = False,
) -> list[dict]:
    """Single entry point for every list view (library, collections, tags, searches, trash).

    ``compact`` drops columns list views never show (abstract, mirrors...) to shrink payloads.
    """
    conn = get_conn()
    where: list[str] = ["i.deleted_at IS NOT NULL" if trash else "i.deleted_at IS NULL"]
    params: list[Any] = []

    if saved_search_id:
        row = conn.execute("SELECT match, conditions FROM saved_searches WHERE id=?", (saved_search_id,)).fetchone()
        if row:
            match = row[0]
            conditions = json.loads(row[1] or "[]")
    if collection_id is not None:
        ids = collection_descendants(collection_id) if include_subcollections else [collection_id]
        where.append(f"i.id IN (SELECT item_id FROM item_collections WHERE collection_id IN "
                     f"({','.join('?' for _ in ids)}))")
        params.extend(ids)
    if unfiled:
        where.append("i.id NOT IN (SELECT item_id FROM item_collections)")
    if starred:
        where.append("i.starred = 1")
    if status:
        if status == "failed":
            where.append("i.status IN ('failed','needs_metadata')")
        else:
            where.append("i.status = ?")
            params.append(status)
    if author:
        where.append("(i.authors LIKE ? OR i.creators LIKE ?)")
        params.extend([f"%{author}%", f"%{author}%"])
    if subject:
        where.append("(i.subjects LIKE ? OR i.keywords LIKE ?)")
        params.extend([f"%\"{subject}\"%", f"%\"{subject}\"%"])
    if item_type:
        where.append("i.item_type = ?")
        params.append(item_type)
    if has_notes:
        where.append("i.id IN (SELECT item_id FROM notes WHERE deleted_at IS NULL)")
    for tag in tags or []:
        where.append("i.id IN (SELECT it.item_id FROM item_tags it JOIN tags t ON t.id=it.tag_id "
                     "WHERE t.name = ? COLLATE NOCASE)")
        params.append(tag)
    if q and q.strip():
        phrase = _fts_phrase(q)
        like = f"%{q.strip()}%"
        if mode == "titleCreatorYear":
            where.append("(i.title LIKE ? OR i.creators LIKE ? OR CAST(i.year AS TEXT) = ? OR i.short_title LIKE ?)")
            params.extend([like, like, q.strip(), like])
        else:
            parts = ["i.id IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?)",
                     "i.id IN (SELECT it.item_id FROM item_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name LIKE ?)",
                     "i.id IN (SELECT item_id FROM notes WHERE deleted_at IS NULL AND html LIKE ?)",
                     "i.creators LIKE ?"]
            params.extend([phrase, like, like, like])
            if mode == "everything":
                parts.append("i.id IN (SELECT CAST(item_id AS INTEGER) FROM fulltext_fts WHERE fulltext_fts MATCH ?)")
                params.append(phrase)
            where.append("(" + " OR ".join(parts) + ")")
    if conditions:
        built = [_condition_sql(c) for c in conditions if c.get("field")]
        if built:
            joiner = " OR " if match == "any" else " AND "
            where.append("(" + joiner.join(sql for sql, _ in built) + ")")
            for _, p in built:
                params.extend(p)

    order = {
        "added": "i.added_at", "modified": "i.updated_at", "year": "COALESCE(i.year, 0)",
        "title": "i.title COLLATE NOCASE", "creator": "i.authors COLLATE NOCASE",
        "publication": "i.journal COLLATE NOCASE", "type": "i.item_type",
        "deleted": "i.deleted_at", "impact": "i.impact_factor",
        # SQLite sorts NULL before every value in both directions of a plain ORDER BY; for ASC that
        # buries rated items behind every unrated one, so ASC explicitly pushes NULLs to the end instead.
    }.get(sort, "i.added_at")
    if sort == "impact" and direction.lower() != "desc":
        order = "(i.impact_factor IS NULL), i.impact_factor"
    direction = "ASC" if direction.lower() == "asc" else "DESC"
    # Compact lists skip large text columns entirely (less I/O and JSON decoding than SELECT *).
    select = "i.*" if not compact else ", ".join(f"i.{c}" for c in _LIST_COLUMNS)
    sql = (f"SELECT {select} FROM items i WHERE {' AND '.join(where)} ORDER BY {order} {direction}, i.id DESC "
           f"LIMIT ? OFFSET ?")
    try:
        rows = conn.execute(sql, [*params, limit, offset]).fetchall()
    except sqlite3.OperationalError:
        if not q:
            raise
        # Malformed FTS expression: degrade to title/creator LIKE search rather than failing.
        return query_items(collection_id=collection_id, include_subcollections=include_subcollections,
                           unfiled=unfiled, starred=starred, trash=trash, status=status, author=author,
                           subject=subject, tags=tags, item_type=item_type, q=q, mode="titleCreatorYear",
                           conditions=conditions, match=match, sort=sort, direction=direction, limit=limit,
                           offset=offset, compact=compact)
    items = rows_to_dicts(rows)
    return decorate_many(items, compact=compact)


# Columns needed by list views (items table, tag selector, duplicates, trash). Everything else is loaded
# by the item pane through GET /api/items/{id}.
_LIST_COLUMNS = ("id", "doi", "title", "authors", "creators", "year", "date", "journal", "publisher", "status",
                 "starred", "added_at", "updated_at", "item_type", "extension", "citation_key", "file_path",
                 "deleted_at", "error_msg", "volume", "pages", "url", "issn",
                 "impact_factor", "impact_factor_source", "impact_factor_year")

# Columns the list views never display; dropped from list payloads (the item pane loads the full item).
_HEAVY_LIST_COLUMNS = ("abstract", "mirrors", "source_query", "subjects", "fields")


def decorate_many(items: list[dict], *, compact: bool = False) -> list[dict]:
    """Batch version of ``decorate`` for lists.

    Benchmark iteration 1 showed per-item decoration (2 extra queries per row, N+1 pattern) made
    listing 2000 items 3.7x slower than v1. Here tags and counts are fetched with one grouped query
    per chunk of ids, independent of the number of rows.
    """
    if not items:
        return items
    conn = get_conn()
    ids = [it["id"] for it in items]
    tags: dict[int, list[dict]] = {}
    notes: dict[int, int] = {}
    atts: dict[int, int] = {}
    for start in range(0, len(ids), 900):              # stay below SQLite's bound-parameter limit
        chunk = ids[start:start + 900]
        marks = ",".join("?" for _ in chunk)
        for r in conn.execute(
            f"SELECT it.item_id, t.id, t.name, t.color, t.type FROM item_tags it JOIN tags t ON t.id=it.tag_id "
            f"WHERE it.item_id IN ({marks}) ORDER BY t.name COLLATE NOCASE", chunk
        ):
            tags.setdefault(r[0], []).append({"id": r[1], "name": r[2], "color": r[3], "type": r[4]})
        for r in conn.execute(
            f"SELECT item_id, COUNT(*) FROM notes WHERE deleted_at IS NULL AND item_id IN ({marks}) GROUP BY item_id",
            chunk,
        ):
            notes[r[0]] = r[1]
        for r in conn.execute(f"SELECT item_id, COUNT(*) FROM attachments WHERE item_id IN ({marks}) GROUP BY item_id",
                              chunk):
            atts[r[0]] = r[1]
    for it in items:
        iid = it["id"]
        it["item_type"] = normalize_type(it.get("item_type"))
        it["type_label"] = ITEM_TYPES[it["item_type"]]["label"]
        if not it.get("creators") and it.get("authors"):
            it["creators"] = clean_creators(it["authors"])
        it["tags"] = tags.get(iid, [])
        it["note_count"] = notes.get(iid, 0)
        it["attachment_count"] = atts.get(iid, 0)
        if compact:
            for col in _HEAVY_LIST_COLUMNS:
                it.pop(col, None)
    return items


# ---------------------------- Saved searches ----------------------------

def list_saved_searches() -> list[dict]:
    return rows_to_dicts(get_conn().execute("SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE").fetchall())


def save_search(name: str, conditions: list[dict], match: str = "all", search_id: Optional[int] = None) -> dict:
    conn = get_conn()
    if search_id:
        conn.execute("UPDATE saved_searches SET name=?, conditions=?, match=? WHERE id=?",
                     (name, _json(conditions), match, search_id))
    else:
        search_id = conn.execute("INSERT INTO saved_searches (name, conditions, match) VALUES (?, ?, ?)",
                                 (name, _json(conditions), match)).lastrowid
    return row_to_dict(conn.execute("SELECT * FROM saved_searches WHERE id=?", (search_id,)).fetchone())


def delete_saved_search(search_id: int) -> None:
    get_conn().execute("DELETE FROM saved_searches WHERE id=?", (search_id,))


# ======================================================================================
# Stats
# ======================================================================================

def stats() -> dict:
    c = get_conn()
    one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "items": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL"),
        "downloaded": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL AND status='downloaded'"),
        "starred": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL AND starred=1"),
        "collections": one("SELECT COUNT(*) FROM collections"),
        "unfiled": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL AND id NOT IN "
                       "(SELECT item_id FROM item_collections)"),
        "trash": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NOT NULL")
                 + one("SELECT COUNT(*) FROM notes WHERE deleted_at IS NOT NULL"),
        "failed": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL AND status IN ('failed','needs_metadata')"),
        "tags": one("SELECT COUNT(*) FROM tags"),
        "notes": one("SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL AND item_id IS NULL"),
    }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


__all__ = [name for name in dir() if not name.startswith("_") and name not in ("os",)]
