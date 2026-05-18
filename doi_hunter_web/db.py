"""SQLite layer. Schema is created on first connect; migrations are additive."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    color TEXT,
    icon TEXT,
    sort_order INTEGER DEFAULT 0,
    drive_folder_id TEXT,
    drive_web_link TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT UNIQUE,
    title TEXT NOT NULL,
    authors TEXT,           -- JSON array of "Family, Given" strings
    year INTEGER,
    journal TEXT,
    publisher TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    abstract TEXT,
    subjects TEXT,          -- JSON array
    keywords TEXT,          -- JSON array
    file_path TEXT,         -- absolute path on disk
    file_size INTEGER,
    status TEXT DEFAULT 'pending',
    error_msg TEXT,
    starred INTEGER DEFAULT 0,
    notes TEXT,
    source_query TEXT,      -- the DOI/title the user originally typed
    added_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    item_type TEXT DEFAULT 'paper',  -- 'paper' | 'book'
    isbn TEXT,                       -- JSON array (books)
    extension TEXT,                  -- 'pdf' | 'epub' | 'mobi' | 'djvu' | ...
    mirrors TEXT,                    -- JSON array of LibGen-style download URLs
    drive_file_id TEXT,
    drive_folder_id TEXT,
    drive_web_link TEXT
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

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, authors, abstract, keywords, journal,
    content='items', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, authors, abstract, keywords, journal)
    VALUES (new.id, new.title, new.authors, new.abstract, new.keywords, new.journal);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, authors, abstract, keywords, journal)
    VALUES ('delete', old.id, old.title, old.authors, old.abstract, old.keywords, old.journal);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, authors, abstract, keywords, journal)
    VALUES ('delete', old.id, old.title, old.authors, old.abstract, old.keywords, old.journal);
    INSERT INTO items_fts(rowid, title, authors, abstract, keywords, journal)
    VALUES (new.id, new.title, new.authors, new.abstract, new.keywords, new.journal);
END;
"""

# SQLite connections aren't thread-safe; per-thread store keeps things simple.
_local = threading.local()


_ITEM_MIGRATIONS = [
    # Each entry: (column_name, type, default_sql_or_None)
    ("item_type", "TEXT", "'paper'"),
    ("isbn", "TEXT", None),
    ("extension", "TEXT", None),
    ("mirrors", "TEXT", None),
    ("drive_file_id", "TEXT", None),
    ("drive_folder_id", "TEXT", None),
    ("drive_web_link", "TEXT", None),
]

_COLLECTION_MIGRATIONS = [
    ("drive_folder_id", "TEXT", None),
    ("drive_web_link", "TEXT", None),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to existing libraries. Idempotent: skips columns that exist."""
    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    for name, typ, default in _ITEM_MIGRATIONS:
        if name in item_cols:
            continue
        sql = f"ALTER TABLE items ADD COLUMN {name} {typ}"
        if default is not None:
            sql += f" DEFAULT {default}"
        conn.execute(sql)

    collection_cols = {row[1] for row in conn.execute("PRAGMA table_info(collections)").fetchall()}
    for name, typ, default in _COLLECTION_MIGRATIONS:
        if name in collection_cols:
            continue
        sql = f"ALTER TABLE collections ADD COLUMN {name} {typ}"
        if default is not None:
            sql += f" DEFAULT {default}"
        conn.execute(sql)


def _connect() -> sqlite3.Connection:
    p: Path = db_path()
    conn = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = get_conn()
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
    for k in ("authors", "subjects", "keywords", "isbn", "mirrors"):
        if k in d and d[k]:
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                d[k] = []
        elif k in d:
            d[k] = []
    return d


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]
