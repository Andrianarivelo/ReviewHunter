"""Exact, hand-checkable incidence graphs for descriptive concept analytics."""
from itertools import combinations

import pytest

from doi_hunter_web.concept_analysis import analyse


def graph(memberships, communities=None, curated=()):
    """Build synthetic metadata/evidence without an application database."""
    communities = communities or {}
    ids = sorted(set().union(*map(set, memberships.values()))) if memberships else []
    papers = [{"id": iid, "title": f"Synthetic paper {iid}"} for iid in ids]
    nodes = [{"id": key, "label": key, "count": len(set(values)), "cluster": communities.get(key, 0),
              "curated": key in curated, "items": [p for p in papers if p["id"] in values],
              "evidence": [{"id": iid, "source": "keywords", "sentence": key} for iid in sorted(set(values))]}
             for key, values in memberships.items()]
    edges = [{"source": a, "target": b, "weight": len(set(memberships[a]) & set(memberships[b]))}
             for a, b in combinations(sorted(memberships), 2) if set(memberships[a]) & set(memberships[b])]
    return nodes, edges, papers


def evaluate(memberships, **kwargs):
    """Evaluate a complete selected graph with unambiguous default caps."""
    nodes, edges, papers = graph(memberships, **kwargs)
    result = analyse(nodes, edges, papers, total_terms=len(nodes), displayed_edges=len(edges))
    return result, nodes, edges


def test_prevalence_jaccard_and_shared_evidence_use_distinct_papers():
    result, nodes, edges = evaluate({"temperature": [1, 2, 3], "dopamine": [2, 3, 4], "rare": [4]})
    link = next(edge for edge in result["strongest_links"] if edge["source"] == "dopamine" and edge["target"] == "temperature")
    assert link["shared_count"] == 2 and link["jaccard"] == 0.5
    assert link["item_ids"] == [2, 3]
    assert {entry["id"] for entry in link["evidence"]} == {2, 3}
    assert {entry["concept_id"] for entry in link["evidence"]} == {"temperature", "dopamine"}
    assert next(node for node in nodes if node["id"] == "temperature")["prevalence"] == 0.75
    assert {node["id"] for node in result["core_themes"]} == {"temperature", "dopamine"}
    assert [node["id"] for node in result["rare_concepts"]] == ["rare"]
    assert all(edge["shared_count"] == edge["weight"] for edge in edges)


def test_broad_metadata_is_retained_but_excluded_from_rankings():
    result, nodes, edges = evaluate({"general": [1, 2, 3], "dopamine": [1, 2], "temperature": [2, 3]}, curated=["general"])
    broad = next(node for node in nodes if node["id"] == "general")
    assert broad["broad_metadata"] and broad["items"] and broad["evidence"]
    assert broad["degree"] == 0
    assert result["summary"]["broad_metadata_count"] == 1
    assert all(edge["source"] != "general" and edge["target"] != "general" for edge in result["strongest_links"])
    assert any(edge["broad_metadata"] for edge in edges)
    assert not evaluate({"general": [1, 2]})[1][0]["broad_metadata"]


def test_bridges_require_multiple_detected_neighbour_communities():
    result, _, _ = evaluate({"bridge": [1, 2], "left": [1], "right": [2]},
                            communities={"bridge": 0, "left": 0, "right": 1})
    bridge = result["bridge_concepts"][0]
    assert bridge["id"] == "bridge" and bridge["participation"] == 0.5
    assert bridge["cross_community_weight"] == 1 and bridge["neighbour_communities"] == [0, 1]
    assert evaluate({"bridge": [1, 2], "left": [1], "right": [2]})[0]["bridge_concepts"] == []


def test_unique_combination_requires_both_components_to_recur():
    result, _, _ = evaluate({"temperature": [1, 2], "dopamine": [1, 3], "singleton": [1]})
    assert len(result["unusual_combinations"]) == 1
    paper = result["unusual_combinations"][0]
    assert paper["id"] == 1 and paper["score"] == 1
    assert paper["eligible_pair_count"] == paper["unique_pair_count"] == 1
    assert paper["pairs"][0]["jaccard"] == pytest.approx(1 / 3)
    assert all(entry["id"] == 1 for entry in paper["evidence"])
    assert paper["items"] == [paper["paper"]]
    assert evaluate({"a": [1], "b": [1], "c": [2]})[0]["unusual_combinations"] == []


def test_missing_metadata_stays_in_prevalence_denominator_and_caps_are_visible():
    nodes, edges, papers = graph({"a": [1, 2], "b": [1, 2], "c": [2]})
    papers.extend([{"id": 3, "title": "No metadata"}, {"id": 4, "title": "No metadata either"}])
    result = analyse(nodes, edges, papers, total_terms=8, displayed_edges=1, scope_capped=True)
    assert nodes[0]["prevalence"] == 0.5
    assert result["summary"]["papers_with_concepts"] == 2
    assert result["limits"]["node_selection_capped"] and result["limits"]["visual_edges_capped"]
    assert result["limits"]["scope_capped"] and len(result["strongest_links"]) == 3


def test_empty_and_single_paper_graphs_do_not_claim_core_or_unusual():
    assert evaluate({})[0]["summary"]["papers"] == 0
    result, _, _ = evaluate({"a": [1], "b": [1]})
    assert not result["core_themes"] and not result["unusual_combinations"]


def test_concepts_endpoint_preserves_graph_and_enriches_real_metadata(library):
    from doi_hunter_web import catalog, insight

    first = catalog.create_item({"title": "Dopamine and temperature", "keywords": ["general", "dopamine", "temperature"]})
    second = catalog.create_item({"title": "Dopamine signal", "keywords": ["general", "dopamine"]})
    result = insight.concepts()
    broad = next(node for node in result["nodes"] if node["id"] == "general")
    assert broad["broad_metadata"] and len(broad["evidence"]) == 2
    assert result["analytics"]["summary"]["papers"] == 2
    assert all("jaccard" in edge and "item_ids" in edge for edge in result["edges"])
    assert {p["id"] for p in broad["items"]} == {first["id"], second["id"]}
    assert result == insight.concepts()
