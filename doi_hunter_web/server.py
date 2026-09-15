"""FastAPI server. Single process, single SQLite file, fans out job progress via WebSocket."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import api_v2, books, catalog, config, connector, gdrive, library
from .config import default_google_drive_root, library_root, papers_dir
from .security import LocalOriginGuard, NoStoreApi

log = logging.getLogger("doihunter.server")

app = FastAPI(title="ReviewHunter", version=__version__)

# Loopback-only hardening: rejects foreign Host headers and cross-site writes (see security.py).
_trusted_ports = {config.PORT, int(config.get_settings().get("https_port") or 8766)}
app.add_middleware(NoStoreApi)
app.add_middleware(LocalOriginGuard, ports=_trusted_ports)

# ----------- WebSocket fanout -----------

class JobBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: str, data: dict) -> None:
        """Thread-safe: callable from worker threads."""
        msg = {"event": event, **data}
        if self._loop is None:
            return
        for q in list(self._subs):
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, msg)
            except asyncio.QueueFull:
                pass


bus = JobBus()
app.state.bus = bus
_scheduler_stop = threading.Event()
_started = False


@app.on_event("startup")
async def _startup() -> None:
    global _started
    bus.bind_loop(asyncio.get_running_loop())
    if _started:  # the HTTPS listener shares this app: background work starts only once
        return
    _started = True
    await asyncio.to_thread(_ensure_citation_keys)
    if config.get_settings().get("auto_retrieve_pdf_metadata", True):
        threading.Thread(target=_retrieve_missing_metadata, daemon=True, name="metadata-backfill").start()
    threading.Thread(target=gdrive.scheduler_loop, args=(_scheduler_stop,), daemon=True,
                     name="backup-scheduler").start()
    # First-run nicety: if the user has the legacy ./downloads/ folder, adopt those PDFs.
    # Opt-in only: the repository ships a sample downloads/ folder that must not pollute new libraries.
    legacy = Path.cwd() / "downloads"
    if os.environ.get("REVIEWHUNTER_IMPORT_LEGACY_DOWNLOADS") == "1" and legacy.exists() and library.stats()["items"] == 0:
        result = library.import_pdf_folder(legacy)
        log.info(f"first-run import from {legacy}: {result}")


@app.on_event("shutdown")
async def _shutdown() -> None:
    _scheduler_stop.set()


def _ensure_citation_keys() -> None:
    """Legacy items (pre-v2) get a Better-BibTeX-style citation key once."""
    from .db import get_conn, row_to_dict

    conn = get_conn()
    for row in conn.execute("SELECT * FROM items WHERE citation_key IS NULL OR citation_key=''").fetchall():
        item = row_to_dict(row)
        conn.execute("UPDATE items SET citation_key=? WHERE id=?", (catalog.make_citation_key(item, conn), item["id"]))


def _retrieve_missing_metadata() -> None:
    """Background pass: items that have a PDF but neither DOI nor authors get 'Retrieve Metadata'.

    Typical case: PDFs imported by file name before v2. Only verified matches are applied
    (see fulltext.retrieve_metadata). Each item is tried once; attempted ids are remembered in
    settings so an unidentifiable PDF does not cost network calls at every start.
    """
    from . import fulltext
    from .db import get_conn

    attempted = set(config.get_settings().get("metadata_backfill_attempted") or [])
    rows = get_conn().execute(
        "SELECT DISTINCT i.id FROM items i JOIN attachments a ON a.item_id=i.id "
        "WHERE i.deleted_at IS NULL AND (i.doi IS NULL OR i.doi='') "
        "AND (i.creators IS NULL OR i.creators IN ('', '[]')) "
        "AND a.kind='file' AND lower(a.path) LIKE '%.pdf'"
    ).fetchall()
    for (item_id,) in rows:
        if item_id in attempted:
            continue
        try:
            result = fulltext.retrieve_metadata(item_id)
            log.info(f"metadata backfill item {item_id}: {result}")
            if result.get("ok"):
                bus.publish("item_changed", {"id": item_id})
        except Exception as e:  # one bad PDF must not stop the pass
            log.warning(f"metadata backfill failed for item {item_id}: {e}")
        attempted.add(item_id)
        config.update_settings({"metadata_backfill_attempted": sorted(attempted)})


app.include_router(api_v2.router)
app.include_router(connector.router)


# ----------- Models -----------

class CollectionIn(BaseModel):
    name: str
    parent_id: Optional[int] = None
    color: Optional[str] = None


class CollectionPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    parent_id: Optional[int] = None


class AddIn(BaseModel):
    queries: list[str]
    auto_download: bool = True
    collection_id: Optional[int] = None


class BookIn(BaseModel):
    publications: list[dict]      # FreeBooksAPI publications (already-normalized client-side)
    auto_download: bool = True
    collection_id: Optional[int] = None


class ExportIn(BaseModel):
    destination: str


class BulkActionIn(BaseModel):
    author: Optional[str] = None
    subject: Optional[str] = None
    item_ids: Optional[list[int]] = None
    collection_id: Optional[int] = None
    collection_name: Optional[str] = None
    destination: Optional[str] = None


class StoragePathIn(BaseModel):
    destination: Optional[str] = None
    migrate_existing: bool = True


class ItemPatch(BaseModel):
    title: Optional[str] = None
    authors: Optional[list[str]] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    abstract: Optional[str] = None
    keywords: Optional[list[str]] = None
    starred: Optional[bool] = None
    notes: Optional[str] = None


# ----------- API: stats / health -----------

@app.get("/api/health")
def api_health() -> dict:
    return {"ok": True}


# ----------- API: collections -----------

@app.get("/api/collections")
def api_collections() -> list[dict]:
    return library.list_collections()


@app.post("/api/collections")
def api_create_collection(body: CollectionIn) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "name required")
    return library.create_collection(body.name, body.parent_id, body.color)


@app.patch("/api/collections/{cid}")
def api_patch_collection(cid: int, body: CollectionPatch) -> dict:
    if body.name is not None:
        library.rename_collection(cid, body.name)
    if body.color is not None:
        from .db import get_conn
        get_conn().execute("UPDATE collections SET color=? WHERE id=?", (body.color or None, cid))
    return {"ok": True}


@app.delete("/api/collections/{cid}")
def api_delete_collection(cid: int) -> dict:
    library.delete_collection(cid)
    return {"ok": True}


# ----------- API: items -----------

@app.get("/api/facets")
def api_facets() -> dict:
    """Authors and subjects with counts — used by the left rail's auto-organization."""
    return library.facets()


@app.get("/api/subject-graph")
def api_subject_graph(
    collection_id: Optional[int] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    focus: Optional[str] = None,
    depth: int = 1,
    limit: int = 48,
) -> dict:
    return library.subject_graph(
        collection_id=collection_id,
        author=author,
        subject=subject,
        focus=focus,
        depth=depth,
        limit=limit,
    )


@app.post("/api/items/{item_id}/collections/{cid}")
def api_assign(item_id: int, cid: int) -> dict:
    library.assign_to_collection(item_id, cid)
    return {"ok": True}


@app.delete("/api/items/{item_id}/collections/{cid}")
def api_unassign(item_id: int, cid: int) -> dict:
    library.remove_from_collection(item_id, cid)
    return {"ok": True}


_MIME_BY_EXT = {
    "pdf":  "application/pdf",
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
    "azw3": "application/vnd.amazon.ebook",
    "djvu": "image/vnd.djvu",
    "fb2":  "application/x-fictionbook+xml",
    "rtf":  "application/rtf",
    "cbz":  "application/x-cbz",
    "cbr":  "application/x-cbr",
}


@app.get("/api/items/{item_id}/file")
def api_get_file(item_id: int):
    item = library.get_item(item_id)
    if item and not item.get("file_path"):
        catalog.sync_primary_file(item_id)
        item = library.get_item(item_id)
    if not item or not item.get("file_path"):
        raise HTTPException(404, "file not found")
    p = Path(item["file_path"])
    if not p.exists():
        raise HTTPException(404, "file missing on disk")
    ext = p.suffix.lstrip(".").lower()
    media = _MIME_BY_EXT.get(ext, "application/octet-stream")
    # `inline` lets the browser render the file (PDFs especially) in an iframe.
    return FileResponse(
        str(p),
        media_type=media,
        headers={"Content-Disposition": f'inline; filename="{p.name}"'},
    )


@app.post("/api/items/{item_id}/download")
async def api_download(item_id: int) -> dict:
    asyncio.create_task(_run_download(item_id))
    return {"queued": item_id}


@app.get("/api/insight/{kind}")
async def api_insight(kind: str, collection_id: Optional[int] = None, author: Optional[str] = None,
                      tag: Optional[str] = None, source: str = "abstract", limit: int = 80,
                      focus: Optional[str] = None, include_sub: bool = True) -> dict:
    """Insight maps over a scope: concepts | words | goals | methods (see insight.py)."""
    from . import insight

    fns = {
        "concepts": lambda: insight.concepts(collection_id, author, tag, limit=limit, focus=focus, include_sub=include_sub),
        "words": lambda: insight.words(collection_id, author, tag, source=source, limit=max(limit, 100), include_sub=include_sub),
        "goals": lambda: insight.goals(collection_id, author, tag, include_sub=include_sub),
        "methods": lambda: insight.methods(collection_id, author, tag, include_sub=include_sub),
    }
    if kind not in fns:
        raise HTTPException(404, "unknown insight map")
    return await asyncio.get_running_loop().run_in_executor(None, fns[kind])


@app.post("/api/items/{item_id}/handoff")
async def api_handoff(item_id: int) -> dict:
    """Ask the Chrome extension to fetch the PDF with the user's browser session ("Retry in Chrome").

    The plan (pages and PDF links to try) is built from the resolvers without downloading on the
    server, registered for the extension and announced on the live socket.
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: library.request_browser_handoff(item_id, progress=_on_progress))
    return result


@app.get("/api/pdf-sources/diagnose")
async def api_diagnose_sources(doi: str) -> dict:
    """Run every resolver for a DOI and report candidates, diagnostics and browser steps (Settings > Test)."""
    from . import transport
    from .sources import collect_browser_steps, collect_notes, iter_pdf_urls

    clean = catalog.normalize_doi(doi)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", clean or ""):
        raise HTTPException(400, "a DOI is required")

    def run() -> dict:
        t0 = time.perf_counter()
        with collect_notes() as notes, collect_browser_steps() as steps:
            found = [{"source": r.source, "url": r.url, "needs_browser": r.needs_browser, "referer": r.referer,
                      "note": r.note} for r in iter_pdf_urls(clean)]
        return {"doi": clean, "candidates": found, "notes": list(notes), "browser_steps": list(steps),
                "elapsed_s": round(time.perf_counter() - t0, 2), "transport": transport.transport_name(),
                "proxy": transport.proxy_for(True) or ""}

    return await asyncio.get_running_loop().run_in_executor(None, run)


@app.post("/api/items/{item_id}/open-folder")
def api_open_item_folder(item_id: int) -> dict:
    try:
        return library.open_item_folder(item_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/items/{item_id}/export-file")
def api_export_item_file(item_id: int, body: ExportIn) -> dict:
    if not body.destination.strip():
        raise HTTPException(400, "destination required")
    try:
        return library.export_item_file(item_id, body.destination)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/bulk/assign")
def api_bulk_assign(body: BulkActionIn) -> dict:
    cid = body.collection_id
    if cid is None:
        if not body.collection_name or not body.collection_name.strip():
            raise HTTPException(400, "collection_id or collection_name required")
        cid = library.create_collection(body.collection_name)["id"]
    try:
        result = library.assign_matching_to_collection(
            cid,
            author=body.author,
            subject=body.subject,
            item_ids=body.item_ids,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **result}


@app.post("/api/bulk/export-pdfs")
def api_bulk_export_pdfs(body: BulkActionIn) -> dict:
    if not body.destination or not body.destination.strip():
        raise HTTPException(400, "destination required")
    return library.export_matching_pdfs(
        body.destination,
        author=body.author,
        subject=body.subject,
        item_ids=body.item_ids,
    )


@app.get("/api/export/list")
def api_export_list(
    author: Optional[str] = None,
    subject: Optional[str] = None,
) -> Response:
    csv_text = library.export_matching_list_csv(author=author, subject=subject)
    label = author or subject or "library"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "library"
    return Response(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}_reviewhunter.csv"'},
    )


# ----------- API: storage / Google Drive sync folders -----------

@app.get("/api/storage")
def api_storage() -> dict:
    return library.google_drive_status()


@app.post("/api/storage/export-google-drive")
def api_export_google_drive(body: Optional[StoragePathIn] = None) -> dict:
    destination = (body.destination if body else None) or ""
    if not destination.strip():
        drive = default_google_drive_root()
        if not drive:
            raise HTTPException(400, "Google Drive sync folder not found")
        destination = str(drive)
    return library.export_full_library(destination)


@app.post("/api/storage/use-google-drive")
def api_use_google_drive(body: Optional[StoragePathIn] = None) -> dict:
    destination = (body.destination if body else None) or ""
    if not destination.strip():
        drive = default_google_drive_root()
        if not drive:
            raise HTTPException(400, "Google Drive sync folder not found")
        destination = str(drive / "ReviewHunter" / "papers")
    return library.configure_papers_directory(
        destination,
        migrate_existing=body.migrate_existing if body else True,
    )


# ----------- API: search & add -----------

@app.get("/api/search")
async def api_search(
    q: str,
    kind: str = "article",
    rows: int = 25,
    offset: int = 0,
    sort: Optional[str] = None,
) -> dict:
    """Unified discovery endpoint.

    kind:
      - 'article'  CrossRef bibliographic search (default)
      - 'author'   CrossRef author search — returns the researcher's whole catalogue
      - 'book'     LibGen via libgen-api-modern (with FreeBooksAPI fallback)

    Returns ``{kind, query, total, results, source?}``.
    """
    from . import metadata
    if not q or not q.strip():
        return {"kind": kind, "query": q, "total": 0, "results": []}

    if kind == "book":
        res = await books.search(q, limit=rows)
        result_sort = sort or "pertinence"
        results = metadata.sort_results(res["results"], q, result_sort)
        return {"kind": kind, "query": q,
                "total": len(results), "results": results,
                "source": res["source"], "available": res["available"]}

    if kind == "article" and metadata.looks_like_doi(q):
        m = metadata.fetch_doi(q)
        return {"kind": kind, "query": q,
                "total": 1 if m else 0, "results": [m] if m else []}

    mode = "author" if kind == "author" else "bibliographic"
    result_sort = sort or ("newest" if kind == "author" else "pertinence")
    page = metadata.search_paged(q, rows=rows, offset=offset, mode=mode, result_sort=result_sort)
    return {"kind": kind, "query": q, **page}


@app.post("/api/add")
async def api_add(body: AddIn) -> dict:
    if not body.queries:
        raise HTTPException(400, "queries required")
    asyncio.create_task(_run_add_batch(body.queries, body.collection_id, body.auto_download))
    return {"queued": len(body.queries)}


@app.post("/api/import/folder")
def api_import_folder(folder: str) -> dict:
    return library.import_pdf_folder(folder)


# ----------- API: books (FreeBooksAPI) -----------

@app.get("/api/books/search")
async def api_books_search(q: str, limit: int = 10) -> dict:
    """Search books/publications. Tries libgen-api-modern first, falls back to
    FreeBooksAPI. Returns {available, results, source}; the UI hides the panel
    when neither backend is reachable."""
    return await books.search(q, limit=limit)


@app.get("/api/articles/search")
async def api_articles_search(q: str, limit: int = 10) -> list[dict]:
    """Sci-Mag scientific article search via libgen-api-modern. Each result
    carries a DOI plus direct download links."""
    return await books.search_articles(q, limit=limit)


@app.post("/api/books/add")
async def api_books_add(body: BookIn) -> dict:
    if not body.publications:
        raise HTTPException(400, "publications required")
    asyncio.create_task(_run_add_books(body.publications, body.collection_id, body.auto_download))
    return {"queued": len(body.publications)}


# ----------- WebSocket -----------

@app.websocket("/ws/jobs")
async def ws_jobs(ws: WebSocket) -> None:
    await ws.accept()
    q = await bus.subscribe()
    try:
        await ws.send_text(json.dumps({"event": "hello"}))
        while True:
            msg = await q.get()
            await ws.send_text(json.dumps(msg, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)


# ----------- Background helpers -----------

def _on_progress(stage: str, data: dict) -> None:
    bus.publish(stage, data)


async def _run_download(item_id: int) -> None:
    loop = asyncio.get_running_loop()
    try:
        item = await loop.run_in_executor(None, lambda: library.download_item(item_id, progress=_on_progress))
        bus.publish("item_changed", {"id": item_id, "status": item["status"] if item else "unknown"})
    except Exception as e:
        bus.publish("failed", {"id": item_id, "error": str(e)})


async def _run_add_books(pubs: list[dict], collection_id: Optional[int], auto_download: bool) -> None:
    loop = asyncio.get_running_loop()
    bus.publish("batch_start", {"total": len(pubs)})
    for i, p in enumerate(pubs, start=1):
        bus.publish("batch_progress", {"index": i, "total": len(pubs), "query": p.get("title", "")})
        try:
            item = await loop.run_in_executor(None, lambda p=p: library.add_book(p))
        except Exception as e:
            bus.publish("failed", {"query": p.get("title", ""), "error": str(e)})
            continue
        if collection_id:
            await loop.run_in_executor(
                None, lambda i=item["id"], c=collection_id: library.assign_to_collection(i, c)
            )
        bus.publish("item_added", {"id": item["id"], "title": item["title"]})
        if auto_download and item.get("mirrors"):
            await loop.run_in_executor(
                None, lambda i=item["id"]: library.download_item(i, progress=_on_progress)
            )
            bus.publish("item_changed", {"id": item["id"]})
    bus.publish("batch_done", {"total": len(pubs)})


async def _run_add_batch(queries: list[str], collection_id: Optional[int], auto_download: bool) -> None:
    loop = asyncio.get_running_loop()
    bus.publish("batch_start", {"total": len(queries)})
    for i, q in enumerate(queries, start=1):
        bus.publish("batch_progress", {"index": i, "total": len(queries), "query": q})
        try:
            item = await loop.run_in_executor(None, lambda q=q: library.add_from_query(q))
        except Exception as e:
            bus.publish("failed", {"query": q, "error": str(e)})
            continue
        if collection_id:
            await loop.run_in_executor(
                None, lambda i=item["id"], c=collection_id: library.assign_to_collection(i, c)
            )
        bus.publish("item_added", {"id": item["id"], "title": item["title"]})
        if auto_download and item.get("doi"):
            await loop.run_in_executor(
                None, lambda i=item["id"]: library.download_item(i, progress=_on_progress)
            )
            bus.publish("item_changed", {"id": item["id"]})
    bus.publish("batch_done", {"total": len(queries)})


# ----------- Static UI -----------

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Word add-in task pane (served over https://localhost:8766 by __main__).
_addin_dir = Path(__file__).resolve().parents[1] / "word_addin"
_addin_dir.mkdir(exist_ok=True)
app.mount("/addin", StaticFiles(directory=str(_addin_dir), html=True), name="addin")


_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="1" '
    'y2="1"><stop offset="0" stop-color="#0284c7"/><stop offset="1" stop-color="#14b8a6"/></linearGradient></defs>'
    '<rect width="64" height="64" rx="14" fill="url(#g)"/><text x="32" y="45" font-family="Arial" font-weight="800" '
    'font-size="38" text-anchor="middle" fill="white">R</text></svg>'
)


@app.get("/favicon.ico")
def favicon() -> Response:
    """Browsers request /favicon.ico on every page (reader, reports); serve the brand mark."""
    return Response(_FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/reader")
def reader_page():
    return FileResponse(str(_static_dir / "reader.html"))


@app.get("/")
def index():
    return FileResponse(str(_static_dir / "index.html"))


@app.exception_handler(404)
async def _404(request, exc):
    return JSONResponse({"error": "not found"}, status_code=404)
