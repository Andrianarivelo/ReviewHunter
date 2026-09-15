"""Create a disposable, explicitly synthetic insight-map demonstration library.

Run this script before ``ui_snapshot.py --insights``. The generated library stays
beneath the requested Temp/claude directory; an existing database is never edited.
The text attachments exercise the production full-text indexing path without
implying that these invented teaching examples are published research.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "claude"

# Scientific phrases are intentionally repeated across documents to exercise
# frequency, distinctiveness, co-occurrence, and conservative method detection.
PAPERS = [
    {
        "title": "Ambient temperature and stimulant self-administration",
        "authors": ["Example, Alice"],
        "abstract": "We examined the effect of ambient temperature on methamphetamine self-administration. Male rats self-administered methamphetamine under a progressive ratio schedule. Ambient temperatures increased self-administration.",
        "keywords": ["ambient temperature", "self-administration"],
        "methods": "Male Wistar rats (n = 24) were randomly assigned to treatment groups. Methamphetamine was administered intravenously at 0.1 mg/kg. Rats performed operant self-administration under a progressive ratio schedule. Body temperature was measured by telemetry. A two-way ANOVA compared temperature groups. The animal ethics committee approved the study.",
        "collection": "parent",
    },
    {
        "title": "Ambient temperatures and locomotor activity",
        "authors": ["Example, Alice", "Example, Bob"],
        "abstract": "We investigated whether ambient temperature alters locomotor activity. Male and female rats received cocaine. Ambient temperature and body temperature were measured using telemetry.",
        "keywords": ["ambient temperatures", "locomotor activity"],
        "methods": "Male and female Sprague-Dawley rats (n = 32) received cocaine intraperitoneally at 10 mg/kg. Locomotor activity was measured in an open field. Core temperature was recorded by telemetry. Investigators were blinded to treatment. Repeated measures ANOVA was used for analysis.",
        "collection": "parent",
    },
    {
        "title": "Temperature effects on dopamine release",
        "authors": ["Example, Carol"],
        "abstract": "Our aim was to measure the effect of ambient temperature on dopamine release. Mice received amphetamine. Dopamine release was measured using microdialysis and high performance liquid chromatography.",
        "keywords": ["ambient temperature", "dopamine release"],
        "methods": "Male mice (n = 18) were assigned to amphetamine treatment. Microdialysis probes measured dopamine release. High performance liquid chromatography quantified dopamine. First, mice were trained in an operant chamber. Subsequently, dopamine was sampled after drug administration. Linear regression related temperature to dopamine concentration.",
        "collection": "child",
    },
    {
        "title": "Human working memory and visual attention",
        "authors": ["Example, Dana"],
        "abstract": "We compared working memory and visual attention in human participants. Electroencephalography measured event-related potentials during visual attention and working memory tasks.",
        "keywords": ["working memory", "visual attention"],
        "methods": "Human participants (n = 40) provided informed consent. Electroencephalography recorded event-related potentials. Participants completed a working memory task. A power analysis determined sample size. Data are available in an open repository. A mixed effects model assessed performance.",
        "collection": None,
    },
]


def create_fixture(destination: Path) -> dict:
    """Seed a new temporary library and return reproducible scope identifiers."""
    destination = destination.resolve()
    if not destination.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError(f"Fixture must stay beneath {TEMP_ROOT}")
    if (destination / "library.db").exists():
        raise ValueError("Refusing to change an existing database; choose a new fixture path")
    destination.mkdir(parents=True, exist_ok=True)
    os.environ["REVIEWHUNTER_HOME"] = str(destination)
    for name in ("REVIEWHUNTER_PAPERS_DIR", "DOIHUNTER_PAPERS_DIR"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(ROOT))
    from doi_hunter_web import catalog, db, fulltext, library

    db.reset_connections()
    parent = library.create_collection("Temperature_addiction (synthetic)")
    child = library.create_collection("Dopamine (synthetic)", parent_id=parent["id"])
    collections = {"parent": parent["id"], "child": child["id"]}
    ids = []
    for index, paper in enumerate(PAPERS, 1):
        item_id = library.upsert_item({
            "title": paper["title"], "authors": paper["authors"],
            "abstract": paper["abstract"], "keywords": paper["keywords"],
            "year": 2020 + index, "status": "metadata", "source_query": "synthetic UI fixture",
        })
        path = destination / f"synthetic-paper-{index}.txt"
        text = f"{paper['title']}\nAbstract\n{paper['abstract']}\nIntroduction\nA monkey study is cited once for context.\nMaterials and methods\n{paper['methods']}\nResults\nSynthetic results are provided only to test the interface.\nReferences\n"
        path.write_text(text, encoding="utf-8")
        catalog.register_file(item_id, path, title="Synthetic Methods-section text")
        if fulltext.index_item(item_id) < 100:
            raise RuntimeError("Synthetic document was not indexed")
        if paper["collection"]:
            library.assign_to_collection(item_id, collections[paper["collection"]])
        catalog.add_tag(item_id, "temperature" if paper["collection"] else "cognition")
        ids.append(item_id)
    result = {"home": str(destination), "synthetic": True, "items": ids, "collections": collections,
              "titles": [paper["title"] for paper in PAPERS],
              "parent_items": ids[:2], "descendant_items": ids[:3], "port": 8775}
    (destination / "fixture.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    db.reset_connections()
    return result


def main() -> None:
    """Create a new unique fixture unless a safe, unused destination is provided."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    destination = args.home or Path(tempfile.mkdtemp(prefix="insight-ui-", dir=TEMP_ROOT))
    print(json.dumps(create_fixture(destination), indent=2))


if __name__ == "__main__":
    main()
