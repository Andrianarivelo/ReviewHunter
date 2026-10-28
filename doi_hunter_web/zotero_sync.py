"""Smart Zotero connector: detect Zotero, list its libraries, and keep ReviewHunter in sync with them.

What "smart" means here
-----------------------
* Detection: the Zotero installation (``D:\\Program Files\\Zotero`` or the usual places, version read from
  ``app/application.ini``), the data directory Zotero really uses (``extensions.zotero.dataDir`` in the
  profile's ``prefs.js``), and whether Zotero is running (its connector server answers on port 23119).
* Library aware: My Library and every group library are listed separately (feeds are ignored) and
  each one is mirrored into a destination chosen by the user (the library root, a collection, or the
  collection of a shared ReviewHunter library, which turns a Zotero group into a Drive-shared library).
* Incremental: every mirrored Zotero object is remembered in ``zotero_links`` together with a *base*
  snapshot of both sides at the last sync. A re-sync then performs a three-way merge per field:

      Zotero changed a field, ReviewHunter did not       -> Zotero's value is applied
      ReviewHunter changed a field, Zotero did not       -> the local edit is kept
      both changed the same field                        -> ``zotero_conflict_policy`` decides
                                                            ("zotero" wins by default, or "local")

  Tags and collection memberships are merged as sets (additions and removals made in Zotero are
  replayed; tags or collections added in ReviewHunter are never removed by a sync).
* Deletions: an item moved to Zotero's trash (or purged) goes to the ReviewHunter trash, where it can
  be restored; it comes back automatically if it is restored in Zotero.
* Cheap when idle: a fingerprint of ``zotero.sqlite`` (+ WAL) lets the background loop skip unchanged
  databases without copying them.

Safety: Zotero's files are only ever COPIED (see ``zotero_import._open_copy``); nothing is written to
the Zotero data directory. Zotero itself documents that external programs must never modify
zotero.sqlite, so this connector is one-way (Zotero -> ReviewHunter) by design.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import catalog, config
from .db import get_conn, tx
from .zotero_import import (ANNOTATION_TYPES, SEARCH_FIELD_MAP, SEARCH_OP_MAP, _fields_table, _has_table,
                            _open_copy, _types_table, map_item_fields)

log = logging.getLogger("doihunter.zotero_sync")

CONNECTOR_URL = "http://127.0.0.1:23119"
REGULAR_EXCLUDED = ("note", "attachment", "annotation")

_lock = threading.Lock()                    # one sync at a time (manual button vs background loop)
_state: dict[str, Any] = {"running": False, "progress": None, "error": "", "last_fingerprint": None}
_libraries_cache: dict[str, Any] = {}       # {"fp": (data_dir, fingerprint), "libraries": [...]}

Progress = Optional[Callable[[str, dict], None]]


# ======================================================================================
# Detection
# ======================================================================================

def _install_candidates() -> list[Path]:
    """Places where Zotero is commonly installed, the user's explicit choice first."""
    out: list[Path] = []
    custom = (config.get_settings().get("zotero_install_dir") or "").strip()
    if custom:
        out.append(Path(custom).expanduser())
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                out.append(Path(base) / "Zotero")
        for drive in "CDEFG":   # non-default drives, e.g. D:\Program Files\Zotero
            out.append(Path(f"{drive}:\\Program Files\\Zotero"))
    elif sys.platform == "darwin":
        out.append(Path("/Applications/Zotero.app/Contents/Resources"))
    else:
        out += [Path("/usr/lib/zotero"), Path("/opt/zotero"), Path.home() / "Zotero_linux-x86_64"]
    seen, unique = set(), []
    for p in out:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def read_install_version(install_dir: Path) -> Optional[str]:
    """Zotero's version from ``app/application.ini`` (``[App] Version=10.0.6``)."""
    for ini in (install_dir / "app" / "application.ini", install_dir / "application.ini"):
        try:
            m = re.search(r"^Version=(.+)$", ini.read_text(encoding="utf-8", errors="ignore"), re.M)
        except OSError:
            continue
        if m:
            return m.group(1).strip()
    return None


def find_install() -> dict:
    for cand in _install_candidates():
        # is_file(): %LOCALAPPDATA%\Zotero is Zotero's cache folder and contains a *folder* named "zotero".
        exe = next((cand / n for n in ("zotero.exe", "zotero", "Zotero") if (cand / n).is_file()), None)
        if exe or (cand / "app" / "application.ini").is_file():
            return {"found": True, "dir": str(cand), "exe": str(exe) if exe else "",
                    "version": read_install_version(cand)}
    return {"found": False, "dir": "", "exe": "", "version": None}


def _profile_dirs() -> list[Path]:
    if os.name == "nt":
        roots = [Path(os.environ.get("APPDATA", "")) / "Zotero" / "Zotero" / "Profiles"]
    elif sys.platform == "darwin":
        roots = [Path.home() / "Library" / "Application Support" / "Zotero" / "Profiles"]
    else:
        roots = [Path.home() / ".zotero" / "zotero"]
    return [p for root in roots if root.exists() for p in root.iterdir() if p.is_dir()]


def parse_prefs_data_dir(prefs_text: str) -> Optional[str]:
    """The custom data directory from a Zotero ``prefs.js`` (only when ``useDataDir`` is true)."""
    if not re.search(r'user_pref\("extensions\.zotero\.useDataDir",\s*true\)', prefs_text):
        return None
    m = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\)', prefs_text)
    return json.loads(f'"{m.group(1)}"') if m else None   # prefs.js strings use JSON escapes ("C:\\Users")


def find_data_dir() -> Optional[Path]:
    """User setting > Zotero's own preference > ~/Zotero."""
    custom = (config.get_settings().get("zotero_data_dir") or "").strip()
    if custom and (Path(custom).expanduser() / "zotero.sqlite").exists():
        return Path(custom).expanduser()
    for profile in _profile_dirs():
        try:
            found = parse_prefs_data_dir((profile / "prefs.js").read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if found and (Path(found) / "zotero.sqlite").exists():
            return Path(found)
    from .zotero_import import default_data_dir

    return default_data_dir()


def zotero_running(timeout: float = 1.5) -> bool:
    """Zotero's connector server answers ``/connector/ping`` while the app is open (no proxy, loopback)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{CONNECTOR_URL}/connector/ping", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def fingerprint(data_dir: Path) -> Optional[tuple]:
    """(size, mtime) of zotero.sqlite and its WAL. Unchanged fingerprint = nothing new in Zotero."""
    sig = []
    for suffix in ("", "-wal"):
        f = Path(str(Path(data_dir) / "zotero.sqlite") + suffix)
        sig.append((f.stat().st_size, f.stat().st_mtime_ns) if f.exists() else None)
    return tuple(sig) if sig[0] else None


def list_libraries(z) -> list[dict]:
    """Zotero libraries with counts. Keys are stable across computers: "user" or "group:<groupID>"."""
    types = _types_table(z)
    not_deleted = " AND i.itemID NOT IN (SELECT itemID FROM deletedItems)" if _has_table(z, "deletedItems") else ""
    counts = {r[0]: r[1] for r in z.execute(
        f"SELECT i.libraryID, COUNT(*) FROM items i JOIN {types} t ON t.itemTypeID=i.itemTypeID "
        f"WHERE t.typeName NOT IN {REGULAR_EXCLUDED}{not_deleted} GROUP BY i.libraryID")}
    col_counts = {r[0]: r[1] for r in z.execute("SELECT libraryID, COUNT(*) FROM collections GROUP BY libraryID")}
    if not _has_table(z, "libraries"):   # very old or synthetic databases: a single personal library
        lib_ids = sorted({r[0] for r in z.execute("SELECT DISTINCT libraryID FROM items")}) or [1]
        return [{"key": "user", "library_id": lib_ids[0], "type": "user", "name": "My Library", "editable": True,
                 "items": counts.get(lib_ids[0], 0), "collections": col_counts.get(lib_ids[0], 0)}]
    groups = {}
    if _has_table(z, "groups"):
        groups = {r["libraryID"]: (r["groupID"], r["name"]) for r in z.execute("SELECT groupID, libraryID, name FROM groups")}
    out = []
    for r in z.execute("SELECT libraryID, type, editable FROM libraries ORDER BY libraryID"):
        if r["type"] == "user":
            key, name = "user", "My Library"
        elif r["type"] == "group" and r["libraryID"] in groups:
            key, name = f"group:{groups[r['libraryID']][0]}", groups[r["libraryID"]][1]
        else:
            continue   # feeds (RSS) and orphan rows are not reference libraries
        out.append({"key": key, "library_id": r["libraryID"], "type": r["type"], "name": name,
                    "editable": bool(r["editable"]), "items": counts.get(r["libraryID"], 0),
                    "collections": col_counts.get(r["libraryID"], 0)})
    return out


def detect() -> dict:
    """Everything the Settings > Zotero panel shows. Never raises."""
    s = config.get_settings()
    data_dir = find_data_dir()
    out: dict[str, Any] = {
        "install": find_install(), "running": zotero_running(), "data_dir": str(data_dir) if data_dir else "",
        "found": bool(data_dir), "libraries": [], "settings": {
            k: s.get(k) for k in ("zotero_libraries", "zotero_auto_sync", "zotero_sync_interval_minutes",
                                  "zotero_conflict_policy", "zotero_mirror_deletions", "zotero_copy_files",
                                  "zotero_install_dir", "zotero_data_dir")},
        "last_sync": s.get("zotero_last_sync") or {}, "sync": public_state(), "error": "",
    }
    if data_dir:
        try:
            # Copying a large zotero.sqlite on every Settings visit would be slow: reuse the last listing
            # while the database fingerprint is unchanged.
            fp = (str(data_dir), fingerprint(data_dir))
            if _libraries_cache.get("fp") != fp:
                with _open_copy(data_dir) as z:
                    _libraries_cache.update(fp=fp, libraries=list_libraries(z))
            out["libraries"] = [dict(lib) for lib in _libraries_cache["libraries"]]
        except Exception as e:   # locked, corrupt, unreadable: report instead of failing the page
            out["error"] = str(e)
        configured = s.get("zotero_libraries") or {}
        linked = {r[0]: r[1] for r in get_conn().execute("SELECT lib, COUNT(*) FROM zotero_links WHERE kind='item' GROUP BY lib")}
        for lib in out["libraries"]:
            cfg = configured.get(lib["key"]) or {}
            lib["enabled"] = bool(cfg.get("enabled", lib["key"] == "user"))
            lib["target_collection_id"] = cfg.get("target_collection_id")
            lib["linked_items"] = linked.get(lib["key"], 0)
    return out


def public_state() -> dict:
    return {k: v for k, v in _state.items() if k != "last_fingerprint"}


# ======================================================================================
# Reading one Zotero library
# ======================================================================================

def _read_library(z, library_id: int, single_library_db: bool) -> dict:
    """All objects of one library from the snapshot, keyed by Zotero itemID."""
    lib_filter = "" if single_library_db else f" AND i.libraryID={int(library_id)}"
    types = {r[0]: r[1] for r in z.execute(f"SELECT itemTypeID, typeName FROM {_types_table(z)}")}
    fields = {r[0]: r[1] for r in z.execute(f"SELECT fieldID, fieldName FROM {_fields_table(z)}")}
    creator_types = {r[0]: r[1] for r in z.execute("SELECT creatorTypeID, creatorType FROM creatorTypes")}
    cols = {r[1] for r in z.execute("PRAGMA table_info(items)")}
    mod_col = "clientDateModified" if "clientDateModified" in cols else ("dateModified" if "dateModified" in cols else "NULL")
    rows = z.execute(f"SELECT i.itemID, i.itemTypeID, i.key, i.dateAdded, {mod_col} AS modified FROM items i "
                     f"WHERE 1=1{lib_filter}").fetchall()
    ids = {r["itemID"] for r in rows}
    lib = {
        "types": {r["itemID"]: types.get(r["itemTypeID"], "document") for r in rows},
        "keys": {r["itemID"]: r["key"] for r in rows},
        "added": {r["itemID"]: r["dateAdded"] for r in rows},
        "modified": {r["itemID"]: str(r["modified"] or "") for r in rows},
        "deleted": ({r[0] for r in z.execute("SELECT itemID FROM deletedItems")} & ids)
        if _has_table(z, "deletedItems") else set(),
        "values": defaultdict(dict), "creators": defaultdict(list), "tags": defaultdict(dict),
        "item_collections": defaultdict(list), "collections": [], "notes": [], "attachments": [], "annotations": [],
    }
    for r in z.execute("SELECT d.itemID, d.fieldID, v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID"):
        if r[0] in ids:
            lib["values"][r[0]][fields.get(r[1], f"field{r[1]}")] = str(r[2])
    for r in z.execute("SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode, ic.creatorTypeID FROM itemCreators ic "
                       "JOIN creators c ON c.creatorID=ic.creatorID ORDER BY ic.itemID, ic.orderIndex"):
        if r[0] not in ids:
            continue
        ctype = creator_types.get(r[4], "author")
        lib["creators"][r[0]].append({"creatorType": ctype, "name": r[2] or ""} if r[3] == 1 else
                                     {"creatorType": ctype, "lastName": r[2] or "", "firstName": r[1] or ""})
    for r in z.execute("SELECT it.itemID, t.name, it.type FROM itemTags it JOIN tags t ON t.tagID=it.tagID"):
        if r[0] in ids:
            lib["tags"][r[0]][r[1]] = 1 if r[2] == 1 else 0
    ccols = {r[1] for r in z.execute("PRAGMA table_info(collections)")}
    cmod = "clientDateModified" if "clientDateModified" in ccols else "NULL"
    where = "" if single_library_db else f" WHERE libraryID={int(library_id)}"
    lib["collections"] = [dict(r) for r in z.execute(
        f"SELECT collectionID, collectionName, parentCollectionID, key, {cmod} AS modified FROM collections{where}")]
    col_keys = {c["collectionID"]: c["key"] for c in lib["collections"]}
    for r in z.execute("SELECT collectionID, itemID FROM collectionItems"):
        if r[1] in ids and r[0] in col_keys:
            lib["item_collections"][r[1]].append(col_keys[r[0]])
    lib["notes"] = [dict(r) for r in z.execute("SELECT itemID, parentItemID, note FROM itemNotes") if r[0] in ids]
    lib["attachments"] = [dict(r) for r in z.execute(
        "SELECT itemID, parentItemID, linkMode, contentType, path FROM itemAttachments") if r[0] in ids]
    if _has_table(z, "itemAnnotations"):
        lib["annotations"] = [dict(r) for r in z.execute(
            "SELECT itemID, parentItemID, type, text, comment, color, sortIndex, position FROM itemAnnotations")
            if r[0] in ids]
    lib["relations"] = []
    if _has_table(z, "itemRelations"):
        lib["relations"] = [(r[0], r[1]) for r in z.execute(
            "SELECT ir.itemID, ir.object FROM itemRelations ir JOIN relationPredicates p "
            "ON p.predicateID=ir.predicateID WHERE p.predicate='dc:relation'") if r[0] in ids]
    lib["tag_colors"] = []
    if _has_table(z, "syncedSettings"):
        row = z.execute("SELECT value FROM syncedSettings WHERE setting='tagColors' AND libraryID=? LIMIT 1",
                        (library_id,)).fetchone()
        if row:
            try:
                lib["tag_colors"] = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                pass
    return lib


def zotero_record(lib: dict, zid: int, col_ids: dict[str, int]) -> dict:
    """Normalized Zotero-side view of one item (what the three-way merge compares)."""
    item_type, field_values = map_item_fields(lib["types"][zid], lib["values"][zid])
    return {"item_type": item_type, "fields": field_values, "creators": lib["creators"][zid],
            "tags": dict(sorted(lib["tags"][zid].items())),
            "collections": sorted(col_ids[k] for k in lib["item_collections"][zid] if k in col_ids)}


def local_record(item_id: int, mirrored: set[int]) -> Optional[dict]:
    """Same shape as ``zotero_record`` for the local item (only mirrored collections count)."""
    item = catalog.get_item(item_id)
    if not item:
        return None
    return {"item_type": item["item_type"],
            "fields": {k: str(v) for k, v in item["field_values"].items() if v not in (None, "")},
            "creators": catalog.clean_creators(item.get("creators") or []),
            "tags": {t["name"]: int(t["type"] or 0) for t in sorted(item["tags"], key=lambda t: t["name"])},
            "collections": sorted(c for c in item["collections"] if c in mirrored),
            "trashed": bool(item.get("deleted_at"))}


# ======================================================================================
# Links (zotero_links)
# ======================================================================================

def _link(lib_key: str, kind: str, zkey: str) -> Optional[dict]:
    row = get_conn().execute("SELECT local_id, zmodified, base FROM zotero_links WHERE lib=? AND kind=? AND zkey=?",
                             (lib_key, kind, zkey)).fetchone()
    if not row:
        return None
    return {"local_id": row[0], "zmodified": row[1], "base": json.loads(row[2]) if row[2] else None}


def _save_link(lib_key: str, kind: str, zkey: str, local_id: int, zmodified: Optional[str], base: Any) -> None:
    get_conn().execute(
        "INSERT OR REPLACE INTO zotero_links (lib, kind, zkey, local_id, zmodified, base) VALUES (?,?,?,?,?,?)",
        (lib_key, kind, zkey, local_id, zmodified, json.dumps(base, ensure_ascii=False) if base is not None else None))


def _adopt_legacy_map(lib_key: str) -> int:
    """Items imported by the old one-shot importer (table zotero_import_map) become links without a base."""
    conn = get_conn()
    if lib_key != "user" or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='zotero_import_map'").fetchone():
        return 0
    return conn.execute("INSERT OR IGNORE INTO zotero_links (lib, kind, zkey, local_id, zmodified, base) "
                        "SELECT 'user', kind, zkey, local_id, NULL, NULL FROM zotero_import_map").rowcount


# ======================================================================================
# Sync
# ======================================================================================

def _new_report() -> dict:
    keys = ("items", "items_existing", "updated", "unchanged", "conflicts_kept_local", "conflicts_zotero_won",
            "trashed", "restored", "skipped_trash", "collections", "collections_renamed", "notes", "notes_updated",
            "notes_deleted", "files", "links", "missing_files", "annotations", "skipped_annotations", "relations",
            "tag_colors", "saved_searches", "legacy_adopted")
    report: dict[str, Any] = {k: 0 for k in keys}
    report["warnings"] = []
    report["libraries"] = []
    return report


def sync(data_dir: str | Path | None = None, *, libraries: Optional[list[str]] = None, copy_files: Optional[bool] = None,
         include_trash: bool = False, import_saved_searches: bool = False, progress: Progress = None,
         force: bool = True) -> dict:
    """Synchronize the selected Zotero libraries into ReviewHunter.

    ``libraries``: library keys ("user", "group:123"). None = every library (one-shot migration);
    the background loop and the Settings button pass the libraries enabled by the user.
    ``force=False`` returns immediately when zotero.sqlite did not change since the last sync.
    """
    s = config.get_settings()
    folder = Path(data_dir).expanduser() if data_dir else find_data_dir()
    if not folder or not (folder / "zotero.sqlite").exists():
        raise ValueError("zotero.sqlite not found; choose your Zotero data directory")
    copy_files = s.get("zotero_copy_files", True) if copy_files is None else copy_files
    emit = progress or (lambda stage, data: None)
    if not _lock.acquire(blocking=False):
        return {"ok": False, "reason": "a Zotero sync is already running"}
    try:
        fp = fingerprint(folder)
        if not force and fp == _state.get("last_fingerprint"):
            return {"ok": True, "skipped": "unchanged"}
        _state.update(running=True, error="", progress=None)
        report = _new_report()
        started = time.perf_counter()
        with _open_copy(folder) as z:
            libs = list_libraries(z)
            single = not _has_table(z, "libraries")
            wanted = [lib for lib in libs if libraries is None or lib["key"] in libraries]
            configured = s.get("zotero_libraries") or {}
            for lib in wanted:
                target = (configured.get(lib["key"]) or {}).get("target_collection_id")
                if lib["type"] == "group" and target is None:
                    target = _group_collection(lib["name"], lib["key"])
                report["libraries"].append({"key": lib["key"], "name": lib["name"]})
                _sync_library(z, folder, lib, single, target, report, emit, copy_files=copy_files,
                              include_trash=include_trash, policy=s.get("zotero_conflict_policy") or "zotero",
                              mirror_deletions=bool(s.get("zotero_mirror_deletions", True)))
                if import_saved_searches and lib["key"] == "user":
                    _import_saved_searches(z, lib, single, report)
        report["items_total"] = get_conn().execute("SELECT COUNT(*) FROM zotero_links WHERE kind='item'").fetchone()[0]
        report["seconds"] = round(time.perf_counter() - started, 3)
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        report["ok"] = True
        report["warnings"] = report["warnings"][:50]
        _state["last_fingerprint"] = fp
        config.update_settings({"zotero_last_sync": {k: v for k, v in report.items() if k != "warnings"}
                                | {"warning_count": len(report["warnings"])}})
        emit("zotero_done", {k: v for k, v in report.items() if k != "warnings"})
        return report
    except Exception as e:
        _state["error"] = str(e)
        raise
    finally:
        _state.update(running=False, progress=None)
        _lock.release()


def _group_collection(name: str, lib_key: str) -> int:
    """Default destination of a group library: a top-level collection named after the group."""
    settings = config.get_settings()
    libs = dict(settings.get("zotero_libraries") or {})
    cfg = dict(libs.get(lib_key) or {})
    conn = get_conn()
    if cfg.get("target_collection_id") and conn.execute("SELECT 1 FROM collections WHERE id=?",
                                                        (cfg["target_collection_id"],)).fetchone():
        return int(cfg["target_collection_id"])
    label = f"Zotero group - {name}"
    row = conn.execute("SELECT id FROM collections WHERE name=? AND parent_id IS NULL", (label,)).fetchone()
    cid = row[0] if row else conn.execute("INSERT INTO collections (name, parent_id) VALUES (?, NULL)", (label,)).lastrowid
    cfg.update(target_collection_id=cid, enabled=cfg.get("enabled", True))
    libs[lib_key] = cfg
    config.update_settings({"zotero_libraries": libs})
    return cid


def _sync_collections(lib: dict, lib_key: str, target: Optional[int], report: dict) -> dict[str, int]:
    """Mirror Zotero collections (parents first) under ``target``; returns Zotero key -> local id."""
    conn = get_conn()
    by_id = {c["collectionID"]: c for c in lib["collections"]}
    col_ids: dict[str, int] = {}
    pending = sorted(lib["collections"], key=lambda c: c["collectionID"])
    guard = 0
    while pending and guard < 100000:
        guard += 1
        c = pending.pop(0)
        parent_z = by_id.get(c["parentCollectionID"])
        if parent_z and parent_z["key"] not in col_ids:
            pending.append(c)   # parent not processed yet
            continue
        parent_local = col_ids[parent_z["key"]] if parent_z else target
        link = _link(lib_key, "collection", c["key"])
        if link and conn.execute("SELECT 1 FROM collections WHERE id=?", (link["local_id"],)).fetchone():
            cid = link["local_id"]
            base = link["base"] or {}
            # Rename / move only what Zotero changed since the last sync (a local rename is kept otherwise).
            if base.get("name") is not None and base.get("name") != c["collectionName"]:
                conn.execute("UPDATE collections SET name=? WHERE id=?", (c["collectionName"], cid))
                report["collections_renamed"] += 1
            if base.get("parent") != (parent_z["key"] if parent_z else None) and "parent" in base:
                try:
                    catalog.move_collection(cid, parent_local)
                except ValueError as e:
                    report["warnings"].append(f"collection {c['collectionName']}: {e}")
        else:
            row = conn.execute("SELECT id FROM collections WHERE name=? AND parent_id IS ?",
                               (c["collectionName"], parent_local)).fetchone()
            cid = row[0] if row else conn.execute("INSERT INTO collections (name, parent_id) VALUES (?, ?)",
                                                  (c["collectionName"], parent_local)).lastrowid
            report["collections"] += 0 if row else 1
        col_ids[c["key"]] = cid
        _save_link(lib_key, "collection", c["key"], cid, c.get("modified"),
                   {"name": c["collectionName"], "parent": parent_z["key"] if parent_z else None})
    return col_ids


def _merge_scalar(name: str, znew: Any, zbase: Any, local: Any, lbase: Any, policy: str, report: dict,
                  kept: Optional[set] = None) -> tuple[bool, Any]:
    """Three-way decision for one value. Returns (apply?, value).

    A conflict resolved in favour of the local value is added to ``kept``: the caller then keeps the
    OLD local base for it, so the value stays marked as a local edit and a later Zotero change is
    again detected as a conflict instead of silently overwriting it.
    """
    if znew == zbase:
        return False, None                      # Zotero did not touch it: keep whatever is local
    if local != lbase and local != znew:        # both sides changed the same value
        if policy == "local":
            report["conflicts_kept_local"] += 1
            if kept is not None:
                kept.add(name)
            return False, None
        report["conflicts_zotero_won"] += 1
    return local != znew, znew


def _update_item(item_id: int, znew: dict, base: Optional[dict], mirrored: set[int], policy: str,
                 report: dict, kept: Optional[set] = None) -> bool:
    """Apply what changed in Zotero since ``base`` to the local item. Returns True if anything changed."""
    local = local_record(item_id, mirrored)
    if local is None:
        return False
    if base is None:
        # Legacy link (old importer, no base): never overwrite, only fill what is still empty locally.
        zbase = {"item_type": local["item_type"], "fields": {k: local["fields"].get(k) for k in znew["fields"]},
                 "creators": local["creators"] if local["creators"] else None, "tags": {}, "collections": []}
        zbase["fields"] = {k: (v if v else None) for k, v in zbase["fields"].items()}
        lbase = local
    else:
        zbase, lbase = base["z"], base["l"]
    patch: dict[str, Any] = {}
    field_patch: dict[str, Any] = {}
    for f in sorted(set(znew["fields"]) | set(zbase.get("fields") or {})):
        if base is None and local["fields"].get(f):
            continue
        apply, value = _merge_scalar(f, znew["fields"].get(f), (zbase.get("fields") or {}).get(f),
                                     local["fields"].get(f), (lbase.get("fields") or {}).get(f), policy, report, kept)
        if apply:
            field_patch[f] = value or ""
    apply, value = _merge_scalar("item_type", znew["item_type"], zbase.get("item_type"), local["item_type"],
                                 lbase.get("item_type"), policy, report, kept)
    if apply and base is not None:
        patch["item_type"] = value
    apply, value = _merge_scalar("creators", znew["creators"], zbase.get("creators"), local["creators"],
                                 lbase.get("creators"), policy, report, kept)
    if apply and (base is not None or not local["creators"]):
        patch["creators"] = value
    changed = False
    if field_patch:
        patch["field_values"] = field_patch
    if patch:
        try:
            catalog.patch_item(item_id, patch)
            changed = True
        except ValueError as e:     # e.g. the new DOI belongs to another local item
            report["warnings"].append(f"item {item_id}: {e}")
    # Tags and collections: replay Zotero's additions and removals (set difference against the base).
    old_tags, new_tags = zbase.get("tags") or {}, znew["tags"]
    for name in new_tags.keys() - old_tags.keys():
        if name not in local["tags"]:
            catalog.add_tag(item_id, name, tag_type=new_tags[name])
            changed = True
    for name in old_tags.keys() - new_tags.keys():
        if name in local["tags"]:
            catalog.remove_tag(item_id, name)
            changed = True
    conn = get_conn()
    old_cols, new_cols = set(zbase.get("collections") or []), set(znew["collections"])
    for cid in new_cols - old_cols - set(local["collections"]):
        conn.execute("INSERT OR IGNORE INTO item_collections (item_id, collection_id) VALUES (?, ?)", (item_id, cid))
        changed = True
    for cid in (old_cols - new_cols) & set(local["collections"]):
        conn.execute("DELETE FROM item_collections WHERE item_id=? AND collection_id=?", (item_id, cid))
        changed = True
    return changed


def _keep_local_divergence(new_local: dict, old_lbase: dict, kept: set) -> None:
    """For values kept by the "local" policy, store the previous local base (see ``_merge_scalar``)."""
    for name in kept:
        if name in ("item_type", "creators"):
            new_local[name] = old_lbase.get(name)
            continue
        old = (old_lbase.get("fields") or {}).get(name)
        if old is None:
            new_local["fields"].pop(name, None)
        else:
            new_local["fields"][name] = old


def _create_item(lib: dict, zid: int, zrec: dict, report: dict) -> int:
    payload = {
        "item_type": zrec["item_type"], "title": zrec["fields"].get("title") or "Untitled",
        "creators": zrec["creators"], "field_values": zrec["fields"], "doi": zrec["fields"].get("DOI"),
        "fields": {"zoteroKey": lib["keys"][zid]}, "collections": zrec["collections"],
        "status": "metadata_only", "source_query": f"zotero:{lib['keys'][zid]}",
    }
    if zrec["fields"].get("citationKey"):
        payload["citation_key"] = zrec["fields"]["citationKey"]
    item = catalog.create_item(payload)
    iid = item["id"]
    for name, ttype in zrec["tags"].items():
        catalog.add_tag(iid, name, tag_type=ttype)
    if lib["added"].get(zid) and not item.get("_existing"):
        get_conn().execute("UPDATE items SET added_at=? WHERE id=?", (lib["added"][zid], iid))
    report["items_existing" if item.get("_existing") else "items"] += 1
    return iid


def _sync_library(z, data_dir: Path, lib_info: dict, single: bool, target: Optional[int], report: dict,
                  emit: Callable[[str, dict], None], *, copy_files: bool, include_trash: bool, policy: str,
                  mirror_deletions: bool) -> None:
    lib_key = lib_info["key"]
    conn = get_conn()
    report["legacy_adopted"] += _adopt_legacy_map(lib_key)
    lib = _read_library(z, lib_info["library_id"], single)
    with tx():
        col_ids = _sync_collections(lib, lib_key, target, report)
    mirrored = set(col_ids.values())

    # ---- regular items
    regular = sorted(zid for zid, t in lib["types"].items() if t not in REGULAR_EXCLUDED)
    item_map: dict[int, int] = {}
    seen_keys: set[str] = set()
    for index, zid in enumerate(regular, start=1):
        if index % 25 == 0 or index == len(regular):
            _state["progress"] = {"library": lib_info["name"], "stage": "items", "index": index, "total": len(regular)}
            emit("zotero_progress", {"stage": "items", "index": index, "total": len(regular)})
        zkey = lib["keys"][zid]
        seen_keys.add(zkey)
        trashed_in_zotero = zid in lib["deleted"]
        try:
            with tx():
                link = _link(lib_key, "item", zkey)
                local_row = conn.execute("SELECT deleted_at FROM items WHERE id=?",
                                         (link["local_id"],)).fetchone() if link else None
                if link and local_row is None:
                    link = None   # the local item was permanently deleted: re-create it
                if trashed_in_zotero and not link:
                    if not include_trash:
                        report["skipped_trash"] += 1
                        continue
                zrec = zotero_record(lib, zid, col_ids)
                if not link:
                    iid = _create_item(lib, zid, zrec, report)
                    if trashed_in_zotero:
                        catalog.trash_items([iid])
                    _save_link(lib_key, "item", zkey, iid, lib["modified"][zid],
                               {"z": zrec, "l": local_record(iid, mirrored), "trashed_by_sync": trashed_in_zotero})
                    item_map[zid] = iid
                    continue
                iid = link["local_id"]
                item_map[zid] = iid
                base = link["base"]
                trashed_by_sync = bool(base and base.get("trashed_by_sync"))
                if trashed_in_zotero:
                    if mirror_deletions and not local_row["deleted_at"]:
                        catalog.trash_items([iid])
                        report["trashed"] += 1
                        trashed_by_sync = True
                    elif not local_row["deleted_at"]:
                        trashed_by_sync = False
                elif local_row["deleted_at"] and trashed_by_sync:
                    catalog.restore_items([iid])          # restored in Zotero -> restore here
                    report["restored"] += 1
                    trashed_by_sync = False
                if base is not None and link["zmodified"] == lib["modified"][zid] and base.get("z") == zrec:
                    report["unchanged"] += 1
                    if trashed_by_sync != bool(base.get("trashed_by_sync")):
                        base["trashed_by_sync"] = trashed_by_sync
                        _save_link(lib_key, "item", zkey, iid, lib["modified"][zid], base)
                    continue
                kept: set = set()
                if _update_item(iid, zrec, base, mirrored, policy, report, kept):
                    report["updated"] += 1
                else:
                    report["unchanged"] += 1
                new_local = local_record(iid, mirrored)
                if base is not None and new_local is not None:
                    _keep_local_divergence(new_local, base["l"], kept)
                _save_link(lib_key, "item", zkey, iid, lib["modified"][zid],
                           {"z": zrec, "l": new_local, "trashed_by_sync": trashed_by_sync})
        except Exception as e:   # one malformed record must never abort a whole library
            report["warnings"].append(f"item {zkey}: {e}")

    # ---- items purged from Zotero (emptied trash): move the local copy to the trash once
    if mirror_deletions:
        for zkey, local_id, base in conn.execute(
                "SELECT zkey, local_id, base FROM zotero_links WHERE lib=? AND kind='item'", (lib_key,)).fetchall():
            if zkey in seen_keys:
                continue
            row = conn.execute("SELECT deleted_at FROM items WHERE id=?", (local_id,)).fetchone()
            if row and not row[0]:
                catalog.trash_items([local_id])
                report["trashed"] += 1
                b = json.loads(base) if base else {}
                b["trashed_by_sync"] = True
                _save_link(lib_key, "item", zkey, local_id, None, b)

    _sync_notes(lib, lib_key, item_map, include_trash, mirror_deletions, report)
    _sync_attachments(lib, lib_key, data_dir, item_map, include_trash, copy_files, report, emit)
    _sync_annotations(lib, lib_key, report)
    _sync_relations(lib, item_map, report)
    _sync_tag_colors(lib, report)


def _sync_notes(lib: dict, lib_key: str, item_map: dict[int, int], include_trash: bool, mirror_deletions: bool,
                report: dict) -> None:
    conn = get_conn()
    for n in lib["notes"]:
        zid, parent = n["itemID"], n["parentItemID"]
        zkey = lib["keys"][zid]
        html = (n["note"] or "").strip()
        deleted = zid in lib["deleted"] or (parent is not None and parent in lib["deleted"] and not include_trash)
        link = _link(lib_key, "note", zkey)
        if link and not conn.execute("SELECT 1 FROM notes WHERE id=?", (link["local_id"],)).fetchone():
            link = None
        try:
            if link:
                if deleted:
                    if mirror_deletions and not conn.execute("SELECT deleted_at FROM notes WHERE id=?",
                                                             (link["local_id"],)).fetchone()[0]:
                        catalog.delete_note(link["local_id"])
                        report["notes_deleted"] += 1
                    continue
                if link["zmodified"] != lib["modified"][zid]:
                    base_html = (link["base"] or {}).get("z")
                    if base_html != html:
                        current = catalog.get_note(link["local_id"]) or {}
                        if base_html is None or current.get("html") == (link["base"] or {}).get("l") \
                                or (config.get_settings().get("zotero_conflict_policy") or "zotero") == "zotero":
                            catalog.update_note(link["local_id"], html)
                            report["notes_updated"] += 1
                    current = catalog.get_note(link["local_id"]) or {}
                    _save_link(lib_key, "note", zkey, link["local_id"], lib["modified"][zid],
                               {"z": html, "l": current.get("html")})
                continue
            if deleted and not include_trash:
                continue
            if (parent and parent not in item_map) or not html:
                continue
            note = catalog.create_note(item_map.get(parent) if parent else None, html)
            if lib["added"].get(zid):
                conn.execute("UPDATE notes SET created_at=? WHERE id=?", (lib["added"][zid], note["id"]))
            _save_link(lib_key, "note", zkey, note["id"], lib["modified"][zid], {"z": html, "l": note["html"]})
            report["notes"] += 1
        except Exception as e:
            report["warnings"].append(f"note {zkey}: {e}")


def _sync_attachments(lib: dict, lib_key: str, data_dir: Path, item_map: dict[int, int], include_trash: bool,
                      copy_files: bool, report: dict, emit: Callable[[str, dict], None]) -> None:
    conn = get_conn()
    rows = lib["attachments"]
    for index, a in enumerate(rows, start=1):
        if index % 25 == 0 or index == len(rows):
            emit("zotero_progress", {"stage": "files", "index": index, "total": len(rows)})
        zid, parent = a["itemID"], a["parentItemID"]
        zkey = lib["keys"][zid]
        if zid in lib["deleted"] and not include_trash:
            continue
        link = _link(lib_key, "attachment", zkey)
        if link and conn.execute("SELECT 1 FROM attachments WHERE id=?", (link["local_id"],)).fetchone():
            continue
        try:
            vals = lib["values"][zid]
            title = vals.get("title") or (Path(a["path"].split(":", 1)[-1]).name if a["path"] else "Attachment")
            if parent:
                item_id = item_map.get(parent)
                if not item_id:
                    continue
            else:
                # Standalone attachment: Zotero shows it as a top-level row; we wrap it in a Document item.
                item_id = catalog.create_item({"item_type": "document", "title": title,
                                               "fields": {"zoteroKey": zkey}}, dedupe_doi=False)["id"]
            if a["linkMode"] == 3:   # linked URL
                if not vals.get("url"):
                    continue
                att = catalog.add_link_attachment(item_id, vals["url"], title)
                report["links"] += 1
            else:
                path = a["path"] or ""
                src: Optional[Path] = None
                if path.startswith("storage:"):
                    src = data_dir / "storage" / zkey / path[len("storage:"):]
                elif path.startswith("attachments:"):
                    report["warnings"].append(f"{title}: relative linked file (base directory unknown): {path}")
                    continue
                elif path:
                    src = Path(path)
                if not src or not src.exists():
                    report["missing_files"] += 1
                    continue
                if copy_files and a["linkMode"] in (0, 1):
                    att = catalog.store_file_bytes(item_id, src.read_bytes(), src.name, title=title,
                                                   kind="snapshot" if (a["contentType"] or "").startswith("text/html")
                                                   else "file", url=vals.get("url"))
                else:
                    att = catalog.register_file(item_id, src, title=title, url=vals.get("url"))
                report["files"] += 1
            _save_link(lib_key, "attachment", zkey, att["id"], lib["modified"][zid], None)
        except Exception as e:
            report["warnings"].append(f"attachment {zkey}: {e}")


def _sync_annotations(lib: dict, lib_key: str, report: dict) -> None:
    conn = get_conn()
    att_local = {lib_zid: _link(lib_key, "attachment", lib["keys"][lib_zid]) for lib_zid in
                 {a["parentItemID"] for a in lib["annotations"]} if lib_zid in lib["keys"]}
    for a in lib["annotations"]:
        zkey = lib["keys"][a["itemID"]]
        parent = att_local.get(a["parentItemID"])
        if not parent or a["itemID"] in lib["deleted"]:
            continue
        link = _link(lib_key, "annotation", zkey)
        if link and conn.execute("SELECT 1 FROM annotations WHERE id=?", (link["local_id"],)).fetchone():
            continue
        kind = ANNOTATION_TYPES.get(a["type"])
        if not kind:
            report["skipped_annotations"] += 1
            continue
        try:
            pos = json.loads(a["position"] or "{}")
        except json.JSONDecodeError:
            pos = {}
        ann = catalog.create_annotation(parent["local_id"], {
            "type": kind, "color": a["color"] or "#ffd400", "page": int(pos.get("pageIndex", 0)) + 1,
            "text": a["text"], "comment": a["comment"], "position": {"rects": pos.get("rects") or []},
            "sort_index": a["sortIndex"]})
        _save_link(lib_key, "annotation", zkey, ann["id"], lib["modified"].get(a["itemID"]), None)
        report["annotations"] += 1


def _sync_relations(lib: dict, item_map: dict[int, int], report: dict) -> None:
    key_to_local = {lib["keys"][zid]: iid for zid, iid in item_map.items()}
    # Zotero stores a relation once per direction; count each direction that did not exist before this sync.
    existing = {(r[0], r[1]) for r in get_conn().execute("SELECT item_id, related_id FROM related_items")}
    for zid, obj in lib["relations"]:
        m = re.search(r"/items/([A-Z0-9]{8})$", obj or "")
        if zid in item_map and m and m.group(1) in key_to_local:
            a, b = item_map[zid], key_to_local[m.group(1)]
            if (a, b) not in existing:
                catalog.relate(a, b)
                report["relations"] += 1


def _sync_tag_colors(lib: dict, report: dict) -> None:
    conn = get_conn()
    for tc in lib["tag_colors"]:
        if not isinstance(tc, dict):
            continue
        tid = catalog._tag_id(tc.get("name", ""), create=False)
        if tid and tc.get("color"):
            if conn.execute("UPDATE tags SET color=? WHERE id=? AND color IS NOT ?", (tc["color"], tid, tc["color"])).rowcount:
                report["tag_colors"] += 1


def _import_saved_searches(z, lib_info: dict, single: bool, report: dict) -> None:
    """Saved searches (one-shot migration only): conditions we cannot express are dropped."""
    if not _has_table(z, "savedSearches"):
        return
    conn = get_conn()
    cols = {r[1] for r in z.execute("PRAGMA table_info(savedSearches)")}
    where = "" if single or "libraryID" not in cols else f" WHERE libraryID={int(lib_info['library_id'])}"
    for s in z.execute(f"SELECT savedSearchID, savedSearchName FROM savedSearches{where}").fetchall():
        conds, match = [], "all"
        for c in z.execute("SELECT condition, operator, value FROM savedSearchConditions WHERE savedSearchID=?", (s[0],)):
            if c[0] == "joinMode":
                match = "any" if c[2] == "any" else "all"
                continue
            field = SEARCH_FIELD_MAP.get(c[0])
            if not field:
                continue
            value = c[2]
            if field == "collection":
                link = _link(lib_info["key"], "collection", str(value).split("_")[-1])
                if not link:
                    continue
                value = str(link["local_id"])
            conds.append({"field": field, "op": SEARCH_OP_MAP.get(c[1], "contains"), "value": value})
        if conds and not conn.execute("SELECT 1 FROM saved_searches WHERE name=?", (s[1],)).fetchone():
            catalog.save_search(s[1], conds, match)
            report["saved_searches"] += 1


# ======================================================================================
# Configuration + background loop
# ======================================================================================

def configure(patch: dict) -> dict:
    """Update connector settings. ``libraries`` = {key: {enabled, target_collection_id}}."""
    s = config.get_settings()
    update: dict[str, Any] = {}
    if "libraries" in patch:
        libs = dict(s.get("zotero_libraries") or {})
        for key, cfg in (patch["libraries"] or {}).items():
            merged = dict(libs.get(key) or {})
            merged.update({k: v for k, v in (cfg or {}).items() if k in ("enabled", "target_collection_id")})
            libs[key] = merged
        update["zotero_libraries"] = libs
    for key in ("auto_sync", "sync_interval_minutes", "conflict_policy", "mirror_deletions", "copy_files",
                "install_dir", "data_dir"):
        if key in patch:
            update[f"zotero_{key}"] = patch[key]
    if update.get("zotero_conflict_policy") not in (None, "zotero", "local"):
        raise ValueError("conflict_policy must be 'zotero' or 'local'")
    config.update_settings(update)
    _state["last_fingerprint"] = None   # settings changed: next background pass must really sync
    return detect()


def enabled_libraries() -> list[str]:
    libs = config.get_settings().get("zotero_libraries") or {}
    keys = [k for k, cfg in libs.items() if (cfg or {}).get("enabled")]
    if "user" not in libs:
        keys.append("user")   # My Library is enabled unless the user switched it off
    return keys


def scheduler_loop(stop: threading.Event, tick_s: int = 60, on_change: Optional[Callable[[dict], None]] = None) -> None:
    """Background auto-sync: at most every ``zotero_sync_interval_minutes``, only if zotero.sqlite changed."""
    stop.wait(45)
    last_run = 0.0
    while not stop.is_set():
        try:
            s = config.get_settings()
            minutes = max(1.0, float(s.get("zotero_sync_interval_minutes") or 10))
            if s.get("zotero_auto_sync") and time.time() - last_run >= minutes * 60:
                last_run = time.time()
                result = sync(libraries=enabled_libraries(), force=False)
                if on_change and result.get("ok") and not result.get("skipped"):
                    on_change(result)
        except Exception as e:
            log.warning(f"Zotero auto-sync failed: {e}")
        stop.wait(tick_s)
