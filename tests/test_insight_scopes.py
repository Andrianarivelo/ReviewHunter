"""Collection unions and their true comparison sets, using only disposable libraries."""
import pytest


@pytest.fixture()
def scoped_library(library):
    """Include overlapping memberships, a descendant, an unrelated collection and an unfiled paper."""
    from doi_hunter_web import catalog
    from doi_hunter_web.db import get_conn

    conn = get_conn()
    parent = conn.execute("INSERT INTO collections(name) VALUES ('Thermal studies')").lastrowid
    child = conn.execute("INSERT INTO collections(name,parent_id) VALUES ('Thermal child',?)", (parent,)).lastrowid
    sibling = conn.execute("INSERT INTO collections(name) VALUES ('Drug studies')").lastrowid
    unrelated = conn.execute("INSERT INTO collections(name) VALUES ('Visual studies')").lastrowid
    empty = conn.execute("INSERT INTO collections(name) VALUES ('Empty collection')").lastrowid
    records = [
        ("Shared thermal cocaine", "We tested whether cocaine alters reward seeking. Cocaine reward seeking increased.", "Alice", "focus", [parent, sibling]),
        ("Child thermal cocaine", "We tested whether cocaine alters reward seeking. Cocaine reward seeking increased.", "Alice", "focus", [child]),
        ("Drug cohort", "We tested whether cocaine alters reward seeking. Cocaine reward seeking increased.", "Bob", "other", [sibling]),
        ("Visual cohort", "We examined visual perception. Visual perception concerns image processing.", "Alice", "focus", [unrelated]),
        ("Unfiled visual cohort", "We examined visual perception. Visual perception concerns image processing.", "Carol", "other", []),
    ]
    items = []
    for title, abstract, author, tag, memberships in records:
        item = catalog.create_item({"title": title, "abstract": abstract, "creators": [author], "tags": [tag]})
        for cid in memberships:
            conn.execute("INSERT INTO item_collections VALUES (?,?)", (item["id"], cid))
        items.append(item["id"])
    deleted = catalog.create_item({"title": "Deleted cocaine", "abstract": "Cocaine cocaine cocaine."})
    conn.execute("INSERT INTO item_collections VALUES (?,?)", (deleted["id"], sibling))
    conn.execute("UPDATE items SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (deleted["id"],))
    return {"parent": parent, "child": child, "sibling": sibling, "unrelated": unrelated,
            "empty": empty, "items": items, "deleted": deleted["id"]}


def test_union_deduplicates_overlap_and_nested_selection(scoped_library):
    from doi_hunter_web import insight

    f = scoped_library
    selected = [f["parent"], f["child"], f["sibling"], f["parent"]]
    papers = insight.scope_items(collection_ids=selected)
    assert {p["id"] for p in papers} == set(f["items"][:3])
    assert len(papers) == 3
    direct = insight.scope_items(collection_ids=[f["parent"], f["sibling"]], include_sub=False)
    assert {p["id"] for p in direct} == {f["items"][0], f["items"][2]}


def test_union_remains_intersection_with_author_and_tag(scoped_library):
    from doi_hunter_web import insight

    f = scoped_library
    papers = insight.scope_items(collection_ids=[f["parent"], f["sibling"]], author="Alice", tag="focus")
    assert {p["id"] for p in papers} == set(f["items"][:2])
    assert insight.scope_items(collection_ids=[f["parent"]], author="Bob") == []


def test_explicit_list_precedence_and_empty_reset(scoped_library):
    from doi_hunter_web import insight

    f = scoped_library
    # Supplying an explicit empty selection resets only the collection constraint.
    assert len(insight.scope_items(collection_id=f["parent"], collection_ids=[])) == 5
    assert len(insight.scope_items(collection_ids=[], author="Alice", tag="focus")) == 3
    explicit = insight.scope_items(collection_id=f["parent"], collection_ids=[f["unrelated"]])
    assert [p["id"] for p in explicit] == [f["items"][3]]
    assert insight.scope_items(collection_ids=[f["empty"]]) == []


@pytest.mark.parametrize("invalid", [[0], [-1], [True], ["1"]])
def test_invalid_collection_values_never_become_whole_library(scoped_library, invalid):
    from doi_hunter_web import insight

    with pytest.raises(ValueError):
        insight.scope_items(collection_ids=invalid)


def test_unknown_collection_in_mixed_selection_fails_explicitly(scoped_library):
    from doi_hunter_web import insight

    with pytest.raises(LookupError, match="Unknown collection IDs"):
        insight.scope_items(collection_ids=[scoped_library["parent"], 99999])


@pytest.mark.parametrize("kind", ["concepts", "words", "goals", "methods"])
def test_every_map_preserves_union_and_legacy_scope(scoped_library, kind):
    from doi_hunter_web import insight

    f = scoped_library
    mapper = getattr(insight, kind)
    result = mapper(collection_ids=[f["sibling"], f["parent"], f["sibling"]])
    assert result["scope"]["items"] == 3
    assert result["scope"]["collection_ids"] == sorted([f["parent"], f["sibling"]])
    assert "Thermal studies" in result["scope"]["label"] and "Drug studies" in result["scope"]["label"]
    assert mapper(collection_id=f["parent"])["scope"]["items"] == 2
    assert mapper(collection_ids=[f["parent"]], include_sub=False)["scope"]["items"] == 1


def test_capped_union_members_cannot_leak_into_word_comparison(scoped_library, monkeypatch):
    from doi_hunter_web import insight

    f = scoped_library
    monkeypatch.setattr(insight, "MAX_ITEMS", 2)
    result = insight.words(collection_ids=[f["parent"], f["sibling"]])
    assert result["scope"]["items"] == 2
    assert result["scope"]["comparison_items"] == 2
    assert result["limits"]["scope_capped"] is True
    assert result["limits"]["comparison_capped"] is False
    cocaine = next(w for w in result["words"] if w["word"] == "cocaine")
    assert cocaine["rest_count"] == 0 and cocaine["absent_from_rest"] is True
    assert {int(iid) for iid in result["items"]} == {f["items"][1], f["items"][2]}
    # Exactly two matches is not a truncated scope.
    direct = insight.words(collection_ids=[f["parent"], f["sibling"]], include_sub=False)
    assert direct["limits"]["scope_capped"] is False
    assert direct["limits"]["comparison_capped"] is True


def test_union_of_every_collection_does_not_claim_comparison_when_no_rest(scoped_library):
    from doi_hunter_web import insight
    from doi_hunter_web.db import get_conn

    f = scoped_library
    get_conn().execute("INSERT INTO item_collections VALUES (?,?)", (f["items"][4], f["unrelated"]))
    result = insight.words(collection_ids=[f["parent"], f["sibling"], f["unrelated"]])
    assert result["scope"]["comparison_items"] == 0
    assert not result["scope"]["comparison_available"]
    assert result["outlier_words"] == []


@pytest.fixture()
def insight_client(scoped_library):
    """A read-only TestClient without startup background enrichment workers."""
    from fastapi.testclient import TestClient
    from doi_hunter_web import config
    from doi_hunter_web.server import app

    client = TestClient(app, base_url=f"http://127.0.0.1:{config.PORT}")
    yield client
    client.close()


@pytest.mark.parametrize("kind", ["concepts", "words", "goals", "methods"])
def test_http_repeated_collection_parameters(insight_client, scoped_library, kind):
    f = scoped_library
    response = insight_client.get(f"/api/insight/{kind}", params=[("collection_ids", f["parent"]),
        ("collection_ids", f["sibling"]), ("collection_ids", f["parent"]), ("include_sub", "false")])
    assert response.status_code == 200, response.text
    assert response.json()["scope"]["items"] == 2
    assert response.json()["scope"]["collection_ids"] == sorted([f["parent"], f["sibling"]])


@pytest.mark.parametrize("value,status", [("not-an-id", 422), ("", 422), ("0", 422), ("-1", 422), ("99999", 404)])
def test_http_invalid_collection_parameters_do_not_widen_scope(insight_client, value, status):
    response = insight_client.get("/api/insight/words", params={"collection_ids": value})
    assert response.status_code == status


def test_http_legacy_scalar_and_metadata_intersection(insight_client, scoped_library):
    f = scoped_library
    assert insight_client.get("/api/insight/words", params={"collection_id": f["parent"]}).json()["scope"]["items"] == 2
    response = insight_client.get("/api/insight/words", params=[("collection_ids", f["parent"]),
        ("collection_ids", f["sibling"]), ("author", "Alice"), ("tag", "focus")])
    assert response.status_code == 200
    assert response.json()["scope"]["items"] == 2
