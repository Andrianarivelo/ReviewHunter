"""HTTP-level tests through FastAPI's TestClient (no network access needed)."""
import json

import pytest


@pytest.fixture()
def client(library, monkeypatch):
    # Disable network enrichment in the connector for deterministic tests.
    from doi_hunter_web import connector

    monkeypatch.setattr(connector, "_merge_enriched", lambda payload: payload)
    from fastapi.testclient import TestClient

    from doi_hunter_web.server import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


APP = {"Origin": "http://127.0.0.1:8765"}
EXT = {"Origin": "chrome-extension://abcdefghijklmnop"}


def test_security_guard(client):
    assert client.get("/api/health").status_code == 200
    # Cross-site page trying to mutate the library.
    r = client.post("/api/items", json={"title": "evil"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    # DNS rebinding: foreign Host header.
    assert client.get("/api/stats", headers={"Host": "evil.example:8765"}).status_code == 403
    # App origin and extension origin are allowed.
    assert client.post("/api/items", json={"title": "ok"}, headers=APP).status_code == 200
    assert client.post("/connector/save", json={"items": [{"title": "x"}]}, headers=EXT).status_code == 200
    # No caching of API data.
    assert client.get("/api/stats").headers["cache-control"] == "no-store"


def test_item_lifecycle_notes_tags(client):
    r = client.post("/api/items", headers=APP, json={
        "item_type": "journalArticle", "creators": [{"creatorType": "author", "lastName": "Hopfield", "firstName": "J"}],
        "field_values": {"title": "Neural networks and physical systems", "date": "1982", "publicationTitle": "PNAS",
                         "DOI": "10.1073/pnas.79.8.2554"}, "tags": ["attractor"]})
    item = r.json()
    iid = item["id"]
    assert item["citation_key"] == "hopfield1982neural" and item["journal"] == "PNAS"
    r = client.patch(f"/api/items/{iid}", headers=APP, json={"field_values": {"volume": "79", "pages": "2554-2558"}})
    assert r.json()["volume"] == "79"
    client.post(f"/api/items/{iid}/tags", headers=APP, json={"name": "memory"})
    assert {t["name"] for t in client.get(f"/api/items/{iid}").json()["tags"]} == {"attractor", "memory"}
    note = client.post("/api/notes", headers=APP, json={"item_id": iid, "html": "<p>energy function</p>"}).json()
    assert client.get("/api/items", params={"q": "energy", "mode": "fields"}).json()[0]["id"] == iid
    client.patch(f"/api/notes/{note['id']}", headers=APP, json={"html": "<p>Lyapunov</p>"})
    assert client.get(f"/api/notes/{note['id']}").json()["title"] == "Lyapunov"
    assert client.delete(f"/api/items/{iid}", headers=APP).json() == {"trashed": 1}
    assert client.get("/api/items").json() == []
    assert len(client.get("/api/items", params={"trash": True}).json()) == 1
    client.post("/api/items/restore", headers=APP, json={"item_ids": [iid]})
    assert len(client.get("/api/items").json()) == 1
    # Tag filter with repeated query params.
    assert len(client.get("/api/items?tag=attractor&tag=memory").json()) == 1
    assert client.get("/api/items?tag=attractor&tag=nope").json() == []


def test_connector_save_attach_and_lookup(client):
    r = client.post("/connector/save", headers=EXT, json={
        "items": [{"item_type": "journalArticle", "title": "Association of elevated ambient temperature",
                   "doi": "10.1016/j.drugalcdep.2017.04.019", "creators": [{"creatorType": "author",
                                                                            "lastName": "Auger", "firstName": "N"}],
                   "url": "https://www.sciencedirect.com/science/article/pii/S0376871617302533"}],
        "tags": ["heat"], "note": "saved from browser"})
    saved = r.json()["saved"][0]
    assert saved["needs_pdf"] is True and saved["existing"] is False
    # PDF downloaded by the browser (with the user's session) is posted as raw bytes.
    pdf = b"%PDF-1.4\n%fake but valid header\n"
    r = client.post(f"/connector/attachment?item_id={saved['id']}&url=https://x/y.pdf", content=pdf,
                    headers={**EXT, "Content-Type": "application/pdf"})
    assert r.status_code == 200 and r.json()["attachment"]["mime"] == "application/pdf"
    # Login page instead of PDF must be refused.
    r = client.post(f"/connector/attachment?item_id={saved['id']}", content=b"<html>login</html>",
                    headers={**EXT, "Content-Type": "application/pdf"})
    assert r.status_code == 415
    item = client.get(f"/api/items/{saved['id']}").json()
    assert item["status"] == "downloaded" and item["notes_list"][0]["title"] == "saved from browser"
    assert client.post("/connector/lookup", headers=EXT, json={"doi": "10.1016/J.DRUGALCDEP.2017.04.019"}).json()["found"]
    # Saving the same DOI again reuses the item and still applies the new tag.
    again = client.post("/connector/save", headers=EXT, json={
        "items": [{"title": "dup", "doi": "10.1016/j.drugalcdep.2017.04.019"}], "tags": ["overdose"]}).json()["saved"][0]
    assert again["id"] == saved["id"] and again["existing"] and again["needs_pdf"] is False
    assert {t["name"] for t in client.get(f"/api/items/{saved['id']}").json()["tags"]} == {"heat", "overdose"}


def test_import_export_and_csl(client):
    bib = "@article{k1, title={Spike timing dependent plasticity}, author={Markram, Henry}, journal={Science}, year={1997}}"
    r = client.post("/api/import/text", headers=APP, json={"text": bib, "create_collection": "Import"})
    body = r.json()
    assert body["created"] == 1 and body["collection_id"]
    items = client.get("/api/items", params={"collection_id": body["collection_id"]}).json()
    assert items[0]["citation_key"] == "k1"
    exp = client.get("/api/export", params={"format": "ris", "collection_id": body["collection_id"]})
    assert "TY  - JOUR" in exp.text and exp.headers["content-disposition"].startswith("attachment")
    csl = client.get("/api/csl/items", params={"ids": str(items[0]["id"])}).json()
    assert csl[0]["author"] == [{"family": "Markram", "given": "Henry"}]
    styles = client.get("/api/csl/styles").json()
    assert any(s["id"] == "apa" for s in styles)
    assert "<style" in client.get("/api/csl/styles/apa").text
    assert "<locale" in client.get("/api/csl/locales/fr").text
    report = client.get("/api/report", params={"ids": str(items[0]["id"])})
    assert "Spike timing dependent plasticity" in report.text


def test_settings_backup_restore_staging(client, library):
    s = client.patch("/api/settings", headers=APP, json={"citation_style": "nature", "unknown": 1}).json()
    assert s["citation_style"] == "nature" and "unknown" not in s
    client.post("/api/items", headers=APP, json={"title": "kept"})
    b = client.post("/api/backups", headers=APP).json()
    assert b["ok"] and b["items"] == 1
    assert client.get("/api/backups").json()[0]["items"] == 1
    r = client.post("/api/backups/restore", headers=APP, json={"path": b["path"]}).json()
    assert r["restart_required"] and r["staged_items"] == 1
    # Restoring arbitrary files outside the backup folders is refused.
    assert client.post("/api/backups/restore", headers=APP, json={"path": "C:/Windows/win.ini"}).status_code == 400
    st = client.get("/api/drive/status").json()
    assert st["mode"] == "none" and "desktop" in st and "api" in st


def test_saved_search_and_duplicates_api(client):
    for y in (2001, 2002):
        client.post("/api/items", headers=APP, json={"field_values": {"title": "A long identical title for dedupe", "date": str(y)},
                                                     "creators": [{"creatorType": "author", "lastName": "Same"}]})
    groups = client.get("/api/duplicates").json()
    assert len(groups) == 1
    ids = [i["id"] for i in groups[0]["items"]]
    m = client.post("/api/duplicates/merge", headers=APP, json={"master_id": ids[0], "other_ids": ids[1:]}).json()
    assert m["merged"] == ids[1:]
    s = client.post("/api/saved-searches", headers=APP, json={
        "name": "Same", "conditions": [{"field": "creator", "op": "contains", "value": "Same"}]}).json()
    assert len(client.get("/api/items", params={"saved_search_id": s["id"]}).json()) == 1
    conds = json.dumps([{"field": "year", "op": "isGreaterThan", "value": "2000"}])
    assert len(client.get("/api/items", params={"conditions": conds}).json()) == 1
