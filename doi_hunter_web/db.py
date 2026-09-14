"""SQLite layer.

The schema is created on first connect and upgraded by *versioned* migrations
(``PRAGMA user_version``). Every migration is idempotent and additive: user data
is never dropped, only reshaped (for example v2 copies the legacy ``file_path``
column into the new ``attachments`` table but keeps the column for backward
compatibility with the download pipeline).

Schema versions
---------------
v1  legacy ReviewHunter tables: collections, items, item_collections, embeddings, items_fts
v2  Zotero-parity tables: tags, notes, attachments, annotations, related, saved searches,
    full-text index, Drive API file map; many new item columns; richer items_fts.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import db_path

# --------------------------------------------------------------------------------------
# v1 base schema (unchanged so old libraries open exactly as before)
# --------------------------------------------------------------------------------------
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    color TEXT,
    icon TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT UNIQUE,
    title TEXT NOT NULL,
    authors TEXT,           -- JSON array of "Family, Given" strings (mirror of creators)
    year INTEGER,
    journal TEXT,           -- container title (journal, book title, website, proceedings)
    publisher TEXT,         -- publisher / university / institution / repository
    volume TEXT,
    issue TEXT,
    pages TEXT,
    abstract TEXT,
    subjects TEXT,          -- JSON array
    keywords TEXT,          -- JSON array
    file_path TEXT,         -- absolute path of the primary file (mirror of first file attachment)
    file_size INTEGER,
    status TEXT DEFAULT 'pending',
    error_msg TEXT,
    starred INTEGER DEFAULT 0,
    notes TEXT,             -- legacy single plain-text note (kept; new notes live in `notes`)
    source_query TEXT,
    added_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    item_type TEXT DEFAULT 'journalArticle',
    isbn TEXT,              -- JSON array
    extension TEXT,
    mirrors TEXT            -- JSON array
);

CREATE TABLE IF NOT EXISTS item_collections (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, collection_id)
);

CREATE TABLE IF NOT EXISTS embeddings (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    vector BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_year ON items(year);
CREATE INDEX IF NOT EXISTS idx_items_added ON items(added_at);
CREATE INDEX IF NOT EXISTS idx_ic_collection ON item_collections(collection_id);
"""

# Legacy column additions that pre-date user_version tracking.
_LEGACY_COLUMNS = [
    ("item_type", "TEXT", "'journalArticle'"),
    ("isbn", "TEXT", None),
    ("extension", "TEXT", None),
    ("mirrors", "TEXT", None),
]

# --------------------------------------------------------------------------------------
# v2 additions
# --------------------------------------------------------------------------------------
V2_ITEM_COLUMNS = [
    # (name, type, default)
    ("creators", "TEXT", None),        # JSON [{creatorType, lastName, firstName} | {creatorType, name}]
    ("date", "TEXT", None),            # free-form date as entered ("2021-05-03", "May 2021")
    ("url", "TEXT", None),
    ("access_date", "TEXT", None),
    ("language", "TEXT", None),
    ("short_title", "TEXT", None),
    ("extra", "TEXT", None),
    ("issn", "TEXT", None),
    ("series", "TEXT", None),
    ("edition", "TEXT", None),
    ("place", "TEXT", None),
    ("num_pages", "TEXT", None),
    ("citation_key", "TEXT", None),
    ("pmid", "TEXT", None),
    ("arxiv_id", "TEXT", None),
    ("fields", "TEXT", None),          # JSON dict of type-specific extra fields
    ("deleted_at", "TEXT", None),      # trash timestamp (NULL = live)
    ("drive_file_id", "TEXT", None),   # Google Drive API id of the primary file
]

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT,
    type INTEGER DEFAULT 0          -- 0 manual, 1 automatic (imported keywords)
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,   -- NULL = standalone note
    html TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_item ON notes(item_id);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'file',   -- file | link | snapshot
    title TEXT,
    path TEXT,
    url TEXT,
    mime TEXT,
    size INTEGER,
    fulltext_indexed INTEGER DEFAULT 0,
    drive_file_id TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_attachments_item ON attachments(item_id);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    type TEXT NOT NULL DEFAULT 'highlight',   -- highlight | underline | note
    color TEXT DEFAULT '#ffd400',
    page INTEGER NOT NULL DEFAULT 1,
    text TEXT,
    comment TEXT,
    position TEXT,                            -- JSON {rects: [[x1,y1,x2,y2], ...]} in PDF points
    sort_index TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_annotations_attachment ON annotations(attachment_id);

CREATE TABLE IF NOT EXISTS related_items (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    related_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, related_id)
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    match TEXT NOT NULL DEFAULT 'all',        -- all | any
    conditions TEXT NOT NULL DEFAULT '[]',    -- JSON [{field, op, value}]
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS fulltext_fts USING fts5(
    content, attachment_id UNINDEXED, item_id UNINDEXED
);

CREATE TABLE IF NOT EXISTS drive_files (
    local_path TEXT PRIMARY KEY,
    drive_id TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    uploaded_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_deleted ON items(deleted_at);
CREATE INDEX IF NOT EXISTS idx_items_citekey ON items(citation_key);
"""

# items_fts is rebuilt in v2 with more searchable columns.
_ITEMS_FTS_V2 = """
DROP TRIGGER IF EXISTS items_ai;
DROP TRIGGER IF EXISTS items_ad;
DROP TRIGGER IF EXISTS items_au;
DROP TABLE IF EXISTS items_fts;

CREATE VIRTUAL TABLE items_fts USING fts5(
    title, authors, abstract, keywords, journal, doi, extra, short_title, citation_key,
    content='items', content_rowid='id'
);

CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, authors, abstract, keywords, journal, doi, extra, short_title, citation_key)
    VALUES (new.id, new.title, new.authors, new.abstract, new.keywords, new.journal, new.doi, new.extra,
            new.short_title, new.citation_key);
END;
CREATE TRIGGER items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, authors, abstract, keywords, journal, doi, extra, short_title, citation_key)
    VALUES ('delete', old.id, old.title, old.authors, old.abstract, old.keywords, old.journal, old.doi, old.extra,
            old.short_title, old.citation_key);
END;
CREATE TRIGGER items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, authors, abstract, keywords, journal, doi, extra, short_title, citation_key)
    VALUES ('delete', old.id, old.title, old.authors, old.abstract, old.keywords, old.journal, old.doi, old.extra,
            old.short_title, old.citation_key);
    INSERT INTO items_fts(rowid, title, authors, abstract, keywords, journal, doi, extra, short_title, citation_key)
    VALUES (new.id, new.title, new.authors, new.abstract, new.keywords, new.journal, new.doi, new.extra,
            new.short_title, new.citation_key);
END;

INSERT INTO items_fts(items_fts) VALUES ('rebuild');
"""

SCHEMA_VERSION = 2

# JSON-encoded columns, decoded automatically by row_to_dict.
JSON_LIST_COLUMNS = ("authors", "subjects", "keywords", "isbn", "mirrors", "creators")
JSON_DICT_COLUMNS = ("fields", "position", "conditions")

# SQLite connections aren't thread-safe; per-thread store keeps things simple.
_local = threading.local()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str, Any]]) -> None:
    """Add missing columns. Idempotent: existing columns are skipped."""
    existing = _columns(conn, table)
    for name, typ, default in columns:
        if name in existing:
            continue
        sql = f"ALTER TABLE {table} ADD COLUMN {name} {typ}"
        if default is not None:
            sql += f" DEFAULT {default}"
        conn.execute(sql)


def _authors_to_creators(authors_json: str | None) -> list[dict]:
    """Legacy "Family, Given" strings -> Zotero-style creator dicts."""
    try:
        authors = json.loads(authors_json) if authors_json else []
    except (json.JSONDecodeError, TypeError):
        authors = []
    creators = []
    for a in authors:
        a = str(a or "").strip()
        if not a:
            continue
        if "," in a:
            last, first = [p.strip() for p in a.split(",", 1)]
            creators.append({"creatorType": "author", "lastName": last, "firstName": first})
        else:
            creators.append({"creatorType": "author", "name": a})
    return creators


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """v1 -> v2. Runs inside one transaction so a crash leaves v1 intact."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        _add_columns(conn, "items", V2_ITEM_COLUMNS)
        # executescript() would silently COMMIT our transaction, so run statements one by one.
        # _SCHEMA_V2 contains no triggers, hence splitting on ';' is safe.
        for statement in _SCHEMA_V2.split(";"):
            if statement.strip():
                conn.execute(statement)
        # Legacy item types: NULL / 'paper' become Zotero's journalArticle.
        conn.execute(
            "UPDATE items SET item_type='journalArticle' WHERE item_type IS NULL OR item_type='' OR item_type='paper'"
        )
        # Structured creators from the legacy authors mirror.
        for row in conn.execute("SELECT id, authors FROM items WHERE creators IS NULL").fetchall():
            conn.execute(
                "UPDATE items SET creators=? WHERE id=?",
                (json.dumps(_authors_to_creators(row[1]), ensure_ascii=False), row[0]),
            )
        # Date string from year.
        conn.execute("UPDATE items SET date=CAST(year AS TEXT) WHERE date IS NULL AND year IS NOT NULL")
        # Primary files become attachment rows (file_path column kept as mirror).
        for row in conn.execute(
            "SELECT id, file_path, file_size, extension FROM items WHERE file_path IS NOT NULL AND file_path != ''"
        ).fetchall():
            exists = conn.execute(
                "SELECT 1 FROM attachments WHERE item_id=? AND path=?", (row[0], row[1])
            ).fetchone()
            if exists:
                continue
            ext = (row[3] or Path(row[1]).suffix.lstrip(".") or "pdf").lower()
            mime = "application/pdf" if ext == "pdf" else None
            conn.execute(
                "INSERT INTO attachments (item_id, kind, title, path, mime, size) VALUES (?, 'file', ?, ?, ?, ?)",
                (row[0], "Full Text PDF" if ext == "pdf" else f"{ext.upper()} file", row[1], mime, row[2]),
            )
        # Legacy plain-text notes become child notes.
        for row in conn.execute("SELECT id, notes FROM items WHERE notes IS NOT NULL AND TRIM(notes) != ''").fetchall():
            html = "<p>" + (row[1].replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")) + "</p>"
            conn.execute("INSERT INTO notes (item_id, html) VALUES (?, ?)", (row[0], html))
        conn.execute("UPDATE items SET notes=NULL WHERE notes IS NOT NULL")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    # FTS rebuild uses CREATE TRIGGER ... BEGIN ... END, run outside the explicit tx.
    conn.executescript(_ITEMS_FTS_V2)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def migrate(conn: sqlite3.Connection) -> None:
    """Bring any library up to SCHEMA_VERSION."""
    _add_columns(conn, "items", _LEGACY_COLUMNS)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        _migrate_to_v2(conn)
    else:
        # Fresh DBs created at v2 still need tables (IF NOT EXISTS keeps it idempotent).
        _add_columns(conn, "items", V2_ITEM_COLUMNS)
        conn.executescript(_SCHEMA_V2)


def _connect() -> sqlite3.Connection:
    p: Path = db_path()
    conn = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    # Schema creation + migration run once per process, serialized: several threads (HTTP and HTTPS
    # listeners, scheduler) may open their first connection at the same moment.
    global _migrated_path
    with _migrate_lock:
        if _migrated_path != str(p):
            fresh = not conn.execute("SELECT 1 FROM sqlite_master WHERE name='items'").fetchone()
            conn.executescript(_SCHEMA_V1)
            if fresh:
                # Brand-new library: create legacy FTS so the v2 migration path is identical for everyone.
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(title, content='items', content_rowid='id')"
                )
            migrate(conn)
            _migrated_path = str(p)
    return conn


_migrate_lock = threading.Lock()
_migrated_path: str | None = None


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def reset_connections() -> None:
    """Forget this thread's connection (used by tests and restore-from-backup)."""
    global _migrated_path
    _migrated_path = None
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _local.conn = None


@contextmanager
def tx():
    """Explicit transaction. Nested use is flattened (SQLite has no nested BEGIN)."""
    conn = get_conn()
    if conn.in_transaction:
        yield conn
        return
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_LIST_COLUMNS:
        if k in d:
            if d[k]:
                try:
                    d[k] = json.loads(d[k])
                except (json.JSONDecodeError, TypeError):
                    d[k] = []
            else:
                d[k] = []
    for k in JSON_DICT_COLUMNS:
        if k in d:
            if d[k]:
                try:
                    d[k] = json.loads(d[k])
                except (json.JSONDecodeError, TypeError):
                    d[k] = {}
            else:
                d[k] = {} if k != "conditions" else []
    return d


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]
