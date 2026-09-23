"""Registry markup and damaged PDF font evidence remain independently verified."""
from types import SimpleNamespace

from doi_hunter_web import fulltext, identifiers, metadata


def payload(title):
    return {"title": title, "doi": "10.1234/article", "item_type": "journalArticle",
            "creators": [{"lastName": "Smith"}, {"lastName": "Jones"}]}


def test_registry_italics_do_not_change_identity_or_return_literal_tags(monkeypatch):
    title = "<i>In Vivo</i>Visualization of Neural Receptors during Physiological Activation"
    page = "In Vivo Visualization of Neural Receptors during Physiological Activation"
    monkeypatch.setattr(fulltext, "_pdf_identifiers", lambda path: ([("doi", "10.1234/article")], [], page))
    monkeypatch.setattr(identifiers, "resolve_doi", lambda doi: payload(title))
    result, source, _ = fulltext._find_verified_metadata({}, "fake.pdf")
    assert result["title"] == page and source == "doi:10.1234/article"


def test_greek_letters_are_not_discarded_during_exact_verification():
    title = "Simultaneous monitoring of gamma-aminobutyric acid in neural tissue"
    assert fulltext._title_in_text(title, title.replace("gamma", "\u03b3"))
    assert not fulltext._title_in_text(title, title.replace("gamma", "\u03b2"))


def test_font_hyphen_glyph_requires_long_title_and_two_author_surnames():
    title = "Drug-induced neuro-respiratory toxicity in the rat: contribution of tolerance and withdrawal syndrome"
    page = title.replace("-", "A") + "\nJane Smith and Alex Jones"
    assert fulltext._verified_title_position(payload(title), page, glyph_fallback=True) >= 0
    assert fulltext._verified_title_position(payload(title), page) == -1
    assert fulltext._verified_title_position(payload(title), page.replace("Jones", "Other"), glyph_fallback=True) == -1
    assert fulltext._verified_title_position(payload(title), page.replace("rat", "mouse"), glyph_fallback=True) == -1


def test_greek_pdf_code_needs_author_corroboration_and_other_letters_exact():
    title = "In vivo simultaneous monitoring of \u03b3-aminobutyric acid and glutamate using microdialysis"
    page = title.replace("\u03b3", "/C227") + "\nJane Smith and Alex Jones"
    hints = [title.replace("\u03b3", "[gamma]")]
    assert fulltext._verified_title_position(payload(title), page, glyph_fallback=True, title_guesses=hints) >= 0
    assert fulltext._verified_title_position(payload(title), page, glyph_fallback=True) == -1
    assert fulltext._verified_title_position(payload(title), page, glyph_fallback=True, title_guesses=[title.replace("\u03b3", "beta")]) == -1
    assert fulltext._verified_title_position(payload(title), page.replace("glutamate", "dopamine"), glyph_fallback=True, title_guesses=hints) == -1
    assert fulltext._verified_title_position(payload(title), page.split("\n")[0], glyph_fallback=True, title_guesses=hints) == -1


def test_lookup_search_cannot_enable_glyph_fallback_without_printed_doi(monkeypatch):
    title = "Drug-induced neuro-respiratory toxicity in the rat: contribution of tolerance and withdrawal syndrome"
    page = title.replace("-", "A") + "\nJane Smith and Alex Jones"
    monkeypatch.setattr(fulltext, "_pdf_identifiers", lambda path: ([], [], page))
    monkeypatch.setattr(metadata, "search_crossref", lambda *a, **kw: [payload(title)])
    assert fulltext._find_verified_metadata({}, "fake.pdf")[0] is None


def test_oup_article_id_is_only_a_candidate_and_other_domains_not_trimmed(monkeypatch):
    page = "Downloaded from https://academic.oup.com/toxsci/advance-article-abstract/doi/10.1093/toxsci/kfy073/4947790\nhttps://other.org/doi/10.1093/toxsci/abc/123"
    reader = SimpleNamespace(is_encrypted=False, metadata={}, pages=[SimpleNamespace(extract_text=lambda: page)])
    monkeypatch.setattr("pypdf.PdfReader", lambda path: reader)
    evidence = fulltext.extract_pdf_evidence("fake.pdf")
    assert evidence["identifiers"][0] == ("doi", "10.1093/toxsci/kfy073")
    assert ("doi", "10.1093/toxsci/abc") not in evidence["identifiers"]
    assert evidence["doi"] == ""


def test_legacy_attribute_xmp_and_producer_title_do_not_override_info():
    class Object(dict):
        def get_object(self):
            return self

    class Stream:
        def get_object(self):
            return self

        def get_data(self):
            return b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" dc:title="el_5565 3187..3196" dc:identifier="doi:10.1234/own"/></rdf:RDF>'''

    fields = fulltext._xmp_fields(SimpleNamespace(trailer={"/Root": Object({"/Metadata": Stream()})}))
    assert fields["identifiers"] == ["doi:10.1234/own"]
    assert fulltext._pdf_title(fields["titles"][0]) == ""


def test_abstract_ends_at_inline_keyword_list_without_including_footer():
    from doi_hunter_web.pdf_ingest import extract_abstract
    paragraph = "An explicitly delimited abstract with source-backed observations. " * 3
    text = "Abstract\n" + paragraph + "\n Key words: Experimental methods; Neural activity\nDownloaded from publisher"
    assert extract_abstract(text) == paragraph.strip()
    assert extract_abstract("Abstract\n" + paragraph + "\nUnlabelled body text") == ""
