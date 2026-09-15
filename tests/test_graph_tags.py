"""Concept map and Subjects facet must see v2 tags, not only the legacy subjects / keywords columns.

Items saved by the connector or by identifier carry their keywords as tags (item_tags); the legacy
columns stay empty. Before this fix the map said "No subjects or keywords yet" for such libraries.
"""


def test_subject_graph_and_facets_use_tags(library):
    from doi_hunter_web import catalog, library as lib

    a = catalog.create_item({"item_type": "journalArticle", "title": "Heat and methamphetamine", "doi": "10.1/a",
                             "tags": ["methamphetamine", "hyperthermia", "dopamine"]})
    b = catalog.create_item({"item_type": "journalArticle", "title": "Cocaine and body temperature", "doi": "10.1/b",
                             "tags": ["Cocaine", "hyperthermia"]})
    # A legacy item with the old columns still counts.
    c_id = lib.upsert_item({"title": "Old style item", "doi": "10.1/c", "subjects": ["dopamine"], "keywords": ["reward"]})

    subjects = {s["name"].casefold(): s["count"] for s in lib.list_subjects()}
    assert subjects["hyperthermia"] == 2 and subjects["dopamine"] == 2 and subjects["cocaine"] == 1 and subjects["reward"] == 1

    graph = lib.subject_graph()
    labels = {n["id"]: n for n in graph["nodes"]}
    assert graph["total_terms"] == 5 and graph["scope"]["items"] == 3
    assert labels["hyperthermia"]["count"] == 2 and labels["dopamine"]["count"] == 2
    assert {tuple(sorted((e["source"], e["target"]))) for e in graph["edges"]} >= {("hyperthermia", "methamphetamine"), ("cocaine", "hyperthermia"), ("dopamine", "reward")}
    assert {it["id"] for it in labels["hyperthermia"]["items"]} == {a["id"], b["id"]}

    # Clicking a tag node scopes the map to the items carrying that tag (legacy filter alone finds none).
    focused = lib.subject_graph(subject="hyperthermia")
    assert focused["scope"]["items"] == 2 and focused["nodes"][0]["id"] == "hyperthermia" and focused["nodes"][0]["focused"]
    legacy = lib.subject_graph(subject="reward")
    assert legacy["scope"]["items"] == 1 and c_id in {it["id"] for it in legacy["nodes"][0]["items"]}
