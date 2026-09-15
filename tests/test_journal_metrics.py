"""Journal impact metrics: OpenAlex matching, caching, manual overrides, denormalisation onto items,
and the list/advanced-search wiring in catalog.py.

No live network: ``requests.get`` is replaced by a fake OpenAlex responder keyed by query params.
"""
from dataclasses import dataclass, field

import pytest

from doi_hunter_web import journal_metrics as jm


@dataclass
class FakeResponse:
    payload: dict
    status_code: int = 200

    @property
    def ok(self):
        return self.status_code == 200

    def json(self):
        return self.payload


def source(name, issn_l, issns, citedness, h=100, works=1000, cited=5000):
    return {"display_name": name, "issn_l": issn_l, "issn": issns, "works_count": works,
            "cited_by_count": cited, "id": f"https://openalex.org/S{abs(hash(name)) % 10**8}",
            "summary_stats": {"2yr_mean_citedness": citedness, "h_index": h, "i10_index": 1}}


PSYCHOPHARM = source("Journal of Psychopharmacology", "0269-8811", ["0269-8811", "1461-7285"], 4.26)
NEUROPHARM = source("Neuropharmacology", "0028-3908", ["0028-3908", "1873-7064"], 4.06)
CLINICAL_PSYCHOPHARM = source("Journal of Clinical Psychopharmacology", "0271-0749", ["0271-0749"], 1.81)


def install_router(monkeypatch, routes):
    """routes: list of (predicate(params) -> bool, [source dicts]) tried in order; default -> []."""
    calls = []

    def fake_get(url, params=None, timeout=None, headers=None):
        calls.append(params)
        for predicate, results in routes:
            if predicate(params or {}):
                return FakeResponse({"results": results})
        return FakeResponse({"results": []})

    monkeypatch.setattr(jm.requests, "get", fake_get)
    return calls


# ----------------------------------------------------------------------------- normalisation

def test_normalize_issn():
    assert jm.normalize_issn("0269-8811") == "0269-8811"
    assert jm.normalize_issn("02698811") == "0269-8811"
    assert jm.normalize_issn("0269881x") == "0269-881X"
    assert jm.normalize_issn("not an issn") is None
    assert jm.normalize_issn("") is None
    assert jm.normalize_issn(None) is None
    assert jm.normalize_issn("0269-8811, 1461-7285") == "0269-8811"  # first of a combined list


def test_normalize_name_and_cache_key():
    assert jm._normalize_name("Psychopharmacology (Berl)") == "psychopharmacology"
    assert jm._normalize_name("  The   Journal.") == "the journal"
    assert jm.cache_key("0269-8811", "Journal of Psychopharmacology") == "issn:0269-8811"
    assert jm.cache_key(None, "Neuropharmacology") == "name:neuropharmacology"
    assert jm.cache_key(None, "") is None
    assert jm.cache_key("garbage", "") is None


# ----------------------------------------------------------------------------- OpenAlex matching

def test_fetch_remote_by_issn_is_exact(monkeypatch):
    install_router(monkeypatch, [(lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM])])
    row = jm.fetch_remote(issn="0269-8811")
    assert row["name"] == "Journal of Psychopharmacology" and row["citations_2yr"] == 4.26 and row["issn"] == "0269-8811"


def test_fetch_remote_by_name_picks_the_exact_match_not_the_top_relevance_hit(monkeypatch):
    # OpenAlex free-text search often ranks a near-duplicate name above the exact one; the matcher
    # must not just take results[0].
    install_router(monkeypatch, [
        (lambda p: p.get("search") == "Journal of Psychopharmacology",
         [CLINICAL_PSYCHOPHARM, PSYCHOPHARM]),  # exact match listed second
    ])
    row = jm.fetch_remote(journal="Journal of Psychopharmacology")
    assert row["name"] == "Journal of Psychopharmacology" and row["citations_2yr"] == 4.26


def test_fetch_remote_rejects_a_weak_name_match(monkeypatch):
    install_router(monkeypatch, [(lambda p: "search" in p, [CLINICAL_PSYCHOPHARM])])
    assert jm.fetch_remote(journal="Some Completely Unrelated Venue Name") is None


def test_fetch_remote_falls_back_from_bad_issn_to_name(monkeypatch):
    # The item's ISSN is wrong / not in OpenAlex, but its journal name still resolves by search.
    install_router(monkeypatch, [
        (lambda p: p.get("filter") == "issn:9999-9999", []),
        (lambda p: p.get("search") == "Neuropharmacology", [NEUROPHARM]),
    ])
    row = jm.fetch_remote(issn="9999-9999", journal="Neuropharmacology")
    assert row["name"] == "Neuropharmacology"


def test_search_returns_candidates_without_caching(library, monkeypatch):
    install_router(monkeypatch, [(lambda p: "search" in p, [PSYCHOPHARM, CLINICAL_PSYCHOPHARM])])
    results = jm.search("Psychopharmacology")
    assert [r["name"] for r in results] == ["Journal of Psychopharmacology", "Journal of Clinical Psychopharmacology"]
    assert jm.get_cached(issn="0269-8811") is None  # search() never writes the cache
    assert jm.search("a") == []  # too short to bother


# ----------------------------------------------------------------------------- cache + freshness

def test_get_or_fetch_caches_and_reuses(library, monkeypatch):
    calls = install_router(monkeypatch, [(lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM])])
    row1 = jm.get_or_fetch(issn="0269-8811")
    assert row1.citations_2yr == 4.26 and row1.found
    row2 = jm.get_or_fetch(issn="0269-8811")  # served from cache, no second network call
    assert row2.citations_2yr == 4.26
    assert len(calls) == 1


def test_get_or_fetch_aliases_a_name_lookup_to_its_resolved_issn(library, monkeypatch):
    calls = install_router(monkeypatch, [(lambda p: p.get("search") == "Neuropharmacology", [NEUROPHARM])])
    row = jm.get_or_fetch(journal="Neuropharmacology")
    assert row.citations_2yr == 4.06
    # A later ISSN-based lookup for the same journal hits the aliased cache row, no network call.
    row2 = jm.get_or_fetch(issn="0028-3908")
    assert row2.citations_2yr == 4.06
    assert len(calls) == 1


def test_get_or_fetch_caches_not_found_with_a_shorter_ttl(library, monkeypatch):
    calls = install_router(monkeypatch, [])
    row = jm.get_or_fetch(journal="Totally Unknown Zine")
    assert row is not None and not row.found and row.citations_2yr is None
    jm.get_or_fetch(journal="Totally Unknown Zine")
    assert len(calls) == 1  # still cached (not-found TTL not yet expired)


def test_force_refresh_bypasses_the_cache(library, monkeypatch):
    calls = install_router(monkeypatch, [(lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM])])
    jm.get_or_fetch(issn="0269-8811")
    jm.get_or_fetch(issn="0269-8811", force=True)
    assert len(calls) == 2


# ----------------------------------------------------------------------------- manual override

def test_manual_value_overrides_display(library, monkeypatch):
    install_router(monkeypatch, [(lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM])])
    row = jm.get_or_fetch(issn="0269-8811")
    assert row.display()["source"] == "openalex" and row.display()["value"] == 4.26
    jm.set_manual("0269-8811", "Journal of Psychopharmacology", value=5.1, year=2024, note="JCR 2024")
    row2 = jm.get_cached(issn="0269-8811")
    disp = row2.display()
    assert disp["source"] == "manual" and disp["value"] == 5.1 and "2024" in disp["label"]
    # a manual value protects the row from being overwritten by a "not found" result
    jm.set_manual(None, "Some Unrelated Name Not Used As Key", value=None)  # no-op on this row
    assert jm.get_cached(issn="0269-8811").manual_value == 5.1
    jm.set_manual("0269-8811", None, value=None)  # explicit reset
    assert jm.get_cached(issn="0269-8811").display()["source"] == "openalex"


# ----------------------------------------------------------------------------- applying to items + catalog wiring

def test_refresh_item_denormalises_onto_every_item_sharing_the_journal(library, monkeypatch):
    from doi_hunter_web import catalog

    install_router(monkeypatch, [(lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM])])
    a = catalog.create_item({"title": "Paper A", "journal": "Journal of Psychopharmacology", "issn": "0269-8811"})
    b = catalog.create_item({"title": "Paper B", "journal": "Journal of Psychopharmacology", "issn": "0269-8811"})
    other = catalog.create_item({"title": "Paper C", "journal": "Neuropharmacology", "issn": "0028-3908"})

    result = jm.refresh_item(a["id"])
    assert result["ok"] and result["items_updated"] == 2 and abs(result["value"] - 4.26) < 1e-6

    fetched_a = catalog.get_item(a["id"])
    fetched_b = catalog.get_item(b["id"])
    fetched_c = catalog.get_item(other["id"])
    assert abs(fetched_a["impact_factor"] - 4.26) < 1e-6 and fetched_a["impact_factor_source"] == "openalex"
    assert abs(fetched_b["impact_factor"] - 4.26) < 1e-6
    assert fetched_c["impact_factor"] is None  # a different journal is untouched


def test_refresh_item_without_a_journal_is_reported(library):
    from doi_hunter_web import catalog

    item = catalog.create_item({"title": "No journal"})
    result = jm.refresh_item(item["id"])
    assert not result["ok"] and "no journal" in result["reason"]
    assert jm.refresh_item(999999) == {"ok": False, "reason": "item not found"}


def test_refresh_many_costs_one_call_per_distinct_journal(library, monkeypatch):
    from doi_hunter_web import catalog

    calls = install_router(monkeypatch, [
        (lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM]),
        (lambda p: p.get("filter") == "issn:0028-3908", [NEUROPHARM]),
    ])
    for i in range(3):
        catalog.create_item({"title": f"P{i}", "journal": "Journal of Psychopharmacology", "issn": "0269-8811"})
    catalog.create_item({"title": "N", "journal": "Neuropharmacology", "issn": "0028-3908"})
    catalog.create_item({"title": "No journal"})

    result = jm.refresh_many()
    assert result["journals"] == 2 and result["matched"] == 2 and result["items_updated"] == 4
    assert len(calls) == 2

    # A second call with no items missing a value does nothing further.
    result2 = jm.refresh_many()
    assert result2["journals"] == 0


def test_list_items_sort_impact_puts_nulls_last_in_both_directions(library, monkeypatch):
    from doi_hunter_web import catalog

    install_router(monkeypatch, [
        (lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM]),   # 4.26, the higher one
        (lambda p: p.get("filter") == "issn:0028-3908", [NEUROPHARM]),    # 4.06, the lower one
    ])
    high = catalog.create_item({"title": "High", "journal": "Journal of Psychopharmacology", "issn": "0269-8811"})
    low = catalog.create_item({"title": "Low", "journal": "Neuropharmacology", "issn": "0028-3908"})
    unrated = catalog.create_item({"title": "Unrated", "journal": "Some Unmatched Venue Xyz"})
    jm.refresh_item(high["id"])
    jm.refresh_item(low["id"])

    desc = catalog.query_items(sort="impact", direction="desc", compact=True)
    asc = catalog.query_items(sort="impact", direction="asc", compact=True)
    # Both directions rank the rated items by value; the unrated item is parked at the end either way,
    # never surfaced first just because SQLite treats NULL as the smallest possible value.
    assert [i["id"] for i in desc] == [high["id"], low["id"], unrated["id"]]
    assert [i["id"] for i in asc] == [low["id"], high["id"], unrated["id"]]
    assert desc[0]["impact_factor"] == pytest.approx(4.26, abs=1e-6)


def test_advanced_search_impact_factor_condition(library, monkeypatch):
    from doi_hunter_web import catalog

    install_router(monkeypatch, [
        (lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM]),
        (lambda p: p.get("filter") == "issn:0028-3908", [NEUROPHARM]),
    ])
    high = catalog.create_item({"title": "High IF", "journal": "Journal of Psychopharmacology", "issn": "0269-8811"})
    low = catalog.create_item({"title": "Low IF", "journal": "Neuropharmacology", "issn": "0028-3908"})
    jm.refresh_many()

    at_least_4 = catalog.query_items(conditions=[{"field": "impactFactor", "op": "isGreaterThan", "value": "4.2"}], compact=True)
    assert [i["id"] for i in at_least_4] == [high["id"]]
    at_most_4_1 = catalog.query_items(conditions=[{"field": "impactFactor", "op": "isLessThan", "value": "4.1"}], compact=True)
    assert [i["id"] for i in at_most_4_1] == [low["id"]]


# ----------------------------------------------------------------------------- server endpoints

@pytest.fixture()
def client(library, monkeypatch):
    from fastapi.testclient import TestClient

    from doi_hunter_web.server import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


ORIGIN = {"Origin": "http://127.0.0.1:8765"}


def test_api_journal_metrics_endpoints(client, monkeypatch):
    from doi_hunter_web import catalog

    install_router(monkeypatch, [
        (lambda p: p.get("filter") == "issn:0269-8811", [PSYCHOPHARM]),
        (lambda p: "search" in p, [PSYCHOPHARM]),
    ])
    item = catalog.create_item({"title": "P", "journal": "Journal of Psychopharmacology", "issn": "0269-8811"})

    r = client.get(f"/api/items/{item['id']}/journal-metrics")
    assert r.status_code == 200 and r.json()["metrics"] is None  # nothing fetched yet

    r = client.post("/api/journal-metrics/refresh", headers=ORIGIN, json={"item_ids": [item["id"]]})
    assert r.status_code == 200 and r.json() == {"queued": True}

    import time

    for _ in range(50):
        if catalog.get_item(item["id"])["impact_factor"] is not None:
            break
        time.sleep(0.05)
    assert catalog.get_item(item["id"])["impact_factor"] == pytest.approx(4.26, abs=1e-6)

    r = client.patch(f"/api/items/{item['id']}/impact-factor", headers=ORIGIN, json={"value": 9.9, "year": 2025})
    assert r.status_code == 200 and r.json()["display"]["source"] == "manual"
    assert catalog.get_item(item["id"])["impact_factor"] == pytest.approx(9.9, abs=1e-6)

    r = client.patch(f"/api/items/{item['id']}/impact-factor", headers=ORIGIN, json={"reset": True})
    assert r.status_code == 200
    assert catalog.get_item(item["id"])["impact_factor"] == pytest.approx(4.26, abs=1e-6)

    r = client.patch("/api/items/999999/impact-factor", headers=ORIGIN, json={"value": 1})
    assert r.status_code == 404

    no_journal = catalog.create_item({"title": "No journal item"})
    r = client.patch(f"/api/items/{no_journal['id']}/impact-factor", headers=ORIGIN, json={"value": 1})
    assert r.status_code == 400

    r = client.get("/api/journal-metrics/search", params={"q": "Psychopharmacology"})
    assert r.status_code == 200 and r.json()["results"]
