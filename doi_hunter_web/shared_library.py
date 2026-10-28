"""Shared libraries: several people read and edit one common library through a synced folder.

The folder is typically a Google Drive folder shared with collaborators (Drive for desktop makes it a
local folder such as ``G:\\My Drive\\ReviewHunter Shared\\Dopamine Lab``), but any synced folder works
(OneDrive, Dropbox, a network share). There is no server: every ReviewHunter instance reads the folder,
merges it, and writes its own changes back.

Why not put a database (or one big JSON file) in Drive?
    Two people saving the same file at the same time makes Drive keep one version and drop (or fork)
    the other, and a live SQLite file in a sync folder corrupts. This protocol avoids both: **each member
    only ever writes files inside its own folder**, so Drive never sees two writers on one file.

Folder layout (format 1)
------------------------
::

    reviewhunter-shared.json          manifest: library id, name, creator, format
    members/<member>.json             each member's card (name, e-mail, last sync) - written by that member
    records/<member>/<record>.json    that member's latest values for one record it has edited
    files/<aa>/<sha256>.<ext>         attachments, content-addressed (same bytes = same name, no conflicts)

A record (one item, note or collection) holds ``fields: {name: [value, timestamp]}``. Reading the library
means merging every member's copy of every record: for each field the value with the highest
``(timestamp, member)`` wins (a last-writer-wins register per field, i.e. a state-based CRDT). The merge
is commutative, associative and idempotent, so every member converges to the same library whatever the
order in which Drive delivers files, and two people editing *different* fields of a paper both keep their
edit. Timestamps come from a hybrid logical clock (never smaller than the newest timestamp already seen),
so an edit made after seeing a colleague's edit always wins over it even if the computer clocks differ.

Item fields: ``f:<zoteroField>`` (title, DOI, abstractNote...), ``item_type``, ``creators`` (list),
``tag:<name>`` (True = present), ``col:<collection record>`` (True = member), ``file:<sha256>`` (file
card) and ``deleted``. Sets (tags, collections, files) are thus merged element by element.

Locally a shared library is a top-level collection (icon "shared"); its sub-collections, the items in
them, their child notes and their files are shared. Anything else in the personal library stays private.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import __version__, catalog, config
from .db import get_conn, tx

log = logging.getLogger("doihunter.shared")

FORMAT = 1
MANIFEST = "reviewhunter-shared.json"
ROOT_RID = "root"                     # the library's top-level collection (not a record)
SHARED_ICON = "shared"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")      # file keys come from other members: never trust them as paths

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_file_cache: dict[str, tuple[int, int, Optional[dict]]] = {}   # path -> (size, mtime_ns, parsed record)


# ======================================================================================
# Identity + clock
# ======================================================================================

def member() -> dict:
    """This computer's member identity (created once, stored in settings.json)."""
    s = config.get_settings()
    patch = {}
    if not s.get("member_id"):
        patch["member_id"] = uuid.uuid4().hex
    if not s.get("member_name"):
        try:
            patch["member_name"] = getpass.getuser()
        except Exception:
            patch["member_name"] = "ReviewHunter user"
    if patch:
        s = config.update_settings(patch)
    return {"id": s["member_id"], "name": s["member_name"], "email": s.get("member_email") or ""}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: Optional[int] = None) -> str:
    return datetime.fromtimestamp((ms or _now_ms()) / 1000).isoformat(timespec="seconds")


# ======================================================================================
# The merge (pure functions, unit-tested without any folder)
# ======================================================================================

def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def merge_records(records: Iterable[tuple[str, dict]]) -> dict[str, dict]:
    """Merge every member's record files.

    ``records``: (member_id, record) pairs, record = {"id", "type", "fields": {name: [value, ts]}}.
    Returns {record_id: {"type": str, "fields": {name: (value, ts, member)}}}.
    Winner per field = max (ts, member, canonical value): a total order, so the result does not depend
    on the order of the input (Drive conflict copies of a file are therefore harmless duplicates).
    """
    out: dict[str, dict] = {}
    for member_id, rec in records:
        rid, rtype = rec.get("id"), rec.get("type")
        if not isinstance(rid, str) or rtype not in ("item", "note", "collection"):
            continue
        slot = out.setdefault(rid, {"type": rtype, "fields": {}})
        for name, cell in (rec.get("fields") or {}).items():
            if not (isinstance(cell, list) and len(cell) == 2 and isinstance(cell[1], int)):
                continue
            value, ts = cell
            current = slot["fields"].get(name)
            cand = (ts, member_id, _canon(value))
            if current is None or cand > (current[1], current[2], _canon(current[0])):
                slot["fields"][name] = (value, ts, member_id)
    return out


def values_of(merged_record: dict) -> dict[str, Any]:
    """Field values of a merged record; None (removed / false) values are dropped."""
    return {k: v[0] for k, v in merged_record["fields"].items() if v[0] not in (None, False)}


def max_clock(merged: dict[str, dict]) -> int:
    return max((cell[1] for rec in merged.values() for cell in rec["fields"].values()), default=0)


def diff(current: dict, base: dict) -> dict:
    """Fields whose value differs from the base; a field that disappeared maps to None."""
    return {k: current.get(k) for k in set(current) | set(base) if current.get(k) != base.get(k)}


# ======================================================================================
# Folder I/O
# ======================================================================================

class SharedFolder:
    """Reads and writes the shared folder. Every write is atomic (temp file + rename)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ---- manifest / members
    def manifest(self) -> dict:
        try:
            data = json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"not a ReviewHunter shared library (missing or unreadable {MANIFEST}): {self.root}") from e
        if data.get("format", 0) > FORMAT:
            raise ValueError("this shared library was created by a newer ReviewHunter; please update")
        return data

    def members(self) -> list[dict]:
        out = []
        for f in sorted((self.root / "members").glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    @staticmethod
    def write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    # ---- records
    def read_records(self, warnings: Optional[list] = None) -> list[tuple[str, dict]]:
        """All members' records. Unchanged files come from a (size, mtime) cache; corrupt or half-synced
        files are skipped (they will be read again once Drive finishes writing them)."""
        out = []
        base = self.root / "records"
        if not base.exists():
            return out
        for mdir in base.iterdir():
            if not mdir.is_dir():
                continue
            for f in mdir.glob("*.json"):
                key = str(f)
                try:
                    st = f.stat()
                except OSError:
                    continue
                cached = _file_cache.get(key)
                if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
                    rec = cached[2]
                else:
                    try:
                        rec = json.loads(f.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                        rec = None
                        if warnings is not None:
                            warnings.append(f"unreadable record skipped (still syncing?): {f.name}")
                    _file_cache[key] = (st.st_size, st.st_mtime_ns, rec)
                if rec:
                    out.append((mdir.name, rec))
        return out

    def record_path(self, member_id: str, rid: str) -> Path:
        return self.root / "records" / member_id / f"{rid}.json"

    def publish(self, member_id: str, rid: str, rtype: str, changes: dict, ts: int) -> dict:
        """Merge ``changes`` into this member's own file for the record and write it."""
        path = self.record_path(member_id, rid)
        try:
            own = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            own = {"id": rid, "type": rtype, "fields": {}}
        for name, value in changes.items():
            own["fields"][name] = [value, ts]
        own.update(id=rid, type=rtype, member=member_id, updated_at=_iso(ts))
        self.write_json(path, own)
        return own

    # ---- files
    def blob_path(self, sha: str, ext: str) -> Path:
        ext = re.sub(r"[^a-z0-9]", "", (ext or "bin").lower())[:8] or "bin"
        return self.root / "files" / sha[:2] / f"{sha}.{ext}"

    def put_blob(self, src: Path, sha: str, ext: str) -> bool:
        target = self.blob_path(sha, ext)
        if target.exists() and target.stat().st_size == src.stat().st_size:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
        return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _attachment_sha(att: dict) -> Optional[str]:
    """SHA-256 of an attachment file, cached by (size, mtime). A missing file keeps its last known hash,
    so a file that is temporarily unavailable is never published as deleted."""
    conn = get_conn()
    row = conn.execute("SELECT size, mtime, sha256 FROM shared_attachment_hash WHERE attachment_id=?",
                       (att["id"],)).fetchone()
    p = Path(att["path"]) if att.get("path") else None
    if not p or not p.exists():
        return row[2] if row else None
    st = p.stat()
    if row and row[0] == st.st_size and abs((row[1] or 0) - st.st_mtime) < 0.001:
        return row[2]
    sha = _sha256(p)
    conn.execute("INSERT OR REPLACE INTO shared_attachment_hash (attachment_id, size, mtime, sha256) VALUES (?,?,?,?)",
                 (att["id"], st.st_size, st.st_mtime, sha))
    return sha


# ======================================================================================
# Library registry (create / join / leave / list / discover)
# ======================================================================================

def _row(library_id: str) -> dict:
    row = get_conn().execute("SELECT * FROM shared_libraries WHERE library_id=?", (library_id,)).fetchone()
    if not row:
        raise ValueError("unknown shared library")
    return dict(row)


def _lock_for(library_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(library_id, threading.Lock())


def _safe_folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip().strip(".")
    return re.sub(r"\s+", " ", cleaned)[:80] or "Shared library"


def default_parent() -> Optional[str]:
    """Suggested place for new shared libraries: <Drive>/ReviewHunter Shared."""
    root = config.default_google_drive_root()
    return str(root / "ReviewHunter Shared") if root else None


def create(name: str, parent_dir: str, *, collection_id: Optional[int] = None) -> dict:
    """Create a shared library folder; ``collection_id`` shares an existing collection (and its subtree)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a name is required")
    if not parent_dir:
        raise ValueError("choose the folder (for example your Google Drive folder) where the library will live")
    parent = Path(parent_dir).expanduser()
    root = parent / _safe_folder_name(name)
    if (root / MANIFEST).exists():
        raise ValueError(f"{root} already contains a shared library: use Join instead")
    if collection_id is not None and get_conn().execute(
            "SELECT 1 FROM shared_libraries WHERE collection_id=?", (collection_id,)).fetchone():
        raise ValueError("this collection is already shared")
    me = member()
    library_id = uuid.uuid4().hex
    for sub in ("members", "records", "files"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    SharedFolder.write_json(root / MANIFEST, {
        "format": FORMAT, "app": "ReviewHunter", "library_id": library_id, "name": name,
        "created_at": _iso(), "created_by": {"id": me["id"], "name": me["name"]},
        "readme": "Shared ReviewHunter library. Share this folder (Editor = can add papers, Viewer = read only). "
                  "Do not edit or move files by hand.",
    })
    _register(library_id, name, root, collection_id)
    return sync(library_id)


def join(root_path: str) -> dict:
    root = Path(root_path).expanduser()
    manifest = SharedFolder(root).manifest()
    existing = get_conn().execute("SELECT library_id FROM shared_libraries WHERE library_id=?",
                                  (manifest["library_id"],)).fetchone()
    if existing:
        get_conn().execute("UPDATE shared_libraries SET root_path=? WHERE library_id=?", (str(root), existing[0]))
        return sync(existing[0])
    _register(manifest["library_id"], manifest.get("name") or root.name, root, None)
    return sync(manifest["library_id"])


def _register(library_id: str, name: str, root: Path, collection_id: Optional[int]) -> None:
    conn = get_conn()
    with tx():
        if collection_id is None:
            collection_id = conn.execute("INSERT INTO collections (name, parent_id, icon) VALUES (?, NULL, ?)",
                                         (name, SHARED_ICON)).lastrowid
        else:
            conn.execute("UPDATE collections SET icon=? WHERE id=?", (SHARED_ICON, collection_id))
        conn.execute("INSERT INTO shared_libraries (library_id, name, root_path, collection_id) VALUES (?,?,?,?)",
                     (library_id, name, str(root), collection_id))


def leave(library_id: str) -> dict:
    """Stop syncing. Local items and the collection stay (as a normal private collection)."""
    lib = _row(library_id)
    folder = SharedFolder(lib["root_path"])
    me = member()
    try:
        card = folder.root / "members" / f"{me['id']}.json"
        if card.exists():
            data = json.loads(card.read_text(encoding="utf-8"))
            data["left_at"] = _iso()
            folder.write_json(card, data)
    except (OSError, json.JSONDecodeError):
        pass
    conn = get_conn()
    with tx():
        conn.execute("DELETE FROM shared_links WHERE library_id=?", (library_id,))
        conn.execute("DELETE FROM shared_libraries WHERE library_id=?", (library_id,))
        if lib["collection_id"]:
            conn.execute("UPDATE collections SET icon=NULL WHERE id=?", (lib["collection_id"],))
    return {"ok": True}


def list_libraries() -> list[dict]:
    conn = get_conn()
    out = []
    for row in conn.execute("SELECT * FROM shared_libraries ORDER BY name COLLATE NOCASE").fetchall():
        lib = dict(row)
        lib["last_result"] = json.loads(lib["last_result"]) if lib.get("last_result") else None
        lib["available"] = (Path(lib["root_path"]) / MANIFEST).exists()
        lib["members"] = SharedFolder(lib["root_path"]).members() if lib["available"] else []
        lib["items"] = conn.execute("SELECT COUNT(*) FROM shared_links WHERE library_id=? AND kind='item'",
                                    (lib["library_id"],)).fetchone()[0]
        out.append(lib)
    return out


def discover(extra_roots: Iterable[str] = (), max_depth: int = 4, budget_s: float = 4.0) -> list[dict]:
    """Find shared library folders (manifest files) inside Drive folders that are not joined yet.

    Collaborators' folders appear in Drive for desktop once added to "My Drive" (Shared with me >
    right click > Organize > Add shortcut). Search is bounded in depth and time.
    """
    joined = {r[0] for r in get_conn().execute("SELECT library_id FROM shared_libraries")}
    roots = [Path(p) for p in extra_roots if p] + list(config.google_drive_candidates())
    found: dict[str, dict] = {}
    deadline = time.monotonic() + budget_s
    skip = {"node_modules", ".git", "$RECYCLE.BIN", "System Volume Information", "files", "records", "members"}

    def walk(folder: Path, depth: int) -> None:
        if time.monotonic() > deadline or depth > max_depth:
            return
        manifest = folder / MANIFEST
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("library_id") and data["library_id"] not in joined:
                    found[data["library_id"]] = {"library_id": data["library_id"], "name": data.get("name"),
                                                 "path": str(folder), "created_by": data.get("created_by")}
            except (OSError, json.JSONDecodeError):
                pass
            return
        try:
            subdirs = [d for d in folder.iterdir() if d.is_dir() and d.name not in skip and not d.name.startswith(".")]
        except OSError:
            return
        for d in sorted(subdirs):
            walk(d, depth + 1)

    for r in roots:
        if r.exists():
            walk(r, 0)
    return sorted(found.values(), key=lambda d: (d["name"] or "").casefold())


# ======================================================================================
# Local state -> flat records
# ======================================================================================

def _item_flat(item: dict, col_rid: dict[int, str], file_cards: dict[str, dict]) -> dict:
    flat: dict[str, Any] = {"item_type": item["item_type"]}
    for k, v in item["field_values"].items():
        if v not in (None, ""):
            flat[f"f:{k}"] = str(v)
    creators = catalog.clean_creators(item.get("creators") or [])
    if creators:
        flat["creators"] = creators
    for t in item["tags"]:
        flat[f"tag:{t['name']}"] = True
    for cid in item["collections"]:
        if cid in col_rid:
            flat[f"col:{col_rid[cid]}"] = True
    for sha, card in file_cards.items():
        flat[f"file:{sha}"] = card
    if item.get("deleted_at"):
        flat["deleted"] = True
    return flat


def _file_cards(item: dict) -> tuple[dict[str, dict], dict[str, Path]]:
    cards, paths = {}, {}
    for att in item.get("attachments") or []:
        if att["kind"] not in ("file", "snapshot") or not att.get("path"):
            continue
        sha = _attachment_sha(att)
        if not sha:
            continue
        p = Path(att["path"])
        cards[sha] = {"name": p.name, "title": att.get("title") or p.name, "mime": att.get("mime") or "",
                      "kind": att["kind"]}
        if p.exists():
            paths[sha] = p
    return cards, paths


class _Ctx:
    """Everything one sync pass needs, so the helpers stay small."""

    def __init__(self, lib: dict, folder: SharedFolder, me: dict):
        self.lib, self.folder, self.me = lib, folder, me
        self.library_id = lib["library_id"]
        self.root_cid = lib["collection_id"]
        self.links: dict[str, dict] = {}            # rid -> link row
        self.by_local: dict[tuple[str, int], str] = {}
        self.report: dict[str, Any] = {k: 0 for k in (
            "published", "applied", "created_local", "items_added", "items_updated", "items_removed",
            "notes_added", "notes_updated", "collections_added", "files_uploaded", "files_downloaded",
            "files_pending")}
        self.report["warnings"] = []
        self.blobs: dict[str, Path] = {}             # sha -> local path to upload
        self.col_rid: dict[int, str] = {}            # local collection id -> record id (filled by _local_state)
        self.rid_col: dict[str, int] = {}
        conn = get_conn()
        for r in conn.execute("SELECT record_id, kind, local_id, origin, base FROM shared_links WHERE library_id=?",
                              (self.library_id,)):
            link = {"rid": r[0], "kind": r[1], "local_id": r[2], "origin": r[3],
                    "base": json.loads(r[4]) if r[4] else {"l": {}, "r": {}}}
            self.links[r[0]] = link
            self.by_local[(r[1], r[2])] = r[0]

    def link(self, rid: str, kind: str, local_id: int, origin: str = "local") -> dict:
        link = {"rid": rid, "kind": kind, "local_id": local_id, "origin": origin, "base": {"l": {}, "r": {}}}
        self.links[rid] = link
        self.by_local[(kind, local_id)] = rid
        return link

    def save(self, link: dict) -> None:
        get_conn().execute(
            "INSERT OR REPLACE INTO shared_links (library_id, record_id, kind, local_id, origin, base) VALUES (?,?,?,?,?,?)",
            (self.library_id, link["rid"], link["kind"], link["local_id"], link["origin"],
             json.dumps(link["base"], ensure_ascii=False)))


def _local_state(ctx: _Ctx) -> dict[str, tuple[str, Optional[dict]]]:
    """rid -> (kind, flat) for everything shared locally. flat None = linked object deleted locally."""
    conn = get_conn()
    state: dict[str, tuple[str, Optional[dict]]] = {}
    # Collections: BFS from the root so a parent always has its record id before its children.
    col_rid: dict[int, str] = {ctx.root_cid: ROOT_RID}
    queue = [ctx.root_cid]
    while queue:
        parent = queue.pop(0)
        for cid, name in conn.execute("SELECT id, name FROM collections WHERE parent_id=? ORDER BY id", (parent,)):
            rid = ctx.by_local.get(("collection", cid)) or ctx.link(uuid.uuid4().hex, "collection", cid)["rid"]
            col_rid[cid] = rid
            state[rid] = ("collection", {"name": name, "parent": col_rid[parent]})
            queue.append(cid)
    ctx.col_rid = col_rid
    ctx.rid_col = {rid: cid for cid, rid in col_rid.items()}
    # Items in the shared tree + items linked before (they may have left the tree or be gone).
    marks = ",".join("?" for _ in col_rid)
    item_ids = {r[0] for r in conn.execute(
        f"SELECT DISTINCT item_id FROM item_collections WHERE collection_id IN ({marks})", list(col_rid))}
    item_ids |= {link["local_id"] for link in ctx.links.values() if link["kind"] == "item"}
    for iid in sorted(item_ids):
        item = catalog.get_item(iid)
        rid = ctx.by_local.get(("item", iid))
        if item is None:
            if rid:
                state[rid] = ("item", None)
            continue
        if not rid:
            if not any(c in col_rid for c in item["collections"]) or item.get("deleted_at"):
                continue    # never shared, not in the tree: private
            rid = ctx.link(uuid.uuid4().hex, "item", iid)["rid"]
        cards, paths = _file_cards(item)
        ctx.blobs.update(paths)
        state[rid] = ("item", _item_flat(item, col_rid, cards))
        for note in conn.execute("SELECT id, html, deleted_at FROM notes WHERE item_id=?", (iid,)).fetchall():
            nrid = ctx.by_local.get(("note", note[0]))
            if not nrid:
                if note[2]:
                    continue
                nrid = ctx.link(uuid.uuid4().hex, "note", note[0])["rid"]
            flat = {"item": rid, "html": note[1]}
            if note[2]:
                flat["deleted"] = True
            state[nrid] = ("note", flat)
    # Linked collections / notes that no longer exist locally.
    for rid, link in ctx.links.items():
        if rid not in state and link["kind"] in ("collection", "note"):
            table = "collections" if link["kind"] == "collection" else "notes"
            exists = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (link["local_id"],)).fetchone()
            if not exists or link["kind"] == "collection":   # a collection moved out of the tree counts as removed
                state[rid] = (link["kind"], None)
    return state


# ======================================================================================
# Sync
# ======================================================================================

def sync(library_id: str, *, repair: bool = False) -> dict:
    """One full pass: read + merge the folder, publish local edits, apply remote edits, move files.

    ``repair=True`` publishes nothing and re-applies the whole shared state (used when the local
    collection was deleted or links look inconsistent).
    """
    lock = _lock_for(library_id)
    if not lock.acquire(blocking=False):
        return {"ok": False, "reason": "already syncing"}
    try:
        return _sync(library_id, repair=repair)
    finally:
        lock.release()


def _sync(library_id: str, *, repair: bool) -> dict:
    started = time.perf_counter()
    lib = _row(library_id)
    folder = SharedFolder(lib["root_path"])
    folder.manifest()   # raises a clear error when the folder is unavailable (Drive not running...)
    me = member()
    conn = get_conn()
    if not lib["collection_id"] or not conn.execute("SELECT 1 FROM collections WHERE id=?",
                                                    (lib["collection_id"],)).fetchone():
        cid = conn.execute("INSERT INTO collections (name, parent_id, icon) VALUES (?, NULL, ?)",
                           (lib["name"], SHARED_ICON)).lastrowid
        conn.execute("UPDATE shared_libraries SET collection_id=? WHERE library_id=?", (cid, library_id))
        lib["collection_id"] = cid
        repair = True
    ctx = _Ctx(lib, folder, me)
    if repair:
        for link in ctx.links.values():
            link["base"] = {"l": {}, "r": {}}

    # 1. Role: an editor can write its member card. Drive "Viewer" access makes the folder read-only.
    role = "editor"
    try:
        card = {"id": me["id"], "name": me["name"], "email": me["email"], "app_version": __version__,
                "last_sync_at": _iso()}
        folder.write_json(folder.root / "members" / f"{me['id']}.json", card)
    except OSError:
        role = "viewer"

    # 2. Read + merge everyone's records.
    records = folder.read_records(ctx.report["warnings"])
    merged = merge_records(records)
    hlc = max(int(lib["hlc"] or 0), max_clock(merged))

    # 3. Local changes since the last sync -> publish (editors only).
    local = _local_state(ctx)
    if role == "editor" and not repair:
        published: dict[str, tuple[str, dict]] = {}
        for rid, (kind, flat) in local.items():
            base_l = ctx.links[rid]["base"]["l"]
            changes = {"deleted": True} if flat is None else diff(flat, base_l)
            if flat is None and base_l.get("deleted"):
                changes = {}
            if changes:
                published[rid] = (kind, changes)
        if published:
            hlc = max(hlc + 1, _now_ms())
            for sha, path in ctx.blobs.items():          # files first: a record never points to a missing blob
                try:
                    if folder.put_blob(path, sha, path.suffix.lstrip(".")):
                        ctx.report["files_uploaded"] += 1
                except OSError as e:
                    ctx.report["warnings"].append(f"upload {path.name}: {e}")
            own = [(m, r) for m, r in records if not (m == me["id"] and r.get("id") in published)]
            for rid, (kind, changes) in published.items():
                own.append((me["id"], folder.publish(me["id"], rid, kind, changes, hlc)))
                ctx.report["published"] += 1
            records = own
            merged = merge_records(records)
        else:
            # Files of already-published items may still be missing from the folder (first sync was offline).
            for sha, path in ctx.blobs.items():
                try:
                    if folder.put_blob(path, sha, path.suffix.lstrip(".")):
                        ctx.report["files_uploaded"] += 1
                except OSError:
                    pass

    # 4. Apply the merged state to the local library.
    with tx():
        _apply_collections(ctx, merged, local)
    for rid in sorted(r for r, rec in merged.items() if rec["type"] == "item"):
        try:
            with tx():
                _apply_item(ctx, rid, merged[rid], local.get(rid, ("item", None))[1])
        except Exception as e:
            ctx.report["warnings"].append(f"item {rid[:8]}: {e}")
    for rid in sorted(r for r, rec in merged.items() if rec["type"] == "note"):
        try:
            with tx():
                _apply_note(ctx, rid, merged[rid], local.get(rid, ("note", None))[1])
        except Exception as e:
            ctx.report["warnings"].append(f"note {rid[:8]}: {e}")

    # 5. New local base for every link = local state after applying (re-read only what changed).
    for rid, link in ctx.links.items():
        if link.get("_touched"):
            kind, flat = _reread(ctx, link)
        else:
            flat = (local.get(rid) or (link["kind"], None))[1]
        if flat is not None:
            link["base"]["l"] = flat
        elif rid in local:
            link["base"]["l"] = {"deleted": True}
        if rid in merged:
            remote = values_of(merged[rid])
            pending = link.pop("_pending", {})
            for k, old in pending.items():       # not applied yet (file still syncing): retry next time
                if old is None:
                    remote.pop(k, None)
                else:
                    remote[k] = old
            link["base"]["r"] = remote
        link.pop("_touched", None)
        ctx.save(link)

    report = ctx.report
    report.update(ok=True, role=role, records=len(merged), seconds=round(time.perf_counter() - started, 3),
                  finished_at=_iso(), members=len(folder.members()))
    report["warnings"] = report["warnings"][:50]
    conn.execute("UPDATE shared_libraries SET hlc=?, role=?, last_sync_at=?, last_result=? WHERE library_id=?",
                 (hlc, role, report["finished_at"], json.dumps({k: v for k, v in report.items() if k != "warnings"}
                                                               | {"warning_count": len(report["warnings"])}),
                  library_id))
    return report


def _reread(ctx: _Ctx, link: dict) -> tuple[str, Optional[dict]]:
    conn = get_conn()
    if link["kind"] == "item":
        item = catalog.get_item(link["local_id"])
        if not item:
            return "item", None
        cards, _ = _file_cards(item)
        return "item", _item_flat(item, ctx.col_rid, cards)
    if link["kind"] == "note":
        row = conn.execute("SELECT item_id, html, deleted_at FROM notes WHERE id=?", (link["local_id"],)).fetchone()
        if not row:
            return "note", None
        flat = {"item": ctx.by_local.get(("item", row[0])), "html": row[1]}
        if row[2]:
            flat["deleted"] = True
        return "note", flat
    row = conn.execute("SELECT name, parent_id FROM collections WHERE id=?", (link["local_id"],)).fetchone()
    if not row:
        return "collection", None
    return "collection", {"name": row[0], "parent": ctx.col_rid.get(row[1], ROOT_RID)}


def _remote_changes(link: Optional[dict], merged_rec: dict) -> dict:
    return diff(values_of(merged_rec), link["base"]["r"] if link else {})


def _apply_collections(ctx: _Ctx, merged: dict, local: dict) -> None:
    conn = get_conn()
    pending = sorted(rid for rid, rec in merged.items() if rec["type"] == "collection")
    for _ in range(len(pending) + 1):            # parents before children, whatever the order
        progress = False
        for rid in list(pending):
            vals = values_of(merged[rid])
            link = ctx.links.get(rid)
            parent_rid = vals.get("parent") or ROOT_RID
            parent_cid = ctx.rid_col.get(parent_rid)
            if link is None:
                if vals.get("deleted") or not vals.get("name"):
                    pending.remove(rid)
                    continue
                if parent_cid is None:
                    continue                          # parent not created yet
                cid = conn.execute("INSERT INTO collections (name, parent_id) VALUES (?, ?)",
                                   (vals["name"], parent_cid)).lastrowid
                link = ctx.link(rid, "collection", cid, origin="remote")
                link["_touched"] = True
                ctx.col_rid[cid], ctx.rid_col[rid] = rid, cid
                ctx.report["collections_added"] += 1
            else:
                changes = _remote_changes(link, merged[rid])
                exists = conn.execute("SELECT 1 FROM collections WHERE id=?", (link["local_id"],)).fetchone()
                if changes.get("deleted") and exists:
                    # Deleted by a member. A collection this user moved OUT of the shared tree is only
                    # unshared (kept as a private collection), never deleted.
                    if link["local_id"] in ctx.col_rid:
                        conn.execute("DELETE FROM collections WHERE id=?", (link["local_id"],))
                    link["_touched"] = True
                elif exists and not vals.get("deleted"):
                    if "name" in changes and vals.get("name"):
                        conn.execute("UPDATE collections SET name=? WHERE id=?", (vals["name"], link["local_id"]))
                        link["_touched"] = True
                    if "parent" in changes and parent_cid is not None:
                        try:
                            catalog.move_collection(link["local_id"], parent_cid)
                            link["_touched"] = True
                        except ValueError as e:
                            ctx.report["warnings"].append(f"collection {vals.get('name')}: {e}")
            pending.remove(rid)
            progress = True
        if not pending or not progress:
            break


def _apply_item(ctx: _Ctx, rid: str, merged_rec: dict, local_flat: Optional[dict]) -> None:
    conn = get_conn()
    vals = values_of(merged_rec)
    in_library = any(k.startswith("col:") and ctx.rid_col.get(k[4:]) for k in vals) and not vals.get("deleted")
    link = ctx.links.get(rid)
    if link is None or not conn.execute("SELECT 1 FROM items WHERE id=?", (link["local_id"],)).fetchone():
        if not in_library:
            return
        fields = {k[2:]: v for k, v in vals.items() if k.startswith("f:")}
        item = catalog.create_item({"item_type": vals.get("item_type") or "journalArticle",
                                    "title": fields.get("title") or "Untitled", "creators": vals.get("creators") or [],
                                    "field_values": fields, "doi": fields.get("DOI"), "status": "metadata_only",
                                    "source_query": f"shared:{ctx.library_id}"})
        origin = "local" if item.get("_existing") else "remote"
        if link is None:
            link = ctx.link(rid, "item", item["id"], origin=origin)
        else:
            link.update(local_id=item["id"], origin=origin, base={"l": {}, "r": {}})
            ctx.by_local[("item", item["id"])] = rid
        ctx.report["items_added"] += 1
        is_new = True
        changes = dict(vals)
        local_flat = _reread(ctx, link)[1] or {}     # only what create_item did not already set gets patched
    else:
        is_new = False
        changes = _remote_changes(link, merged_rec)
        if not changes:
            return
    iid = link["local_id"]
    local_flat = local_flat or {}
    link["_touched"] = True
    link.setdefault("_pending", {})
    patch: dict[str, Any] = {}
    field_patch = {k[2:]: (v or "") for k, v in changes.items() if k.startswith("f:") and local_flat.get(k) != v}
    if field_patch:
        patch["field_values"] = field_patch
    if "item_type" in changes and changes["item_type"] and changes["item_type"] != local_flat.get("item_type"):
        patch["item_type"] = changes["item_type"]
    if "creators" in changes and changes["creators"] != local_flat.get("creators"):
        patch["creators"] = changes["creators"] or []
    if patch:
        try:
            catalog.patch_item(iid, patch)
        except ValueError as e:
            ctx.report["warnings"].append(f"{vals.get('f:title', rid[:8])}: {e}")
            for k in field_patch:
                link["_pending"][f"f:{k}"] = link["base"]["r"].get(f"f:{k}")
    for k, v in changes.items():
        if k.startswith("tag:"):
            if v:
                catalog.add_tag(iid, k[4:])
            else:
                catalog.remove_tag(iid, k[4:])
        elif k.startswith("col:"):
            cid = ctx.rid_col.get(k[4:])
            if cid is None:
                link["_pending"][k] = link["base"]["r"].get(k)
            elif v:
                conn.execute("INSERT OR IGNORE INTO item_collections (item_id, collection_id) VALUES (?, ?)", (iid, cid))
            else:
                conn.execute("DELETE FROM item_collections WHERE item_id=? AND collection_id=?", (iid, cid))
        elif k.startswith("file:"):
            _apply_file(ctx, link, iid, k[5:], v)
    # A shared paper is ONE object: deleted by a member = moved to everyone's trash (recoverable; restoring
    # it anywhere restores it for everyone). Collection memberships are kept so a restore puts it back.
    if vals.get("deleted"):
        if "deleted" in changes:
            catalog.trash_items([iid])
            ctx.report["items_removed"] += 1
    elif not in_library:
        # Removed from every shared collection: a paper that came from the library goes to the trash,
        # one that was this user's own before stays in the personal library.
        if link["origin"] == "remote" and any(k.startswith("col:") for k in changes):
            catalog.trash_items([iid])
        ctx.report["items_removed"] += 1
    elif "deleted" in changes:
        catalog.restore_items([iid])
    if not is_new:
        ctx.report["items_updated"] += 1
    ctx.report["applied"] += 1


def _apply_file(ctx: _Ctx, link: dict, iid: int, sha: str, card: Optional[dict]) -> None:
    if not _SHA_RE.match(sha) or (card is not None and not isinstance(card, dict)):
        ctx.report["warnings"].append(f"ignored malformed file entry {sha[:16]!r}")
        return
    conn = get_conn()
    local = {(_attachment_sha(a)): a for a in catalog.list_attachments(iid) if a["kind"] in ("file", "snapshot")}
    if card is None:
        att = local.get(sha)
        if att:
            catalog.delete_attachment(att["id"], delete_file=False)   # file kept on disk, only detached
        return
    if sha in local:
        if card.get("title") and local[sha].get("title") != card["title"]:
            catalog.rename_attachment(local[sha]["id"], card["title"])
        return
    name = card.get("name") or f"{sha[:12]}.pdf"
    blob = ctx.folder.blob_path(sha, Path(name).suffix.lstrip("."))
    if not blob.exists():
        ctx.report["files_pending"] += 1                 # Drive has not delivered the file yet
        link["_pending"][f"file:{sha}"] = link["base"]["r"].get(f"file:{sha}")
        return
    data = blob.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:          # partially synced or damaged: try again later
        ctx.report["files_pending"] += 1
        link["_pending"][f"file:{sha}"] = link["base"]["r"].get(f"file:{sha}")
        return
    att = catalog.store_file_bytes(iid, data, name, title=card.get("title"), kind=card.get("kind") or "file")
    st = Path(att["path"]).stat()
    conn.execute("INSERT OR REPLACE INTO shared_attachment_hash (attachment_id, size, mtime, sha256) VALUES (?,?,?,?)",
                 (att["id"], st.st_size, st.st_mtime, sha))
    ctx.report["files_downloaded"] += 1


def _apply_note(ctx: _Ctx, rid: str, merged_rec: dict, local_flat: Optional[dict]) -> None:
    conn = get_conn()
    vals = values_of(merged_rec)
    link = ctx.links.get(rid)
    if link is None or not conn.execute("SELECT 1 FROM notes WHERE id=?", (link["local_id"],)).fetchone():
        item_link = ctx.links.get(vals.get("item") or "")
        if vals.get("deleted") or not vals.get("html") or not item_link:
            return
        if not conn.execute("SELECT 1 FROM items WHERE id=?", (item_link["local_id"],)).fetchone():
            return
        note = catalog.create_note(item_link["local_id"], vals["html"])
        if link is None:
            link = ctx.link(rid, "note", note["id"], origin="remote")
        else:
            link.update(local_id=note["id"], base={"l": {}, "r": {}})
            ctx.by_local[("note", note["id"])] = rid
        link["_touched"] = True
        ctx.report["notes_added"] += 1
        return
    changes = _remote_changes(link, merged_rec)
    if not changes:
        return
    link["_touched"] = True
    if "html" in changes and vals.get("html") and vals["html"] != (local_flat or {}).get("html"):
        catalog.update_note(link["local_id"], vals["html"])
        ctx.report["notes_updated"] += 1
    if "deleted" in changes:
        if vals.get("deleted"):
            catalog.delete_note(link["local_id"])
        else:
            catalog.restore_note(link["local_id"])


# ======================================================================================
# Background loop
# ======================================================================================

def sync_all() -> list[dict]:
    out = []
    for row in get_conn().execute("SELECT library_id, name FROM shared_libraries WHERE auto_sync=1").fetchall():
        try:
            out.append({"library_id": row[0], **sync(row[0])})
        except Exception as e:   # folder offline (Drive stopped, USB disk...): try again next tick
            out.append({"library_id": row[0], "ok": False, "error": str(e)})
            get_conn().execute("UPDATE shared_libraries SET last_result=? WHERE library_id=?",
                               (json.dumps({"ok": False, "error": str(e), "finished_at": _iso()}), row[0]))
    return out


def scheduler_loop(stop: threading.Event, on_change=None) -> None:
    stop.wait(20)
    while not stop.is_set():
        try:
            results = sync_all()
            if on_change and any(r.get("applied") or r.get("items_added") or r.get("items_removed") for r in results):
                on_change(results)
        except Exception as e:
            log.warning(f"shared library sync failed: {e}")
        interval = max(15, int(config.get_settings().get("shared_sync_interval_seconds") or 60))
        stop.wait(interval)
