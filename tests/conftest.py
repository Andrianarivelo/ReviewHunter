"""Shared pytest fixtures.

Every test gets a brand-new, empty library in a temporary folder
(REVIEWHUNTER_HOME), so the user's real library is never touched.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def library(tmp_path, monkeypatch):
    """Isolated library root + fresh DB connection for the current thread."""
    monkeypatch.setenv("REVIEWHUNTER_HOME", str(tmp_path / "lib"))
    monkeypatch.delenv("REVIEWHUNTER_PAPERS_DIR", raising=False)
    from doi_hunter_web import db

    db.reset_connections()
    yield tmp_path / "lib"
    db.reset_connections()


@pytest.fixture()
def sample_pdf(tmp_path):
    """A tiny valid one-page PDF containing a DOI in its text layer."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 700 Td (Dopamine neurons encode reward prediction error doi:10.1234/rh.test.5) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "sample.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path
