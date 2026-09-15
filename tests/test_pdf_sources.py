"""PDF source resolvers v3: wall classification, Sci-Hub proxy pages with Referer, open-access cascade,
diagnostics, the browser plan and the connector endpoints.

No network access: ``transport.get`` is replaced by a fake router keyed by URL, scholarly APIs by a
fake ``_api_get``, and certificate / DNS diagnosis by fixed strings.
"""
from dataclasses import dataclass, field

import pytest

DOI = "10.1177/0269881107082286"

# Minimal pages reproducing the structures observed on 2026-09-14 / 2026-09-15 (not copies of the real pages).
ARTICLE_HTML = """<!DOCTYPE html><html><head><title>Sci-Hub. High ambient temperature increases intravenous
methamphetamine self-administration / Journal of Psychopharmacology, 2007</title></head><body>
<div class="pdf"><object type="application/pdf" data="//sci-hub.red/storage/moscow/3858/abc/cornish2007.pdf#navpanes=0&view=FitH"></object></div>
<div class="download"><a href="//sci-hub.red/storage/moscow/3858/abc/cornish2007.pdf">download</a></div>
</body></html>"""
PROXY_HTML = f"""<!DOCTYPE html><html><head><title>Sci-Hub | High ambient temperature | {DOI}</title></head><body>
<div id="minu"><a id="header" href="https://sci-hub.example/">sci-hub</a></div>
<embed type="application/pdf" src="https://pdf-host.example/pdf/{DOI}.pdf#view=FitH" id="pdf">
</body></html>"""
ALTCHA_HTML = """<!DOCTYPE html><html><head><title>Sci-Hub : êtes-vous un robot ?</title>
<script async defer src="/scripts/altcha.min.js" type="module"></script></head>
<body><altcha-widget challengeurl="/captcha/challenge/1"></altcha-widget></body></html>"""
DDOS_GUARD_HTML = '<!doctype html><html><head><title>DDoS-Guard</title></head><body data-ddg-origin="true"><h1>Checking your browser before accessing</h1></body></html>'
CLOUDFLARE_HTML = '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head><body><div id="cf-chl-widget"></div></body></html>'
NCBI_HTML = '<html><head><title>Preparing to download ...</title></head><body><h1>Preparing to download ...</h1></body></html>'
FORTI_HTML = '<html><head><title>UNIGE - Site bloqué</title></head><body><img id="ftntlogo"><div>Fortinet FortiGuard</div></body></html>'
ANTIBOT_HTML = '<html><head><title>Loading...</title></head><body><div class="s">Antibot solution</div></body></html>'


@dataclass
class FakeResponse:
    """Stand-in for transport.Response (same attributes the resolvers use)."""

    url: str
    status_code: int = 200
    text: str = ""
    headers: dict = field(default_factory=lambda: {"content-type": "text/html"})
    content: bytes = b""

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    def is_pdf(self):
        return self.content[:5] == b"%PDF-" or "application/pdf" in self.headers.get("content-type", "")


def install_router(monkeypatch, routes):
    """Replace transport.get with a URL -> FakeResponse | TransportError table; record calls."""
    from doi_hunter_web import sources, transport

    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        hit = routes.get(url)
        if hit is None:
            raise transport.TransportError("connect", f"connection refused {url}")
        if isinstance(hit, Exception):
            raise hit
        return hit

    monkeypatch.setattr(transport, "get", fake_get)
    monkeypatch.setattr(transport, "describe_tls_failure", lambda url: "certificate not trusted (fake)")
    monkeypatch.setattr(transport, "describe_dns_failure", lambda url: "domain no longer exists (fake)")
    monkeypatch.setattr(sources, "_settings", lambda: {})
    for env in ("REVIEWHUNTER_SCIHUB_MIRRORS", "DOIHUNTER_SCIHUB_MIRRORS", "REVIEWHUNTER_SCIDB_BASES", "DOIHUNTER_SCIDB_BASES"):
        monkeypatch.delenv(env, raising=False)
    return calls


# ----------------------------------------------------------------------------- walls

def test_wall_classification():
    from doi_hunter_web.sources import is_bot_challenge
    from doi_hunter_web.transport import classify_challenge

    assert classify_challenge(ALTCHA_HTML).kind == "interactive"
    assert classify_challenge(DDOS_GUARD_HTML).kind == "auto"
    assert classify_challenge(CLOUDFLARE_HTML).kind == "auto" and "Cloudflare" in classify_challenge(CLOUDFLARE_HTML).label
    assert classify_challenge(NCBI_HTML).kind == "auto"
    assert classify_challenge(ANTIBOT_HTML).kind == "auto"
    blocked = classify_challenge(FORTI_HTML)
    assert blocked.kind == "blocked" and "Site bloqué" in blocked.label
    assert classify_challenge(ARTICLE_HTML) is None
    assert classify_challenge(PROXY_HTML) is None
    assert classify_challenge("") is None
    # A long real page mentioning "robot" or "blocked" far below the first screen is not a wall.
    assert classify_challenge("<html><title>Robot kinematics</title>" + "x" * 30000 + "are you a robot; site bloqué") is None
    assert is_bot_challenge(ALTCHA_HTML) and not is_bot_challenge(ARTICLE_HTML)


# ----------------------------------------------------------------------------- Sci-Hub

def test_scihub_proxy_page_gives_pdf_with_referer(monkeypatch):
    from doi_hunter_web import sources

    monkeypatch.setattr(sources, "SCIHUB_MIRRORS", ["https://sci-hub.example"])
    install_router(monkeypatch, {f"https://sci-hub.example/{DOI}": FakeResponse(url=f"https://sci-hub.example/{DOI}", text=PROXY_HTML)})
    with sources.collect_notes() as notes:
        found = sources._scihub_candidates(DOI)
    assert notes == []
    assert found[0].url == f"https://pdf-host.example/pdf/{DOI}.pdf"          # fragment stripped
    assert found[0].referer == f"https://sci-hub.example/{DOI}"               # hot-link protection
    assert found[0].source == "Sci-Hub (sci-hub.example)" and not found[0].needs_browser


def test_scihub_article_page_and_alias_dedup(monkeypatch):
    from doi_hunter_web import sources

    monkeypatch.setattr(sources, "SCIHUB_MIRRORS", ["https://sci-hub.fr", "https://sci-hub.ru", "https://sci-hub.st"])
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
    assert [u for u, _ in calls].count(f"https://sci-hub.ru/{DOI}") == 1
    assert all(kw.get("shadow") for _, kw in calls)                          # proxy scope "shadow" applies


def test_walls_are_reported_and_handed_to_the_browser(monkeypatch):
    from doi_hunter_web import sources

    monkeypatch.setattr(sources, "SCIHUB_MIRRORS", ["https://sci-hub.fr", "https://sci-hub.wf"])
    monkeypatch.setattr(sources, "ANNAS_BASES", ["https://annas-archive.pk"])
    install_router(monkeypatch, {
        f"https://sci-hub.fr/{DOI}": FakeResponse(url=f"https://sci-hub.fr/{DOI}", text=ALTCHA_HTML),
        f"https://sci-hub.wf/{DOI}": FakeResponse(url=f"https://sci-hub.wf/{DOI}", text=FORTI_HTML),
        f"https://annas-archive.pk/scidb/{DOI}": FakeResponse(url=f"https://annas-archive.pk/scidb/{DOI}/?&check=1",
                                                             status_code=403, text=DDOS_GUARD_HTML),
    })
    with sources.collect_notes() as notes, sources.collect_browser_steps() as steps:
        found = list(sources.iter_pdf_urls(DOI, [sources._scihub_candidates, sources._scidb_candidates]))
    assert found == []
    assert any("sci-hub.fr" in n and "human verification" in n for n in notes)
    assert any("sci-hub.wf" in n and "web filter" in n for n in notes)
    assert any("annas-archive.pk" in n and "DDoS-Guard" in n and "real browser" in n for n in notes)
    kinds = {s["url"]: s["wall"] for s in steps}
    assert kinds[f"https://sci-hub.fr/{DOI}"] == "interactive"
    assert kinds[f"https://annas-archive.pk/scidb/{DOI}/?&check=1"] == "auto"
    assert not any("sci-hub.wf" in u for u in kinds)                           # block pages are not retried


def test_network_failures_are_classified(monkeypatch):
    from doi_hunter_web import sources, transport

    install_router(monkeypatch, {
        "https://a.example/x": transport.TransportError("tls", "CERTIFICATE_VERIFY_FAILED"),
        "https://b.example/x": transport.TransportError("dns", "getaddrinfo failed"),
        "https://c.example/x": transport.TransportError("timeout", "slow"),
        "https://d.example/x": transport.TransportError("proxy", "refused"),
    })
    with sources.collect_notes() as notes:
        for host in "abcd":
            assert sources._fetch_page(f"https://{host}.example/x", f"Mirror {host}") is None
    assert notes[0].startswith("Mirror a") and "certificate not trusted" in notes[0]
    assert "domain no longer exists" in notes[1]
    assert "20 s" in notes[2]
    assert "proxy" in notes[3]


def test_notes_are_scoped_to_the_context(monkeypatch):
    from doi_hunter_web import sources

    sources._note("outside any context")  # must not raise nor leak
    sources.browser_step("https://x.example/p", "X")  # idem
    with sources.collect_notes() as notes, sources.collect_browser_steps() as steps:
        sources._note("inside")
        sources._note("inside")  # duplicates collapsed
        sources.browser_step("https://x.example/p", "X")
        sources.browser_step("https://x.example/p", "X")
    assert notes == ["inside"] and len(steps) == 1


def test_shadow_pages_and_configured_mirrors(monkeypatch):
    from doi_hunter_web import sources

    for env in ("REVIEWHUNTER_SCIHUB_MIRRORS", "DOIHUNTER_SCIHUB_MIRRORS", "REVIEWHUNTER_SCIDB_BASES", "DOIHUNTER_SCIDB_BASES"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(sources, "_settings", lambda: {})
    pages = sources.shadow_library_pages(DOI)
    assert pages[0]["url"] == f"https://sci-hub.ren/{DOI}" and pages[0]["kind"] == "page"
    assert all("annas-archive.gs" not in p["url"] and "annas-archive.org" not in p["url"] for p in pages)
    # Settings lists (with or without scheme) override the built-in list; the environment overrides both.
    monkeypatch.setattr(sources, "_settings", lambda: {"scihub_mirrors": ["mirror.example", "https://two.example/"]})
    assert [p["url"] for p in sources.shadow_library_pages(DOI)[:2]] == [f"https://mirror.example/{DOI}", f"https://two.example/{DOI}"]
    monkeypatch.setenv("REVIEWHUNTER_SCIHUB_MIRRORS", "https://sci-hub.env/")
    assert sources.shadow_library_pages(DOI)[0]["url"] == f"https://sci-hub.env/{DOI}"


# ----------------------------------------------------------------------------- open access

def test_open_access_cascade(monkeypatch):
    from doi_hunter_web import sources

    doi = "10.1016/j.pbb.2017.10.008"
    answers = {
        "api.openalex.org": {"best_oa_location": {"pdf_url": "https://europepmc.org/articles/pmc5688002?pdf=render"},
                             "locations": [{"is_oa": True, "pdf_url": "https://repo.example/a.pdf", "source": {"display_name": "eScholarship"}}],
                             "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5688002"}},
        "api.semanticscholar.org": {"openAccessPdf": {"url": "https://europepmc.org/articles/pmc5688002?pdf=render"},
                                    "externalIds": {"ArXiv": "1706.03762", "PubMedCentral": "5688002"}},
        "www.ebi.ac.uk": {"resultList": {"result": [{"pmcid": "PMC5688002", "fullTextUrlList": {"fullTextUrl": [
            {"documentStyle": "pdf", "availabilityCode": "OA", "url": "https://europepmc.org/articles/PMC5688002?pdf=render"}]}}]}},
    }

    def fake_api(url, params, label, timeout=15):
        for host, data in answers.items():
            if host in url:
                return data
        return None

    monkeypatch.setattr(sources, "_api_get", fake_api)
    monkeypatch.setattr(sources, "_settings", lambda: {"unpaywall_email": ""})
    found = sources._open_access_candidates(doi)
    urls = [r.url for r in found]
    assert urls[0] == "https://europepmc.org/articles/pmc5688002?pdf=render"
    assert "https://repo.example/a.pdf" in urls and "https://arxiv.org/pdf/1706.03762" in urls
    assert len(urls) == len(set(urls))
    pmc = [r for r in found if "PubMed Central" in r.source]
    assert len(pmc) == 1 and pmc[0].needs_browser and pmc[0].url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC5688002/pdf/"
    epmc = [r for r in found if r.url == "https://europepmc.org/articles/PMC5688002?pdf=render"]
    assert epmc and not epmc[0].needs_browser


# ----------------------------------------------------------------------------- browser plan

def test_browser_plan_order_and_filters(monkeypatch):
    from doi_hunter_web import sources
    from doi_hunter_web.sources import Resolved

    monkeypatch.setattr(sources, "_settings", lambda: {})
    for env in ("REVIEWHUNTER_SCIHUB_MIRRORS", "DOIHUNTER_SCIHUB_MIRRORS", "REVIEWHUNTER_SCIDB_BASES", "DOIHUNTER_SCIDB_BASES"):
        monkeypatch.delenv(env, raising=False)
    skipped = [Resolved(url="https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/", source="Open access (PubMed Central)", needs_browser=True)]
    failed = [
        (Resolved(url="https://publisher.example/x.pdf", source="Publisher PDF", referer="https://publisher.example/x"), "HTTP 403"),
        (Resolved(url="https://dead.example/x.pdf", source="Open access (repo)"), "domain no longer exists"),
        (Resolved(url="https://pdf-host.example/p.pdf", source="Sci-Hub (sci-hub.ren)", referer=f"https://sci-hub.ren/{DOI}"), "Cloudflare browser check instead of the PDF"),
    ]
    steps = [
        {"url": f"https://sci-hub.fr/{DOI}", "source": "Sci-Hub (sci-hub.fr)", "kind": "page", "referer": "", "wall": "interactive", "note": "ALTCHA"},
        {"url": f"https://sci-hub.wf/{DOI}", "source": "Sci-Hub (sci-hub.wf)", "kind": "page", "referer": "", "wall": "blocked", "note": "web filter"},
    ]
    plan = sources.browser_plan(DOI, steps, skipped, failed)
    urls = [p["url"] for p in plan]
    assert urls[0] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/"                 # needs_browser first
    assert urls[1] == "https://publisher.example/x.pdf" and plan[1]["referer"] == "https://publisher.example/x"
    assert "https://dead.example/x.pdf" not in urls                                        # dead domain not retried
    assert "https://pdf-host.example/p.pdf" in urls
    assert f"https://sci-hub.fr/{DOI}" in urls and plan[urls.index(f"https://sci-hub.fr/{DOI}")]["wall"] == "interactive"
    assert f"https://sci-hub.wf/{DOI}" not in urls                                        # block page excluded
    assert f"https://sci-hub.ren/{DOI}" in urls                                          # remaining landing pages last
    assert len(urls) == len(set(urls)) and len(plan) <= sources.MAX_BROWSER_STEPS


# ----------------------------------------------------------------------------- library hand-off

def test_download_item_hands_off_to_the_browser(library, monkeypatch):
    from doi_hunter_web import catalog, handoff, library as lib, sources
    from doi_hunter_web.sources import Resolved

    monkeypatch.setattr(sources, "_settings", lambda: {})
    item = catalog.create_item({"item_type": "journalArticle", "title": "High ambient temperature", "doi": DOI})
    candidates = [
        Resolved(url="https://europepmc.org/articles/PMC1?pdf=render", source="Open access (Europe PMC)"),
        Resolved(url="https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/", source="Open access (PubMed Central)", needs_browser=True),
    ]
    monkeypatch.setattr(lib, "iter_pdf_urls", lambda doi: iter(candidates))

    def failing_download(url, target, item_id, progress, *, expect_pdf, referer="", shadow=False):
        sources._note("Sci-Hub (sci-hub.fr): human verification required")
        sources.browser_step(f"https://sci-hub.fr/{DOI}", "Sci-Hub (sci-hub.fr)", wall="interactive")
        raise ValueError("Cloudflare browser check instead of the PDF")

    monkeypatch.setattr(lib, "_download_url_to_target", failing_download)
    events = []
    result = lib.download_item(item["id"], progress=lambda stage, data: events.append((stage, data)))
    assert result["status"] == "failed"
    assert "Chrome extension asked to try" in result["error_msg"]
    assert "human verification required" in result["error_msg"]
    rec = handoff.get(item["id"])
    assert rec and rec["state"] == "pending"
    urls = [s["url"] for s in rec["plan"]]
    assert urls[0] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/"       # browser-only copy first
    assert "https://europepmc.org/articles/PMC1?pdf=render" in urls          # retried in Chrome (JS check)
    assert f"https://sci-hub.fr/{DOI}" in urls
    stages = [s for s, _ in events]
    assert "browser_handoff" in stages and stages.index("failed") < stages.index("browser_handoff")
    assert any(s == "source" and "left to the Chrome extension" in d["source"] for s, d in events)
    assert handoff.pending()[0]["item_id"] == item["id"]
    # A later successful download clears the hand-off.
    handoff.clear(item["id"])
    assert handoff.get(item["id"]) is None


# ----------------------------------------------------------------------------- connector endpoints

@pytest.fixture()
def client(library, monkeypatch):
    from doi_hunter_web import connector

    monkeypatch.setattr(connector, "_merge_enriched", lambda payload: payload)
    from doi_hunter_web import fulltext
    monkeypatch.setattr(fulltext, "_find_verified_metadata", lambda *a: (None, "", []))
    monkeypatch.setattr(connector, "_schedule_open_access_fallback", lambda *a, **k: None)
    from fastapi.testclient import TestClient

    from doi_hunter_web.server import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


EXT = {"Origin": "chrome-extension://abcdefghijklmnop"}


def test_connector_reports_saved_state(client, sample_pdf, monkeypatch):
    from doi_hunter_web import sources

    monkeypatch.setattr(sources, "_settings", lambda: {})
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


def test_connector_handoff_endpoints(client):
    from doi_hunter_web import catalog, handoff

    item = catalog.create_item({"item_type": "journalArticle", "title": "Deep learning", "doi": "10.1038/nature14539"})
    assert client.get("/connector/handoffs").json() == {"handoffs": []}
    handoff.register(item["id"], "10.1038/nature14539", "Deep learning",
                     [{"url": "https://sci-hub.ren/10.1038/nature14539", "source": "Sci-Hub (sci-hub.ren)", "kind": "page", "referer": "", "wall": "", "note": ""}],
                     "all sources failed")
    listed = client.get("/connector/handoffs").json()["handoffs"]
    assert len(listed) == 1 and listed[0]["item_id"] == item["id"] and listed[0]["plan"][0]["kind"] == "page"

    r = client.post(f"/connector/handoffs/{item['id']}/status", headers=EXT, json={"state": "running", "message": "Trying Sci-Hub", "step": "Sci-Hub (sci-hub.ren)"})
    assert r.status_code == 200 and r.json()["state"] == "running"
    assert client.get("/connector/handoffs").json()["handoffs"][0]["state"] == "running"

    r = client.post(f"/connector/handoffs/{item['id']}/status", headers=EXT, json={"state": "failed", "message": "no source worked"})
    assert r.status_code == 200
    assert client.get("/connector/handoffs").json()["handoffs"] == []                   # failed ones are not re-run
    assert client.get("/connector/handoffs", params={"include_failed": "true"}).json()["handoffs"][0]["state"] == "failed"
    assert "Chrome extension: no source worked" in catalog.get_item(item["id"])["error_msg"]

    handoff.update(item["id"], "pending")
    r = client.post(f"/connector/handoffs/{item['id']}/status", headers=EXT, json={"state": "done", "message": "PDF attached"})
    assert r.status_code == 200 and handoff.get(item["id"]) is None                      # done clears the record
    assert client.post("/connector/handoffs/424242/status", headers=EXT, json={"state": "done"}).status_code == 404


def test_api_handoff_request_builds_a_plan(client, monkeypatch):
    from doi_hunter_web import catalog, library as lib, sources
    from doi_hunter_web.sources import Resolved

    monkeypatch.setattr(sources, "_settings", lambda: {})
    item = catalog.create_item({"item_type": "journalArticle", "title": "Deep learning", "doi": "10.1038/nature14539"})
    monkeypatch.setattr(lib, "iter_pdf_urls", lambda doi: iter([Resolved(url="https://publisher.example/nature14539.pdf", source="Publisher PDF")]))
    r = client.post(f"/api/items/{item['id']}/handoff", headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200 and r.json()["ok"]
    plan = r.json()["plan"]
    assert plan[0]["url"] == "https://publisher.example/nature14539.pdf"
    assert any(p["url"].startswith("https://sci-hub.ren/") for p in plan)
    assert client.get("/connector/handoffs").json()["handoffs"][0]["item_id"] == item["id"]
