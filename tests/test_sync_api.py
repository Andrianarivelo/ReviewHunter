"""HTTP boundary of the Zotero connector and shared libraries, plus the Zotero-group -> shared-library bridge."""
import time

import pytest

from test_zotero_sync import FakeZotero

APP = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture()
def client(library, monkeypatch):
    from fastapi.testclient import TestClient

    from doi_hunter_web import zotero_sync
    from doi_hunter_web.server import app

    monkeypatch.setattr(zotero_sync, "zotero_running", lambda timeout=1.5: True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def _wait(predicate, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_web_pages_cannot_drive_the_connector(client):
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/zotero/sync", json={}, headers=evil).status_code == 403
    assert client.post("/api/shared/join", json={"path": "C:/"}, headers=evil).status_code == 403


def test_zotero_status_config_and_background_sync(client, tmp_path):
    z = FakeZotero(tmp_path)
    z.sql("INSERT INTO collections VALUES (2,'Lab reading',NULL,NULL,2,'COLGRP01')")
    z.add_item(1, "USERITM1", fields={1: "Mine"})
    z.add_item(3, "GROUPIT1", lib=2, fields={1: "From the group"}, collections=[2])
    r = client.post("/api/zotero/config", json={"data_dir": str(z.dir), "libraries": {
        "user": {"enabled": False}, "group:98765": {"enabled": True}}}, headers=APP)
    assert r.status_code == 200, r.text
    status = r.json()
    assert status["running"] and status["data_dir"] == str(z.dir)
    assert {lib["key"]: lib["enabled"] for lib in status["libraries"]} == {"user": False, "group:98765": True}

    assert client.post("/api/zotero/sync", json={}, headers=APP).json()["queued"]
    assert _wait(lambda: client.get("/api/zotero/status").json()["last_sync"].get("items") == 1)
    titles = {i["title"] for i in client.get("/api/items").json()}
    assert titles == {"From the group"}                                  # My Library was switched off
    assert client.post("/api/zotero/config", json={"conflict_policy": "bogus"}, headers=APP).status_code == 400


def test_shared_library_endpoints(client, tmp_path):
    drive = tmp_path / "Drive"
    drive.mkdir()
    assert client.post("/api/shared/member", json={"name": "Alice", "email": "a@lab.org"}, headers=APP).json()["name"] == "Alice"
    r = client.post("/api/shared/create", json={"name": "Journal club", "parent_dir": str(drive)}, headers=APP)
    assert r.status_code == 200 and r.json()["ok"], r.text
    overview = client.get("/api/shared").json()
    lib = overview["libraries"][0]
    assert lib["name"] == "Journal club" and lib["available"] and lib["members"][0]["email"] == "a@lab.org"
    assert client.post(f"/api/shared/{lib['library_id']}/sync", json={}, headers=APP).json()["ok"]
    r = client.post("/api/shared/create", json={"name": "Journal club", "parent_dir": str(drive)}, headers=APP)
    assert r.status_code == 400 and "Join" in r.json()["detail"]
    assert client.post("/api/shared/join", json={"path": str(tmp_path)}, headers=APP).status_code == 400
    assert client.post(f"/api/shared/{lib['library_id']}/leave", json={}, headers=APP).json()["ok"]


def test_zotero_group_feeds_a_shared_library(library, tmp_path):
    """Bridge: a Zotero group mirrored into a shared library's collection reaches every member."""
    from doi_hunter_web import config, shared_library as sl, zotero_sync

    drive = tmp_path / "Drive"
    drive.mkdir()
    sl.create("Lab", str(drive))
    lib = sl.list_libraries()[0]
    config.update_settings({"zotero_libraries": {"group:98765": {"enabled": True,
                                                                 "target_collection_id": lib["collection_id"]}}})
    z = FakeZotero(tmp_path)
    z.add_item(3, "GROUPIT1", lib=2, fields={1: "Group paper", 4: "10.1000/g.1"})
    zotero_sync.sync(z.dir, libraries=["group:98765"])
    # Zotero puts group items without a collection at the group root = the shared collection.
    from doi_hunter_web.db import get_conn

    iid = get_conn().execute("SELECT id FROM items WHERE title='Group paper'").fetchone()[0]
    get_conn().execute("INSERT OR IGNORE INTO item_collections VALUES (?, ?)", (iid, lib["collection_id"]))
    report = sl.sync(lib["library_id"])
    assert report["published"] >= 1
    records = list((drive / "Lab" / "records").rglob("*.json"))
    assert any("Group paper" in f.read_text(encoding="utf-8") for f in records)
