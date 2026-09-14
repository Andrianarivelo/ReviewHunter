"""Direct Zotero migration against a synthetic zotero.sqlite built with Zotero's real table layout."""
import json
import sqlite3


def _make_zotero(tmp_path, pdf_bytes: bytes):
    data = tmp_path / "Zotero"
    (data / "storage" / "ATTKEY01").mkdir(parents=True)
    (data / "storage" / "ATTKEY01" / "Schultz 1997.pdf").write_bytes(pdf_bytes)
    z = sqlite3.connect(data / "zotero.sqlite")
    z.executescript("""
    CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT NOT NULL, dateAdded TIMESTAMP, dateModified TIMESTAMP,
      clientDateModified TIMESTAMP, libraryID INT NOT NULL, key TEXT NOT NULL, version INT DEFAULT 0, clientVersion INT DEFAULT 0, synced INT DEFAULT 0);
    CREATE TABLE itemTypesCombined (itemTypeID INT NOT NULL, typeName TEXT NOT NULL, display INT DEFAULT 1, custom INT NOT NULL);
    CREATE TABLE fieldsCombined (fieldID INT NOT NULL, fieldName TEXT NOT NULL, label TEXT, fieldFormatID INT, custom INT NOT NULL);
    CREATE TABLE itemData (itemID INT, fieldID INT, valueID);
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
    CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, linkMode INT, contentType TEXT,
      charsetID INT, path TEXT, syncState INT DEFAULT 0);
    CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INT NOT NULL, type INTEGER NOT NULL, authorName TEXT,
      text TEXT, comment TEXT, color TEXT, pageLabel TEXT, sortIndex TEXT NOT NULL, position TEXT NOT NULL, isExternal INT NOT NULL);
    CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL);
    CREATE TABLE itemRelations (itemID INT NOT NULL, predicateID INT NOT NULL, object TEXT NOT NULL);
    CREATE TABLE relationPredicates (predicateID INTEGER PRIMARY KEY, predicate TEXT UNIQUE);
    CREATE TABLE syncedSettings (setting TEXT NOT NULL, libraryID INT NOT NULL, value NOT NULL, version INT DEFAULT 0, synced INT DEFAULT 0);
    CREATE TABLE savedSearches (savedSearchID INTEGER PRIMARY KEY, savedSearchName TEXT NOT NULL);
    CREATE TABLE savedSearchConditions (savedSearchID INT, searchConditionID INT, condition TEXT, operator TEXT, value TEXT, required NONE);

    INSERT INTO itemTypesCombined VALUES (1,'journalArticle',1,0),(2,'note',1,0),(3,'attachment',1,0),(4,'annotation',1,0),(5,'film',1,0),(6,'book',1,0);
    INSERT INTO fieldsCombined VALUES (1,'title',NULL,NULL,0),(2,'publicationTitle',NULL,NULL,0),(3,'date',NULL,NULL,0),
      (4,'DOI',NULL,NULL,0),(5,'url',NULL,NULL,0),(6,'runningTime',NULL,NULL,0),(7,'distributor',NULL,NULL,0),(8,'libraryCatalog',NULL,NULL,0),(9,'volume',NULL,NULL,0);
    INSERT INTO creatorTypes VALUES (1,'author'),(2,'director'),(3,'editor');
    INSERT INTO items (itemID, itemTypeID, dateAdded, libraryID, key) VALUES
      (1,1,'2020-01-02 10:00:00',1,'ITEMKEY1'),(2,5,'2021-01-01 00:00:00',1,'ITEMKEY2'),(3,2,'2021-01-01',1,'NOTEKEY1'),
      (4,3,'2021-01-01',1,'ATTKEY01'),(5,4,'2021-01-01',1,'ANNKEY01'),(6,3,'2021-01-01',1,'LINKKEY1'),(7,6,'2019-05-05',1,'TRASHED1'),
      (8,2,'2021-02-02',1,'NOTEKEY2');
    INSERT INTO itemDataValues VALUES (1,'A Neural Substrate of Prediction and Reward'),(2,'Science'),(3,'1997-03-14'),
      (4,'10.1126/science.275.5306.1593'),(5,'Brain Movie'),(6,'95 min'),(7,'NeuroFilms'),(8,'PubMed'),(9,'https://example.org/a'),
      (10,'Deleted book'),(11,'275'),(12,'Publisher page');
    INSERT INTO itemData VALUES (1,1,1),(1,2,2),(1,3,3),(1,4,4),(1,8,8),(1,9,11),(2,1,5),(2,6,6),(2,7,7),(6,5,9),(6,1,12),(7,1,10);
    INSERT INTO creators VALUES (1,'Wolfram','Schultz',0),(2,'Peter','Dayan',0),(3,NULL,'Cajal Institute',1);
    INSERT INTO itemCreators VALUES (1,1,1,0),(1,2,1,1),(2,3,2,0);
    INSERT INTO collections VALUES (1,'Neuroscience',NULL,NULL,1,'COLKEY01'),(2,'Dopamine',1,NULL,1,'COLKEY02');
    INSERT INTO collectionItems VALUES (2,1,0),(1,2,0);
    INSERT INTO tags VALUES (1,'reward'),(2,'Dopamine');
    INSERT INTO itemTags VALUES (1,1,0),(1,2,1);
    INSERT INTO itemNotes VALUES (3,1,'<div data-schema-version="8"><p>Classic <strong>TD</strong> paper</p></div>','Classic TD paper'),
      (8,NULL,'<p>Standalone idea</p>','Standalone idea');
    INSERT INTO itemAttachments VALUES (4,1,1,'application/pdf',NULL,'storage:Schultz 1997.pdf',0),(6,1,3,'text/html',NULL,NULL,0);
    INSERT INTO itemAnnotations VALUES (5,4,1,NULL,'prediction error','key sentence','#ff6666','1','00000|000120|00072',
      '{"pageIndex":0,"rects":[[72,640,300,652]]}',0);
    INSERT INTO deletedItems (itemID) VALUES (7);
    INSERT INTO relationPredicates VALUES (1,'dc:relation');
    INSERT INTO itemRelations VALUES (1,1,'http://zotero.org/users/local/abcd/items/ITEMKEY2'),(2,1,'http://zotero.org/users/local/abcd/items/ITEMKEY1');
    """)
    z.execute("INSERT INTO syncedSettings (setting, libraryID, value) VALUES ('tagColors', 1, ?)",
              (json.dumps([{"name": "reward", "color": "#5FB236", "position": 0}]),))
    z.execute("INSERT INTO savedSearches VALUES (1, 'Dopamine papers')")
    z.executemany("INSERT INTO savedSearchConditions VALUES (1, ?, ?, ?, ?, NULL)",
                  [(0, "joinMode", "any", "any"), (1, "tag", "is", "Dopamine"), (2, "title", "contains", "Reward")])
    z.commit()
    z.close()
    return data


def test_zotero_full_migration(library, tmp_path):
    from doi_hunter_web import catalog as c, zotero_import
    from doi_hunter_web.db import get_conn

    data = _make_zotero(tmp_path, b"%PDF-1.4 schultz")
    summary = zotero_import.inspect(data)
    assert summary["items"] == 3 and summary["attachments"] == 2 and summary["annotations"] == 1

    report = zotero_import.import_library(data)
    assert report["items"] == 2 and report["skipped_trash"] == 1, report
    assert report["notes"] == 2 and report["files"] == 1 and report["links"] == 1 and report["annotations"] == 1
    assert report["relations"] == 2 and report["tag_colors"] == 1 and report["saved_searches"] == 1

    art = c.query_items(q="Neural Substrate", mode="fields")[0]
    full = c.get_item(art["id"])
    assert full["doi"] == "10.1126/science.275.5306.1593" and full["journal"] == "Science" and full["volume"] == "275"
    assert [cr["lastName"] for cr in full["creators"]] == ["Schultz", "Dayan"]
    assert full["added_at"].startswith("2020-01-02")
    assert "Library Catalog: PubMed" in full["extra"]
    assert {t["name"]: (t["type"], t["color"]) for t in full["tags"]} == {"reward": (0, "#5FB236"), "Dopamine": (1, None)}
    assert full["notes_list"][0]["title"] == "Classic TD paper"
    pdf = next(a for a in full["attachments"] if a["mime"] == "application/pdf")
    assert pdf["exists"] and open(pdf["path"], "rb").read() == b"%PDF-1.4 schultz"
    assert any(a["kind"] == "link" and a["url"] == "https://example.org/a" for a in full["attachments"])
    ann = c.list_annotations(pdf["id"])[0]
    assert ann["page"] == 1 and ann["position"]["rects"] == [[72, 640, 300, 652]] and ann["color"] == "#ff6666"

    film = next(i for i in c.query_items() if i["title"] == "Brain Movie")
    film = c.get_item(film["id"])
    assert film["item_type"] == "videoRecording" and film["creators"][0] == {"creatorType": "director", "name": "Cajal Institute"}
    assert "Original Zotero type: film" in film["extra"] and film["publisher"] == "NeuroFilms"
    assert [r["id"] for r in film["related"]] == [art["id"]]

    cols = {col["name"]: col for col in c.list_collections()}
    assert cols["Dopamine"]["parent_id"] == cols["Neuroscience"]["id"]
    assert [i["id"] for i in c.query_items(collection_id=cols["Neuroscience"]["id"])] == sorted([film["id"], art["id"]], reverse=True) or \
        {i["id"] for i in c.query_items(collection_id=cols["Neuroscience"]["id"])} == {film["id"], art["id"]}
    assert len(c.list_notes(standalone=True)) == 1

    saved = c.list_saved_searches()[0]
    assert saved["match"] == "any" and len(c.query_items(saved_search_id=saved["id"])) == 1

    # Idempotent: a second run adds nothing.
    again = zotero_import.import_library(data)
    assert again["items"] == 0 and again.get("notes", 0) == 0 and again.get("files", 0) == 0
    assert get_conn().execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
