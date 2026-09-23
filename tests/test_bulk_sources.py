"""Offline bulk-source parsing, preservation, and hostile-input regression tests."""
import io
import json
import zipfile
from xml.sax.saxutils import escape

import pytest

from doi_hunter_web import bulk_sources as sources


def docx(body, extra=None):
    """Build tiny test archives in memory without Word or filesystem extraction."""
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        for name, value in extra or []:
            archive.writestr(name, value)
    return target.getvalue()


def paragraph(text):
    return "<w:p><w:r><w:t>" + escape(text) + "</w:t></w:r></w:p>"


def table(rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join("<w:tc>" + paragraph(cell) + "</w:tc>" for cell in row) + "</w:tr>" for row in rows) + "</w:tbl>"


def queries(result):
    assert not result["errors"], result
    return [entry["query"] for entry in result["entries"]]


def test_pasted_dois_and_title_list_remain_distinct():
    result = sources.parse_text("10.1234/a\nhttps://doi.org/10.1234/b\nA title with a comma, and a colon: implications")
    assert queries(result) == ["10.1234/a", "10.1234/b", "A title with a comma, and a colon: implications"]
    assert result["warnings"]
    assert queries(sources.parse_text("10.1234/a\nA second title\n10.1234/b")) == ["10.1234/a", "A second title", "10.1234/b"]


def test_bibliography_headings_are_explicitly_skipped_and_author_labels_are_not_json():
    result = sources.parse_text("References\n[Smith, 2024] A paper title\n10.1234/a")
    assert queries(result) == ["[Smith, 2024] A paper title", "10.1234/a"]
    assert "heading skipped" in result["warnings"][0]


def test_numbered_wrapped_citations_are_not_misdetected_as_json():
    text = "[1] Smith, J. (2024). Neural signals\nand perception. Journal 2, 1-9. doi:10.1234/ONE.\n[2] Jones, A. Another\nwrapped title without DOI."
    result = sources.parse_text(text)
    assert queries(result) == ["10.1234/one", "Jones, A. Another wrapped title without DOI."]
    assert result["format"] == "text"


def test_single_numbered_citation_and_indented_continuation():
    assert queries(sources.parse_text("1. A wrapped citation\nwith a second line")) == ["A wrapped citation with a second line"]
    assert queries(sources.parse_text("First citation\n  continuation\nSecond title")) == ["First citation continuation", "Second title"]


def test_bullet_list_and_balanced_doi_parentheses():
    result = sources.parse_text("- One citation (doi:10.1234/a(test)).\n* Another title")
    assert queries(result) == ["10.1234/a(test)", "Another title"]


def test_multiple_dois_are_explicitly_rejected_instead_of_picking_one():
    result = sources.parse_text("A review cites 10.1234/a and 10.1234/b")
    assert not result["entries"] and result["errors"]
    assert "multiple DOIs" in result["warnings"][0]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_csv_structured_metadata_and_quoted_delimiters(delimiter):
    import csv
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(["DOI", "Title", "Authors", "Year", "Journal", "URL"])
    writer.writerow(["https://doi.org/10.1234/ABC", "Signals, choice; memory", "Smith, Jane; Jones, Alex", "2024", "Neural Journal", "https://example.org/paper"])
    result = sources.parse_bulk_source(stream.getvalue(), filename="papers.csv")
    assert queries(result) == ["10.1234/abc"]
    metadata = result["entries"][0]["metadata"]
    assert metadata["title"] == "Signals, choice; memory"
    assert metadata["year"] == 2024 and metadata["journal"] == "Neural Journal"
    assert metadata["creators"] == [{"creatorType": "author", "lastName": "Smith", "firstName": "Jane"}, {"creatorType": "author", "lastName": "Jones", "firstName": "Alex"}]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"])
def test_unicode_encodings_preserve_non_ascii_titles(encoding):
    text = "title;year\nM\u00e9moire et d\u00e9cision;2024"
    result = sources.parse_bulk_source(filename="papers.csv", data=text.encode(encoding))
    assert queries(result) == ["M\u00e9moire et d\u00e9cision"]


def test_csv_multiline_quoted_title_is_one_record():
    result = sources.parse_bulk_source('title,year\n"Neural signals\nand decision",2024\nOther title,2023', filename="papers.csv")
    assert queries(result) == ["Neural signals and decision", "Other title"]
    assert result["entries"][0]["metadata"]["title"] == "Neural signals\nand decision"


@pytest.mark.parametrize("text,expected", [
    ("10.1234/a\n10.1234/b", ["10.1234/a", "10.1234/b"]),
    ("First title\nSecond title", ["First title", "Second title"]),
    ("10.1234/a,First title\n10.1234/b,Second title", ["10.1234/a", "10.1234/b"]),
])
def test_headerless_csv_lists(text, expected):
    assert queries(sources.parse_bulk_source(text, filename="papers.csv")) == expected


def test_csv_bad_rows_and_unsupported_columns_have_visible_warnings():
    result = sources.parse_bulk_source("title,year,custom\nValid title,2024,notes\nBad title,2024,unquoted,comma\nOther title,unknown,notes", filename="papers.csv")
    assert queries(result) == ["Valid title", "Other title"]
    assert len(result["warnings"]) == 3
    assert result["entries"][1]["metadata"]["date"] == "unknown"


def test_invalid_doi_uses_valid_title_without_preserving_invalid_identifier():
    result = sources.parse_bulk_source("doi,title\ninvalid,Actual title", filename="papers.csv")
    assert queries(result) == ["Actual title"]
    assert "doi" not in result["entries"][0]["metadata"] and result["warnings"]


def test_docx_paragraphs_and_table_rows_preserve_document_order_and_fields():
    body = paragraph("First paper") + table([["Title", "DOI", "Year"], ["Second paper", "10.1234/second", "2020"]]) + paragraph("Third paper")
    result = sources.parse_bulk_source(filename="papers.docx", data=docx(body))
    assert queries(result) == ["First paper", "10.1234/second", "Third paper"]
    assert result["entries"][1]["metadata"]["title"] == "Second paper"
    assert result["entries"][1]["metadata"]["year"] == 2020


def test_docx_run_boundaries_and_line_breaks_do_not_split_references():
    body = '<w:p><w:r><w:t>A neural </w:t></w:r><w:r><w:t>study</w:t><w:br/><w:t>in mice</w:t></w:r></w:p>'
    assert queries(sources.parse_bulk_source(filename="p.docx", data=docx(body))) == ["A neural study in mice"]


def test_docx_content_controls_preserve_order_and_notes_exclusion_is_visible():
    body = paragraph("References") + "<w:sdt><w:sdtContent>" + paragraph("First paper") + "</w:sdtContent></w:sdt>" + paragraph("Second paper")
    result = sources.parse_bulk_source(filename="p.docx", data=docx(body, [("word/footnotes.xml", "<notes/>")]))
    assert queries(result) == ["First paper", "Second paper"]
    assert len(result["warnings"]) == 2


def test_docx_nested_tables_fail_visibly():
    body = "<w:tbl><w:tr><w:tc>" + table([["A nested title"]]) + "</w:tc></w:tr></w:tbl>"
    result = sources.parse_bulk_source(filename="p.docx", data=docx(body))
    assert result["errors"] and not result["entries"]


@pytest.mark.parametrize("filename,data", [
    ("papers.doc", b"\xd0\xcf\x11\xe0"), ("papers.docx", b"not a zip"),
    ("papers.pdf", b"%PDF-1.7"), ("papers.txt", b"\x00\x00\x00\x01"),
    ("papers.csv", b"title\nM\xe9moire"),
])
def test_invalid_formats_and_binary_are_actionable_errors(filename, data):
    result = sources.parse_bulk_source(filename=filename, data=data)
    assert result["errors"] and not result["entries"]
    if filename.endswith(".doc"):
        assert "Save As .docx" in result["errors"][0]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/absolute", "..\\escape"])
def test_docx_unsafe_paths_are_rejected_without_extraction(name):
    result = sources.parse_bulk_source(filename="p.docx", data=docx(paragraph("A title"), [(name, "bad")]))
    assert result["errors"] and not result["entries"]


def test_docx_entity_declarations_and_corrupt_xml_are_rejected():
    for body in ["<!DOCTYPE x [<!ENTITY a 'bad'>]>", "<broken>"]:
        result = sources.parse_bulk_source(filename="p.docx", data=docx(body))
        assert result["errors"] and not result["entries"]


def test_docx_zip_bomb_and_missing_document_are_rejected():
    result = sources.parse_bulk_source(filename="p.docx", data=docx(paragraph("Valid"), [("large.txt", "a" * (2 * 1024 * 1024))]))
    assert result["errors"] and "compression ratio" in result["errors"][0]
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("other.xml", "<x/>")
    assert sources.parse_bulk_source(filename="p.docx", data=target.getvalue())["errors"]


def test_limits_fail_atomically(monkeypatch):
    result = sources.parse_text("\n".join(f"Paper title {i}" for i in range(501)))
    assert result["errors"] and not result["entries"]
    monkeypatch.setattr(sources, "MAX_TEXT_BYTES", 10)
    assert sources.parse_text("A title longer than ten bytes")["errors"]
    monkeypatch.setattr(sources, "MAX_FILE_BYTES", 5)
    assert sources.parse_bulk_source(filename="p.txt", data=b"too large")["errors"]


@pytest.mark.parametrize("text,filename", [
    ('@article{a, title={Neural signals}, year={2024}, doi={10.1234/a}}', "p.bib"),
    ("TY  - JOUR\nTI  - Neural signals\nPY  - 2024\nDO  - 10.1234/a\nER  -\n", "p.ris"),
    (json.dumps([{"type": "article-journal", "title": "Neural signals", "DOI": "10.1234/a", "issued": {"date-parts": [[2024]]}}]), "p.json"),
])
def test_existing_bibliographic_formats_preserve_metadata(text, filename):
    result = sources.parse_bulk_source(text, filename=filename)
    assert queries(result) == ["10.1234/a"]
    assert result["entries"][0]["metadata"]["title"] == "Neural signals"
    assert result["entries"][0]["metadata"]["year"] == 2024


@pytest.mark.parametrize("text,filename", [
    ('@article{a, title={Unclosed}', "p.bib"),
    ('[{"title":"Good"}, 42]', "p.json"),
    ('{"items":null}', "p.json"),
    ('title,title\nA,B', "p.csv"),
    ('title,year\n"unclosed,2024', "p.csv"),
])
def test_malformed_structured_records_are_not_silently_lost(text, filename):
    result = sources.parse_bulk_source(text, filename=filename)
    assert result["errors"] and not result["entries"]


@pytest.mark.parametrize("field,value", [("title", [1, 2]), ("DOI", 42), ("author", [42]), ("issued", 42)])
def test_malformed_csl_fields_are_not_silently_coerced_or_discarded(field, value):
    result = sources.parse_bulk_source(json.dumps([{**{"title": "A title"}, field: value}]), filename="p.json")
    assert result["errors"] and not result["entries"]


def test_invalid_structured_doi_does_not_replace_a_valid_title_query():
    result = sources.parse_bulk_source('@article{a, title={An actual title}, doi={broken}}', filename="p.bib")
    assert queries(result) == ["An actual title"]
    assert "doi" not in result["entries"][0]["metadata"] and result["warnings"]


def test_docx_bounded_expansion_and_xml_read_limits(monkeypatch):
    data = docx(paragraph("A valid title"))
    monkeypatch.setattr(sources, "MAX_XML_BYTES", 20)
    assert sources.parse_bulk_source(filename="p.docx", data=data)["errors"]
    monkeypatch.setattr(sources, "MAX_ARCHIVE_BYTES", 20)
    assert sources.parse_bulk_source(filename="p.docx", data=data)["errors"]


def test_parse_file_does_not_modify_source(tmp_path):
    source = tmp_path / "papers.txt"
    source.write_text("A title\nAnother title", encoding="utf-8")
    before = source.read_bytes()
    assert queries(sources.parse_file(source)) == ["A title", "Another title"]
    assert source.read_bytes() == before
    assert sources.parse_file(tmp_path / "missing.txt")["errors"]
