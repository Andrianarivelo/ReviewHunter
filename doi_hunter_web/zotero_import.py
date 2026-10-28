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
* This module holds the shared building blocks (snapshot, field mapping); the import itself is done
  by the incremental connector engine in ``zotero_sync`` (``import_library`` = one sync of every
  library), so re-running it updates instead of duplicating, and items whose DOI exists are merged.

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
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import catalog

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
    """Context manager: copy zotero.sqlite (+ its WAL) to a temp dir and open the copy read-only.

    Why a copy: Zotero keeps an EXCLUSIVE lock on its database while it runs, so a direct read (or
    SQLite's online backup API, which retries a busy lock forever) blocks. Copying the files is
    allowed, but Zotero may be writing at that moment, so the copy is accepted only when the
    source files did not change during the copy AND the copy passes ``PRAGMA quick_check``.
    """

    ATTEMPTS = 4

    def __init__(self, data_dir: Path):
        self.src = Path(data_dir) / "zotero.sqlite"
        if not self.src.exists():
            raise ValueError(f"zotero.sqlite not found in {data_dir}")
        self.tmp: Optional[str] = None
        self.conn: Optional[sqlite3.Connection] = None

    def _signature(self) -> tuple:
        """(size, mtime) of the database and its side files: equal before/after = stable copy."""
        sig = []
        for suffix in ("", "-wal", "-journal"):
            f = Path(str(self.src) + suffix)
            sig.append((f.stat().st_size, f.stat().st_mtime_ns) if f.exists() else None)
        return tuple(sig)

    def __enter__(self) -> sqlite3.Connection:
        self.tmp = tempfile.mkdtemp(prefix="rh-zotero-")
        dst = Path(self.tmp) / "zotero.sqlite"
        last_error = "unknown error"
        for attempt in range(self.ATTEMPTS):
            for leftover in Path(self.tmp).glob("zotero.sqlite*"):
                leftover.unlink()
            before = self._signature()
            shutil.copy2(self.src, dst)
            for suffix in ("-journal", "-wal"):
                side = Path(str(self.src) + suffix)
                if side.exists():
                    shutil.copy2(side, Path(str(dst) + suffix))
            if self._signature() != before:
                last_error = "Zotero wrote to its database during the copy"
            else:
                # Open read-write on the private copy so SQLite can replay the copied WAL, then check it.
                conn = sqlite3.connect(str(dst))
                ok = conn.execute("PRAGMA quick_check").fetchone()[0]
                conn.close()
                if ok == "ok":
                    self.conn = sqlite3.connect(f"file:{dst.as_posix()}?mode=ro", uri=True)
                    self.conn.row_factory = sqlite3.Row
                    return self.conn
                last_error = f"integrity check failed: {ok}"
            time.sleep(0.4 * (attempt + 1))
        self.__exit__()
        raise RuntimeError(f"could not take a consistent snapshot of zotero.sqlite ({last_error}); "
                           "try again or close Zotero")

    def __exit__(self, *exc) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None


def _has_table(z: sqlite3.Connection, name: str) -> bool:
    return bool(z.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _types_table(z: sqlite3.Connection) -> str:
    return "itemTypesCombined" if _has_table(z, "itemTypesCombined") else "itemTypes"


def _fields_table(z: sqlite3.Connection) -> str:
    return "fieldsCombined" if _has_table(z, "fieldsCombined") else "fields"



def map_item_fields(ztype: str, vals: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Zotero item type + raw field values -> (ReviewHunter item type, field_values).

    Fields we do not model are kept as "Label: value" lines in Extra, so nothing is lost. The same
    function is used for every sync, which makes the output stable: an unchanged Zotero item always
    maps to the same field_values (required by the three-way merge in zotero_sync).
    """
    item_type = ztype if ztype in catalog.ITEM_TYPES else TYPE_FALLBACK.get(ztype, "document")
    extra_lines = [vals.get("extra", "")] if vals.get("extra") else []
    if item_type != ztype:
        extra_lines.append(f"Original Zotero type: {ztype}")
    field_values: dict[str, str] = {}
    allowed = set(catalog.ITEM_TYPES[item_type]["fields"])
    for name, value in sorted(vals.items()):
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
            sibling = next((f for f in sorted(allowed) if catalog.FIELD_DEFS[f][1] == catalog.FIELD_DEFS[target][1]),
                           None)
            if not sibling:
                extra_lines.append(f"{catalog.FIELD_DEFS[target][0]}: {value}")
                continue
            target = sibling
        if target in field_values:      # two Zotero fields mapped to one: keep the first, park the other in Extra
            extra_lines.append(f"{name}: {value}")
            continue
        field_values[target] = str(value)
    extra = "\n".join(line for line in extra_lines if line)
    if extra:
        field_values["extra"] = extra
    if not field_values.get("title"):
        field_values["title"] = vals.get("title") or vals.get("caseName") or vals.get("subject") or "Untitled"
    return item_type, field_values


def import_library(data_dir: str | Path, *, copy_files: bool = True, include_trash: bool = False,
                   progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """One-shot migration of EVERY Zotero library (My Library + groups) into this library.

    Kept for the Import dialog and scripts. It runs the incremental connector engine
    (``zotero_sync.sync``) once, so a later "Sync now" continues from here without duplicates.
    """
    from . import zotero_sync

    return zotero_sync.sync(data_dir, libraries=None, copy_files=copy_files, include_trash=include_trash,
                            import_saved_searches=True, progress=progress)
