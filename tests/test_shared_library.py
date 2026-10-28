"""Collaborative shared libraries over a synced folder (Google Drive, OneDrive, network share...).

Two members, Alice and Bob, each have their own ReviewHunter library (separate REVIEWHUNTER_HOME, hence
separate databases and member ids) and share one folder, exactly as two computers sharing a Drive folder.
"""
import itertools
import json
import random

import pytest


@pytest.fixture()
def team(tmp_path, monkeypatch):
    """Switch between members with team.as_('alice') / team.as_('bob'); team.drive is the shared parent."""
    from doi_hunter_web import config, db

    monkeypatch.delenv("REVIEWHUNTER_PAPERS_DIR", raising=False)

    class Team:
        drive = tmp_path / "GoogleDrive"

        def as_(self, name):
            monkeypatch.setenv("REVIEWHUNTER_HOME", str(tmp_path / name))
            db.reset_connections()
            if not config.get_settings().get("member_name"):
                config.update_settings({"member_name": name.title()})
            return self

    Team.drive.mkdir()
    t = Team()
    yield t
    db.reset_connections()


def _by_title(title):
    from doi_hunter_web import catalog as c

    hit = [i for i in c.query_items(limit=1000, trash=False) if i["title"] == title]
    return c.get_item(hit[0]["id"]) if hit else None


def _setup_library(team, pdf_bytes=b"%PDF-1.4 shared paper"):
    """Alice shares a collection with one paper (tags, note, PDF) and one sub-collection."""
    from doi_hunter_web import catalog as c, shared_library as sl
    from doi_hunter_web.db import get_conn

    team.as_("alice")
    conn = get_conn()
    cid = conn.execute("INSERT INTO collections (name) VALUES ('Dopamine Lab')").lastrowid
    sub = conn.execute("INSERT INTO collections (name, parent_id) VALUES ('Methods', ?)", (cid,)).lastrowid
    item = c.create_item({"title": "Reward prediction error", "doi": "10.1000/rpe.1",
                          "creators": [{"creatorType": "author", "firstName": "Wolfram", "lastName": "Schultz"}],
                          "tags": ["dopamine", "reward"], "collections": [cid, sub]})
    c.create_note(item["id"], "<p>Key paper for the review</p>")
    c.store_file_bytes(item["id"], pdf_bytes, "paper.pdf")
    report = sl.create("Dopamine Lab", str(team.drive), collection_id=cid)
    assert report["ok"] and report["role"] == "editor" and report["published"] >= 3 and report["files_uploaded"] == 1
    lib_id = sl.list_libraries()[0]["library_id"]
    return lib_id, item["id"]


def test_merge_is_order_independent_idempotent_and_per_field():
    from doi_hunter_web.shared_library import merge_records, values_of

    recs = [("a", {"id": "r1", "type": "item", "fields": {"f:title": ["A title", 5], "tag:x": [True, 5]}}),
            ("b", {"id": "r1", "type": "item", "fields": {"f:title": ["B title", 7], "f:abstractNote": ["B abs", 3]}}),
            ("c", {"id": "r1", "type": "item", "fields": {"tag:x": [None, 6], "f:title": ["C title", 7]}})]
    reference = merge_records(recs)
    for perm in itertools.permutations(recs):
        assert merge_records(list(perm)) == reference
    assert merge_records(recs + recs) == reference                       # duplicates (Drive conflict copies)
    # Per field: newest title (tie on ts=7 broken by member id "c" > "b"), b's abstract kept, x removed.
    assert values_of(reference["r1"]) == {"f:title": "C title", "f:abstractNote": "B abs"}


def test_merge_converges_for_random_histories():
    """Property test: any delivery order of any member files gives the same library."""
    from doi_hunter_web.shared_library import merge_records

    rng = random.Random(7)
    for _ in range(200):
        recs = [(m, {"id": f"r{rng.randint(1, 3)}", "type": "item",
                     "fields": {f"f:{rng.choice('abc')}": [rng.choice(["x", "y", None]), rng.randint(1, 9)]
                                for _ in range(3)}})
                for m in rng.sample("abcd", 3)]
        expected = merge_records(recs)
        shuffled = recs[:]
        rng.shuffle(shuffled)
        assert merge_records(shuffled) == expected


def test_create_and_join_brings_everything(team):
    from doi_hunter_web import catalog as c, shared_library as sl

    _setup_library(team)
    team.as_("bob")
    found = sl.discover([str(team.drive)])
    assert [f["name"] for f in found] == ["Dopamine Lab"]
    report = sl.join(found[0]["path"])
    assert report["items_added"] == 1 and report["notes_added"] == 1 and report["files_downloaded"] == 1, report
    paper = _by_title("Reward prediction error")
    assert paper["doi"] == "10.1000/rpe.1" and paper["creators"][0]["lastName"] == "Schultz"
    assert {t["name"] for t in paper["tags"]} == {"dopamine", "reward"}
    assert "Key paper" in paper["notes_list"][0]["html"]
    pdf = paper["attachments"][0]
    assert pdf["exists"] and open(pdf["path"], "rb").read() == b"%PDF-1.4 shared paper"
    cols = {col["name"]: col for col in c.list_collections()}
    assert cols["Dopamine Lab"]["icon"] == "shared" and cols["Methods"]["parent_id"] == cols["Dopamine Lab"]["id"]
    assert set(paper["collections"]) == {cols["Dopamine Lab"]["id"], cols["Methods"]["id"]}
    lib = sl.list_libraries()[0]
    assert {m["name"] for m in lib["members"]} == {"Alice", "Bob"}


def test_concurrent_edits_to_different_fields_both_survive_and_sync_is_stable(team):
    from doi_hunter_web import catalog as c, shared_library as sl

    lib_id, _ = _setup_library(team)
    team.as_("bob")
    sl.join(str(team.drive / "Dopamine Lab"))
    # Both edit before seeing each other's change.
    c.patch_item(_by_title("Reward prediction error")["id"], {"field_values": {"abstractNote": "Bob's abstract"}})
    c.add_tag(_by_title("Reward prediction error")["id"], "bob-tag")
    team.as_("alice")
    alice_item = _by_title("Reward prediction error")["id"]
    c.patch_item(alice_item, {"field_values": {"volume": "275"}})
    c.remove_tag(alice_item, "reward")
    for who in ("alice", "bob", "alice", "bob"):
        team.as_(who)
        sl.sync(lib_id)
    for who in ("alice", "bob"):
        team.as_(who)
        paper = _by_title("Reward prediction error")
        assert paper["abstract"] == "Bob's abstract" and paper["volume"] == "275", who
        assert {t["name"] for t in paper["tags"]} == {"dopamine", "bob-tag"}, who
        # Converged: another pass publishes and applies nothing (no ping-pong between members).
        again = sl.sync(lib_id)
        assert again["published"] == 0 and again["applied"] == 0, (who, again)


def test_same_field_conflict_converges_to_the_later_edit(team):
    from doi_hunter_web import catalog as c, shared_library as sl

    lib_id, item_id = _setup_library(team)
    team.as_("bob")
    sl.join(str(team.drive / "Dopamine Lab"))
    team.as_("alice")
    c.patch_item(item_id, {"field_values": {"title": "Alice's title"}})
    sl.sync(lib_id)
    team.as_("bob")
    bob_item = _by_title("Reward prediction error")["id"]
    c.patch_item(bob_item, {"field_values": {"title": "Bob's title"}})   # edited without having seen Alice's
    sl.sync(lib_id)                                                       # Bob publishes later: newer clock
    team.as_("alice")
    sl.sync(lib_id)
    assert c.get_item(item_id)["title"] == "Bob's title"
    team.as_("bob")
    assert c.get_item(bob_item)["title"] == "Bob's title"


def test_new_papers_deletion_and_restore_propagate(team):
    from doi_hunter_web import catalog as c, shared_library as sl

    lib_id, item_id = _setup_library(team)
    team.as_("bob")
    sl.join(str(team.drive / "Dopamine Lab"))
    root = next(col for col in c.list_collections() if col["name"] == "Dopamine Lab")
    c.create_item({"title": "Bob adds a paper", "collections": [root["id"]]})
    bob_copy = _by_title("Reward prediction error")["id"]
    c.trash_items([bob_copy])
    sl.sync(lib_id)
    team.as_("alice")
    report = sl.sync(lib_id)
    assert report["items_added"] == 1 and report["items_removed"] == 1
    assert _by_title("Bob adds a paper") is not None and c.get_item(item_id)["deleted_at"]
    c.restore_items([item_id])                                            # Alice restores it for everyone
    sl.sync(lib_id)
    team.as_("bob")
    sl.sync(lib_id)
    restored = c.get_item(bob_copy)
    assert not restored["deleted_at"] and root["id"] in restored["collections"]


def test_viewer_receives_but_cannot_publish(team, monkeypatch):
    from doi_hunter_web import catalog as c, shared_library as sl

    lib_id, _ = _setup_library(team)
    team.as_("bob")
    sl.join(str(team.drive / "Dopamine Lab"))

    def read_only(path, data):
        raise PermissionError("Drive: you only have Viewer access")

    monkeypatch.setattr(sl.SharedFolder, "write_json", staticmethod(read_only))
    c.patch_item(_by_title("Reward prediction error")["id"], {"field_values": {"title": "Viewer edit"}})
    report = sl.sync(lib_id)
    assert report["role"] == "viewer" and report["published"] == 0


def test_half_synced_and_conflict_copy_files_are_tolerated(team):
    from doi_hunter_web import shared_library as sl

    lib_id, _ = _setup_library(team)
    alice_dir = next((team.drive / "Dopamine Lab" / "records").iterdir())
    some = next(alice_dir.glob("*.json"))
    (alice_dir / f"{some.stem} (1).json").write_text(some.read_text(encoding="utf-8"), encoding="utf-8")  # Drive copy
    (alice_dir / "deadbeef.json").write_text('{"id": "x", "fields": {"f:tit', encoding="utf-8")          # half written
    team.as_("bob")
    report = sl.join(str(team.drive / "Dopamine Lab"))
    assert report["ok"] and report["items_added"] == 1
    assert any("unreadable record" in w for w in report["warnings"])


def test_file_not_yet_delivered_by_drive_is_retried(team):
    from doi_hunter_web import shared_library as sl

    lib_id, _ = _setup_library(team)
    blob = next((team.drive / "Dopamine Lab" / "files").rglob("*.pdf"))
    data = blob.read_bytes()
    blob.unlink()                                                         # Drive has not synced the PDF yet
    team.as_("bob")
    first = sl.join(str(team.drive / "Dopamine Lab"))
    assert first["files_pending"] == 1 and not _by_title("Reward prediction error")["attachments"]
    blob.write_bytes(data)
    second = sl.sync(lib_id)
    assert second["files_downloaded"] == 1 and _by_title("Reward prediction error")["attachments"][0]["exists"]


def test_collection_moved_out_is_unshared_not_deleted(team):
    from doi_hunter_web import catalog as c, shared_library as sl
    from doi_hunter_web.db import get_conn

    lib_id, _ = _setup_library(team)
    methods = next(col for col in c.list_collections() if col["name"] == "Methods")
    c.move_collection(methods["id"], None)                               # Alice takes it out of the shared tree
    sl.sync(lib_id)
    assert get_conn().execute("SELECT 1 FROM collections WHERE id=?", (methods["id"],)).fetchone()
    team.as_("bob")
    sl.join(str(team.drive / "Dopamine Lab"))
    assert "Methods" not in {col["name"] for col in c.list_collections()}


def test_leave_keeps_local_papers(team):
    from doi_hunter_web import catalog as c, shared_library as sl

    lib_id, item_id = _setup_library(team)
    sl.leave(lib_id)
    assert sl.list_libraries() == [] and c.get_item(item_id) is not None
    card = json.loads(next((team.drive / "Dopamine Lab" / "members").glob("*.json")).read_text(encoding="utf-8"))
    assert card.get("left_at")


def test_crafted_records_from_a_member_cannot_escape_the_folder(team, tmp_path):
    """Records are written by other people: a path-like file key must never be read or written."""
    from doi_hunter_web import shared_library as sl

    lib_id, _ = _setup_library(team)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF private")
    from doi_hunter_web import catalog as c

    rec = next(f for f in (team.drive / "Dopamine Lab" / "records").rglob("*.json")
               if json.loads(f.read_text(encoding="utf-8"))["type"] == "item")
    data = json.loads(rec.read_text(encoding="utf-8"))
    data["fields"]["file:../../../secret"] = [{"name": "x.pdf"}, 9999999999999]
    rec.write_text(json.dumps(data), encoding="utf-8")
    team.as_("bob")
    report = sl.join(str(team.drive / "Dopamine Lab"))
    assert report["ok"] and any("malformed file entry" in w for w in report["warnings"])
    paper = _by_title("Reward prediction error")
    assert len(paper["attachments"]) == 1                                  # only the genuine PDF
    assert all(b"private" not in open(a["path"], "rb").read() for a in c.list_attachments(paper["id"]))
