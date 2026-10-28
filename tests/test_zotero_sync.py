"""Smart Zotero connector: library-aware, incremental three-way sync against a synthetic Zotero 10 database.

The fixture reproduces the Zotero tables the connector reads (same names and columns as Zotero's
userdata.sql), with three libraries: My Library (1), a group library (2) and an RSS feed (3).
Each test then edits the Zotero database the way Zotero would (values + clientDateModified) and
checks what a re-sync does to the ReviewHunter library.
"""
import json
import sqlite3

import pytest

SCHEMA = """
CREATE TABLE libraries (libraryID INTEGER PRIMARY KEY, type TEXT NOT NULL, editable INT NOT NULL, filesEditable INT NOT NULL,
  version INT NOT NULL DEFAULT 0, storageVersion INT NOT NULL DEFAULT 0, lastSync INT NOT NULL DEFAULT 0, archived INT NOT NULL DEFAULT 0);
CREATE TABLE groups (groupID INTEGER PRIMARY KEY, libraryID INT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', version INT NOT NULL DEFAULT 0);
CREATE TABLE feeds (libraryID INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT NOT NULL, dateAdded TIMESTAMP, dateModified TIMESTAMP,
  clientDateModified TIMESTAMP, libraryID INT NOT NULL, key TEXT NOT NULL, version INT DEFAULT 0, synced INT DEFAULT 0);
CREATE TABLE itemTypesCombined (itemTypeID INT NOT NULL, typeName TEXT NOT NULL, display INT DEFAULT 1, custom INT NOT NULL);
CREATE TABLE fieldsCombined (fieldID INT NOT NULL, fieldName TEXT NOT NULL, label TEXT, fieldFormatID INT, custom INT NOT NULL);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID, PRIMARY KEY (itemID, fieldID));
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value UNIQUE);
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INT);
CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT NOT NULL, parentCollectionID INT,
  clientDateModified TIMESTAMP, libraryID INT NOT NULL, key TEXT NOT NULL);
CREATE TABLE collectionItems (collectionID INT, itemID INT, orderIndex INT DEFAULT 0);
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE itemTags (itemID INT, tagID INT, type INT NOT NULL);
CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT, title TEXT);
CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, linkMode INT, contentType TEXT, charsetID INT, path TEXT);
CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INT NOT NULL, type INTEGER NOT NULL, authorName TEXT,
  text TEXT, comment TEXT, color TEXT, pageLabel TEXT, sortIndex TEXT NOT NULL, position TEXT NOT NULL, isExternal INT NOT NULL);
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL);
CREATE TABLE itemRelations (itemID INT NOT NULL, predicateID INT NOT NULL, object TEXT NOT NULL);
CREATE TABLE relationPredicates (predicateID INTEGER PRIMARY KEY, predicate TEXT UNIQUE);
CREATE TABLE syncedSettings (setting TEXT NOT NULL, libraryID INT NOT NULL, value NOT NULL, version INT DEFAULT 0, synced INT DEFAULT 0);

INSERT INTO libraries (libraryID, type, editable, filesEditable) VALUES (1,'user',1,1),(2,'group',1,1),(3,'feed',0,0);
INSERT INTO groups (groupID, libraryID, name) VALUES (98765, 2, 'Dopamine Lab');
INSERT INTO feeds VALUES (3, 'bioRxiv neuroscience', 'https://example.org/rss');
INSERT INTO itemTypesCombined VALUES (1,'journalArticle',1,0),(2,'note',1,0),(3,'attachment',1,0),(4,'annotation',1,0),(6,'book',1,0);
INSERT INTO fieldsCombined VALUES (1,'title',NULL,NULL,0),(2,'publicationTitle',NULL,NULL,0),(3,'date',NULL,NULL,0),
  (4,'DOI',NULL,NULL,0),(5,'url',NULL,NULL,0),(9,'volume',NULL,NULL,0),(10,'abstractNote',NULL,NULL,0);
INSERT INTO creatorTypes VALUES (1,'author'),(3,'editor');
"""


class FakeZotero:
    """A Zotero data directory whose database the tests edit like Zotero would."""

    def __init__(self, root):
        self.dir = root / "Zotero"
        (self.dir / "storage").mkdir(parents=True)
        self.db = self.dir / "zotero.sqlite"
        con = self._con()
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        self.clock = 0

    def _con(self):
        return sqlite3.connect(self.db)

    def _now(self):
        self.clock += 1
        return f"2026-10-28 10:00:{self.clock:02d}"

    def _value(self, con, value):
        row = con.execute("SELECT valueID FROM itemDataValues WHERE value=?", (value,)).fetchone()
        return row[0] if row else con.execute("INSERT INTO itemDataValues (value) VALUES (?)", (value,)).lastrowid

    def add_item(self, item_id, key, lib=1, type_id=1, fields=None, creators=(), tags=(), collections=()):
        con = self._con()
        con.execute("INSERT INTO items (itemID, itemTypeID, dateAdded, clientDateModified, libraryID, key) VALUES (?,?,?,?,?,?)",
                    (item_id, type_id, "2025-01-01 00:00:00", self._now(), lib, key))
        for fid, value in (fields or {}).items():
            con.execute("INSERT INTO itemData VALUES (?,?,?)", (item_id, fid, self._value(con, value)))
        for i, (first, last) in enumerate(creators):
            cid = con.execute("INSERT INTO creators (firstName, lastName, fieldMode) VALUES (?,?,0)", (first, last)).lastrowid
            con.execute("INSERT INTO itemCreators VALUES (?,?,1,?)", (item_id, cid, i))
        for tag in tags:
            self._tag(con, item_id, tag)
        for col in collections:
            con.execute("INSERT INTO collectionItems VALUES (?,?,0)", (col, item_id))
        con.commit()
        con.close()

    def _tag(self, con, item_id, name):
        row = con.execute("SELECT tagID FROM tags WHERE name=?", (name,)).fetchone()
        tid = row[0] if row else con.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid
        con.execute("INSERT INTO itemTags VALUES (?,?,0)", (item_id, tid))

    def touch(self, con, item_id):
        con.execute("UPDATE items SET clientDateModified=? WHERE itemID=?", (self._now(), item_id))

    def set_field(self, item_id, fid, value):
        con = self._con()
        con.execute("INSERT OR REPLACE INTO itemData VALUES (?,?,?)", (item_id, fid, self._value(con, value)))
        self.touch(con, item_id)
        con.commit()
        con.close()

    def sql(self, statement, params=(), touch=None):
        con = self._con()
        con.execute(statement, params)
        if touch:
            self.touch(con, touch)
        con.commit()
        con.close()

    def add_tag(self, item_id, name):
        con = self._con()
        self._tag(con, item_id, name)
        self.touch(con, item_id)
        con.commit()
        con.close()


@pytest.fixture()
def zot(tmp_path, library, monkeypatch):
    z = FakeZotero(tmp_path)
    z.sql("INSERT INTO collections VALUES (1,'Reward',NULL,NULL,1,'COLUSER1'),(2,'Lab reading',NULL,NULL,2,'COLGRP01')")
    z.add_item(1, "USERITM1", fields={1: "A neural substrate of prediction", 2: "Science", 3: "1997", 4: "10.1126/science.275.5306.1593"},
               creators=[("Wolfram", "Schultz")], tags=["reward"], collections=[1])
    z.add_item(2, "USERITM2", fields={1: "Temporal difference models", 3: "2004"}, creators=[("Peter", "Dayan")])
    z.add_item(3, "GROUPIT1", lib=2, fields={1: "Group paper on striatum", 4: "10.1000/group.1"}, collections=[2])
    z.add_item(4, "FEEDITM1", lib=3, fields={1: "Feed item that must never be imported"})
    z.sql("INSERT INTO items (itemID, itemTypeID, dateAdded, clientDateModified, libraryID, key) VALUES (5,2,'2025','2025',1,'NOTE0001')")
    z.sql("INSERT INTO itemNotes VALUES (5, 1, '<p>first version</p>', 'first version')")
    monkeypatch.setattr("doi_hunter_web.zotero_sync.zotero_running", lambda timeout=1.5: False)
    return z


def _item(title):
    from doi_hunter_web import catalog as c

    hits = [i for i in c.query_items(limit=1000) if i["title"] == title]
    return c.get_item(hits[0]["id"]) if hits else None


def test_libraries_are_listed_separately_and_feeds_ignored(zot):
    from doi_hunter_web import zotero_import, zotero_sync

    with zotero_import._open_copy(zot.dir) as z:
        libs = zotero_sync.list_libraries(z)
    assert [(lib["key"], lib["name"], lib["items"]) for lib in libs] == [("user", "My Library", 2),
                                                                       ("group:98765", "Dopamine Lab", 1)]


def test_sync_only_selected_library_then_group_goes_to_its_own_collection(zot):
    from doi_hunter_web import catalog as c, zotero_sync

    report = zotero_sync.sync(zot.dir, libraries=["user"])
    assert report["items"] == 2 and report["notes"] == 1
    assert _item("Group paper on striatum") is None and _item("Feed item that must never be imported") is None

    report = zotero_sync.sync(zot.dir, libraries=["group:98765"])
    assert report["items"] == 1
    cols = {col["name"]: col for col in c.list_collections()}
    group_root = cols["Zotero group - Dopamine Lab"]
    assert cols["Lab reading"]["parent_id"] == group_root["id"]           # group tree nested under its root
    assert cols["Reward"]["parent_id"] is None                             # My Library stays at the root
    assert _item("Group paper on striatum")["collections"] == [cols["Lab reading"]["id"]]


def test_incremental_three_way_merge(zot):
    from doi_hunter_web import catalog as c, zotero_sync

    zotero_sync.sync(zot.dir, libraries=["user"])
    art = _item("A neural substrate of prediction")
    # Local edit in ReviewHunter (abstract) + Zotero edits (title, volume, tag) on other fields.
    c.patch_item(art["id"], {"field_values": {"abstractNote": "my local abstract"}})
    zot.set_field(1, 1, "A neural substrate of prediction and reward")
    zot.set_field(1, 9, "275")
    zot.add_tag(1, "dopamine")
    zot.sql("DELETE FROM itemTags WHERE itemID=1 AND tagID=(SELECT tagID FROM tags WHERE name='reward')", touch=1)

    report = zotero_sync.sync(zot.dir, libraries=["user"])
    assert report["items"] == 0 and report["updated"] == 1 and report["unchanged"] == 1, report
    art = c.get_item(art["id"])
    assert art["title"] == "A neural substrate of prediction and reward" and art["volume"] == "275"
    assert art["abstract"] == "my local abstract"                                   # local edit survives
    assert {t["name"] for t in art["tags"]} == {"dopamine"}                          # Zotero removal replayed
    # A tag added locally is never removed by a later sync.
    c.add_tag(art["id"], "to-read")
    zot.set_field(1, 2, "Science (New York, N.Y.)")
    zotero_sync.sync(zot.dir, libraries=["user"])
    art = c.get_item(art["id"])
    assert {t["name"] for t in art["tags"]} == {"dopamine", "to-read"} and art["journal"] == "Science (New York, N.Y.)"


def test_conflict_policy(zot):
    from doi_hunter_web import catalog as c, config, zotero_sync

    zotero_sync.sync(zot.dir, libraries=["user"])
    art = _item("Temporal difference models")
    c.patch_item(art["id"], {"field_values": {"title": "TD models (my title)"}})
    zot.set_field(2, 1, "Temporal difference models of dopamine")
    config.update_settings({"zotero_conflict_policy": "local"})
    report = zotero_sync.sync(zot.dir, libraries=["user"])
    assert report["conflicts_kept_local"] == 1 and c.get_item(art["id"])["title"] == "TD models (my title)"

    zot.set_field(2, 1, "Temporal difference models of dopamine, v2")
    config.update_settings({"zotero_conflict_policy": "zotero"})
    report = zotero_sync.sync(zot.dir, libraries=["user"])
    assert report["conflicts_zotero_won"] == 1
    assert c.get_item(art["id"])["title"] == "Temporal difference models of dopamine, v2"


def test_deletions_go_to_trash_and_come_back(zot):
    from doi_hunter_web import catalog as c, zotero_sync

    zotero_sync.sync(zot.dir, libraries=["user"])
    iid = _item("Temporal difference models")["id"]
    zot.sql("INSERT INTO deletedItems (itemID) VALUES (2)", touch=2)
    assert zotero_sync.sync(zot.dir, libraries=["user"])["trashed"] == 1
    assert c.get_item(iid)["deleted_at"]
    zot.sql("DELETE FROM deletedItems WHERE itemID=2", touch=2)
    assert zotero_sync.sync(zot.dir, libraries=["user"])["restored"] == 1
    assert not c.get_item(iid)["deleted_at"]
    # Purged from Zotero (trash emptied): local copy goes to the trash, never deleted permanently.
    zot.sql("DELETE FROM items WHERE itemID=2")
    assert zotero_sync.sync(zot.dir, libraries=["user"])["trashed"] == 1
    assert c.get_item(iid)["deleted_at"]


def test_notes_collections_and_new_items_follow_zotero(zot):
    from doi_hunter_web import catalog as c, zotero_sync

    zotero_sync.sync(zot.dir, libraries=["user"])
    zot.sql("UPDATE itemNotes SET note='<p>second version</p>' WHERE itemID=5", touch=5)
    zot.sql("UPDATE collections SET collectionName='Reward learning' WHERE collectionID=1")
    zot.add_item(6, "USERITM3", fields={1: "A brand new paper", 4: "10.1000/new.6"}, collections=[1])
    report = zotero_sync.sync(zot.dir, libraries=["user"])
    assert report["items"] == 1 and report["notes_updated"] == 1 and report["collections_renamed"] == 1
    art = _item("A neural substrate of prediction")
    assert "second version" in art["notes_list"][0]["html"]
    cols = {col["name"]: col["id"] for col in c.list_collections()}
    assert "Reward learning" in cols and _item("A brand new paper")["collections"] == [cols["Reward learning"]]


def test_unchanged_database_is_skipped_cheaply(zot):
    from doi_hunter_web import zotero_sync

    zotero_sync.sync(zot.dir, libraries=["user"])
    assert zotero_sync.sync(zot.dir, libraries=["user"], force=False) == {"ok": True, "skipped": "unchanged"}
    zot.set_field(2, 3, "2005")
    assert zotero_sync.sync(zot.dir, libraries=["user"], force=False)["updated"] == 1


def test_legacy_import_map_is_adopted_without_overwriting_local_edits(zot):
    from doi_hunter_web import catalog as c, zotero_sync
    from doi_hunter_web.db import get_conn

    # Simulate a library imported by the previous one-shot importer, then edited locally.
    item = c.create_item({"title": "A neural substrate of prediction", "journal": "Local journal name"})
    conn = get_conn()
    conn.execute("CREATE TABLE zotero_import_map (kind TEXT, zkey TEXT, local_id INTEGER, PRIMARY KEY (kind, zkey))")
    conn.execute("INSERT INTO zotero_import_map VALUES ('item', 'USERITM1', ?)", (item["id"],))
    report = zotero_sync.sync(zot.dir, libraries=["user"])
    assert report["legacy_adopted"] == 1 and report["items"] == 1          # only USERITM2 is new
    art = c.get_item(item["id"])
    assert art["journal"] == "Local journal name"                          # existing value kept
    assert art["doi"] == "10.1126/science.275.5306.1593" and art["creators"][0]["lastName"] == "Schultz"  # gaps filled


def test_prefs_and_install_detection(tmp_path):
    from doi_hunter_web import zotero_sync

    prefs = ('user_pref("extensions.zotero.dataDir", "C:\\\\Users\\\\me\\\\Zotero");\n'
             'user_pref("extensions.zotero.useDataDir", true);\n')
    assert zotero_sync.parse_prefs_data_dir(prefs) == "C:\\Users\\me\\Zotero"
    assert zotero_sync.parse_prefs_data_dir(prefs.replace("true", "false")) is None
    install = tmp_path / "Zotero"
    (install / "app").mkdir(parents=True)
    (install / "app" / "application.ini").write_text("[App]\nName=Zotero\nVersion=10.0.6\n")
    assert zotero_sync.read_install_version(install) == "10.0.6"


def test_snapshot_copes_with_wal_and_never_writes_to_zotero(zot):
    from doi_hunter_web import zotero_sync

    # Keep a writer open in WAL mode, like a running Zotero: the copy must include the WAL content.
    live = sqlite3.connect(zot.db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("UPDATE itemDataValues SET value='Temporal difference models (WAL)' WHERE value='Temporal difference models'")
    live.commit()
    before = zot.db.read_bytes()
    zotero_sync.sync(zot.dir, libraries=["user"])
    assert _item("Temporal difference models (WAL)") is not None
    assert zot.db.read_bytes() == before
    live.close()


def test_install_detection_skips_the_cache_folder(tmp_path, library, monkeypatch):
    """The %LOCALAPPDATA% Zotero cache (holds a *folder* named zotero) must not be taken for the installation."""
    from doi_hunter_web import zotero_sync

    cache = tmp_path / "LocalAppData" / "Zotero"
    (cache / "zotero").mkdir(parents=True)
    install = tmp_path / "Program Files" / "Zotero"
    (install / "app").mkdir(parents=True)
    (install / "zotero.exe").write_bytes(b"MZ")
    (install / "app" / "application.ini").write_text("[App]\nVersion=10.0.6\n")
    monkeypatch.setattr(zotero_sync, "_install_candidates", lambda: [cache, install])
    found = zotero_sync.find_install()
    assert found["dir"] == str(install) and found["version"] == "10.0.6"
