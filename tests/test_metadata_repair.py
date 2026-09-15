"""Metadata quality fixes reported on a real library (2026-09-14).

1. PDF text extraction split an IEEE DOI ("10.1 109/ACCESS...") and the title guess was the
   journal banner ("Received January 8, 2020..."), so 'Retrieve Metadata' found nothing.
2. Abstracts scraped from ScienceDirect started with a glued heading ("AbstractSubstance use...").
3. Items saved from the browser connector without a PDF never looked for a legal open-access copy.

Network calls are replaced by fakes: tests are deterministic and never touch the user's library.
"""
from types import SimpleNamespace

# First page text exactly as pypdf extracted it from the user's IEEE Access PDF (header only).
IEEE_PAGE = (
    "Received January 8, 2020, accepted January 17, 2020, date of publication January 21, 2020, "
    "date of current version January 28, 2020.\n"
    "Digital Object Identifier 10.1 109/ACCESS.2020.2968390\n"
    "Clustered Hybrid Wind Power Prediction\n"
    "Model Based on ARMA, PSO-SVM,\n"
    "and Clustering Methods\n"
    "YURONG WANG\n 1, (Member, IEEE), DONGCHUAN WANG\n 2,\n"
    "ABSTRACT Wind power prediction is the key technology...\n"
)
TRUE_TITLE = "Clustered Hybrid Wind Power Prediction Model Based on ARMA, PSO-SVM, and Clustering Methods"


# ----------------------------------------------------------------------------- pure helpers

def test_repair_broken_doi_prefix():
    from doi_hunter_web import identifiers

    assert identifiers.extract_identifiers(IEEE_PAGE) == [("doi", "10.1109/access.2020.2968390")]
    fixed = identifiers.repair_broken_dois("a 10. 1038/nature1 and 10.1016/j.x.2017 b")
    assert "10.1038/nature1" in fixed and "10.1016/j.x.2017" in fixed


def test_clean_abstract_heading_only():
    from doi_hunter_web.catalog import clean_abstract

    assert clean_abstract("AbstractSubstance use disorder") == "Substance use disorder"
    assert clean_abstract("Abstract: We show") == "We show"
    assert clean_abstract("ABSTRACT Wind power") == "Wind power"
    assert clean_abstract("Summary Background x") == "Background x"
    # Real words that merely start with the heading are kept.
    assert clean_abstract("Abstraction is key") == "Abstraction is key"
    assert clean_abstract("ABSTRACTION is key") == "ABSTRACTION is key"
    assert clean_abstract("Summarizing data") == "Summarizing data"


def test_title_verification_ignores_line_breaks():
    from doi_hunter_web import fulltext

    assert fulltext._title_in_text(TRUE_TITLE, IEEE_PAGE)
    assert not fulltext._title_in_text("Wind Power Prediction Based on the Clustered Combination", IEEE_PAGE)
    assert not fulltext._title_in_text("Methods", IEEE_PAGE)  # too short to verify


# ----------------------------------------------------------------------------- retrieval logic

def _fake_payload(doi, title):
    return {"doi": doi, "title": title, "item_type": "journalArticle", "journal": "IEEE Access",
            "creators": [{"creatorType": "author", "lastName": "Wang", "firstName": "Yurong"}]}


def test_doi_in_header_is_accepted(monkeypatch):
    from doi_hunter_web import fulltext, identifiers

    monkeypatch.setattr(fulltext, "_pdf_identifiers",
                        lambda p: (identifiers.extract_identifiers(IEEE_PAGE), [], IEEE_PAGE))
    monkeypatch.setattr(identifiers, "resolve_doi", lambda d: _fake_payload(d, TRUE_TITLE))
    payload, source, _ = fulltext._find_verified_metadata({"title": "x.pdf"}, "x.pdf")
    assert payload["doi"] == "10.1109/access.2020.2968390" and source.startswith("doi:")


def test_cited_reference_doi_is_rejected(monkeypatch):
    """A DOI far below the header whose title is not printed in the PDF is a citation, not the paper."""
    from doi_hunter_web import fulltext, identifiers, metadata

    page = "Some Unrelated Paper Title About Cortex\n" + "x " * 1000 + "\nReferences\n[1] doi 10.1234/cited.1\n"
    monkeypatch.setattr(fulltext, "_pdf_identifiers",
                        lambda p: (identifiers.extract_identifiers(page), [], page))
    monkeypatch.setattr(identifiers, "resolve_doi", lambda d: _fake_payload(d, "A Completely Different Cited Work"))
    monkeypatch.setattr(metadata, "search_crossref", lambda q, rows=5: [])
    payload, _, _ = fulltext._find_verified_metadata({"title": "Untitled"}, "x.pdf")
    assert payload is None


def test_crossref_candidate_must_be_printed_in_pdf(monkeypatch):
    from doi_hunter_web import fulltext, identifiers, metadata

    page = IEEE_PAGE.replace("Digital Object Identifier 10.1 109/ACCESS.2020.2968390\n", "")
    monkeypatch.setattr(fulltext, "_pdf_identifiers", lambda p: ([], [], page))
    monkeypatch.setattr(metadata, "search_crossref", lambda q, rows=5: [
        {"title": "Wind Power Prediction Based on the Clustered Combination of ARMA and PSO-SVM Methods",
         "doi": "10.1109/ispec48194.2019.8975127"},
        {"title": TRUE_TITLE, "doi": "10.1109/access.2020.2968390"},
    ])
    monkeypatch.setattr(identifiers, "resolve_doi", lambda d: _fake_payload(d, TRUE_TITLE))
    payload, source, _ = fulltext._find_verified_metadata({"title": "file"}, "x.pdf")
    assert payload["doi"] == "10.1109/access.2020.2968390"  # the similar 2019 conference paper is skipped
    assert source.startswith("title printed in PDF")


# ----------------------------------------------------------------------------- open-access fallback

def test_open_access_fallback_attaches_only_real_pdf(library, monkeypatch, sample_pdf):
    from doi_hunter_web import catalog, library as lib, sources

    item = catalog.create_item({"title": "Heat and ED visits", "doi": "10.1016/j.envint.2023.108233"})
    monkeypatch.setattr(sources, "_open_access_candidates", lambda doi: [
        sources.Resolved(url="https://repo.example/landing", source="Open access (landing page)"),
        sources.Resolved(url="https://repo.example/paper.pdf", source="Open access (Repo)"),
    ])
    bodies = {"https://repo.example/landing": b"<html>login</html>",
              "https://repo.example/paper.pdf": sample_pdf.read_bytes()}
    # The fallback downloads through the browser-like transport (transport.get), not plain requests.
    monkeypatch.setattr(lib.transport, "get",
                        lambda url, **kw: SimpleNamespace(status_code=200, content=bodies[url]))
    result = lib.fetch_open_access_pdf(item["id"])
    assert result["ok"] and result["url"] == "https://repo.example/paper.pdf"
    files = [a for a in catalog.list_attachments(item["id"]) if a["kind"] == "file"]
    assert len(files) == 1
    # Second call is a no-op: never attach twice.
    assert lib.fetch_open_access_pdf(item["id"])["reason"] == "already has a file"


def test_connector_abstract_heading_cleaned(library):
    from doi_hunter_web import catalog

    item = catalog.create_item({"title": "T", "abstract": "AbstractSubstance use disorder is growing"})
    assert catalog.get_item(item["id"])["abstract"] == "Substance use disorder is growing"


def test_open_access_pdf_of_another_paper_is_rejected(monkeypatch):
    """A PDF with a real text layer that does not print the item title is never attached."""
    from doi_hunter_web import library as lib

    class FakePage:
        def extract_text(self):
            return "A Different Paper On Hippocampal Replay\n" + "lorem ipsum " * 100

    monkeypatch.setattr("pypdf.PdfReader", lambda f: type("R", (), {"pages": [FakePage()]})())
    assert not lib._pdf_matches_title(b"%PDF-", "Associations between short-term ambient temperature exposure")
    assert lib._pdf_matches_title(b"%PDF-", "A Different Paper On Hippocampal Replay")


def test_journal_record_is_not_a_paper(monkeypatch):
    """A CrossRef journal-level DOI whose name is printed in the page banner must not be applied."""
    from doi_hunter_web import fulltext, identifiers, metadata

    page = "INTERNATIONAL JOURNAL OF SCIENTIFIC RESEARCH IN ENGINEERING AND MANAGEMENT\nComparative Study Of Linear Scheduling For High-Rise Buildings\n"
    monkeypatch.setattr(fulltext, "_pdf_identifiers", lambda p: ([], [], page))
    monkeypatch.setattr(metadata, "search_crossref", lambda q, rows=5: [
        {"title": "INTERNATIONAL JOURNAL OF SCIENTIFIC RESEARCH IN ENGINEERING AND MANAGEMENT", "doi": "10.55041/ijsrem"}])
    monkeypatch.setattr(identifiers, "resolve_doi", lambda d: {
        "doi": d, "item_type": "document", "creators": [],
        "title": "INTERNATIONAL JOURNAL OF SCIENTIFIC RESEARCH IN ENGINEERING AND MANAGEMENT"})
    payload, _, _ = fulltext._find_verified_metadata({"title": "x"}, "x.pdf")
    assert payload is None


def test_first_printed_title_wins_over_quoted_article(monkeypatch):
    """A letter that quotes the article it answers: the letter (title printed first) is the paper."""
    from doi_hunter_web import fulltext, identifiers

    page = ("Correspondence\nMajor Potential Confounder Not Addressed\nJennifer Vines\n"
            "In response to: Randomized, Controlled Intervention Trial of Male Circumcision for Reduction of HIV\n"
            "doi 10.1371/journal.pmed.0020298 and doi 10.1371/journal.pmed.0030063\n")
    titles = {"10.1371/journal.pmed.0020298": "Randomized, Controlled Intervention Trial of Male Circumcision for Reduction of HIV",
              "10.1371/journal.pmed.0030063": "Major Potential Confounder Not Addressed"}
    monkeypatch.setattr(fulltext, "_pdf_identifiers", lambda p: (identifiers.extract_identifiers(page), [], page))
    monkeypatch.setattr(identifiers, "resolve_doi", lambda d: _fake_payload(d, titles[d]))
    payload, _, _ = fulltext._find_verified_metadata({"title": "x"}, "x.pdf")
    assert payload["doi"] == "10.1371/journal.pmed.0030063"
