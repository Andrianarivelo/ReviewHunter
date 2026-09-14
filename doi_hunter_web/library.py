"""Library service - the brain that ties metadata, files, and the DB together.

Public surface used by the API:
    add_from_query, download_item, import_pdf_folder, build_filename,
    list_items, get_item, list_collections, create_collection, ...
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from doi_hunter.netinfo import get_headers
from doi_hunter.utils import sanitize_filename

from . import catalog, config
from .config import papers_dir
from .db import get_conn, row_to_dict, rows_to_dicts, tx
from .metadata import (
    extract_keywords,
    fetch_doi,
    looks_like_doi,
    search_crossref,
    suggest_collections,
)
from .sources import iter_pdf_urls
from .freebooks import resolve_book_url

log = logging.getLogger("doihunter.library")


def _download_timeout() -> tuple[int, int]:
    try:
        read_timeout = int(
            os.environ.get("REVIEWHUNTER_DOWNLOAD_TIMEOUT")
            or os.environ.get("DOIHUNTER_DOWNLOAD_TIMEOUT", "45")
        )
    except ValueError:
        read_timeout = 45
    return (15, read_timeout)


# ---------------- Filename construction ----------------

def _short_title(title: str, words: int = 8) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", title)
    return " ".join(parts[:words]) or "Untitled"


def build_filename(item: dict) -> str:
    """Zotero-style: 'Smith et al - 2023 - Short Title.pdf' (or .epub, .mobi, ...)."""
    authors = item.get("authors") or []
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except json.JSONDecodeError:
            authors = [authors]

    if authors:
        first = authors[0].split(",")[0].strip()
        if len(authors) == 1:
            who = first
        elif len(authors) == 2:
            second = authors[1].split(",")[0].strip()
            who = f"{first} and {second}"
        else:
            who = f"{first} et al"
    else:
        who = "Unknown"

    year = item.get("year") or "n.d."
    title = _short_title(item.get("title") or "Untitled")
    ext = (item.get("extension") or "pdf").lstrip(".").lower()
    raw = f"{who} - {year} - {title}.{ext}"
    return sanitize_filename(raw)


# ---------------- Item CRUD ----------------

def _serialize(item: dict) -> dict:
    out = dict(item)
    for k in ("authors", "subjects", "keywords", "isbn", "mirrors"):
        v = out.get(k)
        if isinstance(v, list):
            out[k] = json.dumps(v, ensure_ascii=False)
    return out


_ITEM_COLS = [
    "title", "doi", "authors", "year", "journal", "publisher", "volume",
    "issue", "pages", "abstract", "subjects", "keywords", "file_path",
    "file_size", "status", "error_msg", "source_query",
    "item_type", "isbn", "extension", "mirrors",
]


def upsert_item(item: dict) -> int:
    """Insert or update by DOI. Returns item id."""
    s = _serialize(item)
    conn = get_conn()
    if s.get("doi"):
        existing = conn.execute("SELECT id FROM items WHERE doi = ?", (s["doi"],)).fetchone()
        if existing:
            iid = existing["id"]
            fields = [k for k in _ITEM_COLS if k in s and k != "doi"]
            if fields:
                set_sql = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
                conn.execute(f"UPDATE items SET {set_sql} WHERE id=?",
                             [s.get(k) for k in fields] + [iid])
            if "authors" in fields:
                conn.execute("UPDATE items SET creators=NULL WHERE id=?", (iid,))
            _post_upsert(iid)
            return iid
    if not s.get("item_type"):
        s["item_type"] = "journalArticle"
    vals = [s.get(c) for c in _ITEM_COLS]
    cur = conn.execute(
        f"INSERT INTO items ({','.join(_ITEM_COLS)}) VALUES ({','.join('?' for _ in _ITEM_COLS)})",
        vals,
    )
    _post_upsert(cur.lastrowid)
    return cur.lastrowid


def _post_upsert(item_id: int) -> None:
    """Fill v2 columns (creators, date, citation key, normalized type) for legacy writers."""
    conn = get_conn()
    row = row_to_dict(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
    if not row:
        return
    updates: dict[str, Any] = {}
    if not row.get("creators") and row.get("authors"):
        updates["creators"] = json.dumps(catalog.clean_creators(row["authors"]), ensure_ascii=False)
    if not row.get("date") and row.get("year"):
        updates["date"] = str(row["year"])
    normalized = catalog.normalize_type(row.get("item_type"))
    if normalized != row.get("item_type"):
        updates["item_type"] = normalized
    if not row.get("citation_key"):
        creators = json.loads(updates["creators"]) if "creators" in updates else row.get("creators")
        updates["citation_key"] = catalog.make_citation_key({**row, "creators": creators}, conn)
    if row.get("deleted_at"):
        updates["deleted_at"] = None
    if updates:
        conn.execute(f"UPDATE items SET {', '.join(k + '=?' for k in updates)} WHERE id=?",
                     [*updates.values(), item_id])


def get_item(item_id: int) -> Optional[dict]:
    """Full item (v2 shape, legacy keys preserved)."""
    return catalog.get_item(item_id)


def update_item(item_id: int, patch: dict) -> Optional[dict]:
    return catalog.patch_item(item_id, patch)


def delete_item(item_id: int, delete_file: bool = False, *, permanent: bool = False) -> bool:
    """Move to trash (default, Zotero behaviour) or delete permanently."""
    if permanent:
        return catalog.delete_items_permanently([item_id], delete_files=delete_file) > 0
    return catalog.trash_items([item_id]) > 0


def list_items(
    collection_id: Optional[int] = None,
    starred: bool = False,
    query: Optional[str] = None,
    status: Optional[str] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 500,
    **extra: Any,
) -> list[dict]:
    return catalog.query_items(
        collection_id=collection_id, starred=starred, q=query, status=status,
        author=author, subject=subject, limit=limit, mode=extra.pop("mode", "fields"), **extra,
    )


# ---------------- Facets (auto-organization) ----------------

def list_authors(limit: int = 200) -> list[dict]:
    """Aggregate distinct authors across the library, with item counts."""
    rows = get_conn().execute("SELECT authors FROM items WHERE authors IS NOT NULL AND deleted_at IS NULL").fetchall()
    counter: dict[str, int] = {}
    for r in rows:
        try:
            authors = json.loads(r["authors"]) if r["authors"] else []
        except (json.JSONDecodeError, TypeError):
            authors = []
        for a in authors:
            name = (a or "").strip()
            if name:
                counter[name] = counter.get(name, 0) + 1
    pairs = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": n, "count": c} for n, c in pairs[:limit]]


def list_subjects(limit: int = 200) -> list[dict]:
    """Aggregate distinct subjects + keywords across the library, with counts."""
    rows = get_conn().execute("SELECT subjects, keywords FROM items WHERE deleted_at IS NULL").fetchall()
    counter: dict[str, int] = {}
    for r in rows:
        for col in ("subjects", "keywords"):
            try:
                vals = json.loads(r[col]) if r[col] else []
            except (json.JSONDecodeError, TypeError):
                vals = []
            for v in vals:
                name = (v or "").strip()
                if name and len(name) > 1:
                    counter[name] = counter.get(name, 0) + 1
    pairs = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": n, "count": c} for n, c in pairs[:limit]]


def facets() -> dict:
    return {"authors": list_authors(), "subjects": list_subjects()}


# ---------------- Subject graph ----------------

_GRAPH_STOP_TERMS = {
    "article",
    "case reports",
    "clinical trial",
    "comparative study",
    "editorial",
    "english abstract",
    "evaluation study",
    "historical article",
    "journal article",
    "letter",
    "meta-analysis",
    "multicenter study",
    "observational study",
    "practice guideline",
    "published erratum",
    "randomized controlled trial",
    "research support, american recovery and reinvestment act",
    "research support, n.i.h., extramural",
    "research support, n.i.h., intramural",
    "research support, non-u.s. gov't",
    "research support, u.s. gov't, non-p.h.s.",
    "review",
    "systematic review",
    "validation study",
}


def _graph_key(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().casefold())


def _graph_terms(item: dict) -> list[tuple[str, str]]:
    labels: dict[str, str] = {}
    for field in ("subjects", "keywords"):
        values = item.get(field) or []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except (json.JSONDecodeError, TypeError):
                values = [values]
        for raw in values:
            label = re.sub(r"\s+", " ", str(raw or "").strip())
            key = _graph_key(label)
            if not key or key in _GRAPH_STOP_TERMS:
                continue
            if len(key) < 2 or key.isnumeric():
                continue
            labels.setdefault(key, label)
    return sorted(labels.items(), key=lambda kv: kv[1].casefold())


def subject_graph(
    *,
    collection_id: Optional[int] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    focus: Optional[str] = None,
    depth: int = 1,
    limit: int = 48,
) -> dict:
    """Build a local subject/keyword co-occurrence graph.

    Nodes are subjects and extracted keywords. Edges mean two terms appear on
    the same library item; edge weight is the number of shared items.
    """
    limit = max(12, min(limit, 90))
    depth = max(1, min(depth, 2))
    items = list_items(collection_id=collection_id, author=author, subject=subject, limit=10000)

    label_by_key: dict[str, str] = {}
    term_items: dict[str, list[dict]] = defaultdict(list)
    counts: Counter[str] = Counter()
    edges: Counter[tuple[str, str]] = Counter()

    for item in items:
        terms = _graph_terms(item)
        if not terms:
            continue
        keys = sorted({key for key, _label in terms})[:24]
        for key, label in terms:
            label_by_key.setdefault(key, label)
        for key in keys:
            counts[key] += 1
            term_items[key].append(item)
        for a, b in combinations(keys, 2):
            edges[(a, b)] += 1

    focus_key = _graph_key(focus or subject or "")
    selected: set[str]
    if focus_key and focus_key in counts:
        selected = {focus_key}
        frontier = {focus_key}
        for _ in range(depth):
            next_frontier: set[str] = set()
            ranked_neighbors: list[tuple[int, int, str]] = []
            for (a, b), weight in edges.items():
                neighbor = ""
                if a in frontier and b not in selected:
                    neighbor = b
                elif b in frontier and a not in selected:
                    neighbor = a
                if neighbor:
                    ranked_neighbors.append((weight, counts[neighbor], neighbor))
            for _weight, _count, neighbor in sorted(ranked_neighbors, reverse=True):
                if len(selected) >= limit:
                    break
                selected.add(neighbor)
                next_frontier.add(neighbor)
            if not next_frontier or len(selected) >= limit:
                break
            frontier = next_frontier
    else:
        selected = {key for key, _count in counts.most_common(limit)}

    def sample_items(key: str) -> list[dict]:
        sample = sorted(
            term_items.get(key, []),
            key=lambda it: ((it.get("year") or 0), it.get("added_at") or ""),
            reverse=True,
        )[:8]
        return [
            {
                "id": it["id"],
                "title": it.get("title") or "(untitled)",
                "year": it.get("year"),
                "journal": it.get("journal") or it.get("publisher") or "",
                "authors": it.get("authors") or [],
                "status": it.get("status") or "",
            }
            for it in sample
        ]

    def related_terms(key: str) -> list[dict]:
        related = []
        for (a, b), weight in edges.items():
            other = b if a == key else a if b == key else ""
            if other:
                related.append({"id": other, "label": label_by_key.get(other, other), "weight": weight})
        related.sort(key=lambda r: (-r["weight"], r["label"].casefold()))
        return related[:8]

    node_payload = []
    for key in selected:
        node_payload.append(
            {
                "id": key,
                "label": label_by_key.get(key, key),
                "count": counts[key],
                "focused": key == focus_key,
                "items": sample_items(key),
                "related": related_terms(key),
            }
        )
    node_payload.sort(key=lambda n: (not n["focused"], -n["count"], n["label"].casefold()))

    edge_payload = [
        {
            "source": a,
            "target": b,
            "weight": weight,
        }
        for (a, b), weight in edges.items()
        if a in selected and b in selected
    ]
    edge_payload.sort(key=lambda e: -e["weight"])

    scope_parts = []
    if collection_id is not None:
        collection = next((c for c in list_collections() if c["id"] == collection_id), None)
        scope_parts.append(collection["name"] if collection else "Folder")
    if author:
        scope_parts.append(author)
    if subject:
        scope_parts.append(subject)

    return {
        "scope": {
            "collection_id": collection_id,
            "author": author,
            "subject": subject,
            "focus": focus or subject or "",
            "label": " / ".join(scope_parts) if scope_parts else "All subjects",
            "items": len(items),
        },
        "nodes": node_payload,
        "edges": edge_payload[:180],
        "total_terms": len(counts),
    }


# ---------------- Collections ----------------

def list_collections() -> list[dict]:
    return catalog.list_collections()


def create_collection(name: str, parent_id: Optional[int] = None, color: Optional[str] = None) -> dict:
    cur = get_conn().execute(
        "INSERT INTO collections (name, parent_id, color) VALUES (?, ?, ?)",
        (name.strip(), parent_id, color),
    )
    return {"id": cur.lastrowid, "name": name, "parent_id": parent_id, "color": color, "count": 0}


def rename_collection(cid: int, name: str) -> None:
    get_conn().execute("UPDATE collections SET name=? WHERE id=?", (name.strip(), cid))


def delete_collection(cid: int) -> None:
    get_conn().execute("DELETE FROM collections WHERE id=?", (cid,))


def assign_to_collection(item_id: int, collection_id: int) -> None:
    get_conn().execute(
        "INSERT OR IGNORE INTO item_collections (item_id, collection_id) VALUES (?, ?)",
        (item_id, collection_id),
    )


def remove_from_collection(item_id: int, collection_id: int) -> None:
    get_conn().execute(
        "DELETE FROM item_collections WHERE item_id=? AND collection_id=?",
        (item_id, collection_id),
    )


# ---------------- Local file actions ----------------

def _matching_items(
    *,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
    limit: int = 10000,
) -> list[dict]:
    if item_ids:
        return [item for item in (get_item(i) for i in item_ids) if item]
    return list_items(author=author, subject=subject, limit=limit)


def _open_path(path: Path) -> None:
    path = path.resolve()
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_item_folder(item_id: int) -> dict:
    item = get_item(item_id)
    if not item:
        raise ValueError("item not found")
    file_path = item.get("file_path")
    folder = Path(file_path).parent if file_path and Path(file_path).exists() else papers_dir()
    _open_path(folder)
    return {"ok": True, "path": str(folder)}


def _unique_target(destination: Path, source: Path) -> Path:
    target = destination / source.name
    if not target.exists():
        return target
    stem, suffix = source.stem, source.suffix
    for i in range(2, 10000):
        candidate = destination / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many filename collisions for {source.name}")


def export_item_file(item_id: int, destination: str | Path) -> dict:
    item = get_item(item_id)
    if not item:
        raise ValueError("item not found")
    src_raw = item.get("file_path")
    if not src_raw:
        raise ValueError("item has no file on disk")
    src = Path(src_raw)
    if not src.exists():
        raise ValueError("file is missing on disk")
    dest = Path(destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    target = _unique_target(dest, src)
    shutil.copy2(src, target)
    return {"exported": 1, "skipped": 0, "path": str(dest), "files": [str(target)]}


def assign_matching_to_collection(
    collection_id: int,
    *,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
) -> dict:
    items = _matching_items(author=author, subject=subject, item_ids=item_ids)
    with tx():
        for item in items:
            assign_to_collection(item["id"], collection_id)
    return {"count": len(items), "collection_id": collection_id}


def export_matching_pdfs(
    destination: str | Path,
    *,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
) -> dict:
    items = _matching_items(author=author, subject=subject, item_ids=item_ids)
    dest = Path(destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    exported = 0
    skipped = 0
    files: list[str] = []
    for item in items:
        src_raw = item.get("file_path")
        if not src_raw:
            skipped += 1
            continue
        src = Path(src_raw)
        if not src.exists() or src.suffix.lower() != ".pdf":
            skipped += 1
            continue
        target = _unique_target(dest, src)
        shutil.copy2(src, target)
        exported += 1
        files.append(str(target))
    return {"exported": exported, "skipped": skipped, "path": str(dest), "files": files}


def export_matching_list_csv(
    *,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
) -> str:
    items = _matching_items(author=author, subject=subject, item_ids=item_ids)
    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=["title", "authors", "year", "journal", "publisher", "doi", "status", "file_path"],
    )
    writer.writeheader()
    for item in items:
        writer.writerow({
            "title": item.get("title") or "",
            "authors": "; ".join(item.get("authors") or []),
            "year": item.get("year") or "",
            "journal": item.get("journal") or "",
            "publisher": item.get("publisher") or "",
            "doi": item.get("doi") or "",
            "status": item.get("status") or "",
            "file_path": item.get("file_path") or "",
        })
    return out.getvalue()


# ---------------- Storage / backup ----------------

def _library_csv_text(items: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "id", "title", "authors", "year", "journal", "publisher", "doi",
            "status", "item_type", "file_path", "subjects", "keywords", "notes",
        ],
    )
    writer.writeheader()
    for item in items:
        writer.writerow({
            "id": item.get("id") or "",
            "title": item.get("title") or "",
            "authors": "; ".join(item.get("authors") or []),
            "year": item.get("year") or "",
            "journal": item.get("journal") or "",
            "publisher": item.get("publisher") or "",
            "doi": item.get("doi") or "",
            "status": item.get("status") or "",
            "item_type": item.get("item_type") or "paper",
            "file_path": item.get("file_path") or "",
            "subjects": "; ".join(item.get("subjects") or []),
            "keywords": "; ".join(item.get("keywords") or []),
            "notes": item.get("notes") or "",
        })
    return out.getvalue()


def _backup_sqlite_database(target: Path) -> None:
    source = get_conn()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(str(target))
    try:
        source.backup(backup)
    finally:
        backup.close()


def export_full_library(destination: str | Path) -> dict:
    """Export DB snapshot, CSV, manifest, and downloaded files to a folder."""
    root = Path(destination).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    export_root = root / "ReviewHunter" / "backups" / f"ReviewHunter-export-{timestamp}"
    files_root = export_root / "files"
    export_root.mkdir(parents=True, exist_ok=True)
    files_root.mkdir(parents=True, exist_ok=True)

    items = list_items(limit=100000)
    _backup_sqlite_database(export_root / "library.db")
    (export_root / "library.csv").write_text(_library_csv_text(items), encoding="utf-8")

    copied = 0
    skipped = 0
    exported_files: list[str] = []
    for item in items:
        src_raw = item.get("file_path")
        if not src_raw:
            skipped += 1
            continue
        src = Path(src_raw)
        if not src.exists() or not src.is_file():
            skipped += 1
            continue
        target = _unique_target(files_root, src)
        shutil.copy2(src, target)
        copied += 1
        exported_files.append(str(target))

    manifest = {
        "app": "ReviewHunter",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "library_root": str(config.library_root()),
        "papers_dir": str(papers_dir()),
        "db_path": str(config.db_path()),
        "items": len(items),
        "downloaded_files_copied": copied,
        "files_skipped": skipped,
        "files_dir": str(files_root),
    }
    (export_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "path": str(export_root),
        "items": len(items),
        "files": copied,
        "skipped": skipped,
        "db": str(export_root / "library.db"),
        "csv": str(export_root / "library.csv"),
    }


def configure_papers_directory(destination: str | Path, *, migrate_existing: bool = True) -> dict:
    """Set the primary download folder and optionally copy existing files there."""
    dest = Path(destination).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    old_dir = papers_dir()

    copied = 0
    skipped = 0
    if migrate_existing:
        # Every file attachment (not only the primary PDF) follows the library to the new folder.
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, item_id, path FROM attachments WHERE kind IN ('file','snapshot') AND path IS NOT NULL"
        ).fetchall()
        for row in rows:
            src = Path(row["path"])
            if not src.exists() or not src.is_file():
                skipped += 1
                continue
            try:
                if src.resolve().parent == dest:
                    continue
            except OSError:
                pass
            target = _unique_target(dest, src)
            shutil.copy2(src, target)
            conn.execute("UPDATE attachments SET path=?, size=? WHERE id=?",
                         (str(target), target.stat().st_size, row["id"]))
            conn.execute("UPDATE items SET file_path=? WHERE file_path=?", (str(target), str(src)))
            copied += 1

    config.set_papers_dir(dest)
    return {
        "ok": True,
        "old_path": str(old_dir),
        "path": str(dest),
        "migrated": copied,
        "skipped": skipped,
    }


def google_drive_status() -> dict:
    candidates = config.google_drive_candidates()
    primary = config.default_google_drive_root()
    return {
        "library_root": str(config.library_root()),
        "db_path": str(config.db_path()),
        "papers_dir": str(papers_dir()),
        "google_drive_candidates": [str(p) for p in candidates],
        "default_google_drive": str(primary) if primary else "",
        "using_google_drive_for_papers": any(
            str(papers_dir()).casefold().startswith(str(candidate).casefold())
            for candidate in candidates
        ),
    }


# ---------------- Pipelines ----------------

def add_from_query(query: str, *, auto_categorize: bool = True) -> dict:
    """Resolve a DOI or free-text title, persist as an item, attach categories."""
    query = query.strip()
    meta: Optional[dict] = None
    if looks_like_doi(query):
        meta = fetch_doi(query)
    if meta is None:
        candidates = search_crossref(query, rows=1)
        if candidates:
            meta = candidates[0]
    if meta is None:
        # Last resort — store the bare title with status=failed_metadata so the user
        # can still retry. We don't want a search miss to swallow the query.
        item_id = upsert_item({
            "title": query, "status": "needs_metadata", "source_query": query,
        })
        return get_item(item_id)

    meta["keywords"] = extract_keywords(meta.get("title") or "", meta.get("abstract") or "")
    meta["status"] = "metadata_only"
    meta["source_query"] = query
    item_id = upsert_item(meta)

    if auto_categorize:
        item = get_item(item_id)
        with tx():
            for cid in suggest_collections(item, list_collections()):
                assign_to_collection(item_id, cid)

    return get_item(item_id)


def add_book(pub: dict, *, auto_categorize: bool = True) -> dict:
    """Persist a FreeBooksAPI publication as a library item (item_type='book')."""
    pub = dict(pub)
    pub.setdefault("item_type", "book")
    pub.setdefault("status", "metadata_only")
    pub.setdefault("source_query", f"book:{pub.get('title','')}")
    pub["keywords"] = extract_keywords(pub.get("title") or "", pub.get("publisher") or "")
    item_id = upsert_item(pub)
    if auto_categorize:
        item = get_item(item_id)
        with tx():
            for cid in suggest_collections(item, list_collections()):
                assign_to_collection(item_id, cid)
    return get_item(item_id)


def _download_book_options(item: dict, item_id: int, progress: Optional[Callable[[str, dict], None]]) -> list[tuple[str, str]]:
    """Walk the item's mirror list, return resolved direct file URL candidates."""
    mirrors = item.get("mirrors") or []
    if not mirrors:
        return []
    options: list[tuple[str, str]] = []
    for m in mirrors:
        if progress:
            progress("source", {"id": item_id, "source": f"FreeBooks: {m}"})
        url = resolve_book_url(m)
        if url:
            options.append((f"FreeBooks: {m}", url))
    return options


def _download_url_to_target(
    url: str,
    target: Path,
    item_id: int,
    progress: Optional[Callable[[str, dict], None]],
    *,
    expect_pdf: bool,
) -> int:
    """Stream one candidate into target atomically. Raises if it is not usable."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.part")
    if tmp.exists():
        tmp.unlink()

    try:
        with requests.get(url, headers=get_headers(), stream=True, timeout=_download_timeout(), allow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            written = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
                    if progress and total:
                        progress("progress", {"id": item_id, "loaded": written, "total": total})

        size = tmp.stat().st_size
        if size <= 0:
            raise ValueError("empty response")
        if expect_pdf:
            with open(tmp, "rb") as f:
                head = f.read(1024)
            if b"%PDF" not in head:
                raise ValueError("response was not a PDF")

        tmp.replace(target)
        return size
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def download_item(item_id: int, *, progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """Stream a paper (publisher/Sci-Hub/SciDB) or a book (LibGen mirrors) to disk."""
    item = get_item(item_id)
    if not item:
        raise ValueError(f"item {item_id} not found")

    is_book = (item.get("item_type") == "book")
    if not is_book and not item.get("doi"):
        get_conn().execute(
            "UPDATE items SET status='failed', error_msg='no DOI' WHERE id=?", (item_id,)
        )
        if progress:
            progress("failed", {"id": item_id, "error": "no DOI"})
        return get_item(item_id)
    if is_book and not item.get("mirrors"):
        get_conn().execute(
            "UPDATE items SET status='failed', error_msg='no mirrors' WHERE id=?", (item_id,)
        )
        if progress:
            progress("failed", {"id": item_id, "error": "no mirrors for book"})
        return get_item(item_id)

    if item.get("file_path") and Path(item["file_path"]).exists() and Path(item["file_path"]).stat().st_size > 0:
        get_conn().execute("UPDATE items SET status='downloaded' WHERE id=?", (item_id,))
        if progress:
            progress("skipped", {"id": item_id})
        return get_item(item_id)

    get_conn().execute("UPDATE items SET status='downloading', error_msg=NULL WHERE id=?", (item_id,))
    if progress:
        progress("downloading", {"id": item_id})

    if is_book:
        source_options = _download_book_options(item, item_id, progress)
        no_source_msg = "no working LibGen mirror"
    else:
        source_options = ((r.source, r.url) for r in iter_pdf_urls(item["doi"]))
        no_source_msg = "no source (Publisher + Sci-Hub + SciDB)"

    target = papers_dir() / build_filename(item)
    if target.exists():
        # Filename collision (different DOI, same author+year+title) — disambiguate.
        target = target.with_stem(f"{target.stem} ({item_id})")

    last_error = ""
    expect_pdf = target.suffix.lower() == ".pdf"
    attempted = 0
    total_options = len(source_options) if isinstance(source_options, list) else None
    for index, (source, url) in enumerate(source_options, start=1):
        attempted = index
        if progress:
            label = f"{source} ({index}/{total_options})" if total_options else f"{source} (attempt {index})"
            progress("source", {"id": item_id, "source": label})
        try:
            size = _download_url_to_target(url, target, item_id, progress, expect_pdf=expect_pdf)
        except Exception as e:
            last_error = f"{source}: {e}"
            log.warning(f"download option failed for item {item_id} via {source}: {e}")
            if progress:
                progress("source_failed", {"id": item_id, "source": source, "error": str(e)})
            continue

        get_conn().execute(
            "UPDATE items SET file_path=?, file_size=?, status='downloaded', error_msg=NULL, "
            "updated_at=datetime('now') WHERE id=?",
            (str(target), size, item_id),
        )
        catalog.register_file(item_id, target, url=url)
        _index_fulltext_async(item_id)
        if progress:
            progress("done", {"id": item_id, "path": str(target), "size": size})
        return get_item(item_id)

    if attempted == 0:
        get_conn().execute(
            "UPDATE items SET status='failed', error_msg=? WHERE id=?", (no_source_msg, item_id)
        )
        if progress:
            progress("failed", {"id": item_id, "error": no_source_msg})
        return get_item(item_id)

    error = f"all sources failed: {last_error}" if last_error else "all sources failed"
    log.error(f"download failed for item {item_id}: {error}")
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass
    get_conn().execute(
        "UPDATE items SET status='failed', error_msg=? WHERE id=?", (error[:300], item_id)
    )
    if progress:
        progress("failed", {"id": item_id, "error": error})
    return get_item(item_id)


# ---------------- Bulk import ----------------

def import_pdf_folder(folder: str | Path) -> dict:
    """Adopt PDFs from a folder into the library: copy/move + create items.

    We use the filename as a starting title; metadata enrichment is a follow-up.
    """
    folder = Path(folder)
    if not folder.exists():
        return {"imported": 0, "skipped": 0}
    imported = 0
    skipped = 0
    for pdf in folder.glob("*.pdf"):
        target = papers_dir() / pdf.name
        if target.exists():
            skipped += 1
            continue
        try:
            shutil.copy2(pdf, target)
        except OSError:
            skipped += 1
            continue
        title = pdf.stem.replace("_", " ").strip() or "Imported PDF"
        iid = upsert_item({
            "title": title,
            "file_path": str(target),
            "file_size": target.stat().st_size,
            "status": "downloaded",
            "source_query": f"import:{pdf.name}",
        })
        catalog.register_file(iid, target)
        _index_fulltext_async(iid)
        imported += 1
    return {"imported": imported, "skipped": skipped}


def stats() -> dict:
    return catalog.stats()


def _index_fulltext_async(item_id: int) -> None:
    """Index PDF text in a background thread (never blocks or breaks a download)."""
    import threading

    def run() -> None:
        try:
            from . import fulltext
            fulltext.index_item(item_id)
        except Exception as e:  # pragma: no cover - best effort
            log.warning(f"full-text indexing failed for item {item_id}: {e}")

    threading.Thread(target=run, daemon=True).start()
