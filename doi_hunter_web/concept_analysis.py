"""Descriptive analytics for the observed concept-to-paper incidence graph.

No metric here estimates scientific novelty, significance, causality or a research
gap. Rankings describe the selected concepts in the currently analysed papers.
Source metadata and evidence are carried through unchanged.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

# Conservative display hints, not deletion rules. Domain terms remain untouched.
BROAD_METADATA_TERMS = frozenset({
    "general", "miscellaneous", "other", "unspecified", "unclassified", "multidisciplinary",
    "general medicine", "general science", "general neuroscience", "general psychology",
})
CORE_PREVALENCE = 0.5
RANK_LIMIT = 20
PAPER_RANK_LIMIT = 12
PAIR_EXAMPLE_LIMIT = 6


def _node_record(node: dict) -> dict:
    """Copy an analytical node without modifying its source evidence or labels."""
    return {key: node[key] for key in ("id", "label", "count", "cluster", "prevalence",
            "degree", "weighted_degree", "items", "evidence", "broad_metadata")}


def analyse(nodes: list[dict], edges: list[dict], papers: list[dict], *,
            total_terms: int, displayed_edges: int, scope_capped: bool = False) -> dict:
    """Annotate graph metrics and return deterministic, evidence-bearing rankings.

    ``edges`` must contain all edges among the selected nodes, before a visual
    edge cap. Paper membership, rather than edge weights supplied by callers,
    determines shared counts and Jaccard overlap. Each paper counts once.
    """
    by_id = {node["id"]: node for node in nodes}
    memberships = {key: {paper["id"] for paper in node["items"]} for key, node in by_id.items()}
    total_papers = len({paper["id"] for paper in papers})
    for key, node in by_id.items():
        broad = bool(node.get("curated") and str(key).casefold().strip() in BROAD_METADATA_TERMS)
        node.update({"prevalence": len(memberships[key]) / total_papers if total_papers else 0,
                     "broad_metadata": broad, "broad_reason": "Broad indexing label" if broad else "",
                     "degree": 0, "weighted_degree": 0})

    neighbours: dict[str, dict[str, int]] = defaultdict(dict)
    clean_edges = []
    pair_info = {}
    for edge in edges:
        a, b = edge["source"], edge["target"]
        shared = memberships[a] & memberships[b]
        union = memberships[a] | memberships[b]
        broad = by_id[a]["broad_metadata"] or by_id[b]["broad_metadata"]
        edge.update({"shared_count": len(shared), "jaccard": len(shared) / len(union) if union else 0,
                     "item_ids": sorted(shared), "broad_metadata": broad})
        if not broad and shared:
            neighbours[a][b] = neighbours[b][a] = len(shared)
            clean_edges.append(edge)
            pair_info[tuple(sorted((a, b)))] = edge
    for key, node in by_id.items():
        node["degree"] = len(neighbours[key])
        node["weighted_degree"] = sum(neighbours[key].values())

    meaningful = [node for node in nodes if not node["broad_metadata"]]
    core = sorted((node for node in meaningful if node["count"] >= 2 and node["prevalence"] >= CORE_PREVALENCE),
                  key=lambda node: (-node["count"], -node["weighted_degree"], node["id"]))
    rare = sorted((node for node in meaningful if node["count"] == 1), key=lambda node: node["id"])
    peripheral = sorted((node for node in meaningful if node["degree"] <= 1),
                        key=lambda node: (node["degree"], node["count"], node["id"]))
    strongest = sorted(clean_edges, key=lambda edge: (-edge["shared_count"], -edge["jaccard"], edge["source"], edge["target"]))

    def link_record(edge: dict) -> dict:
        """Attach both independently observed source cues for every shared paper."""
        a, b = edge["source"], edge["target"]
        ids = set(edge["item_ids"])
        return {**edge, "source_label": by_id[a]["label"], "target_label": by_id[b]["label"],
                "evidence": [{**entry, "concept_id": key} for key in (a, b)
                             for entry in by_id[key]["evidence"] if entry["id"] in ids]}

    bridges = []
    for node in meaningful:
        weights = Counter()
        for other, weight in neighbours[node["id"]].items():
            weights[by_id[other]["cluster"]] += weight
        # One neighbouring community supplies no evidence of bridging communities.
        if len(weights) < 2 or not any(cluster != node["cluster"] for cluster in weights):
            continue
        strength = sum(weights.values())
        bridges.append({**_node_record(node), "neighbour_communities": sorted(weights),
                        "cross_community_weight": sum(weight for cluster, weight in weights.items() if cluster != node["cluster"]),
                        "participation": 1 - sum((weight / strength) ** 2 for weight in weights.values())})
    bridges.sort(key=lambda node: (-node["participation"], -node["cross_community_weight"], node["id"]))

    # A singleton concept alone does not make a combination analytically unusual.
    # Restrict candidates to pairs whose two concepts independently recur.
    recurring = {node["id"] for node in meaningful if node["count"] >= 2}
    paper_terms: dict[int, list[str]] = defaultdict(list)
    for key in sorted(recurring):
        for paper_id in memberships[key]:
            paper_terms[paper_id].append(key)
    unusual = []
    for paper in papers:
        candidates = [pair_info[pair] for pair in combinations(paper_terms[paper["id"]], 2) if pair in pair_info]
        unique_pairs = [edge for edge in candidates if edge["shared_count"] == 1]
        if not unique_pairs:
            continue
        unique_pairs.sort(key=lambda edge: (edge["jaccard"], edge["source"], edge["target"]))
        examples = [link_record(edge) for edge in unique_pairs[:PAIR_EXAMPLE_LIMIT]]
        unusual.append({**paper, "paper": paper, "items": [paper],
                        "evidence": [entry for pair in examples for entry in pair["evidence"]],
                        "score": len(unique_pairs) / len(candidates),
                        "unique_pair_count": len(unique_pairs), "eligible_pair_count": len(candidates),
                        "pairs": examples,
                        "pair_examples_capped": len(unique_pairs) > PAIR_EXAMPLE_LIMIT})
    unusual.sort(key=lambda paper: (-paper["score"], -paper["unique_pair_count"], paper["id"]))
    covered_ids = set().union(*(memberships[node["id"]] for node in meaningful)) if meaningful else set()
    return {
        "summary": {"papers": total_papers, "papers_with_concepts": len(covered_ids),
                    "selected_concepts": len(nodes), "analytical_concepts": len(meaningful),
                    "broad_metadata_count": len(nodes) - len(meaningful), "cooccurring_pairs": len(clean_edges),
                    "communities": len({node["cluster"] for node in meaningful})},
        "core_themes": [_node_record(node) for node in core[:RANK_LIMIT]],
        "strongest_links": [link_record(edge) for edge in strongest[:RANK_LIMIT]],
        "bridge_concepts": bridges[:RANK_LIMIT],
        "rare_concepts": [_node_record(node) for node in rare[:RANK_LIMIT]],
        "peripheral_concepts": [_node_record(node) for node in peripheral[:RANK_LIMIT]],
        "unusual_combinations": unusual[:PAPER_RANK_LIMIT],
        "candidate_counts": {"core_themes": len(core), "strongest_links": len(strongest), "bridge_concepts": len(bridges),
                             "rare_concepts": len(rare), "peripheral_concepts": len(peripheral), "unusual_combinations": len(unusual)},
        "limits": {"node_selection_capped": total_terms > len(nodes), "visual_edges_capped": len(edges) > displayed_edges,
                   "all_selected_edges": len(edges), "displayed_edges": displayed_edges, "scope_capped": scope_capped,
                   "rank_limit": RANK_LIMIT, "paper_rank_limit": PAPER_RANK_LIMIT, "pair_example_limit": PAIR_EXAMPLE_LIMIT},
        "semantics": {
            "basis": "Descriptive co-occurrence in retained tags, keywords and title/abstract phrases. No full-text concept scan.",
            "prevalence": "Fraction of analysed scoped papers containing the concept; missing metadata remains in the denominator.",
            "core_themes": "Concepts present in at least half of scoped papers and at least two papers.",
            "strongest_links": "Ranked by shared paper count, then Jaccard overlap: shared papers divided by papers containing either concept.",
            "bridges": "Weighted participation across the detected neighbour communities, 1 minus the sum of squared community weight fractions. Community assignments are graph-dependent.",
            "peripheral": "At most one neighbour among selected non-broad concepts; rarity means exactly one paper.",
            "unusual_combinations": "Pairs occurring in one paper while both component concepts occur in at least two. Score is the fraction of that paper's eligible pairs meeting this rule. Descriptive rarity, not novelty or a statistical outlier.",
            "broad_metadata": "Broad indexing labels remain in source data and graph exports but are excluded from analytical rankings and degree calculations.",
            "limits": "Analytics use selected nodes and all their edges before the visual edge cap. Term extraction, node selection and scope caps can change rankings.",
        },
    }
