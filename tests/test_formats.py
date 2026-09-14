"""Import/export formats: BibTeX, RIS, CSL-JSON, Word XML round trips."""
import json
import xml.dom.minidom

BIB = r"""
@article{schultz1997neural,
  title = {A Neural Substrate of Prediction and Reward},
  author = {Schultz, Wolfram and Dayan, Peter and Montague, P. Read},
  journal = {Science},
  volume = {275},
  number = {5306},
  pages = {1593--1599},
  year = {1997},
  month = mar,
  doi = {10.1126/science.275.5306.1593},
  keywords = {dopamine, reinforcement learning}
}
@phdthesis{gerstner1993,
  title = {Kodierung und Signal{\"u}bertragung in neuronalen Systemen},
  author = {Gerstner, Wulfram},
  school = {TU M{\"u}nchen},
  year = {1993}
}
@misc{vaswani2017,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and {Google Brain}},
  eprint = {1706.03762},
  archivePrefix = {arXiv},
  year = {2017}
}
"""

RIS = """TY  - JOUR
TI  - Toward an integration of deep learning and neuroscience
AU  - Marblestone, Adam H.
AU  - Wayne, Greg
PY  - 2016/09/14/
T2  - Frontiers in Computational Neuroscience
VL  - 10
SP  - 94
DO  - 10.3389/fncom.2016.00094
KW  - deep learning
ER  -

TY  - BOOK
TI  - Theoretical Neuroscience
AU  - Dayan, Peter
AU  - Abbott, L. F.
PB  - MIT Press
PY  - 2001
SN  - 9780262041997
ER  -
"""


def test_bibtex_import(library):
    from doi_hunter_web import formats

    items = formats.parse(BIB)
    assert [i["item_type"] for i in items] == ["journalArticle", "thesis", "preprint"]
    art = items[0]
    assert art["citation_key"] == "schultz1997neural"
    assert art["creators"][2] == {"creatorType": "author", "lastName": "Montague", "firstName": "P. Read"}
    assert art["pages"] == "1593-1599" and art["date"] == "1997-03" and art["tags"] == ["dopamine", "reinforcement learning"]
    assert items[1]["title"] == "Kodierung und Signalübertragung in neuronalen Systemen"
    assert items[1]["publisher"] == "TU München" and items[1]["fields"]["thesisType"] == "PhD thesis"
    assert items[2]["creators"][1] == {"creatorType": "author", "name": "Google Brain"}
    assert items[2]["fields"]["archiveID"] == "arXiv:1706.03762"


def test_ris_import(library):
    from doi_hunter_web import formats

    items = formats.parse(RIS)
    assert len(items) == 2
    assert items[0]["journal"] == "Frontiers in Computational Neuroscience"
    assert items[0]["date"] == "2016-09-14" and items[0]["doi"] == "10.3389/fncom.2016.00094"
    assert items[1]["item_type"] == "book" and items[1]["isbn"] == ["9780262041997"]


def test_round_trips(library):
    from doi_hunter_web import catalog as c, formats

    created = [c.create_item(p) for p in formats.parse(BIB)]
    items = [c.get_item(i["id"]) for i in created]

    bib = formats.export_bibtex(items)
    again = formats.parse(bib, "bibtex")
    assert [a["title"] for a in again] == [i["title"] for i in items]
    assert again[0]["creators"] == items[0]["creators"]
    assert "{fMRI}" in formats.export_bibtex([{**items[0], "title": "An fMRI study"}])

    ris = formats.export_ris(items)
    again = formats.parse(ris, "ris")
    assert [a["title"] for a in again] == [i["title"] for i in items]
    assert again[0]["pages"] == "1593-1599"

    csl = json.loads(formats.export_csljson(items))
    assert csl[0]["type"] == "article-journal" and csl[0]["issued"] == {"date-parts": [[1997, 3]]}
    assert csl[1]["type"] == "thesis" and csl[1]["genre"] == "PhD thesis"
    again = formats.parse(json.dumps(csl), "csljson")
    assert again[0]["citation_key"] == "schultz1997neural" and again[0]["volume"] == "275"

    xml_text = formats.export_word_xml(items)
    dom = xml.dom.minidom.parseString(xml_text.encode("utf-8"))  # must be well-formed
    assert dom.getElementsByTagName("b:Source").length == 3
    assert "<b:Last>Schultz</b:Last>" in xml_text and "<b:Last>Google Brain</b:Last>" in xml_text
    solo = formats.export_word_xml([{"id": 9, "title": "Report", "creators": [{"creatorType": "author", "name": "WHO"}]}])
    assert "<b:Corporate>WHO</b:Corporate>" in solo

    csv_text = formats.export_csv(items)
    assert csv_text.splitlines()[0].startswith("Key,Item Type")


def test_detect_format():
    from doi_hunter_web import formats

    assert formats.detect_format(BIB) == "bibtex"
    assert formats.detect_format(RIS) == "ris"
    assert formats.detect_format('[{"type":"book"}]') == "csljson"
    assert formats.detect_format("10.1038/nature14539\nAttention is all you need") == "identifiers"


def test_identifier_classification():
    from doi_hunter_web import identifiers as ids

    assert ids.classify("https://doi.org/10.1038/NATURE14539.") == ("doi", "10.1038/nature14539")
    assert ids.classify("arXiv:1706.03762v5") == ("arxiv", "1706.03762v5")
    assert ids.classify("1706.03762") == ("arxiv", "1706.03762")
    assert ids.classify("978-0-262-04199-7") == ("isbn", "9780262041997")
    assert ids.classify("0262041995")[0] == "isbn"
    assert ids.classify("PMID: 9054347") == ("pmid", "9054347")
    assert ids.classify("Theoretical neuroscience") == ("title", "Theoretical neuroscience")
    assert not ids.isbn_valid("9780262041990")


def test_fulltext_index_and_doi_detection(library, sample_pdf):
    from doi_hunter_web import catalog as c, fulltext

    it = c.create_item({"title": "Imported PDF"})
    att = c.register_file(it["id"], sample_pdf)
    assert fulltext.index_attachment(att) > 10
    hits = c.query_items(q="prediction", mode="everything")
    assert [h["id"] for h in hits] == [it["id"]]
    assert c.query_items(q="prediction", mode="fields") == []
    ids, _guesses, _text = fulltext._pdf_identifiers(str(sample_pdf))
    assert ("doi", "10.1234/rh.test.5") in ids
