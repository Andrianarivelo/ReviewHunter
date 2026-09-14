"""Catalog v2: items, creators, tags, notes, attachments, trash, search, duplicates."""
import sqlite3

import pytest


def test_create_patch_and_citation_key(library):
    from doi_hunter_web import catalog as c

    it = c.create_item({"title": "Deep learning of neural manifolds", "creators": ["Smith, John", "Doe, Jane"],
                        "date": "2021-03-02", "doi": "https://doi.org/10.1234/ABC", "tags": ["manifold"]})
    assert it["doi"] == "10.1234/abc"
    assert it["year"] == 2021
    assert it["citation_key"] == "smith2021deep"
    assert [t["name"] for t in it["tags"]] == ["manifold"]
    assert it["authors"] == ["Smith, John", "Doe, Jane"]

    # Same DOI -> existing item updated, not duplicated.
    again = c.create_item({"title": "x", "doi": "10.1234/abc", "journal": "Neuron"})
    assert again["id"] == it["id"] and again["_existing"] and again["journal"] == "Neuron"

    # Second item from same author/year/word gets a suffixed key.
    other = c.create_item({"title": "Deep nets", "creators": ["Smith, Ann"], "year": 2021})
    assert other["citation_key"] == "smith2021deepa"

    patched = c.patch_item(it["id"], {"item_type": "conferencePaper",
                                      "field_values": {"conferenceName": "NeurIPS", "proceedingsTitle": "Proc",
                                                       "date": "May 2019"}})
    assert patched["item_type"] == "conferencePaper"
    assert patched["fields"]["conferenceName"] == "NeurIPS"
    assert patched["journal"] == "Proc" and patched["year"] == 2019
    assert patched["field_values"]["conferenceName"] == "NeurIPS"


def test_doi_conflict_on_patch_is_reported(library):
    from doi_hunter_web import catalog as c

    a = c.create_item({"title": "A", "doi": "10.1/a"})
    b = c.create_item({"title": "B", "doi": "10.1/b"})
    with pytest.raises(ValueError):
        c.patch_item(b["id"], {"field_values": {"DOI": "10.1/A"}})
    assert c.get_item(a["id"])["doi"] == "10.1/a"


def test_tags_notes_related(library):
    from doi_hunter_web import catalog as c

    a = c.create_item({"title": "Alpha"})
    b = c.create_item({"title": "Beta"})
    c.bulk_tags([a["id"], b["id"]], add=["Review", "rl"], remove=[])
    c.remove_tag(b["id"], "RL")  # case-insensitive
    tags = {t["name"]: t["count"] for t in c.list_tags()}
    assert tags == {"Review": 2, "rl": 1}
    tid = next(t["id"] for t in c.list_tags() if t["name"] == "rl")
    c.update_tag(tid, name="review")  # rename onto existing -> merge
    assert {t["name"]: t["count"] for t in c.list_tags()} == {"Review": 2}

    note = c.create_note(a["id"], '<p onclick="x()">Key <script>bad()</script><a href="javascript:x">l</a></p>')
    assert "script" not in note["html"] and "onclick" not in note["html"] and "javascript" not in note["html"]
    assert c.get_item(a["id"])["note_count"] == 1
    c.relate(a["id"], b["id"])
    assert [r["id"] for r in c.get_item(b["id"])["related"]] == [a["id"]]


def test_trash_restore_and_permanent_delete_protects_foreign_files(library, tmp_path):
    from doi_hunter_web import catalog as c

    foreign = tmp_path / "user_file.pdf"
    foreign.write_bytes(b"%PDF-1.4 user data")
    it = c.create_item({"title": "T"})
    c.register_file(it["id"], foreign)
    managed = c.store_file_bytes(it["id"], b"%PDF-1.4 managed", "x.pdf")
    c.trash_items([it["id"]])
    assert c.query_items() == [] and len(c.query_items(trash=True)) == 1
    c.restore_items([it["id"]])
    assert len(c.query_items()) == 1
    c.trash_items([it["id"]])
    c.empty_trash()
    assert foreign.exists(), "files outside the managed papers folder must never be deleted"
    from pathlib import Path
    assert not Path(managed["path"]).exists()


def test_query_modes_collections_and_saved_search(library):
    from doi_hunter_web import catalog as c
    from doi_hunter_web.db import get_conn

    a = c.create_item({"title": "Hippocampal replay", "creators": ["Buzsaki, G"], "year": 2015,
                       "abstract": "sharp wave ripples", "tags": ["memory"]})
    b = c.create_item({"title": "Striatal learning", "creators": ["Graybiel, Ann"], "year": 2008})
    c.create_note(b["id"], "<p>ripples mentioned in my note</p>")
    conn = get_conn()
    parent = conn.execute("INSERT INTO collections(name) VALUES ('Parent')").lastrowid
    child = conn.execute("INSERT INTO collections(name, parent_id) VALUES ('Child', ?)", (parent,)).lastrowid
    conn.execute("INSERT INTO item_collections VALUES (?, ?)", (a["id"], child))

    ids = lambda rows: sorted(r["id"] for r in rows)  # noqa: E731
    assert ids(c.query_items(q="replay", mode="titleCreatorYear")) == [a["id"]]
    assert ids(c.query_items(q="2008", mode="titleCreatorYear")) == [b["id"]]
    assert ids(c.query_items(q="ripples", mode="fields")) == sorted([a["id"], b["id"]])  # abstract + note
    assert ids(c.query_items(collection_id=parent)) == [a["id"]]
    assert ids(c.query_items(collection_id=parent, include_subcollections=False)) == []
    assert ids(c.query_items(unfiled=True)) == [b["id"]]
    s = c.save_search("mem", [{"field": "tag", "op": "is", "value": "MEMORY"},
                              {"field": "year", "op": "isLessThan", "value": "2010"}], match="any")
    assert ids(c.query_items(saved_search_id=s["id"])) == sorted([a["id"], b["id"]])
    with pytest.raises(ValueError):
        c.move_collection(parent, child)
    # Malformed FTS input must not crash.
    assert isinstance(c.query_items(q='"unbalanced AND (', mode="everything"), list)


def test_duplicates_detect_and_merge(library):
    from doi_hunter_web import catalog as c, duplicates

    a = c.create_item({"title": "Reward prediction error signals in dopamine neurons", "creators": ["Schultz, W"],
                       "year": 1997, "tags": ["da"]})
    b = c.create_item({"title": "Reward Prediction-Error signals in dopamine neurons.", "creators": ["Schultz, Wolfram"],
                       "year": 1998, "doi": "10.1126/science.275.5306.1593", "journal": "Science"})
    c.create_item({"title": "Reward prediction error signals in dopamine neurons", "creators": ["Other, X"],
                   "year": 1997})
    groups = duplicates.find_duplicates()
    assert len(groups) == 1 and sorted(i["id"] for i in groups[0]["items"]) == sorted([a["id"], b["id"]])
    c.create_note(b["id"], "<p>n</p>")
    res = duplicates.merge(a["id"], [b["id"]])
    m = res["master"]
    assert m["doi"] == "10.1126/science.275.5306.1593" and m["journal"] == "Science" and m["note_count"] == 1
    assert c.get_item(b["id"])["deleted_at"] is not None
    assert duplicates.find_duplicates() == []


def test_v1_library_migrates(tmp_path, monkeypatch):
    """A legacy v1 database (old ReviewHunter) is upgraded without data loss."""
    lib = tmp_path / "old"
    lib.mkdir()
    conn = sqlite3.connect(lib / "library.db")
    conn.executescript("""
        CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, doi TEXT UNIQUE, title TEXT NOT NULL, authors TEXT,
          year INTEGER, journal TEXT, publisher TEXT, volume TEXT, issue TEXT, pages TEXT, abstract TEXT, subjects TEXT,
          keywords TEXT, file_path TEXT, file_size INTEGER, status TEXT DEFAULT 'pending', error_msg TEXT,
          starred INTEGER DEFAULT 0, notes TEXT, source_query TEXT, added_at TEXT DEFAULT (datetime('now')),
          updated_at TEXT DEFAULT (datetime('now')));
        CREATE VIRTUAL TABLE items_fts USING fts5(title, authors, abstract, keywords, journal, content='items', content_rowid='id');
        INSERT INTO items (title, authors, year, file_path, notes, doi) VALUES
          ('Old paper', '["Cajal, Santiago"]', 1899, 'C:/x/old.pdf', 'my note', '10.1/old');
    """)
    conn.commit()
    conn.close()
    monkeypatch.setenv("REVIEWHUNTER_HOME", str(lib))
    from doi_hunter_web import catalog as c, db

    db.reset_connections()
    it = c.get_item(1)
    assert it["item_type"] == "journalArticle"
    assert it["creators"] == [{"creatorType": "author", "lastName": "Cajal", "firstName": "Santiago"}]
    assert it["attachments"][0]["path"] == "C:/x/old.pdf"
    assert it["notes_list"][0]["html"] == "<p>my note</p>"
    assert c.query_items(q="Old", mode="fields")[0]["id"] == 1
    assert db.get_conn().execute("PRAGMA user_version").fetchone()[0] == 2
    db.reset_connections()
