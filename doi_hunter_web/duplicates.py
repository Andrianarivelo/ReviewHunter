"""Duplicate detection and merging (Zotero 'Duplicate Items').

Detection rules (a pair is a duplicate if ANY rule matches):
  1. same normalized DOI (only possible across case/format variants),
  2. same ISBN,
  3. normalized titles identical (or SequenceMatcher >= 0.93 for long titles)
     AND years within 1 AND first-author last names compatible.

Merging keeps the master's fields, fills its empty fields from the others, moves
collections, tags, notes, attachments and relations to the master, then sends
the other items to the trash (reversible, as in Zotero).
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

from . import catalog
from .db import get_conn, row_to_dict, tx

TITLE_RATIO = 0.93


def _norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _first_last(item: dict) -> str:
    creators = item.get("creators") or []
    if creators:
        c = creators[0]
        return _norm_title(c.get("lastName") or c.get("name") or "")
    return ""


def find_duplicates() -> list[dict]:
    rows = get_conn().execute(
        "SELECT id, title, doi, isbn, year, creators, item_type, added_at FROM items WHERE deleted_at IS NULL"
    ).fetchall()
    items = [row_to_dict(r) for r in rows]
    parent = {it["id"]: it["id"] for it in items}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_key: dict[str, list[int]] = defaultdict(list)
    for it in items:
        if it.get("doi"):
            by_key["doi:" + catalog.normalize_doi(it["doi"])].append(it["id"])
        for isbn in it.get("isbn") or []:
            digits = re.sub(r"[^0-9X]", "", str(isbn).upper())
            if digits:
                by_key["isbn:" + digits].append(it["id"])
    for ids in by_key.values():
        for other in ids[1:]:
            union(ids[0], other)

    # Title blocking by first 3 significant characters keeps the pairwise pass fast.
    blocks: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        nt = _norm_title(it.get("title") or "")
        it["_nt"] = nt
        if len(nt) >= 8:
            blocks[nt[:6]].append(it)
    for block in blocks.values():
        for i, a in enumerate(block):
            for b in block[i + 1:]:
                if a["_nt"] != b["_nt"]:
                    if min(len(a["_nt"]), len(b["_nt"])) < 30:
                        continue
                    if difflib.SequenceMatcher(None, a["_nt"], b["_nt"]).ratio() < TITLE_RATIO:
                        continue
                if a.get("year") and b.get("year") and abs(int(a["year"]) - int(b["year"])) > 1:
                    continue
                la, lb = _first_last(a), _first_last(b)
                if la and lb and la != lb:
                    continue
                if a.get("doi") and b.get("doi") and catalog.normalize_doi(a["doi"]) != catalog.normalize_doi(b["doi"]):
                    continue
                union(a["id"], b["id"])

    groups: dict[int, list[int]] = defaultdict(list)
    for it in items:
        groups[find(it["id"])].append(it["id"])
    out = []
    for ids in groups.values():
        if len(ids) > 1:
            full = [catalog.get_item(i, full=False) for i in sorted(ids)]
            out.append({"items": full, "master_id": min(ids)})
    return out


_FILLABLE = ["journal", "publisher", "volume", "issue", "pages", "abstract", "doi", "url", "issn", "language",
             "short_title", "series", "edition", "place", "num_pages", "pmid", "arxiv_id", "date", "year",
             "access_date"]


def merge(master_id: int, other_ids: list[int], *, field_choices: dict[str, int] | None = None) -> dict:
    """Merge ``other_ids`` into ``master_id``.

    ``field_choices`` optionally maps a column name to the item id whose value should win
    (the UI's per-field version picker).
    """
    other_ids = [i for i in other_ids if i != master_id]
    conn = get_conn()
    master = row_to_dict(conn.execute("SELECT * FROM items WHERE id=?", (master_id,)).fetchone())
    if not master or not other_ids:
        raise ValueError("master and at least one other item are required")
    others = [row_to_dict(conn.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone()) for i in other_ids]
    others = [o for o in others if o]
    by_id = {o["id"]: o for o in others}
    with tx():
        updates: dict[str, Any] = {}
        # Explicit per-field choices first.
        for col, src_id in (field_choices or {}).items():
            if col in _FILLABLE + ["title", "creators", "item_type"] and src_id in by_id:
                updates[col] = by_id[src_id].get(col)
        # Then fill empty master fields.
        for col in _FILLABLE:
            if col in updates or master.get(col):
                continue
            donor = next((o for o in others if o.get(col)), None)
            if donor:
                updates[col] = donor[col]
        if not master.get("creators") and "creators" not in updates:
            donor = next((o for o in others if o.get("creators")), None)
            if donor:
                updates["creators"] = donor["creators"]
        if "creators" in updates:
            updates["authors"] = json.dumps(catalog.authors_mirror(updates["creators"]), ensure_ascii=False)
            updates["creators"] = json.dumps(updates["creators"], ensure_ascii=False)
        isbns = sorted({*(master.get("isbn") or []), *[i for o in others for i in (o.get("isbn") or [])]})
        if isbns:
            updates["isbn"] = json.dumps(isbns)
        if updates.get("doi"):
            # Free the DOI on the donor first (UNIQUE constraint).
            conn.execute("UPDATE items SET doi=NULL WHERE doi=? AND id != ?", (updates["doi"], master_id))
        if updates:
            conn.execute(
                f"UPDATE items SET {', '.join(f'{k}=?' for k in updates)}, updated_at=datetime('now') WHERE id=?",
                [*updates.values(), master_id],
            )
        if any(o.get("starred") for o in others):
            conn.execute("UPDATE items SET starred=1 WHERE id=?", (master_id,))
        for oid in other_ids:
            conn.execute("INSERT OR IGNORE INTO item_collections (item_id, collection_id) "
                         "SELECT ?, collection_id FROM item_collections WHERE item_id=?", (master_id, oid))
            conn.execute("INSERT OR IGNORE INTO item_tags (item_id, tag_id) "
                         "SELECT ?, tag_id FROM item_tags WHERE item_id=?", (master_id, oid))
            conn.execute("UPDATE notes SET item_id=? WHERE item_id=?", (master_id, oid))
            conn.execute("UPDATE attachments SET item_id=? WHERE item_id=?", (master_id, oid))
            conn.execute("UPDATE fulltext_fts SET item_id=? WHERE item_id=?", (master_id, oid))
            conn.execute("INSERT OR IGNORE INTO related_items (item_id, related_id) "
                         "SELECT ?, related_id FROM related_items WHERE item_id=? AND related_id != ?",
                         (master_id, oid, master_id))
            conn.execute("DELETE FROM related_items WHERE item_id=? OR related_id=?", (oid, oid))
            conn.execute("UPDATE items SET deleted_at=datetime('now'), file_path=NULL, "
                         "extra=TRIM(COALESCE(extra,'') || char(10) || 'Merged into item ' || ?) WHERE id=?",
                         (master_id, oid))
        catalog.sync_primary_file(master_id)
    return {"master": catalog.get_item(master_id), "merged": other_ids}
