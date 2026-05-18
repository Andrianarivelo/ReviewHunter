"""FastAPI server. Single process, single SQLite file, fans out job progress via WebSocket."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import books, drive_api, library
from .config import default_google_drive_root, library_root, papers_dir

log = logging.getLogger("doihunter.server")

app = FastAPI(title="ReviewHunter", version="0.1.0")

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


@app.on_event("startup")
async def _startup() -> None:
    bus.bind_loop(asyncio.get_running_loop())
    # First-run nicety: if the user has the legacy ./downloads/ folder, adopt those PDFs.
    legacy = Path.cwd() / "downloads"
    if legacy.exists() and library.stats()["items"] == 0:
        result = library.import_pdf_folder(legacy)
        log.info(f"first-run import from {legacy}: {result}")


# ----------- Models -----------

class CollectionIn(BaseModel):
    name: str
    parent_id: Optional[int] = None
    color: Optional[str] = None


class CollectionPatch(BaseModel):
    name: Optional[str] = None


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


class DriveConnectIn(BaseModel):
    client_secret_path: Optional[str] = None


class DriveFolderIn(BaseModel):
    folder_id: Optional[str] = None
    folder_path: Optional[str] = "ReviewHunter"
    auto_upload: Optional[bool] = None


class DriveAutoUploadIn(BaseModel):
    auto_upload: bool


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

@app.get("/api/stats")
def api_stats() -> dict:
    return {**library.stats(), "library_path": str(library_root()), "papers_path": str(papers_dir())}


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
    return {"ok": True}


@app.delete("/api/collections/{cid}")
def api_delete_collection(cid: int) -> dict:
    library.delete_collection(cid)
    return {"ok": True}


# ----------- API: items -----------

@app.get("/api/items")
def api_items(
    collection_id: Optional[int] = None,
    starred: bool = False,
    q: Optional[str] = None,
    status: Optional[str] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    return library.list_items(
        collection_id=collection_id, starred=starred, query=q, status=status,
        author=author, subject=subject, limit=limit,
    )


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


@app.get("/api/items/{item_id}")
def api_get_item(item_id: int) -> dict:
    item = library.get_item(item_id)
    if not item:
        raise HTTPException(404, "not found")
    return item


@app.patch("/api/items/{item_id}")
def api_patch_item(item_id: int, patch: ItemPatch) -> dict:
    payload: dict[str, Any] = {k: v for k, v in patch.dict().items() if v is not None}
    if "starred" in payload:
        payload["starred"] = 1 if payload["starred"] else 0
    item = library.update_item(item_id, payload)
    if not item:
        raise HTTPException(404, "not found")
    return item


@app.delete("/api/items/{item_id}")
def api_delete_item(item_id: int, delete_file: bool = False) -> dict:
    library.delete_item(item_id, delete_file=delete_file)
    return {"ok": True}


@app.post("/api/items/{item_id}/collections/{cid}")
async def api_assign(item_id: int, cid: int) -> dict:
    library.assign_to_collection(item_id, cid)
    asyncio.create_task(_run_sync_item_to_collection(item_id, cid))
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


@app.post("/api/collections/{cid}/download")
async def api_download_collection(cid: int) -> dict:
    collection = library.get_collection(cid)
    if not collection:
        raise HTTPException(404, "collection not found")
    asyncio.create_task(_run_download_collection(cid))
    return {"queued": cid, "name": collection["name"]}


@app.get("/api/collections/{cid}/zip")
def api_collection_zip(cid: int):
    try:
        payload = library.create_collection_zip(cid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if payload["added"] == 0:
        try:
            Path(payload["path"]).unlink()
        except OSError:
            pass
        raise HTTPException(400, "No downloaded PDFs found in this folder")
    return FileResponse(
        payload["path"],
        media_type="application/zip",
        filename=payload["filename"],
        background=BackgroundTask(lambda p=payload["path"]: Path(p).unlink(missing_ok=True)),
    )


@app.head("/api/collections/{cid}/zip")
def api_collection_zip_head(cid: int):
    try:
        stats = library.collection_pdf_stats(cid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if stats["available"] == 0:
        raise HTTPException(400, "No downloaded PDFs found in this folder")
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{stats['collection']['name']} PDFs.zip").strip("_")
    return Response(
        status_code=204,
        headers={
            "X-ReviewHunter-PDF-Count": str(stats["available"]),
            "Content-Disposition": f'attachment; filename="{filename or "folder-pdfs.zip"}"',
        },
    )


@app.post("/api/collections/{cid}/google-drive")
async def api_collection_google_drive(cid: int) -> dict:
    collection = library.get_collection(cid)
    if not collection:
        raise HTTPException(404, "collection not found")
    asyncio.create_task(_run_upload_collection_to_drive(cid))
    return {"queued": cid, "name": collection["name"]}


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
async def api_bulk_assign(body: BulkActionIn) -> dict:
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
    asyncio.create_task(_run_sync_collection(cid))
    return {"ok": True, **result}


@app.post("/api/bulk/export-pdfs")
def api_bulk_export_pdfs(body: BulkActionIn) -> dict:
    if not body.destination or not body.destination.strip():
        raise HTTPException(400, "destination required")
    return library.export_matching_pdfs(
        body.destination,
        collection_id=body.collection_id,
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


@app.get("/api/storage/pick-folder")
def api_pick_storage_folder(title: str = "Select folder", initial: str = "") -> dict:
    """Open a native folder picker for the local desktop app."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - depends on local GUI support
        raise HTTPException(501, "Native folder picker is unavailable") from exc

    start = initial.strip() or str(default_google_drive_root() or papers_dir() or Path.home())
    try:
        start_path = Path(start).expanduser()
        if not start_path.exists():
            start_path = start_path.parent if start_path.parent.exists() else Path.home()
    except OSError:
        start_path = Path.home()

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title=title[:120] or "Select folder",
            initialdir=str(start_path),
            mustexist=False,
        )
    finally:
        root.destroy()
    return {"path": selected or ""}


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


# ----------- API: Google Drive OAuth/API -----------

@app.get("/api/google-drive/status")
def api_google_drive_status() -> dict:
    return drive_api.status()


@app.get("/api/google-drive/pick-client-secret")
def api_pick_google_client_secret(initial: str = "") -> dict:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - depends on local GUI support
        raise HTTPException(501, "Native file picker is unavailable") from exc

    start = initial.strip() or str(Path.home())
    try:
        start_path = Path(start).expanduser()
        if start_path.is_file():
            start_dir = start_path.parent
        elif start_path.exists():
            start_dir = start_path
        else:
            start_dir = Path.home()
    except OSError:
        start_dir = Path.home()

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        selected = filedialog.askopenfilename(
            parent=root,
            title="Select Google OAuth client JSON",
            initialdir=str(start_dir),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return {"path": selected or ""}


@app.post("/api/google-drive/connect")
def api_google_drive_connect(body: Optional[DriveConnectIn] = None) -> dict:
    try:
        result = drive_api.connect((body.client_secret_path if body else None) or None)
        library.sync_all_collection_folders_to_drive()
        return result
    except (FileNotFoundError, ValueError, drive_api.DriveUnavailable) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/google-drive/disconnect")
def api_google_drive_disconnect() -> dict:
    return drive_api.disconnect()


@app.get("/api/google-drive/folders")
def api_google_drive_folders(q: str = "", limit: int = 20) -> dict:
    try:
        return {"folders": drive_api.list_folders(q, limit=limit)}
    except (drive_api.DriveUnavailable, drive_api.DriveNotConnected) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/google-drive/folder")
def api_google_drive_folder(body: DriveFolderIn) -> dict:
    try:
        result = drive_api.configure_folder(
            folder_id=body.folder_id or "",
            folder_path=body.folder_path or "ReviewHunter",
            auto_upload=body.auto_upload,
        )
        library.sync_all_collection_folders_to_drive()
        return result
    except (ValueError, drive_api.DriveUnavailable, drive_api.DriveNotConnected) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/google-drive/auto-upload")
def api_google_drive_auto_upload(body: DriveAutoUploadIn) -> dict:
    return drive_api.set_auto_upload(body.auto_upload)


@app.post("/api/google-drive/items/{item_id}/upload")
def api_google_drive_upload_item(item_id: int) -> dict:
    try:
        return library.upload_item_to_drive(item_id)
    except (ValueError, FileNotFoundError, drive_api.DriveUnavailable, drive_api.DriveNotConnected) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/google-drive/upload-downloaded")
def api_google_drive_upload_downloaded() -> dict:
    try:
        return library.upload_downloaded_items_to_drive()
    except (drive_api.DriveUnavailable, drive_api.DriveNotConnected) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/google-drive/export-library")
def api_google_drive_export_library() -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="reviewhunter-drive-export-") as tmp:
            export = library.export_full_library(tmp)
            upload = drive_api.upload_directory(export["path"])
        return {"ok": True, "export": export, "upload": upload}
    except (FileNotFoundError, drive_api.DriveUnavailable, drive_api.DriveNotConnected) as exc:
        raise HTTPException(400, str(exc)) from exc


# ----------- API: search & add -----------

@app.get("/api/search")
async def api_search(
    q: str,
    kind: str = "article",
    rows: int = 25,
    offset: int = 0,
    sort: Optional[str] = None,
    exact: bool = False,
    title_contains: bool = False,
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
    exact = exact or metadata.is_exact_query(q)
    query_text = metadata.strip_exact_quotes(q) if exact else q

    if kind == "book":
        search_limit = max(rows, 100) if (exact or title_contains) else rows
        res = await books.search(query_text, limit=search_limit)
        result_sort = sort or "pertinence"
        results = res["results"]
        if exact:
            results = metadata.filter_exact_results(results, query_text)
        if title_contains:
            results = metadata.filter_title_contains_results(results, query_text)
        results = metadata.sort_results(results, query_text, result_sort)
        return {"kind": kind, "query": q,
                "total": len(results), "results": results[offset: offset + rows],
                "source": res["source"], "available": res["available"]}

    if kind == "article" and (metadata.looks_like_doi(query_text) or metadata.looks_like_url(query_text)):
        m = metadata.fetch_doi(query_text) if metadata.looks_like_doi(query_text) else metadata.fetch_url_metadata(query_text)
        return {"kind": kind, "query": q,
                "total": 1 if m else 0, "results": [m] if m else [], "source": "DOI/link lookup"}

    mode = "author" if kind == "author" else "bibliographic"
    result_sort = sort or ("newest" if kind == "author" else "pertinence")
    page = metadata.search_paged(
        query_text,
        rows=rows,
        offset=offset,
        mode=mode,
        result_sort=result_sort,
        exact=exact,
        title_contains=title_contains and kind != "author",
    )
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
async def api_books_search(q: str, limit: int = 10, exact: bool = False, title_contains: bool = False) -> dict:
    """Search books/publications. Tries libgen-api-modern first, falls back to
    FreeBooksAPI. Returns {available, results, source}; the UI hides the panel
    when neither backend is reachable."""
    from . import metadata
    exact = exact or metadata.is_exact_query(q)
    query_text = metadata.strip_exact_quotes(q) if exact else q
    res = await books.search(query_text, limit=max(limit, 100) if (exact or title_contains) else limit)
    if exact:
        res["results"] = metadata.filter_exact_results(res["results"], query_text)
    if title_contains:
        res["results"] = metadata.filter_title_contains_results(res["results"], query_text)
    if exact or title_contains:
        res["results"] = res["results"][:limit]
    return res


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
        if item:
            for cid in item.get("collections") or []:
                await loop.run_in_executor(None, lambda i=item_id, c=cid: library.sync_item_to_collection_drive(i, c))
    except Exception as e:
        bus.publish("failed", {"id": item_id, "error": str(e)})


async def _run_sync_item_to_collection(item_id: int, collection_id: int) -> None:
    loop = asyncio.get_running_loop()
    try:
        uploaded = await loop.run_in_executor(
            None, lambda: library.sync_item_to_collection_drive(item_id, collection_id)
        )
        if uploaded:
            bus.publish("drive_uploaded", {"id": item_id, "link": uploaded.get("webViewLink") or ""})
    except Exception as e:
        bus.publish("drive_failed", {"id": item_id, "error": str(e)})


async def _run_sync_collection(collection_id: int) -> None:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: library.sync_collection_to_drive(collection_id))
        if result.get("ok") and result.get("synced"):
            bus.publish("drive_uploaded", {"id": collection_id, "link": result.get("folder", {}).get("webViewLink") or ""})
    except Exception as e:
        bus.publish("drive_failed", {"id": collection_id, "error": str(e)})


async def _run_upload_collection_to_drive(collection_id: int) -> None:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: library.upload_collection_to_drive(collection_id))
        bus.publish("drive_uploaded", {
            "id": collection_id,
            "link": (result.get("folder") or {}).get("webViewLink") or "",
            "uploaded": result.get("uploaded", 0),
            "skipped": result.get("skipped", 0),
        })
        bus.publish("batch_done", {"total": result.get("uploaded", 0)})
    except Exception as e:
        bus.publish("drive_failed", {"id": collection_id, "error": str(e)})


async def _run_download_collection(collection_id: int) -> None:
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, lambda: library.list_items(collection_id=collection_id, limit=10000))
    bus.publish("batch_start", {"total": len(items)})
    for index, item in enumerate(items, start=1):
        title = item.get("title") or f"Item {item.get('id')}"
        bus.publish("batch_progress", {"index": index, "total": len(items), "query": title})
        try:
            downloaded = await loop.run_in_executor(
                None, lambda i=item["id"]: library.download_item(i, progress=_on_progress)
            )
            bus.publish("item_changed", {"id": item["id"], "status": downloaded["status"] if downloaded else "unknown"})
            await loop.run_in_executor(
                None, lambda i=item["id"], c=collection_id: library.sync_item_to_collection_drive(i, c)
            )
        except Exception as e:
            bus.publish("failed", {"id": item.get("id"), "query": title, "error": str(e)})
    bus.publish("batch_done", {"total": len(items)})


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
            await loop.run_in_executor(
                None, lambda i=item["id"], c=collection_id: library.sync_item_to_collection_drive(i, c)
            )
        bus.publish("item_added", {"id": item["id"], "title": item["title"]})
        if auto_download and item.get("mirrors"):
            await loop.run_in_executor(
                None, lambda i=item["id"]: library.download_item(i, progress=_on_progress)
            )
            if collection_id:
                await loop.run_in_executor(
                    None, lambda i=item["id"], c=collection_id: library.sync_item_to_collection_drive(i, c)
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
            await loop.run_in_executor(
                None, lambda i=item["id"], c=collection_id: library.sync_item_to_collection_drive(i, c)
            )
        bus.publish("item_added", {"id": item["id"], "title": item["title"]})
        if auto_download and item.get("doi"):
            await loop.run_in_executor(
                None, lambda i=item["id"]: library.download_item(i, progress=_on_progress)
            )
            if collection_id:
                await loop.run_in_executor(
                    None, lambda i=item["id"], c=collection_id: library.sync_item_to_collection_drive(i, c)
                )
            bus.publish("item_changed", {"id": item["id"]})
    bus.publish("batch_done", {"total": len(queries)})


# ----------- Static UI -----------

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(_static_dir / "index.html"))


@app.exception_handler(404)
async def _404(request, exc):
    return JSONResponse({"error": "not found"}, status_code=404)
