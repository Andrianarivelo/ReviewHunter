"""Insight maps (concepts, words, goals, methods) on synthetic papers with a fake full text.

Every detection must come with the sentence that produced it (no hallucinated methods or goals).
"""
import pytest

ABSTRACT_A = ("Methamphetamine is a drug that is often consumed at dance parties where the ambient temperature is high. "
              "The present study determined whether such high ambient temperatures alter intravenous methamphetamine "
              "self-administration in the rat. Male Hooded Wistar rats were trained to self-administer intravenous "
              "methamphetamine under a fixed ratio 1 or progressive ratio schedule at 23 C and then at 30 C. "
              "Methamphetamine self-administration was increased at 30 C under both schedules.")
ABSTRACT_B = ("Cocaine produces hyperthermia in rats. We examined the effects of ambient temperature on body temperature "
              "and locomotor activity after cocaine in male and female Sprague-Dawley rats. Rats received cocaine "
              "intraperitoneally at 20 or 30 C while core temperature was recorded by telemetry.")
FULLTEXT_A = """High ambient temperature increases methamphetamine self-administration
Abstract
""" + ABSTRACT_A + """
Introduction
Ambient temperature is high in nightclubs. The present study determined whether such high ambient temperatures alter intravenous methamphetamine self-administration in the rat.
Materials and methods
Subjects
Male Hooded Wistar rats (n = 24) weighing 300 g were housed under a 12 h light-dark cycle with
water available. First, rats were trained to self-administer intravenous methamphetamine.
Then, core temperature was recorded by telemetry. Rats were randomly assigned to treatment.
The ethics committee approved the experiment. We used repeated-measures ANOVA.
Discussion
A monkey study previously found a different pattern. Monkey findings require further evaluation.
References
Human volunteers. Optogenetics. Optogenetics.
"""


@pytest.fixture()
def papers(library, tmp_path):
    """Index real fixture attachments in a throwaway library through the public indexer."""
    from doi_hunter_web import catalog, fulltext
    from doi_hunter_web.db import get_conn

    data = [("Ambient temperature", ABSTRACT_A, ["temperatures"], FULLTEXT_A),
            ("Ambient temperatures", ABSTRACT_B, ["temperature"], "Materials and methods\nFemale rats (n = 12) received cocaine at 2.5 mg/kg. Blinding was not performed.\nResults\nCore temperature increased."),
            ("Visual perception", "Human participants viewed images. Images activated visual cortex. Visual cortex responses predicted perception.", ["vision"], ""),
            ("Unindexed report", "", [], "")]
    created = []
    for index, (title, abstract, tags, text) in enumerate(data):
        item = catalog.create_item({"title": title, "abstract": abstract, "tags": tags,
                                    "creators": ["Researcher, A" if index < 2 else "Other, B"]})
        if text:
            path = tmp_path / f"paper-{index}.txt"
            path.write_text(text, encoding="utf-8")
            catalog.register_file(item["id"], path)
            assert fulltext.index_item(item["id"]) > 0
        created.append(item)
    conn = get_conn()
    parent = conn.execute("INSERT INTO collections(name) VALUES ('Temperature')").lastrowid
    child = conn.execute("INSERT INTO collections(name, parent_id) VALUES ('Child', ?)", (parent,)).lastrowid
    conn.execute("INSERT INTO item_collections VALUES (?, ?)", (created[0]["id"], parent))
    conn.execute("INSERT INTO item_collections VALUES (?, ?)", (created[1]["id"], child))
    return created, parent


def test_concepts_plural_clusters_and_evidence(papers):
    from doi_hunter_web import insight

    result = insight.concepts()
    temperature = next(n for n in result["nodes"] if n["id"] == "temperature")
    assert temperature["count"] == 2
    assert not any(n["id"] == "temperatures" for n in result["nodes"])
    assert result["clusters"] == insight.concepts()["clusters"]
    assert all(n["evidence"] and all(e["sentence"] for e in n["evidence"]) for n in result["nodes"])


@pytest.mark.parametrize("kind", ["concepts", "words", "goals", "methods"])
def test_map_collection_and_author_scope(papers, kind):
    from doi_hunter_web import insight

    items, parent = papers
    mapper = getattr(insight, kind)
    assert mapper(collection_id=parent)["scope"]["items"] == 2
    assert mapper(collection_id=parent, include_sub=False)["scope"]["items"] == 1
    assert mapper(author="Researcher")["scope"]["items"] == 2
    assert mapper(tag="vision")["scope"]["items"] == 1


def test_word_frequency_distinctiveness_and_fulltext_source(papers):
    from doi_hunter_web import insight

    items, parent = papers
    result = insight.words(collection_id=parent)
    words = {w["word"]: w for w in result["words"]}
    assert words["temperature"]["count"] >= 3
    assert words["temperature"]["distinct"] > 0
    assert all(e["source"] == "abstract" for w in words.values() for e in w["evidence"])
    full = insight.words(source="fulltext")
    assert full["limits"]["no_fulltext"] == 2
    assert "visual" not in {w["word"] for w in full["words"]}


def test_goals_no_invented_aim_and_pairs(papers):
    from doi_hunter_web import insight

    result = insight.goals()
    first = next(p for p in result["papers"] if p["title"] == "Ambient temperature")
    assert first["goal_type"] == "test"
    assert any("ambient temperature" in p["factor"] and "methamphetamine" in p["outcome"] for p in first["pairs"])
    visual = next(p for p in result["papers"] if p["title"] == "Visual perception")
    assert visual["aims"] == [] and visual["goal_type"] == "unknown"
    assert all(p["evidence"] for p in first["pairs"])
    assert insight._clean_phrase("meth self-administration using operant chambers") == "methamphetamine self-administration"


def test_methods_evidence_reporting_and_honest_gaps(papers):
    from doi_hunter_web import insight

    result = insight.methods()
    first = next(p for p in result["papers"] if p["title"] == "Ambient temperature")
    detected = {d["id"]: d for d in first["detected"]}
    assert {"rat", "drug_iv", "selfadmin", "anova", "temperature"} <= detected.keys()
    assert "nhp" not in detected and "human" not in detected and "optogenetics" not in detected
    assert detected["anova"]["confidence"] == "high"
    assert detected["temperature"]["sequence_cue"] == "Then"
    assert all(d["match"].lower() in d["evidence"].lower() for d in detected.values())
    checks = {c["id"]: c for c in first["checks"]}
    assert checks["n"]["found"] and checks["ethics"]["found"] and checks["random"]["found"]
    second = next(p for p in result["papers"] if p["title"] == "Ambient temperatures")
    blind = next(c for c in second["checks"] if c["id"] == "blind")
    assert not blind["found"] and blind["status"] == "explicitly_absent"
    assert result["insights"]["gaps"]
    assert "not performed" in result["limits"]["interpretation"]


def test_body_mentions_and_negated_methods():
    from doi_hunter_web.insight import _detect_methods

    found = {}
    _detect_methods("Rats were studied. Rats learned the task. A monkey was mentioned. No optogenetics was used.", "medium", found, 100, min_count=2)
    assert "rat" in found and "nhp" not in found and "optogenetics" not in found


def test_taxonomy_size_and_ambiguous_equipment():
    from doi_hunter_web.insight import _detect_methods
    from doi_hunter_web.method_taxonomy import METHODS

    assert len(METHODS) >= 100
    assert len({m["id"] for m in METHODS}) == len(METHODS)
    found = {}
    _detect_methods("Saline was stored in serum vials. An intravenous catheter guide cannula was used. Food was available ad libitum.", "high", found, 100)
    assert not {"pk", "intracranial", "diet"} & found.keys()


def test_goal_pair_retains_outcome_qualifier_and_title_end(library):
    """Outcome phrases retain 'of' qualifiers and pairs can end at the title boundary."""
    from doi_hunter_web import catalog, insight

    catalog.create_item({"title": "Effect of temperature on acquisition of meth self-administration",
                         "abstract": "Our hypothesis was that temperature matters. We aimed to test the effect of temperature on acquisition of meth self-administration using operant chambers."})
    result = insight.goals()
    assert any(link["outcome"] == "acquisition of methamphetamine self-administration" for link in result["links"])
    assert not any("chambers" in link["outcome"] for link in result["links"])


def test_phrase_does_not_join_sentences():
    from doi_hunter_web.insight import _phrases

    assert "rats temperature" not in _phrases("Rats. Temperature was measured.")


def test_anatomy_and_single_anaesthetic_dose_are_not_analysis_or_design():
    """An anatomical direction is not a posterior distribution; a dose is not a dose-response study."""
    from doi_hunter_web.insight import _detect_methods

    found = {}
    _detect_methods("An incision was made posterior to the xyphoid space. Animals received ketamine at doses of 50 mg/kg and xylazine at 5 mg/kg for surgery.", "high", found, 200)
    assert "bayes" not in found and "doseresponse" not in found
    _detect_methods("We estimated the posterior distribution. The experiment measured dose-response curves.", "high", found, 100)
    assert {"bayes", "doseresponse"} <= found.keys()


def test_parenthesized_drug_route_is_recognized():
    from doi_hunter_web.insight import _detect_methods

    found = {}
    _detect_methods("Animals received the drug (2.5 or 5 mg/kg, i.p.).", "high", found, 50)
    assert found["drug_systemic"]["match"] == "i.p."


def test_complete_export_members_and_goal_count_beyond_preview(library):
    from doi_hunter_web import catalog, insight

    for i in range(14):
        catalog.create_item({"title": f"Report {i}", "tags": ["temperature"]})
    node = next(n for n in insight.concepts()["nodes"] if n["id"] == "temperature")
    assert len(node["items"]) == node["count"] == 14
    assert len(insight.goals()["insights"]["no_aim_found"]) == 14


def test_goal_fulltext_scan_budget_is_disclosed(papers):
    from doi_hunter_web import insight

    items, _ = papers
    assert len(insight.fulltext_of(items[0]["id"], max_chars=20)) <= 20
    assert insight.goals()["limits"]["fulltext_scan_chars"] == insight.GOAL_TEXT_CHARS


def test_api_forwards_scope_and_rejects_unknown_kind(papers):
    """Call the real async endpoint using the isolated fixture's database."""
    import asyncio
    from fastapi import HTTPException
    from doi_hunter_web.server import api_insight

    _, parent = papers
    result = asyncio.run(api_insight("methods", collection_id=parent, include_sub=False))
    assert result["scope"]["items"] == 1
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_insight("missing"))
    assert caught.value.status_code == 404
