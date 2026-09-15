"""Local PDF evidence extraction, isolated from network and the user's library."""
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from doi_hunter_web import fulltext


def _write_pdf(tmp_path, sample_pdf, metadata=None, xmp=None, password=None):
    """Build an actual PDF with controlled embedded fields and optional encryption."""
    writer = PdfWriter(clone_from=PdfReader(sample_pdf))
    if metadata:
        writer.add_metadata(metadata)
    if xmp:
        stream = DecodedStreamObject()
        stream.set_data(xmp.encode("utf-8"))
        stream[NameObject("/Type")] = NameObject("/Metadata")
        stream[NameObject("/Subtype")] = NameObject("/XML")
        writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(password)
    path = tmp_path / "evidence.pdf"
    writer.write(path)
    return path


def test_info_metadata_and_creator_names(tmp_path, sample_pdf):
    path = _write_pdf(tmp_path, sample_pdf, {
        "/Title": "Neural activity during reward learning",
        "/Author": "Aarde, Shawn M.; Pai-Kai Huang; Michael A. Taffe",
        "/DOI": "https://doi.org/10.1234/OWN.Article",
    })
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["title"] == "Neural activity during reward learning"
    assert evidence["doi"] == "10.1234/own.article"
    assert [creator["lastName"] for creator in evidence["creators"]] == ["Aarde", "Huang", "Taffe"]
    assert evidence["creators"][0]["firstName"] == "Shawn M."
    assert evidence["metadata_identifiers"] == [("doi", "10.1234/own.article")]
    assert ("doi", "10.1234/rh.test.5") in evidence["identifiers"]
    assert "Dopamine neurons" in evidence["first_page_text"]
    assert not evidence["encrypted"]


def test_xmp_title_authors_and_prism_attribute(tmp_path, sample_pdf):
    xmp = '''<x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"
          xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
          prism:doi="10.1234/own.xmp">
          <dc:title><rdf:Alt><rdf:li xml:lang="x-default">A verified XMP article title</rdf:li></rdf:Alt></dc:title>
          <dc:creator><rdf:Seq><rdf:li>Shawn M. Aarde</rdf:li><rdf:li>Michael A. Taffe</rdf:li></rdf:Seq></dc:creator>
          <dc:relation>doi:10.1234/cited.relation</dc:relation>
        </rdf:Description>
      </rdf:RDF></x:xmpmeta>'''
    path = _write_pdf(tmp_path, sample_pdf, {"/Title": "main.pdf"}, xmp=xmp)
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["title"] == "A verified XMP article title"
    assert evidence["doi"] == "10.1234/own.xmp"
    assert [creator["lastName"] for creator in evidence["creators"]] == ["Aarde", "Taffe"]
    assert "xmp:prism_doi" in evidence["metadata_sources"]
    assert ("doi", "10.1234/cited.relation") not in evidence["identifiers"]


def test_conflicting_direct_dois_are_not_chosen(tmp_path, sample_pdf):
    path = _write_pdf(tmp_path, sample_pdf, {"/DOI": "10.1234/one", "/Identifier": "10.1234/two"})
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["doi"] == ""
    assert len(evidence["metadata_identifiers"]) == 2
    assert any("Conflicting" in warning for warning in evidence["warnings"])


def test_subject_and_body_doi_are_candidates_not_identity(tmp_path, sample_pdf):
    path = _write_pdf(tmp_path, sample_pdf, {"/Title": "main", "/Subject": "Discussion of doi:10.1234/cited"})
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["doi"] == evidence["title"] == ""
    assert evidence["metadata_identifiers"] == []
    assert ("doi", "10.1234/cited") in evidence["identifiers"]
    assert ("doi", "10.1234/rh.test.5") in evidence["identifiers"]


def test_reference_section_dois_excluded(monkeypatch):
    page = "Own article title\nAuthors\nReferences\nCited paper doi:10.1234/cited"
    reader = SimpleNamespace(is_encrypted=False, metadata={}, xmp_metadata=None,
                             pages=[SimpleNamespace(extract_text=lambda: page)])
    monkeypatch.setattr("pypdf.PdfReader", lambda path: reader)
    evidence = fulltext.extract_pdf_evidence("fake.pdf")
    assert evidence["identifiers"] == []
    assert evidence["doi"] == ""
    assert "Cited paper" not in " ".join(evidence["title_guesses"])


def test_failed_first_page_preserves_page_number(monkeypatch):
    def broken_page():
        """Simulate a damaged content stream while retaining second-page text."""
        raise ValueError("bad stream")

    reader = SimpleNamespace(is_encrypted=False, metadata={}, xmp_metadata=None, pages=[
        SimpleNamespace(extract_text=broken_page),
        SimpleNamespace(extract_text=lambda: "Second page doi:10.1234/second"),
    ])
    monkeypatch.setattr("pypdf.PdfReader", lambda path: reader)
    evidence = fulltext.extract_pdf_evidence("fake.pdf")
    assert evidence["first_page_text"] == ""
    assert evidence["text"].startswith("\nSecond page")
    assert any("page 1" in warning for warning in evidence["warnings"])


def test_password_protected_pdf_returns_honest_warning(tmp_path, sample_pdf):
    path = _write_pdf(tmp_path, sample_pdf, password="secret")
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["encrypted"]
    assert evidence["identifiers"] == []
    assert any("password" in warning for warning in evidence["warnings"])


def test_empty_password_pdf_and_legacy_tuple(tmp_path, sample_pdf):
    path = _write_pdf(tmp_path, sample_pdf, password="")
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["encrypted"]
    assert evidence["text"]
    assert fulltext._pdf_identifiers(str(path)) == (
        evidence["identifiers"], evidence["title_guesses"], evidence["text"],
    )


def test_malformed_xmp_preserves_info_metadata(tmp_path, sample_pdf):
    path = _write_pdf(tmp_path, sample_pdf, {"/Title": "Useful embedded article title"}, xmp="<bad>")
    evidence = fulltext.extract_pdf_evidence(path)
    assert evidence["title"] == "Useful embedded article title"
    assert any("XMP" in warning for warning in evidence["warnings"])
    assert evidence["text"]


def test_header_only_pdf_is_rejected(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-1.7\nThis is not a complete PDF")
    with pytest.raises(ValueError, match="PDF could not be parsed"):
        fulltext.extract_pdf_evidence(path)
