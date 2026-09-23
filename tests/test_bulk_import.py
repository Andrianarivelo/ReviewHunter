"""Bulk imports never guess identities or mutate original source files."""
import threading
import time
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from doi_hunter_web import bulk_api, bulk_import as bulk, catalog, db


def entry(title="A controlled study of neural activity", doi=None):
    """Make a supplied citation whose identity does not require external services."""
    return {"query": doi or title, "metadata": {"title": title, **({"doi": doi} if doi else {})}, "source": "test"}


def wait_job(job_id):
    """Wait with a deadline so concurrency regressions fail rather than hang."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = bulk.snapshot(job_id)
        if result["status"] in bulk.TERMINAL and "finished_at" in result:
            return result
        time.sleep(.01)
    raise AssertionError("worker did not finish")


def test_offline_worker_collection_and_duplicate(library):
    job = bulk.start_job([entry(), entry()], new_collection_name="Batch", auto_search=False, auto_download=False)
    result = wait_job(job["job_id"])
    assert [r["status"] for r in result["records"]] == ["created", "matched"]
    assert result["records"][0]["item_id"] == result["records"][1]["item_id"]
    assert db.get_conn().execute("SELECT COUNT(*) FROM item_collections").fetchone()[0] == 1


def test_preserves_existing_metadata_and_fills_empty(library, monkeypatch):
    old = catalog.create_item({"doi": "10.1234/test", "title": "User corrected title", "year": 1999})
    monkeypatch.setattr(bulk.metadata, "fetch_doi", lambda _: {"doi": "10.1234/test", "title": "Registry title", "year": 2020, "journal": "Journal"})
    result = bulk.process_entry(entry(doi="10.1234/test"), collection_id=None, auto_search=True, auto_download=False)
    saved = catalog.get_item(old["id"])
    assert result["matched"] and saved["title"] == "User corrected title" and saved["year"] == 1999
    assert saved["journal"] == "Journal"


def test_no_first_search_hit(library, monkeypatch):
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [{"title": "Unrelated paper", "doi": "10.1234/other"}])
    result = bulk.process_entry({"query": "citation text", "metadata": {}}, collection_id=None, auto_search=True, auto_download=False)
    assert result["status"] == "needs_review"
    assert db.get_conn().execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_duplicate_exact_titles_do_not_pick_a_doi(library, monkeypatch):
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [{"title": "Repeated title", "doi": f"10.1234/{n}"} for n in range(2)])
    result = bulk.process_entry({"query": "Repeated title", "metadata": {}}, collection_id=None, auto_search=True, auto_download=False)
    assert result["status"] == "needs_review"


def test_trash_is_not_restored(library):
    item = catalog.create_item({"title": "Deleted", "doi": "10.1234/deleted"})
    db.get_conn().execute("UPDATE items SET deleted_at='2020-01-01' WHERE id=?", (item["id"],))
    result = bulk.process_entry(entry(doi="10.1234/deleted"), collection_id=None, auto_search=False, auto_download=False)
    assert result["status"] == "needs_review"
    assert db.get_conn().execute("SELECT deleted_at FROM items WHERE id=?", (item["id"],)).fetchone()[0]


def test_saved_reference_survives_pdf_failure(library, monkeypatch):
    def fail(_):
        raise RuntimeError("network unavailable")
    monkeypatch.setattr(bulk.library, "download_item", fail)
    result = bulk.process_entry(entry(doi="10.1234/test"), collection_id=None, auto_search=False, auto_download=True)
    assert result["status"] == "created" and result["pdf_status"] == "failed"
    assert catalog.get_item(result["item_id"])


def test_cancel_finishes_current_and_cleans_temp(library, tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def controlled(*a, **kw):
        started.set()
        assert release.wait(3)
        return {"status": "created", "item_id": 42}
    monkeypatch.setattr(bulk, "process_entry", controlled)
    staged = tmp_path / "staged"
    staged.mkdir()
    job = bulk.start_job([entry(), entry()], auto_search=False, auto_download=False, temp_dir=str(staged))
    assert started.wait(3)
    bulk.cancel(job["job_id"])
    release.set()
    final = wait_job(job["job_id"])
    assert final["status"] == "cancelled" and final["completed"] == 1
    assert [r["status"] for r in final["records"]] == ["created", "cancelled"]
    assert not staged.exists()


def test_folder_pdf_is_read_only_and_original_name_retained(library, tmp_path, monkeypatch):
    source = tmp_path / "Original study.pdf"
    original = b"%PDF-fake test bytes"
    source.write_bytes(original)
    def ingest(data, **kw):
        assert data == original and kw["filename"] == source.name
        return {"item_id": 1, "existing": False, "metadata_status": "partial", "warnings": ["metadata incomplete"]}
    monkeypatch.setattr(bulk.pdf_ingest, "ingest_pdf", ingest)
    entries, _ = bulk.parse_sources("", [], folder_path=str(tmp_path))
    result = bulk.process_entry(entries[0], collection_id=None, auto_search=False, auto_download=False)
    assert source.read_bytes() == original and result["warnings"] == ["metadata incomplete"]


def test_reject_all_if_batch_exceeds_limit(library, monkeypatch):
    monkeypatch.setattr(bulk, "MAX_ENTRIES", 1)
    with pytest.raises(bulk.BulkInputError):
        bulk.parse_sources("10.1234/one\n10.1234/two", [])
    assert not db.get_conn().execute("SELECT id FROM items").fetchall()


def test_invalid_destination_creates_nothing(library):
    with pytest.raises(bulk.BulkInputError):
        bulk.start_job([entry()], collection_id=999)
    assert not db.get_conn().execute("SELECT id FROM items").fetchall()


def test_http_csv_upload_and_poll(library):
    app = FastAPI()
    app.include_router(bulk_api.router)
    client = TestClient(app)
    response = client.post("/api/bulk-import", files=[("files", ("papers.csv", b"title,year\nA controlled study of neural activity,2020\n", "text/csv"))],
                           data={"auto_search": "false", "auto_download": "false", "new_collection_name": "Uploads"})
    assert response.status_code == 202, response.text
    final = wait_job(response.json()["job_id"])
    assert final["records"][0]["status"] == "created"
    assert client.get(f"/api/bulk-import/{final['job_id']}").status_code == 200


def test_untrusted_metadata_cannot_attach_a_local_path(library):
    supplied = entry()
    supplied["metadata"]["file_path"] = "C:/private.pdf"
    result = bulk.process_entry(supplied, collection_id=None, auto_search=False, auto_download=False)
    assert not catalog.get_item(result["item_id"]).get("file_path")


def test_folder_only_discovers_pdfs(tmp_path):
    (tmp_path / "study.pdf").write_bytes(b"%PDF-test")
    (tmp_path / "unrelated.csv").write_text("title\nDo not import this\n")
    entries, _ = bulk.parse_sources("", [], folder_path=str(tmp_path))
    assert len(entries) == 1 and entries[0]["filename"] == "study.pdf"


def test_conflicting_author_does_not_assign_registry_doi(library, monkeypatch):
    supplied = entry()
    supplied["metadata"]["authors"] = ["Alice Smith"]
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [{"title": supplied["metadata"]["title"], "authors": ["Bob Jones"], "doi": "10.1234/wrong"}])
    result = bulk.process_entry(supplied, collection_id=None, auto_search=True, auto_download=False)
    assert not catalog.get_item(result["item_id"]).get("doi")
    assert result["metadata_status"] == "supplied"


def test_verified_doi_fills_existing_title_reference(library, monkeypatch):
    supplied = entry()
    old = catalog.create_item(supplied["metadata"])
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [{"title": supplied["metadata"]["title"], "doi": "10.1234/verified"}])
    result = bulk.process_entry(supplied, collection_id=None, auto_search=True, auto_download=False)
    assert result["item_id"] == old["id"] and result["matched"]
    assert catalog.get_item(old["id"])["doi"] == "10.1234/verified"


def test_local_same_title_with_conflicting_author_is_not_merged(library):
    supplied = entry()
    old = catalog.create_item({**supplied["metadata"], "authors": ["Alice Smith"]})
    supplied["metadata"]["authors"] = ["Bob Jones"]
    result = bulk.process_entry(supplied, collection_id=None, auto_search=False, auto_download=False)
    assert result["item_id"] != old["id"] and result["created"]


@pytest.mark.parametrize("citation", [
    "Smith, A., & Jones, B. (2020). Cocaine changes neural activity during reward learning. Journal of Neuroscience, 12, 100-110.",
    "Smith A, Jones B. Cocaine changes neural activity during reward learning. J Neurosci. 2020;12:100-110.",
])
def test_full_citation_requires_corroborated_identity(library, monkeypatch, citation):
    candidate = {"title": "Cocaine changes neural activity during reward learning", "year": 2020,
                 "authors": ["Alice Smith", "Bob Jones"], "doi": "10.1234/citation"}
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [candidate])
    result = bulk.process_entry({"query": citation, "metadata": {}}, collection_id=None, auto_search=True, auto_download=False)
    assert result["status"] == "created"
    assert catalog.get_item(result["item_id"])["doi"] == candidate["doi"]


@pytest.mark.parametrize("citation", [
    "Smith A. Cocaine changes neural activity during reward learning. Journal. 2019;12:100.",
    "Smith A. Cocaine changes neural activity during reward learning. Journal. 2020;12:100. Accessed 2021.",
    "Brown A. Cocaine changes neural activity during reward learning. Journal. 2020;12:100.",
    "Smith A. Cocaine changes neural activity during reward learning in juvenile rats. Journal. 2020;12:100.",
    "Cocaine changes neural activity during reward learning. Journal. 2020;12:100.",
])
def test_citation_conflicts_and_partial_titles_are_review_only(library, monkeypatch, citation):
    candidate = {"title": "Cocaine changes neural activity during reward learning", "year": 2020,
                 "authors": ["Alice Smith"], "doi": "10.1234/citation"}
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [candidate])
    result = bulk.process_entry({"query": citation, "metadata": {}}, collection_id=None, auto_search=True, auto_download=False)
    assert result["status"] == "needs_review"


def test_short_generic_citation_title_is_not_identity_evidence(library, monkeypatch):
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [{"title": "Neural activity", "year": 2020, "authors": ["Alice Smith"], "doi": "10.1234/generic"}])
    result = bulk.process_entry({"query": "Smith A. Neural activity. Journal. 2020.", "metadata": {}}, collection_id=None, auto_search=True, auto_download=False)
    assert result["status"] == "needs_review"


def test_two_corroborated_citation_candidates_are_ambiguous(library, monkeypatch):
    candidate = {"title": "Cocaine changes neural activity during reward learning", "year": 2020, "authors": ["Alice Smith"]}
    monkeypatch.setattr(bulk.metadata, "search_crossref", lambda *a, **kw: [{**candidate, "doi": f"10.1234/{n}"} for n in range(2)])
    result = bulk.process_entry({"query": f"Smith A. {candidate['title']}. Journal. 2020.", "metadata": {}}, collection_id=None, auto_search=True, auto_download=False)
    assert result["status"] == "needs_review"


def test_multipart_parsing_runs_off_event_loop(library, monkeypatch):
    app = FastAPI()
    app.include_router(bulk_api.router)
    def fake_start(*args):
        import asyncio
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return {"job_id": "threaded"}
    monkeypatch.setattr(bulk_api, "_start", fake_start)
    response = TestClient(app).post("/api/bulk-import", files={"files": ("papers.txt", b"one title")})
    assert response.status_code == 202


@pytest.mark.parametrize("length", ["invalid", "-1", "1.5"])
def test_bad_content_length_is_a_client_error(length):
    app = FastAPI()
    app.include_router(bulk_api.router)
    response = TestClient(app).post("/api/bulk-import", headers={"Content-Length": length})
    assert response.status_code == 400


def test_chunked_upload_has_receive_limit(monkeypatch):
    app = FastAPI()
    app.include_router(bulk_api.router)
    monkeypatch.setattr(bulk, "MAX_TOTAL_BYTES", 1)
    boundary = "testboundary"
    chunks = [f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="paper.pdf"\r\n\r\n'.encode(),
              b"x" * (1024 * 1024 + 2), f"\r\n--{boundary}--\r\n".encode()]
    response = TestClient(app).post("/api/bulk-import", content=iter(chunks),
                                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert response.status_code == 413


def test_bibtex_suffix_is_accepted(tmp_path):
    path = tmp_path / "papers.bibtex"
    path.write_text("@article{a,title={Neural signals},year={2024},doi={10.1234/test}}")
    entries, _ = bulk.parse_sources("", [(path, path.name)])
    assert entries[0]["metadata"]["doi"] == "10.1234/test"


def test_sqlite_stale_wal_snapshot_retries_fresh_transaction(library, monkeypatch):
    """Reproduce the real read-to-write conflict despite an existing busy timeout."""
    connection = db.get_conn()
    path = connection.execute("PRAGMA database_list").fetchone()[2]
    original = bulk.process_entry
    calls = []
    def contention_once(record, **options):
        calls.append(1)
        if len(calls) == 1:
            with db.tx():
                connection.execute("SELECT count(*) FROM items").fetchone()
                with sqlite3.connect(path) as writer:
                    writer.execute("INSERT INTO collections(name) VALUES ('Concurrent index simulation')")
                connection.execute("INSERT INTO collections(name) VALUES ('Stale snapshot write')")
        return original(record, **options)
    monkeypatch.setattr(bulk, "process_entry", contention_once)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (0, 0, 0))
    result = bulk._process_with_retry(entry(), {"collection_id": None, "auto_search": False, "auto_download": False})
    assert len(calls) == 2 and result["retry_count"] == 1 and result["created"]
    assert not connection.in_transaction
    assert connection.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_retry_after_committed_pdf_keeps_one_reference_and_attachment(library, sample_pdf, monkeypatch):
    """A late lock error must not create a second canonical PDF attachment."""
    monkeypatch.setattr(bulk.pdf_ingest, "_identify", lambda *a: (
        {"title": "A verified study of reward prediction", "doi": "10.1234/retry"}, {}, "test registry"))
    monkeypatch.setattr(bulk.library, "_index_fulltext_async", lambda _: None)
    original = bulk.pdf_ingest.ingest_pdf
    calls = []
    def commit_then_busy(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return result
    monkeypatch.setattr(bulk.pdf_ingest, "ingest_pdf", commit_then_busy)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (0, 0, 0))
    record = {"query": sample_pdf.name, "source": sample_pdf.name, "pdf_path": str(sample_pdf)}
    result = bulk._process_with_retry(record, {"collection_id": None, "auto_search": False, "auto_download": False})
    assert result["retry_count"] == 1 and result["matched"] and result["pdf"]
    assert calls[0]["item_id"] == calls[1]["item_id"]
    assert db.get_conn().execute("SELECT count(*) FROM items").fetchone()[0] == 1
    assert db.get_conn().execute("SELECT count(*) FROM attachments").fetchone()[0] == 1


def test_exhausted_sqlite_retries_mark_failure_and_clean_uploads(library, tmp_path, monkeypatch):
    calls = []
    def busy(*args, **kwargs):
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(bulk, "process_entry", busy)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (0, 0, 0))
    staged = tmp_path / "retry-upload"
    staged.mkdir()
    job = bulk.start_job([entry()], auto_search=False, auto_download=False, temp_dir=str(staged))
    result = wait_job(job["job_id"])
    assert len(calls) == 4
    assert result["records"][0]["status"] == "failed" and result["records"][0]["retry_count"] == 3
    assert result["completed"] == 1 and not staged.exists()


@pytest.mark.parametrize("error", [sqlite3.OperationalError("no such table: missing"),
                                  sqlite3.IntegrityError("UNIQUE constraint failed"),
                                  RuntimeError("database is locked"), OSError("file unavailable")])
def test_non_contention_failures_are_not_retried(library, monkeypatch, error):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(bulk, "process_entry", fail)
    with pytest.raises(type(error)):
        bulk._process_with_retry(entry(), {"collection_id": None, "auto_search": False, "auto_download": False})
    assert len(calls) == 1


def test_pdf_download_busy_retries_existing_reference(library, monkeypatch):
    calls = []
    def busy_then_missing(item_id):
        calls.append(item_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database table is locked")
        return {"error_msg": "No PDF available"}
    monkeypatch.setattr(bulk.library, "download_item", busy_then_missing)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (0, 0, 0))
    result = bulk._process_with_retry(entry(doi="10.1234/downloadretry"),
                                     {"collection_id": None, "auto_search": False, "auto_download": True})
    assert result["created"] and not result["matched"] and result["status"] == "created" and result["retry_count"] == 1
    assert calls[0] == calls[1] and db.get_conn().execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_existing_reference_stays_matched_after_download_retry(library, monkeypatch):
    old = catalog.create_item(entry(doi="10.1234/existingretry")["metadata"])
    calls = []
    def busy_once(item_id):
        calls.append(item_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"error_msg": "No PDF found"}
    monkeypatch.setattr(bulk.library, "download_item", busy_once)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (0, 0, 0))
    result = bulk._process_with_retry(entry(doi="10.1234/existingretry"),
                                     {"collection_id": None, "auto_search": False, "auto_download": True})
    assert result["item_id"] == old["id"] and result["matched"] and not result["created"]
    assert result["status"] == "matched" and result["retry_count"] == 1


def test_exhausted_download_retries_preserve_saved_reference_outcome(library, monkeypatch):
    def busy(item_id):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(bulk.library, "download_item", busy)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (0, 0, 0))
    result = bulk._process_with_retry(entry(doi="10.1234/exhausteddownload"),
                                     {"collection_id": None, "auto_search": False, "auto_download": True})
    assert result["created"] and not result["matched"] and result["status"] == "created"
    assert result["pdf_status"] == "failed" and result["retry_count"] == 3
    assert catalog.get_item(result["item_id"])


def test_cancellation_interrupts_database_retry_backoff(library, tmp_path, monkeypatch):
    attempted = threading.Event()
    calls = []
    def busy(*args, **kwargs):
        calls.append(1)
        attempted.set()
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"status": "created", "item_id": 42}
    monkeypatch.setattr(bulk, "process_entry", busy)
    monkeypatch.setattr(bulk, "SQLITE_RETRY_DELAYS", (10, 10, 10))
    staged = tmp_path / "cancel-retry-upload"
    staged.mkdir()
    job = bulk.start_job([entry(), entry()], auto_search=False, auto_download=False, temp_dir=str(staged))
    assert attempted.wait(3)
    bulk.cancel(job["job_id"])
    result = wait_job(job["job_id"])
    assert len(calls) == 2 and result["status"] == "cancelled"
    assert [r["status"] for r in result["records"]] == ["created", "cancelled"]
    assert result["completed"] == 1 and not staged.exists()
