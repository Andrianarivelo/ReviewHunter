"""PDF import regressions: use real parsable bytes and isolated, offline libraries."""
import io
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TITLE = "Dopamine neurons encode reward prediction error"
DOI = "10.1234/rh.test.5"
META = {"title": TITLE, "doi": DOI, "item_type": "journalArticle", "year": 1997,
        "date": "1997-03-14", "journal": "Science", "publisher": "AAAS", "volume": "275",
        "issue": "5306", "pages": "1593-1599", "abstract": "A verified test abstract.",
        "creators": [{"creatorType": "author", "lastName": "Schultz", "firstName": "Wolfram"}]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test request reaches a registry or leaves a background indexing thread alive."""
    from doi_hunter_web import identifiers, metadata, library
    monkeypatch.setattr(identifiers, "resolve_doi", lambda doi: dict(META) if doi == DOI else None)
    monkeypatch.setattr(identifiers, "resolve_arxiv", lambda doi: None)
    monkeypatch.setattr(metadata, "search_crossref", lambda *a, **k: [])
    monkeypatch.setattr(library, "_index_fulltext_async", lambda iid: None)


def pdf_bytes(text="", metadata=None):
    """Build a tiny PDF with explicit text and document metadata for adversarial fixtures."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    if metadata:
        writer.add_metadata(metadata)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def live_ids():
    """Read only live references, which is what the library list displays."""
    from doi_hunter_web.db import get_conn
    return [r[0] for r in get_conn().execute("SELECT id FROM items WHERE deleted_at IS NULL")]


def test_import_attaches_existing_and_keeps_pdf_bytes(library, sample_pdf):
    from doi_hunter_web import catalog, pdf_ingest
    item = catalog.create_item({"title": TITLE, "doi": DOI, "abstract": "User edited abstract"})
    original = sample_pdf.read_bytes()
    result = pdf_ingest.ingest_pdf(original, "main.pdf", tags=["review"])
    assert result["item_id"] == item["id"] and result["existing"]
    assert live_ids() == [item["id"]]
    final = catalog.get_item(item["id"])
    assert final["abstract"] == "User edited abstract"
    assert final["creators"] == META["creators"] and final["pages"] == META["pages"]
    assert Path(final["file_path"]).name.startswith("Schultz - 1997 - Dopamine")
    assert Path(final["file_path"]).read_bytes() == original == sample_pdf.read_bytes()


def test_absent_reference_created_with_registry_fields(library, sample_pdf):
    from doi_hunter_web import catalog, pdf_ingest
    result = pdf_ingest.ingest_pdf(sample_pdf.read_bytes(), "main.pdf")
    assert not result["existing"] and result["metadata_status"] == "verified"
    item = catalog.get_item(result["item_id"])
    for key in ("doi", "title", "year", "creators", "journal", "publisher", "volume", "issue", "pages", "abstract"):
        assert item[key] == META[key]


def test_repeat_import_is_idempotent(library, sample_pdf):
    from doi_hunter_web import catalog, pdf_ingest
    first = pdf_ingest.ingest_pdf(sample_pdf.read_bytes(), "main.pdf")
    second = pdf_ingest.ingest_pdf(sample_pdf.read_bytes(), "download.pdf")
    assert second["item_id"] == first["item_id"] and second["existing"]
    assert len(catalog.list_attachments(first["item_id"])) == len(live_ids()) == 1


def test_offline_existing_title_with_punctuation_difference(library, monkeypatch):
    from doi_hunter_web import catalog, pdf_ingest, fulltext
    monkeypatch.setattr(fulltext, "_find_verified_metadata", lambda *a: (None, "", []))
    item = catalog.create_item({"title": "Neural reward: prediction-error signals in cortex", "year": 2020})
    data = pdf_bytes("Neural reward prediction error signals in cortex", {"/Title": "Neural reward prediction error signals in cortex"})
    result = pdf_ingest.ingest_pdf(data, "main.pdf")
    assert result["item_id"] == item["id"] and len(live_ids()) == 1


def test_conflicting_doi_is_never_attached_to_selected_item(library, sample_pdf):
    from doi_hunter_web import catalog, pdf_ingest
    other = catalog.create_item({"title": TITLE, "doi": "10.1234/different"})
    with pytest.raises(ValueError, match="different DOI"):
        pdf_ingest.ingest_pdf(sample_pdf.read_bytes(), item_id=other["id"])
    assert catalog.list_attachments(other["id"]) == []


def test_filename_or_browser_hint_cannot_supply_identity(library, monkeypatch):
    from doi_hunter_web import pdf_ingest, metadata, catalog
    monkeypatch.setattr(metadata, "search_crossref", lambda *a, **k: [META])
    data = pdf_bytes("A completely unrelated article about weather systems")
    result = pdf_ingest.ingest_pdf(data, "main.pdf", hints=META)
    assert result["metadata_status"] == "partial"
    assert not catalog.get_item(result["item_id"])["doi"]


def test_inline_cited_doi_does_not_identify_pdf(library):
    from doi_hunter_web import pdf_ingest, catalog
    data = pdf_bytes("A completely unrelated article cites doi:" + DOI)
    result = pdf_ingest.ingest_pdf(data, "main.pdf")
    assert not catalog.get_item(result["item_id"])["doi"]


def test_unreadable_pdf_does_not_create_reference(library):
    from doi_hunter_web import pdf_ingest
    for data in (b"<html>login</html>", b"%PDF-1.4\ntruncated"):
        with pytest.raises(ValueError):
            pdf_ingest.ingest_pdf(data)
    assert live_ids() == []


def test_unidentified_pdf_repeated_different_names_reuses_content(library):
    from doi_hunter_web import pdf_ingest
    data = pdf_bytes()
    first = pdf_ingest.ingest_pdf(data, "main.pdf")
    again = pdf_ingest.ingest_pdf(data, "some-other-name.pdf")
    assert first["metadata_status"] == "partial" and again["item_id"] == first["item_id"]
    assert len(live_ids()) == 1


def test_retrieve_metadata_repairs_orphan_preserving_notes_and_annotations(library, sample_pdf):
    from doi_hunter_web import catalog, fulltext
    owner = catalog.create_item({"title": TITLE, "doi": DOI})
    orphan = catalog.create_item({"item_type": "document", "title": "main", "tags": ["keep"]})
    att = catalog.store_file_bytes(orphan["id"], sample_pdf.read_bytes(), "main.pdf")
    note = catalog.create_note(orphan["id"], "<p>Keep my note</p>")
    result = fulltext.retrieve_metadata(orphan["id"])
    assert result["item_id"] == owner["id"] and result["merged_from"] == orphan["id"]
    assert live_ids() == [owner["id"]]
    assert catalog.get_note(note["id"])["item_id"] == owner["id"]
    assert catalog.get_attachment(att["id"])["item_id"] == owner["id"]
    assert catalog.get_item(orphan["id"])["deleted_at"]  # reversible merge, no hard deletion


def test_import_endpoint_returns_canonical_id(library, sample_pdf, monkeypatch):
    from fastapi.testclient import TestClient
    from doi_hunter_web import catalog, connector
    from doi_hunter_web.server import app
    monkeypatch.setattr(connector, "_schedule_open_access_fallback", lambda *a, **k: None)
    item = catalog.create_item(META)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/import/pdfs", headers={"Origin": "chrome-extension://abcdefghijklmnop"},
            files={"files": ("main.pdf", sample_pdf.read_bytes(), "application/pdf")},
            data={"metadata": '{"title":"main"}', "tags": '["review"]', "note": "Keep this"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item_ids"] == [item["id"]] and body["matched"] == 1
    assert body["results"][0]["title"] == TITLE and len(live_ids()) == 1


def test_ambiguous_title_is_not_merged(library, monkeypatch):
    from doi_hunter_web import catalog, fulltext, pdf_ingest
    monkeypatch.setattr(fulltext, "_find_verified_metadata", lambda *a: (None, "", []))
    catalog.create_item({"title": TITLE, "year": 2020})
    catalog.create_item({"title": TITLE, "year": 2021})
    result = pdf_ingest.ingest_pdf(pdf_bytes(TITLE, {"/Title": TITLE}), "main.pdf")
    assert not result["existing"] and len(live_ids()) == 3


def test_abstract_is_bounded_by_explicit_headings():
    from doi_hunter_web.pdf_ingest import extract_abstract
    abstract = "Methods: We measured neuronal responses. " * 4
    assert extract_abstract("Paper\nABSTRACT\n" + abstract + "\n1. Introduction\nBody") == abstract.strip()
    assert extract_abstract("Paper\nABSTRACT\n" + abstract) == ""


def test_filename_preserves_chemical_title_and_extension():
    from doi_hunter_web.library import build_filename
    name = build_filename({"title": "High ambient temperature facilitates the acquisition of 3,4-methylenedioxymethamphetamine (MDMA) self-administration", "authors": ["Aarde, Shawn", "Huang, Pai-Kai", "Taffe, Michael"], "year": 2017})
    assert "3,4-methylenedioxy" in name and "(MDMA)" in name
    assert name.endswith(".pdf") and len(name) <= 180
    long = build_filename({"title": "A very long title " * 100, "authors": ["X" * 200], "year": 2020})
    assert long.endswith(".pdf") and len(long) <= 180


def test_highlights_preserved_as_note_when_full_abstract_available(library):
    from doi_hunter_web import catalog, pdf_ingest
    highlights = "•First highlight.•Second highlight."
    item = catalog.create_item({"title": TITLE, "doi": DOI, "abstract": highlights})
    payload = {**META, "abstract": "Rationale: " + "Full verified abstract text. " * 10}
    pdf_ingest._enrich(item["id"], payload)
    final = catalog.get_item(item["id"])
    assert final["abstract"] == payload["abstract"]
    assert highlights in final["notes_list"][0]["html"]


def test_folder_import_uses_identity_matching_and_preserves_original(library, sample_pdf):
    from doi_hunter_web import catalog, library as lib
    item = catalog.create_item(META)
    original = sample_pdf.read_bytes()
    assert lib.import_pdf_folder(sample_pdf.parent)["imported"] == 1
    assert live_ids() == [item["id"]] and len(catalog.list_attachments(item["id"])) == 1
    assert sample_pdf.read_bytes() == original
