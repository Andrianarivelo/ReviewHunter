"""PDF source resolvers: human-verification walls, mirror aliases, diagnostics and connector endpoints.

No network access: ``requests.get`` is replaced by a fake router keyed by URL.
"""
from dataclasses import dataclass, field

import pytest

DOI = "10.1177/0269881107082286"

# Minimal pages reproducing the structure observed on 2026-09-14 (not copies of the real pages).
ARTICLE_HTML = """<!DOCTYPE html><html><head><title>Sci-Hub. High ambient temperature increases intravenous
methamphetamine self-administration / Journal of Psychopharmacology, 2007</title></head><body>
<div class="pdf"><object type="application/pdf" data="//sci-hub.red/storage/moscow/3858/abc/cornish2007.pdf#navpanes=0&view=FitH"></object></div>
<div class="download"><a href="//sci-hub.red/storage/moscow/3858/abc/cornish2007.pdf">download</a></div>
</body></html>"""
ALTCHA_HTML = """<!DOCTYPE html><html><head><title>Sci-Hub : êtes-vous un robot ?</title>
<script async defer src="/scripts/altcha.min.js" type="module"></script></head>
<body><altcha-widget challengeurl="/captcha/challenge/1"></altcha-widget></body></html>"""
DDOS_GUARD_HTML = "<html><head><title>DDoS-Guard</title></head><body>Checking your browser</body></html>"
ANTIBOT_HTML = '<html><head><title>Loading...</title></head><body><div class="s">Antibot solution</div></body></html>'


@dataclass
class FakeResponse:
    url: str
    status_code: int = 200
    text: str = ""
    headers: dict = field(default_factory=lambda: {"Content-Type": "text/html"})
    content: bytes = b""


def install_router(monkeypatch, routes):
    """Replace requests.get in sources with a URL -> FakeResponse | Exception table."""
    from doi_hunter_web import sources

    calls = []

    def fake_get(url, **_kw):
        calls.append(url)
        hit = routes.get(url)
        if hit is None:
            import requests

            raise requests.exceptions.ConnectionError(f"connection refused {url}")
        if isinstance(hit, Exception):
            raise hit
        return hit

    monkeypatch.setattr(sources.requests, "get", fake_get)
    return calls


def test_bot_challenge_detection():
    from doi_hunter_web.sources import is_bot_challenge

    assert is_bot_challenge(ALTCHA_HTML)
    assert is_bot_challenge(DDOS_GUARD_HTML)
    assert is_bot_challenge(ANTIBOT_HTML)
    assert not is_bot_challenge(ARTICLE_HTML)
    # A long real page mentioning "robot" far below the first screen is not a wall.
    assert not is_bot_challenge("<html><title>Robot kinematics</title>" + "x" * 30000 + "are you a robot")


def test_scihub_article_page_and_alias_dedup(monkeypatch):
    from doi_hunter_web import sources

    monkeypatch.setattr(sources, "SCIHUB_MIRRORS", ["https://sci-hub.fr", "https://sci-hub.ru", "https://sci-hub.st"])
    monkeypatch.delenv("REVIEWHUNTER_SCIHUB_MIRRORS", raising=False)
    monkeypatch.delenv("DOIHUNTER_SCIHUB_MIRRORS", raising=False)
    calls = install_router(monkeypatch, {
        f"https://sci-hub.fr/{DOI}": FakeResponse(url=f"https://sci-hub.fr/{DOI}", text=ARTICLE_HTML),
        # .ru redirects to .fr: must not be parsed a second time.
        f"https://sci-hub.ru/{DOI}": FakeResponse(url=f"https://sci-hub.fr/{DOI}", text=ARTICLE_HTML),
    })
    with sources.collect_notes() as notes:
        found = sources._scihub_candidates(DOI)
    urls = [r.url for r in found]
    assert urls[0] == "https://sci-hub.red/storage/moscow/3858/abc/cornish2007.pdf"
    assert len(urls) == len(set(urls))
    assert all(r.source == "Sci-Hub (sci-hub.fr)" for r in found)
    assert any("sci-hub.st" in n and "not reachable" in n for n in notes)
    assert calls.count(f"https://sci-hub.ru/{DOI}") == 1


def test_scihub_challenge_is_reported_not_parsed(monkeypatch):
    from doi_hunter_web import sources

    monkeypatch.setattr(sources, "SCIHUB_MIRRORS", ["https://sci-hub.fr"])
    monkeypatch.setattr(sources, "ANNAS_BASES", ["https://annas-archive.pk"])
    for env in ("REVIEWHUNTER_SCIHUB_MIRRORS", "DOIHUNTER_SCIHUB_MIRRORS", "REVIEWHUNTER_SCIDB_BASES", "DOIHUNTER_SCIDB_BASES"):
        monkeypatch.delenv(env, raising=False)
    install_router(monkeypatch, {
        f"https://sci-hub.fr/{DOI}": FakeResponse(url=f"https://sci-hub.fr/{DOI}", text=ALTCHA_HTML),
        f"https://annas-archive.pk/scidb/{DOI}": FakeResponse(url=f"https://annas-archive.pk/scidb/{DOI}/?&check=1",
                                                             status_code=403, text=DDOS_GUARD_HTML),
    })
    with sources.collect_notes() as notes:
        found = list(sources.iter_pdf_urls(DOI, [sources._scihub_candidates, sources._scidb_candidates]))
    assert found == []
    assert any("sci-hub.fr" in n and "human verification" in n for n in notes)
    assert any("annas-archive.pk" in n and "human verification" in n for n in notes)


def test_network_failures_are_classified(monkeypatch):
    import requests

    from doi_hunter_web import sources

    install_router(monkeypatch, {
        "https://a.example/x": requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"),
        "https://b.example/x": requests.exceptions.ConnectionError("NameResolutionError: getaddrinfo failed"),
        "https://c.example/x": requests.exceptions.Timeout("slow"),
    })
    with sources.collect_notes() as notes:
        for host in "abc":
            assert sources._fetch_page(f"https://{host}.example/x", f"Mirror {host}") is None
    assert "certificate not trusted" in notes[0] and notes[0].startswith("Mirror a")
    assert "does not resolve" in notes[1]
    assert "20 s" in notes[2]


def test_notes_are_scoped_to_the_context(monkeypatch):
    from doi_hunter_web import sources

    sources._note("outside any context")  # must not raise nor leak
    with sources.collect_notes() as notes:
        sources._note("inside")
        sources._note("inside")  # duplicates collapsed
    assert notes == ["inside"]


def test_shadow_pages_and_removed_hijacked_mirror(monkeypatch):
    from doi_hunter_web import sources

    for env in ("REVIEWHUNTER_SCIHUB_MIRRORS", "DOIHUNTER_SCIHUB_MIRRORS", "REVIEWHUNTER_SCIDB_BASES", "DOIHUNTER_SCIDB_BASES"):
        monkeypatch.delenv(env, raising=False)
    pages = sources.shadow_library_pages(DOI)
    assert pages[0]["url"] == f"https://sci-hub.fr/{DOI}"
    assert all("annas-archive.gs" not in p["url"] for p in pages)
    monkeypatch.setenv("REVIEWHUNTER_SCIHUB_MIRRORS", "https://sci-hub.example/")
    assert sources.shadow_library_pages(DOI)[0]["url"] == f"https://sci-hub.example/{DOI}"


# ----------------------------------------------------------------------------- connector endpoints

@pytest.fixture()
def client(library, monkeypatch):
    from doi_hunter_web import connector

    monkeypatch.setattr(connector, "_merge_enriched", lambda payload: payload)
    monkeypatch.setattr(connector, "_schedule_open_access_fallback", lambda *a, **k: None)
    from fastapi.testclient import TestClient

    from doi_hunter_web.server import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


EXT = {"Origin": "chrome-extension://abcdefghijklmnop"}


def test_connector_reports_saved_state(client, sample_pdf):
    col = client.post("/api/collections", headers={"Origin": "http://127.0.0.1:8765"}, json={"name": "Thermo"}).json()
    saved = client.post("/connector/save", headers=EXT, json={
        "items": [{"item_type": "journalArticle", "title": "High ambient temperature", "doi": DOI}],
        "collection_id": col["id"]}).json()["saved"][0]
    assert saved["needs_pdf"]

    look = client.post("/connector/lookup", headers=EXT, json={"doi": DOI}).json()
    assert look["found"] and look["items"][0]["has_pdf"] is False

    status = client.get(f"/connector/item/{saved['id']}").json()
    assert status["has_pdf"] is False and status["collections"] == ["Thermo"] and status["doi"] == DOI

    up = client.post(f"/connector/attachment?item_id={saved['id']}", headers={**EXT, "Content-Type": "application/pdf"},
                     content=sample_pdf.read_bytes())
    assert up.status_code == 200
    status = client.get(f"/connector/item/{saved['id']}").json()
    assert status["has_pdf"] is True and status["attachments"][0]["size"] > 0
    assert client.post("/connector/lookup", headers=EXT, json={"doi": DOI}).json()["items"][0]["has_pdf"] is True

    assert client.get("/connector/item/999999").status_code == 404
    pages = client.get("/connector/pdf-sources", params={"doi": f"https://doi.org/{DOI}"}).json()
    assert pages["doi"] == DOI and pages["pages"][0]["url"].endswith(DOI)
    assert client.get("/connector/pdf-sources", params={"doi": "not a doi"}).status_code == 400
