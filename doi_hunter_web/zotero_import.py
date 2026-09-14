"""Direct migration from a Zotero data directory (zotero.sqlite + storage/).

Unlike BibTeX/RIS exports, reading Zotero's database keeps everything a library is made of:

    collections (nested) . items with all fields and creator roles . tags (+ colors)
    child and standalone notes (HTML) . stored and linked files . links
    PDF annotations (highlights, underlines, notes) . related items . trash (optional)
    saved searches (common conditions)

Safety
------
* The Zotero database is COPIED to a temporary file and opened read-only, so Zotero's files are
  never modified (closing Zotero first is still recommended for a consistent snapshot).
* Re-running the import is idempotent: each imported item remembers its Zotero key
  (``fields.zoteroKey``) and is skipped the second time; items whose DOI already exists are merged.

Schema reference: https://github.com/zotero/zotero/blob/main/resource/schema/userdata.sql
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from . import catalog
from .db import get_conn, tx

log = logging.getLogger("doihunter.zotero")

# Zotero item types we do not model natively -> closest ReviewHunter type.
TYPE_FALLBACK = {
    "film": "videoRecording", "tvBroadcast": "videoRecording", "dictionaryEntry": "encyclopediaArticle",
    "standard": "report", "audioRecording": "document", "podcast": "document", "radioBroadcast": "document",
    "case": "document", "statute": "document", "bill": "document", "hearing": "document", "artwork": "document",
    "map": "document", "interview": "document", "email": "document", "forumPost": "blogPost",
    "instantMessage": "document",
}

# Zotero field names that differ from ours (everything else uses identical Zotero names).
FIELD_ALIASES = {
    "seriesTitle": "series", "dictionaryTitle": "encyclopediaTitle", "caseName": "title", "subject": "title",
    "nameOfAct": "title", "court": "publisher", "distributor": "publisher", "studio": "publisher",
    "network": "publisher", "organization": "publisher", "dateDecided": "date", "dateEnacted": "date",
    "issueDate": "date", "number": "reportNumber", "genre": "reportType", "type": "reportType",
    "codeNumber": "volume", "reporterVolume": "volume", "firstPage": "pages", "codePages": "pages",
    "episodeNumber": "issue", "programTitle": "publicationTitle", "forumTitle": "blogTitle",
    "audioRecordingFormat": "medium", "videoRecordingFormat": "medium", "interviewMedium": "medium",
    "artworkMedium": "medium", "numberOfVolumes": "extra:Number of volumes",
    "libraryCatalog": "extra:Library Catalog", "committee": "extra:Committee", "status": "extra:Status",
}

ANNOTATION_TYPES = {1: "highlight", 2: "note", 5: "underline"}   # 3 image, 4 ink, 6 text are skipped
SEARCH_FIELD_MAP = {"title": "title", "creator": "creator", "tag": "tag", "collection": "collection",
                    "note": "note", "fulltextContent": "fulltext", "publicationTitle": "publicationTitle",
                    "year": "year", "itemType": "itemType", "DOI": "doi", "extra": "extra", "abstractNote": "abstract"}
SEARCH_OP_MAP = {"contains": "contains", "doesNotContain": "doesNotContain", "is": "is", "isNot": "isNot",
                 "beginsWith": "beginsWith", "isLessThan": "isLessThan", "isGreaterThan": "isGreaterThan",
                 "isBefore": "isLessThan", "isAfter": "isGreaterThan"}


def default_data_dir() -> Optional[Path]:
    """Zotero's default data directory if it exists (Windows/macOS/Linux: ~/Zotero)."""
    for candidate in (Path.home() / "Zotero", Path(os.environ.get("APPDATA", "")) / "Zotero" / "Zotero"):
        if (candidate / "zotero.sqlite").exists():
            return candidate
    return None


def inspect(data_dir: str | Path) -> dict:
    """Quick summary shown before importing."""
    with _open_copy(Path(data_dir)) as z:
        count = lambda sql: z.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "data_dir": str(data_dir),
            "items": count("SELECT COUNT(*) FROM items i JOIN itemTypesCombined t ON t.itemTypeID=i.itemTypeID "
                           "WHERE t.typeName NOT IN ('note','attachment','annotation')"),
            "notes": count("SELECT COUNT(*) FROM itemNotes"),
            "attachments": count("SELECT COUNT(*) FROM itemAttachments"),
            "annotations": count("SELECT COUNT(*) FROM itemAnnotations") if _has_table(z, "itemAnnotations") else 0,
            "collections": count("SELECT COUNT(*) FROM collections"),
            "tags": count("SELECT COUNT(*) FROM tags"),
        }


class _open_copy:
    """Context manager: copy zotero.sqlite to a temp dir and open it read-only."""

    def __init__(self, data_dir: Path):
        self.src = data_dir / "zotero.sqlite"
        if not self.src.exists():
            raise ValueError(f"zotero.sqlite not found in {data_dir}")
        self.tmp: Optional[str] = None
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        self.tmp = tempfile.mkdtemp(prefix="rh-zotero-")
        dst = Path(self.tmp) / "zotero.sqlite"
        shutil.copy2(self.src, dst)
        for suffix in ("-journal", "-wal"):
            side = Path(str(self.src) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(dst) + suffix))
        self.conn = sqlite3.connect(f"file:{dst.as_posix()}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, *exc) -> None:
        if self.conn:
            self.conn.close()
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


def _has_table(z: sqlite3.Connection, name: str) -> bool:
    return bool(z.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _types_table(z: sqlite3.Connection) -> str:
    return "itemTypesCombined" if _has_table(z, "itemTypesCombined") else "itemTypes"


def _fields_table(z: sqlite3.Connection) -> str:
    return "fieldsCombined" if _has_table(z, "fieldsCombined") else "fields"


def import_library(data_dir: str | Path, *, copy_files: bool = True, include_trash: bool = False,
                   progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """Import a whole Zotero library. Returns counts; never raises for a single bad record."""
    data_dir = Path(data_dir)
    report: dict[str, Any] = defaultdict(int)
    for key in ("items", "items_existing", "skipped_trash", "collections", "notes", "files", "links", "missing_files",
                "annotations", "skipped_annotations", "relations", "tag_colors", "saved_searches"):
        report[key] = 0   # explicit zeros: callers and the UI can rely on every counter being present
    report["warnings"] = []
    emit = progress or (lambda stage, data: None)

    with _open_copy(data_dir) as z:
        types = {r[0]: r[1] for r in z.execute(f"SELECT itemTypeID, typeName FROM {_types_table(z)}")}
        fields = {r[0]: r[1] for r in z.execute(f"SELECT fieldID, fieldName FROM {_fields_table(z)}")}
        creator_types = {r[0]: r[1] for r in z.execute("SELECT creatorTypeID, creatorType FROM creatorTypes")}
        deleted = {r[0] for r in z.execute("SELECT itemID FROM deletedItems")} if _has_table(z, "deletedItems") else set()
        keys = {r[0]: r[1] for r in z.execute("SELECT itemID, key FROM items")}
        added = {r[0]: r[1] for r in z.execute("SELECT itemID, dateAdded FROM items")}

        # ---- field values per item
        values: dict[int, dict[str, str]] = defaultdict(dict)
        for r in z.execute("SELECT d.itemID, d.fieldID, v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID"):
            values[r[0]][fields.get(r[1], f"field{r[1]}")] = str(r[2])

        creators: dict[int, list[dict]] = defaultdict(list)
        for r in z.execute("SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode, ic.creatorTypeID FROM itemCreators ic "
                           "JOIN creators c ON c.creatorID=ic.creatorID ORDER BY ic.itemID, ic.orderIndex"):
            ctype = creator_types.get(r[4], "author")
            if r[3] == 1:
                creators[r[0]].append({"creatorType": ctype, "name": r[2] or ""})
            else:
                creators[r[0]].append({"creatorType": ctype, "lastName": r[2] or "", "firstName": r[1] or ""})

        item_tags: dict[int, list[tuple[str, int]]] = defaultdict(list)
        for r in z.execute("SELECT it.itemID, t.name, it.type FROM itemTags it JOIN tags t ON t.tagID=it.tagID"):
            item_tags[r[0]].append((r[1], r[2]))

        # ---- collections (parents before children)
        col_map: dict[int, int] = {}
        rows = z.execute("SELECT collectionID, collectionName, parentCollectionID FROM collections").fetchall()
        pending = list(rows)
        conn = get_conn()
        guard = 0
        while pending and guard < 10000:
            guard += 1
            r = pending.pop(0)
            parent = r[2]
            if parent and parent not in col_map:
                if any(p[0] == parent for p in pending):
                    pending.append(r)
                    continue
                parent = None
            new_parent = col_map.get(parent) if parent else None
            existing = conn.execute("SELECT id FROM collections WHERE name=? AND parent_id IS ?", (r[1], new_parent)).fetchone()
            col_map[r[0]] = existing[0] if existing else conn.execute(
                "INSERT INTO collections (name, parent_id) VALUES (?, ?)", (r[1], new_parent)).lastrowid
            report["collections"] += 1 if not existing else 0
        col_items: dict[int, list[int]] = defaultdict(list)
        for r in z.execute("SELECT collectionID, itemID FROM collectionItems"):
            if r[0] in col_map:
                col_items[r[1]].append(col_map[r[0]])

        # ---- regular items
        regular = z.execute(
            f"SELECT i.itemID, t.typeName FROM items i JOIN {_types_table(z)} t ON t.itemTypeID=i.itemTypeID "
            "WHERE t.typeName NOT IN ('note','attachment','annotation') ORDER BY i.itemID").fetchall()
        item_map: dict[int, int] = {}
        total = len(regular)
        for index, (zid, ztype) in enumerate(regular, start=1):
            if zid in deleted and not include_trash:
                report["skipped_trash"] += 1
                continue
            if index % 25 == 0 or index == total:
                emit("zotero_progress", {"stage": "items", "index": index, "total": total})
            try:
                item_map[zid] = _import_item(zid, ztype, keys[zid], added.get(zid), values[zid], creators[zid],
                                             item_tags[zid], col_items[zid], zid in deleted, report)
            except Exception as e:  # keep going: one malformed record must not abort a migration
                report["warnings"].append(f"item {keys.get(zid)}: {e}")

        # ---- notes
        for r in z.execute("SELECT itemID, parentItemID, note FROM itemNotes"):
            if (r[0] in deleted and not include_trash) or (r[1] and r[1] not in item_map):
                continue
            if _already(conn, "note", keys[r[0]]):
                continue
            html = (r[2] or "").strip()
            if not html:
                continue
            note = catalog.create_note(item_map.get(r[1]) if r[1] else None, html)
            conn.execute("UPDATE notes SET created_at=COALESCE(?, created_at) WHERE id=?", (added.get(r[0]), note["id"]))
            _remember(conn, "note", keys[r[0]], note["id"])
            report["notes"] += 1

        # ---- attachments
        att_map: dict[int, int] = {}
        att_rows = z.execute("SELECT itemID, parentItemID, linkMode, contentType, path FROM itemAttachments").fetchall()
        for index, r in enumerate(att_rows, start=1):
            if index % 25 == 0 or index == len(att_rows):
                emit("zotero_progress", {"stage": "files", "index": index, "total": len(att_rows)})
            zid, parent, link_mode, ctype, path = r
            if zid in deleted and not include_trash:
                continue
            try:
                att_id = _import_attachment(conn, data_dir, zid, keys[zid], parent, link_mode, ctype, path,
                                            values[zid], item_map, copy_files, report)
                if att_id:
                    att_map[zid] = att_id
            except Exception as e:
                report["warnings"].append(f"attachment {keys.get(zid)}: {e}")

        # ---- annotations
        if _has_table(z, "itemAnnotations"):
            for r in z.execute("SELECT itemID, parentItemID, type, text, comment, color, sortIndex, position "
                               "FROM itemAnnotations"):
                if r[1] not in att_map or _already(conn, "annotation", keys[r[0]]):
                    continue
                kind = ANNOTATION_TYPES.get(r[2])
                if not kind:
                    report["skipped_annotations"] += 1
                    continue
                try:
                    pos = json.loads(r[7] or "{}")
                except json.JSONDecodeError:
                    pos = {}
                ann = catalog.create_annotation(att_map[r[1]], {
                    "type": kind, "color": r[5] or "#ffd400", "page": int(pos.get("pageIndex", 0)) + 1,
                    "text": r[3], "comment": r[4], "position": {"rects": pos.get("rects") or []},
                    "sort_index": r[6]})
                _remember(conn, "annotation", keys[r[0]], ann["id"])
                report["annotations"] += 1

        # ---- relations
        if _has_table(z, "itemRelations"):
            key_to_new = {keys[zid]: new for zid, new in item_map.items()}
            for r in z.execute("SELECT ir.itemID, ir.object FROM itemRelations ir JOIN relationPredicates p "
                               "ON p.predicateID=ir.predicateID WHERE p.predicate='dc:relation'"):
                m = re.search(r"/items/([A-Z0-9]{8})$", r[1] or "")
                if r[0] in item_map and m and m.group(1) in key_to_new:
                    catalog.relate(item_map[r[0]], key_to_new[m.group(1)])
                    report["relations"] += 1

        # ---- tag colors
        if _has_table(z, "syncedSettings"):
            row = z.execute("SELECT value FROM syncedSettings WHERE setting='tagColors' LIMIT 1").fetchone()
            if row:
                try:
                    for tc in json.loads(row[0]):
                        tid = catalog._tag_id(tc.get("name", ""), create=False)
                        if tid and tc.get("color"):
                            conn.execute("UPDATE tags SET color=? WHERE id=?", (tc["color"], tid))
                            report["tag_colors"] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

        # ---- saved searches
        if _has_table(z, "savedSearches"):
            for s in z.execute("SELECT savedSearchID, savedSearchName FROM savedSearches").fetchall():
                conds, match = [], "all"
                for c in z.execute("SELECT condition, operator, value FROM savedSearchConditions WHERE savedSearchID=?",
                                   (s[0],)):
                    if c[0] == "joinMode":
                        match = "any" if c[2] == "any" else "all"
                        continue
                    field = SEARCH_FIELD_MAP.get(c[0])
                    if not field:
                        continue
                    value = c[2]
                    if field == "collection":
                        ckey = str(value).split("_")[-1]
                        zcol = z.execute("SELECT collectionID FROM collections WHERE key=?", (ckey,)).fetchone()
                        if not zcol or zcol[0] not in col_map:
                            continue
                        value = str(col_map[zcol[0]])
                    conds.append({"field": field, "op": SEARCH_OP_MAP.get(c[1], "contains"), "value": value})
                if conds and not conn.execute("SELECT 1 FROM saved_searches WHERE name=?", (s[1],)).fetchone():
                    catalog.save_search(s[1], conds, match)
                    report["saved_searches"] += 1

    report["items_total"] = len(item_map)
    emit("zotero_done", {k: v for k, v in report.items() if k != "warnings"})
    out = dict(report)
    out["warnings"] = report["warnings"][:50]
    return out


# ----------------------------------------------------------------------------- helpers

def _already(conn: sqlite3.Connection, kind: str, zkey: str) -> Optional[int]:
    """Idempotency map stored in a tiny side table (created on demand)."""
    conn.execute("CREATE TABLE IF NOT EXISTS zotero_import_map (kind TEXT, zkey TEXT, local_id INTEGER, "
                 "PRIMARY KEY (kind, zkey))")
    row = conn.execute("SELECT local_id FROM zotero_import_map WHERE kind=? AND zkey=?", (kind, zkey)).fetchone()
    return row[0] if row else None


def _remember(conn: sqlite3.Connection, kind: str, zkey: str, local_id: int) -> None:
    _already(conn, kind, zkey)
    conn.execute("INSERT OR REPLACE INTO zotero_import_map (kind, zkey, local_id) VALUES (?, ?, ?)", (kind, zkey, local_id))


def _import_item(zid: int, ztype: str, zkey: str, date_added: Optional[str], vals: dict[str, str], creators: list[dict],
                 tags: list[tuple[str, int]], collections: list[int], trashed: bool, report: dict) -> int:
    conn = get_conn()
    existing = _already(conn, "item", zkey)
    if existing and conn.execute("SELECT 1 FROM items WHERE id=?", (existing,)).fetchone():
        report["items_existing"] += 1
        return existing

    item_type = ztype if ztype in catalog.ITEM_TYPES else TYPE_FALLBACK.get(ztype, "document")
    extra_lines = [vals.get("extra", "")] if vals.get("extra") else []
    if item_type != ztype:
        extra_lines.append(f"Original Zotero type: {ztype}")
    field_values: dict[str, str] = {}
    allowed = set(catalog.ITEM_TYPES[item_type]["fields"])
    for name, value in vals.items():
        if name == "extra" or value in (None, ""):
            continue
        target = FIELD_ALIASES.get(name, name)
        if target.startswith("extra:"):
            extra_lines.append(f"{target[6:]}: {value}")
            continue
        if target not in catalog.FIELD_DEFS:
            extra_lines.append(f"{name}: {value}")
            continue
        if target not in allowed:
            # Same storage under a sibling field id (e.g. publicationTitle stored as bookTitle) keeps the value.
            sibling = next((f for f in allowed if catalog.FIELD_DEFS[f][1] == catalog.FIELD_DEFS[target][1]), None)
            if not sibling:
                extra_lines.append(f"{catalog.FIELD_DEFS[target][0]}: {value}")
                continue
            target = sibling
        field_values[target] = value
    field_values["extra"] = "\n".join(line for line in extra_lines if line)
    payload = {
        "item_type": item_type,
        "title": vals.get("title") or vals.get("caseName") or vals.get("subject") or "Untitled",
        "creators": creators,
        "field_values": field_values,
        "doi": field_values.get("DOI"),
        "fields": {"zoteroKey": zkey},
        "tags": [],
        "collections": collections,
        "status": "metadata_only",
        "source_query": f"zotero:{zkey}",
    }
    if field_values.get("citationKey"):
        payload["citation_key"] = field_values["citationKey"]
    item = catalog.create_item(payload)
    iid = item["id"]
    with tx():
        for name, ttype in tags:
            catalog.add_tag(iid, name, tag_type=1 if ttype == 1 else 0)
        if date_added:
            conn.execute("UPDATE items SET added_at=? WHERE id=? AND ? IS NOT NULL", (date_added, iid, date_added))
        if trashed:
            conn.execute("UPDATE items SET deleted_at=datetime('now') WHERE id=?", (iid,))
    _remember(conn, "item", zkey, iid)
    report["items_existing" if item.get("_existing") else "items"] += 1
    return iid


def _import_attachment(conn: sqlite3.Connection, data_dir: Path, zid: int, zkey: str, parent: Optional[int],
                       link_mode: int, ctype: Optional[str], path: Optional[str], vals: dict[str, str],
                       item_map: dict[int, int], copy_files: bool, report: dict) -> Optional[int]:
    existing = _already(conn, "attachment", zkey)
    if existing and conn.execute("SELECT 1 FROM attachments WHERE id=?", (existing,)).fetchone():
        return existing
    title = vals.get("title") or (Path(path.split(":", 1)[-1]).name if path else "Attachment")
    if parent:
        item_id = item_map.get(parent)
        if not item_id:
            return None
    else:
        # Standalone attachment: Zotero shows it as a top-level row; we wrap it in a Document item.
        item_id = catalog.create_item({"item_type": "document", "title": title, "fields": {"zoteroKey": zkey}},
                                      dedupe_doi=False)["id"]
    if link_mode == 3:  # linked URL
        url = vals.get("url")
        if not url:
            return None
        att = catalog.add_link_attachment(item_id, url, title)
        report["links"] += 1
    else:
        src: Optional[Path] = None
        if path and path.startswith("storage:"):
            src = data_dir / "storage" / zkey / path[len("storage:"):]
        elif path and path.startswith("attachments:"):
            report["warnings"].append(f"{title}: relative linked file (base directory unknown): {path}")
            return None
        elif path:
            src = Path(path)
        if not src or not src.exists():
            report["missing_files"] += 1
            return None
        if copy_files and link_mode in (0, 1):
            att = catalog.store_file_bytes(item_id, src.read_bytes(), src.name, title=title,
                                           kind="snapshot" if (ctype or "").startswith("text/html") else "file",
                                           url=vals.get("url"))
        else:
            att = catalog.register_file(item_id, src, title=title, url=vals.get("url"))
        report["files"] += 1
    _remember(conn, "attachment", zkey, att["id"])
    return att["id"]
