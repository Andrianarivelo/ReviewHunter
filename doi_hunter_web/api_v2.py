"""REST API v2: every Zotero-parity feature exposed to the web UI and the Word add-in.

Grouped by resource; each handler is a thin wrapper around ``catalog``, ``formats``,
``fulltext``, ``duplicates``, ``gdrive`` or ``addin`` so the logic stays testable
without HTTP. Long-running jobs (identifier batches, PDF imports) run in background
tasks and report progress on the ``/ws/jobs`` WebSocket through ``request.app.state.bus``.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from . import catalog, config, duplicates, formats, fulltext, gdrive, identifiers, zotero_import
from .csl import item_to_csl
from .db import get_conn

log = logging.getLogger("doihunter.api")
router = APIRouter(prefix="/api")

STATIC_DIR = Path(__file__).parent / "static"
BUNDLED_STYLES = STATIC_DIR / "csl" / "styles"
BUNDLED_LOCALES = STATIC_DIR / "csl" / "locales"


def _bus(request: Request):
    return getattr(request.app.state, "bus", None)


def _publish(request: Request, event: str, data: dict) -> None:
    bus = _bus(request)
    if bus is not None:
        bus.publish(event, data)


def _ids(raw: Optional[str]) -> list[int]:
    return [int(x) for x in re.split(r"[,\s]+", raw or "") if x.strip().isdigit()]


def _need(obj: Any, what: str = "not found") -> Any:
    if not obj:
        raise HTTPException(404, what)
    return obj


# ======================================================================================
# Models
# ======================================================================================

class ItemIn(BaseModel):
    item_type: str = "journalArticle"
    title: Optional[str] = None
    creators: list[dict] = []
    field_values: dict[str, Any] = {}
    tags: list[str] = []
    collections: list[int] = []


class IdsIn(BaseModel):
    item_ids: list[int] = []
    note_ids: list[int] = []


class TagsBulkIn(BaseModel):
    item_ids: list[int]
    add: list[str] = []
    remove: list[str] = []


class TagPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    clear_color: bool = False


class NoteIn(BaseModel):
    item_id: Optional[int] = None
    html: str = ""


class LinkIn(BaseModel):
    url: str
    title: Optional[str] = None


class RenameIn(BaseModel):
    title: str


class AnnotationIn(BaseModel):
    type: str = "highlight"
    color: Optional[str] = None
    page: int = 1
    text: Optional[str] = None
    comment: Optional[str] = None
    position: dict[str, Any] = {}
    sort_index: Optional[str] = None


class AnnotationPatch(BaseModel):
    color: Optional[str] = None
    comment: Optional[str] = None
    text: Optional[str] = None
    type: Optional[str] = None
    position: Optional[dict[str, Any]] = None


class SearchIn(BaseModel):
    name: str
    match: str = "all"
    conditions: list[dict[str, Any]] = []


class MergeIn(BaseModel):
    master_id: int
    other_ids: list[int]
    field_choices: dict[str, int] = {}


class CollectionMove(BaseModel):
    parent_id: Optional[int] = None


class CollectionPatchV2(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    parent_id: Optional[int] = None
    move_to_root: bool = False


class IdentifyIn(BaseModel):
    text: str
    collection_id: Optional[int] = None
    tags: list[str] = []
    auto_download: bool = True


class ImportTextIn(BaseModel):
    text: str
    format: Optional[str] = None
    collection_id: Optional[int] = None
    create_collection: Optional[str] = None


class ZoteroImportIn(BaseModel):
    data_dir: Optional[str] = None
    copy_files: bool = True
    include_trash: bool = False


class StyleInstallIn(BaseModel):
    url: str


class DesktopEnableIn(BaseModel):
    root: str
    papers_in_drive: bool = False


class RestoreIn(BaseModel):
    path: Optional[str] = None
    drive_file_id: Optional[str] = None


class OpenPathIn(BaseModel):
    path: str


# ======================================================================================
# Meta
# ======================================================================================

@router.get("/item-types")
def item_types() -> dict:
    return catalog.item_types_payload()


@router.get("/stats")
def stats() -> dict:
    return {**catalog.stats(), "library_path": str(config.library_root()), "papers_path": str(config.papers_dir()),
            "fulltext": fulltext.fulltext_stats()}


@router.get("/settings")
def get_settings() -> dict:
    return config.get_settings()


@router.patch("/settings")
def patch_settings(patch: dict[str, Any]) -> dict:
    return config.update_settings(patch)


# ======================================================================================
# Items
# ======================================================================================

@router.get("/items")
def list_items(
    collection_id: Optional[int] = None,
    include_sub: bool = True,
    unfiled: bool = False,
    starred: bool = False,
    trash: bool = False,
    status: Optional[str] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    tag: list[str] = Query(default=[]),
    item_type: Optional[str] = None,
    q: Optional[str] = None,
    mode: str = "titleCreatorYear",
    saved_search_id: Optional[int] = None,
    conditions: Optional[str] = None,
    match: str = "all",
    has_notes: bool = False,
    sort: str = "added",
    direction: str = "desc",
    limit: int = 5000,
    offset: int = 0,
    full: bool = False,
) -> list[dict]:
    """Item list. Compact by default (no abstract/mirrors); pass full=true for complete records."""
    conds = json.loads(conditions) if conditions else None
    return catalog.query_items(
        collection_id=collection_id, include_subcollections=include_sub, unfiled=unfiled, starred=starred,
        trash=trash, status=status, author=author, subject=subject, tags=tag, item_type=item_type, q=q, mode=mode,
        saved_search_id=saved_search_id, conditions=conds, match=match, has_notes=has_notes, sort=sort,
        direction=direction, limit=min(limit, 20000), offset=offset, compact=not full,
    )


@router.post("/items")
def create_item(body: ItemIn, request: Request) -> dict:
    payload = body.model_dump()
    payload["title"] = body.title or body.field_values.get("title") or "Untitled"
    item = catalog.create_item(payload, dedupe_doi=bool(body.field_values.get("DOI")))
    _publish(request, "item_added", {"id": item["id"], "title": item["title"]})
    return item


@router.get("/items/{item_id}")
def get_item(item_id: int) -> dict:
    return _need(catalog.get_item(item_id))


@router.patch("/items/{item_id}")
def patch_item(item_id: int, patch: dict[str, Any]) -> dict:
    try:
        return _need(catalog.patch_item(item_id, patch))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.delete("/items/{item_id}")
def delete_item(item_id: int, permanent: bool = False, delete_file: bool = True) -> dict:
    if permanent:
        return {"deleted": catalog.delete_items_permanently([item_id], delete_files=delete_file)}
    return {"trashed": catalog.trash_items([item_id])}


@router.post("/items/trash")
def trash_items(body: IdsIn) -> dict:
    n = catalog.trash_items(body.item_ids)
    for nid in body.note_ids:
        catalog.delete_note(nid)
    return {"trashed": n + len(body.note_ids)}


@router.post("/items/restore")
def restore_items(body: IdsIn) -> dict:
    n = catalog.restore_items(body.item_ids)
    for nid in body.note_ids:
        catalog.restore_note(nid)
    return {"restored": n + len(body.note_ids)}


@router.post("/items/delete-permanently")
def delete_permanently(body: IdsIn) -> dict:
    n = catalog.delete_items_permanently(body.item_ids)
    for nid in body.note_ids:
        catalog.delete_note(nid, permanent=True)
    return {"deleted": n + len(body.note_ids)}


@router.post("/trash/empty")
def empty_trash() -> dict:
    return {"deleted": catalog.empty_trash()}


@router.get("/trash/notes")
def trashed_notes() -> list[dict]:
    return catalog.list_notes(trash=True)


@router.post("/items/{item_id}/duplicate")
def duplicate_item(item_id: int) -> dict:
    src = _need(catalog.get_item(item_id))
    payload = {k: src.get(k) for k in ("item_type", "title", "creators", "journal", "publisher", "volume", "issue",
                                       "pages", "abstract", "url", "language", "short_title", "extra", "issn",
                                       "series", "edition", "place", "num_pages", "date", "year", "fields", "isbn")}
    payload["tags"] = [t["name"] for t in src["tags"]]
    payload["collections"] = src["collections"]
    return catalog.create_item(payload, dedupe_doi=False)


@router.post("/items/{item_id}/citation-key")
def regenerate_key(item_id: int) -> dict:
    return _need(catalog.regenerate_citation_key(item_id))


@router.post("/items/{item_id}/retrieve-metadata")
async def retrieve_metadata(item_id: int, request: Request) -> dict:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, fulltext.retrieve_metadata, item_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    _publish(request, "item_changed", {"id": item_id})
    return result


@router.post("/items/{item_id}/rename-files")
def rename_files(item_id: int) -> dict:
    return catalog.rename_files_from_metadata(item_id)


@router.post("/items/{item_id}/find-pdf")
async def find_pdf(item_id: int, request: Request) -> dict:
    from .library import download_item

    loop = asyncio.get_running_loop()

    def progress(stage: str, data: dict) -> None:
        _publish(request, stage, data)

    asyncio.create_task(asyncio.to_thread(download_item, item_id, progress=progress))
    return {"queued": item_id}


# ---------------------------- identifiers ----------------------------

@router.post("/items/identify")
async def identify(body: IdentifyIn, request: Request) -> dict:
    """Magic wand: one identifier / title per line (DOI, ISBN, PMID, arXiv, or free title)."""
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", body.text) if ln.strip()]
    if not lines:
        raise HTTPException(400, "nothing to add")
    asyncio.create_task(_identify_batch(request, lines, body.collection_id, body.tags, body.auto_download))
    return {"queued": len(lines)}


async def _identify_batch(request: Request, lines: list[str], collection_id: Optional[int], tags: list[str],
                          auto_download: bool) -> None:
    from .library import add_from_query, download_item

    loop = asyncio.get_running_loop()
    _publish(request, "batch_start", {"total": len(lines)})
    for i, line in enumerate(lines, start=1):
        _publish(request, "batch_progress", {"index": i, "total": len(lines), "query": line})
        try:
            kind, _value = identifiers.classify(line)
            if kind == "title":
                item = await loop.run_in_executor(None, add_from_query, line)
                pdf_url = None
            else:
                payload = await loop.run_in_executor(None, identifiers.resolve, line)
                if not payload:
                    raise ValueError(f"could not resolve {kind} {line}")
                pdf_url = payload.pop("_pdf_url", None)
                item = await loop.run_in_executor(None, catalog.create_item, payload)
            links = {"tags": tags, "collections": [collection_id] if collection_id else []}
            await loop.run_in_executor(None, catalog._attach_links, item["id"], links)
            _publish(request, "item_added", {"id": item["id"], "title": item["title"]})
            if auto_download and (item.get("doi") or item.get("mirrors")):
                def progress(stage: str, data: dict) -> None:
                    _publish(request, stage, data)

                await loop.run_in_executor(None, lambda iid=item["id"]: download_item(iid, progress=progress))
                _publish(request, "item_changed", {"id": item["id"]})
            elif auto_download and pdf_url:
                await loop.run_in_executor(None, _fetch_pdf_url, item["id"], pdf_url)
                _publish(request, "item_changed", {"id": item["id"]})
        except Exception as e:
            _publish(request, "failed", {"query": line, "error": str(e)})
    _publish(request, "batch_done", {"total": len(lines)})


def _fetch_pdf_url(item_id: int, url: str) -> None:
    try:
        r = requests.get(url, timeout=(15, 60), headers={"User-Agent": "Mozilla/5.0 ReviewHunter"})
        if r.ok and r.content[:5] == b"%PDF-":
            catalog.store_file_bytes(item_id, r.content, "file.pdf", url=url)
            fulltext.index_item(item_id)
    except requests.RequestException as e:
        log.info(f"pdf fetch failed {url}: {e}")


# ======================================================================================
# Tags
# ======================================================================================

@router.get("/tags")
def list_tags(item_ids: Optional[str] = None) -> list[dict]:
    return catalog.list_tags(item_ids=_ids(item_ids) if item_ids is not None else None)


@router.post("/items/{item_id}/tags")
def add_tag(item_id: int, body: dict[str, Any]) -> dict:
    catalog.add_tag(item_id, str(body.get("name") or ""))
    return _need(catalog.get_item(item_id))


@router.delete("/items/{item_id}/tags/{name}")
def remove_tag(item_id: int, name: str) -> dict:
    catalog.remove_tag(item_id, name)
    return _need(catalog.get_item(item_id))


@router.post("/tags/bulk")
def bulk_tags(body: TagsBulkIn) -> dict:
    return catalog.bulk_tags(body.item_ids, body.add, body.remove)


@router.patch("/tags/{tag_id}")
def patch_tag(tag_id: int, body: TagPatch) -> dict:
    return _need(catalog.update_tag(tag_id, name=body.name, color=body.color, clear_color=body.clear_color))


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int) -> dict:
    catalog.delete_tag(tag_id)
    return {"ok": True}


@router.post("/tags/delete-automatic")
def delete_automatic() -> dict:
    return {"deleted": catalog.delete_automatic_tags()}


# ======================================================================================
# Notes
# ======================================================================================

@router.get("/notes")
def list_notes(item_id: Optional[int] = None, standalone: bool = False, q: Optional[str] = None) -> list[dict]:
    notes = catalog.list_notes(item_id=item_id, standalone=standalone)
    if q:
        notes = [n for n in notes if q.lower() in (n["html"] or "").lower()]
    return notes


@router.post("/notes")
def create_note(body: NoteIn) -> dict:
    return catalog.create_note(body.item_id, body.html)


@router.get("/notes/{note_id}")
def get_note(note_id: int) -> dict:
    return _need(catalog.get_note(note_id))


@router.patch("/notes/{note_id}")
def update_note(note_id: int, body: NoteIn) -> dict:
    return _need(catalog.update_note(note_id, body.html))


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, permanent: bool = False) -> dict:
    catalog.delete_note(note_id, permanent=permanent)
    return {"ok": True}


# ======================================================================================
# Attachments & annotations
# ======================================================================================

@router.get("/items/{item_id}/attachments")
def list_attachments(item_id: int) -> list[dict]:
    return catalog.list_attachments(item_id)


@router.post("/items/{item_id}/attachments")
async def upload_attachment(item_id: int, request: Request, file: UploadFile = File(...),
                            title: Optional[str] = Form(None)) -> dict:
    _need(catalog.get_item(item_id, full=False), "item not found")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    att = catalog.store_file_bytes(item_id, data, file.filename or "file.pdf", title=title)
    from .library import _index_fulltext_async

    _index_fulltext_async(item_id)
    _publish(request, "item_changed", {"id": item_id})
    return att


@router.post("/items/{item_id}/links")
def add_link(item_id: int, body: LinkIn) -> dict:
    if not re.match(r"^https?://", body.url):
        raise HTTPException(400, "only http(s) links")
    return catalog.add_link_attachment(item_id, body.url, body.title)


@router.get("/attachments/{att_id}")
def get_attachment(att_id: int) -> dict:
    att = _need(catalog.get_attachment(att_id))
    att["exists"] = bool(att.get("path")) and Path(att["path"]).exists()
    return att


@router.get("/attachments/{att_id}/file")
def attachment_file(att_id: int):
    att = _need(catalog.get_attachment(att_id))
    if not att.get("path") or not Path(att["path"]).exists():
        raise HTTPException(404, "file missing on disk")
    p = Path(att["path"])
    media = att.get("mime") or catalog.MIME_BY_EXT.get(p.suffix.lstrip(".").lower(), "application/octet-stream")
    headers = {"Content-Disposition": f'inline; filename="{p.name.encode("ascii", "ignore").decode()}"'}
    if media == "text/html":
        # Snapshots are untrusted web content: sandbox them (no scripts, no same-origin access).
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src * data:; style-src * 'unsafe-inline'; font-src * data:"
    return FileResponse(str(p), media_type=media, headers=headers)


@router.post("/attachments/{att_id}/open")
def open_attachment(att_id: int) -> dict:
    att = _need(catalog.get_attachment(att_id))
    if att["kind"] == "link":
        import webbrowser

        webbrowser.open(att["url"])
        return {"ok": True}
    if not att.get("path") or not Path(att["path"]).exists():
        raise HTTPException(404, "file missing on disk")
    from .library import _open_path

    _open_path(Path(att["path"]))
    return {"ok": True}


@router.post("/attachments/{att_id}/show")
def show_attachment(att_id: int) -> dict:
    att = _need(catalog.get_attachment(att_id))
    if not att.get("path"):
        raise HTTPException(404, "no file")
    p = Path(att["path"])
    if os.name == "nt" and p.exists():
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        from .library import _open_path

        _open_path(p.parent)
    return {"ok": True}


@router.patch("/attachments/{att_id}")
def rename_attachment(att_id: int, body: RenameIn) -> dict:
    return _need(catalog.rename_attachment(att_id, body.title))


@router.delete("/attachments/{att_id}")
def delete_attachment(att_id: int, delete_file: bool = True) -> dict:
    catalog.delete_attachment(att_id, delete_file=delete_file)
    return {"ok": True}


@router.post("/attachments/{att_id}/reindex")
def reindex_attachment(att_id: int) -> dict:
    att = _need(catalog.get_attachment(att_id))
    return {"characters": fulltext.index_attachment(att)}


@router.get("/attachments/{att_id}/annotations")
def list_annotations(att_id: int) -> list[dict]:
    return catalog.list_annotations(att_id)


@router.post("/attachments/{att_id}/annotations")
def create_annotation(att_id: int, body: AnnotationIn) -> dict:
    _need(catalog.get_attachment(att_id))
    return catalog.create_annotation(att_id, body.model_dump())


@router.patch("/annotations/{ann_id}")
def update_annotation(ann_id: int, body: AnnotationPatch) -> dict:
    return _need(catalog.update_annotation(ann_id, {k: v for k, v in body.model_dump().items() if v is not None}))


@router.delete("/annotations/{ann_id}")
def delete_annotation(ann_id: int) -> dict:
    catalog.delete_annotation(ann_id)
    return {"ok": True}


@router.post("/attachments/{att_id}/annotations/to-note")
def annotations_to_note(att_id: int) -> dict:
    return _need(catalog.annotations_to_note(att_id))


# ======================================================================================
# Related, collections, saved searches, duplicates
# ======================================================================================

@router.post("/items/{item_id}/related/{other_id}")
def relate(item_id: int, other_id: int) -> dict:
    catalog.relate(item_id, other_id)
    return {"ok": True}


@router.delete("/items/{item_id}/related/{other_id}")
def unrelate(item_id: int, other_id: int) -> dict:
    catalog.unrelate(item_id, other_id)
    return {"ok": True}


@router.patch("/collections/{cid}/v2")
def patch_collection(cid: int, body: CollectionPatchV2) -> dict:
    conn = get_conn()
    if body.name is not None and body.name.strip():
        conn.execute("UPDATE collections SET name=? WHERE id=?", (body.name.strip(), cid))
    if body.color is not None:
        conn.execute("UPDATE collections SET color=? WHERE id=?", (body.color or None, cid))
    if body.parent_id is not None or body.move_to_root:
        try:
            catalog.move_collection(cid, None if body.move_to_root else body.parent_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/saved-searches")
def saved_searches() -> list[dict]:
    return catalog.list_saved_searches()


@router.post("/saved-searches")
def create_saved_search(body: SearchIn) -> dict:
    return catalog.save_search(body.name, body.conditions, body.match)


@router.put("/saved-searches/{search_id}")
def update_saved_search(search_id: int, body: SearchIn) -> dict:
    return catalog.save_search(body.name, body.conditions, body.match, search_id)


@router.delete("/saved-searches/{search_id}")
def delete_saved_search(search_id: int) -> dict:
    catalog.delete_saved_search(search_id)
    return {"ok": True}


@router.get("/duplicates")
def find_duplicates() -> list[dict]:
    return duplicates.find_duplicates()


@router.post("/duplicates/merge")
def merge_duplicates(body: MergeIn) -> dict:
    try:
        return duplicates.merge(body.master_id, body.other_ids, field_choices=body.field_choices)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ======================================================================================
# Full text
# ======================================================================================

@router.get("/fulltext/stats")
def fulltext_stats() -> dict:
    return fulltext.fulltext_stats()


@router.post("/fulltext/reindex")
async def fulltext_reindex(force: bool = False) -> dict:
    return await asyncio.to_thread(fulltext.reindex_all, force=force)


# ======================================================================================
# Import / export
# ======================================================================================

def _select_items(ids: Optional[str], collection_id: Optional[int], tag: list[str], q: Optional[str],
                  saved_search_id: Optional[int]) -> list[dict]:
    if ids:
        items = [catalog.get_item(i) for i in _ids(ids)]
        return [i for i in items if i]
    rows = catalog.query_items(collection_id=collection_id, tags=tag, q=q, mode="fields",
                               saved_search_id=saved_search_id, limit=100000)
    return [catalog.get_item(r["id"]) for r in rows]


@router.get("/export")
def export(format: str = "bibtex", ids: Optional[str] = None, collection_id: Optional[int] = None,
           tag: list[str] = Query(default=[]), q: Optional[str] = None, saved_search_id: Optional[int] = None,
           download: bool = True) -> Response:
    if format not in formats.EXPORTERS:
        raise HTTPException(400, f"unknown format {format}")
    fn, media, ext = formats.EXPORTERS[format]
    items = _select_items(ids, collection_id, tag, q, saved_search_id)
    body = fn(items)
    name = "ReviewHunter-export"
    if collection_id:
        col = next((c for c in catalog.list_collections() if c["id"] == collection_id), None)
        if col:
            name = re.sub(r"[^\w.-]+", "_", col["name"]).strip("_") or name
    headers = {"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}.{ext}"'}
    return Response(body, media_type=f"{media}; charset=utf-8", headers=headers)


@router.post("/import/file")
async def import_file(request: Request, file: UploadFile = File(...), collection_id: Optional[int] = Form(None),
                      new_collection: bool = Form(True)) -> dict:
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    return await _import_text(request, text, None, file.filename or "", collection_id,
                              Path(file.filename or "Import").stem if new_collection and not collection_id else None)


@router.post("/import/text")
async def import_text(body: ImportTextIn, request: Request) -> dict:
    return await _import_text(request, body.text, body.format, "", body.collection_id, body.create_collection)


async def _import_text(request: Request, text: str, fmt: Optional[str], filename: str, collection_id: Optional[int],
                       create_collection: Optional[str]) -> dict:
    fmt = fmt or formats.detect_format(text, filename)
    if fmt == "identifiers":
        body = IdentifyIn(text=text, collection_id=collection_id)
        return {"format": fmt, **(await identify(body, request))}
    try:
        payloads = await asyncio.to_thread(formats.parse, text, fmt, filename)
    except Exception as e:
        raise HTTPException(400, f"could not parse {fmt}: {e}")
    if create_collection:
        from .library import create_collection as mk

        collection_id = mk(f"{create_collection} (imported {datetime.now():%Y-%m-%d})")["id"]

    def run() -> dict:
        created = existing = attached = 0
        for p in payloads:
            linked_file = p.pop("_file", "")
            if collection_id:
                p["collections"] = [collection_id]
            item = catalog.create_item(p)
            if item.get("_existing"):
                existing += 1
                catalog._attach_links(item["id"], {"tags": p.get("tags"), "collections": p.get("collections")})
            else:
                created += 1
            # Zotero/JabRef style file fields: "Description:C\\path\\file.pdf:application/pdf" or a plain path.
            for candidate in _file_field_paths(linked_file):
                if Path(candidate).is_file():
                    catalog.register_file(item["id"], candidate)
                    attached += 1
                    break
        return {"created": created, "existing": existing, "attached_files": attached}

    result = await asyncio.to_thread(run)
    _publish(request, "batch_done", {"total": len(payloads)})
    return {"format": fmt, "collection_id": collection_id, "total": len(payloads), **result}


def _file_field_paths(value: str) -> list[str]:
    if not value:
        return []
    out = []
    for part in re.split(r";(?=[^;]*:)", value):
        part = part.strip()
        if part.startswith("file:///"):
            out.append(part[8:])
            continue
        pieces = part.split(":")
        if len(pieces) >= 3 and len(pieces[1]) == 1:  # "desc:C:\path:mime" -> drive letter split
            out.append(pieces[1] + ":" + pieces[2])
        elif len(pieces) >= 2 and pieces[-1].count("/") == 1 and "/" in pieces[-1]:
            out.append(":".join(pieces[1:-1]))
        else:
            out.append(part)
    return out


@router.post("/import/pdfs")
async def import_pdfs(request: Request, files: list[UploadFile] = File(...),
                      collection_id: Optional[int] = Form(None)) -> dict:
    """Drag-and-drop PDFs: one item per file, then 'Retrieve Metadata for PDF' in the background."""
    created = []
    for f in files[:500]:
        data = await f.read()
        if data[:5] != b"%PDF-":
            continue
        title = Path(f.filename or "Imported PDF").stem.replace("_", " ").strip() or "Imported PDF"
        item = catalog.create_item({"item_type": "document", "title": title, "status": "downloaded",
                                    "collections": [collection_id] if collection_id else []}, dedupe_doi=False)
        catalog.store_file_bytes(item["id"], data, f.filename or "file.pdf")
        created.append(item["id"])
    asyncio.create_task(_post_pdf_import(request, created))
    return {"imported": len(created), "item_ids": created}


async def _post_pdf_import(request: Request, item_ids: list[int]) -> None:
    for iid in item_ids:
        try:
            await asyncio.to_thread(fulltext.index_item, iid)
            res = await asyncio.to_thread(fulltext.retrieve_metadata, iid)
            if res.get("ok"):
                await asyncio.to_thread(catalog.rename_files_from_metadata, iid)
            _publish(request, "item_changed", {"id": iid, "metadata": res})
        except Exception as e:
            _publish(request, "failed", {"id": iid, "error": f"metadata: {e}"})


@router.get("/import/zotero/detect")
def zotero_detect(data_dir: Optional[str] = None) -> dict:
    """Find the Zotero data directory (default ~/Zotero) and summarise what would be imported."""
    folder = Path(data_dir).expanduser() if data_dir else zotero_import.default_data_dir()
    if not folder or not (folder / "zotero.sqlite").exists():
        return {"found": False, "data_dir": str(folder) if folder else ""}
    try:
        return {"found": True, **zotero_import.inspect(folder)}
    except Exception as e:
        raise HTTPException(400, f"cannot read Zotero database: {e}")


@router.post("/import/zotero")
async def zotero_run(body: ZoteroImportIn, request: Request) -> dict:
    """Run the migration in the background; progress arrives as zotero_progress / zotero_done events."""
    folder = Path(body.data_dir).expanduser() if body.data_dir else zotero_import.default_data_dir()
    if not folder or not (folder / "zotero.sqlite").exists():
        raise HTTPException(400, "zotero.sqlite not found; choose your Zotero data directory")

    def progress(stage: str, data: dict) -> None:
        _publish(request, stage, data)

    async def run() -> None:
        try:
            await asyncio.to_thread(zotero_import.import_library, folder, copy_files=body.copy_files,
                                    include_trash=body.include_trash, progress=progress)
            await asyncio.to_thread(fulltext.reindex_all)
        except Exception as e:
            _publish(request, "failed", {"error": f"Zotero import failed: {e}"})

    asyncio.create_task(run())
    return {"queued": True, "data_dir": str(folder)}


# ======================================================================================
# Citations (CSL)
# ======================================================================================

@router.get("/csl/items")
def csl_items(ids: Optional[str] = None, collection_id: Optional[int] = None, tag: list[str] = Query(default=[]),
              q: Optional[str] = None) -> list[dict]:
    if ids:
        items = [catalog.get_item(i, full=False) for i in _ids(ids)]
        return [item_to_csl(i) for i in items if i]
    rows = catalog.query_items(collection_id=collection_id, tags=tag, q=q, mode="fields", limit=20000)
    return [item_to_csl(r) for r in rows]


@router.get("/csl/search")
def csl_search(q: str, limit: int = 20) -> list[dict]:
    """Compact search used by the Word add-in citation dialog."""
    rows = catalog.query_items(q=q, mode="fields", limit=limit) if q.strip() else catalog.query_items(limit=limit)
    return [{"id": r["id"], "title": r["title"], "creators": r.get("creators") or [], "year": r.get("year"),
             "journal": r.get("journal"), "item_type": r["item_type"], "citation_key": r.get("citation_key"),
             "csl": item_to_csl(r)} for r in rows]


def _user_styles_dir() -> Path:
    p = config.library_root() / "styles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _style_meta(path: Path) -> Optional[dict]:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:6000]
    except OSError:
        return None
    title = re.search(r"<title>(.*?)</title>", head, re.S)
    note = re.search(r'<category citation-format="([\w-]+)"', head)
    parent = re.search(r'<link href="[^"]*/([\w-]+)" rel="independent-parent"', head)
    return {"id": path.stem, "title": html.unescape(title.group(1).strip()) if title else path.stem,
            "format": note.group(1) if note else "", "dependent_on": parent.group(1) if parent else None}


@router.get("/csl/styles")
def list_styles() -> list[dict]:
    styles: dict[str, dict] = {}
    for folder, source in ((BUNDLED_STYLES, "bundled"), (_user_styles_dir(), "user")):
        if folder.exists():
            for f in folder.glob("*.csl"):
                meta = _style_meta(f)
                if meta:
                    styles[meta["id"]] = {**meta, "source": source}
    return sorted(styles.values(), key=lambda s: s["title"].casefold())


@router.get("/csl/styles/{style_id}")
def get_style(style_id: str):
    if not re.fullmatch(r"[\w.-]+", style_id):
        raise HTTPException(400, "bad style id")
    for folder in (_user_styles_dir(), BUNDLED_STYLES):
        f = folder / f"{style_id.removesuffix('.csl')}.csl"
        if f.exists():
            text = f.read_text(encoding="utf-8")
            meta = _style_meta(f)
            if meta and meta["dependent_on"]:
                # Dependent styles only alias a parent: serve the parent's code.
                return get_style(meta["dependent_on"])
            return Response(text, media_type="application/xml")
    raise HTTPException(404, "style not installed")


_ALLOWED_STYLE_HOSTS = ("https://www.zotero.org/styles/", "https://raw.githubusercontent.com/citation-style-language/",
                        "https://zotero.org/styles/")


@router.post("/csl/styles/install")
def install_style(body: StyleInstallIn) -> dict:
    url = body.url.strip()
    if re.fullmatch(r"[\w-]+", url):
        url = f"https://www.zotero.org/styles/{url}"
    if not url.startswith(_ALLOWED_STYLE_HOSTS):
        raise HTTPException(400, "styles can be installed from zotero.org/styles or the CSL GitHub repository")
    r = requests.get(url, timeout=20, headers={"Accept": "application/vnd.citationstyles.style+xml"})
    if not r.ok or "<style" not in r.text:
        raise HTTPException(400, "not a CSL style")
    return _save_style(r.text)


@router.post("/csl/styles/upload")
async def upload_style(file: UploadFile = File(...)) -> dict:
    text = (await file.read()).decode("utf-8", errors="replace")
    if "<style" not in text:
        raise HTTPException(400, "not a CSL style")
    return _save_style(text)


def _save_style(text: str) -> dict:
    m = re.search(r"<id>\s*https?://www\.zotero\.org/styles/([\w-]+)\s*</id>", text)
    style_id = m.group(1) if m else re.sub(r"\W+", "-", (re.search(r"<title>(.*?)</title>", text) or [None, "style"])[1]).lower()
    target = _user_styles_dir() / f"{style_id}.csl"
    target.write_text(text, encoding="utf-8")
    meta = _style_meta(target) or {"id": style_id}
    if meta.get("dependent_on") and not any(
            (d / f"{meta['dependent_on']}.csl").exists() for d in (BUNDLED_STYLES, _user_styles_dir())):
        install_style(StyleInstallIn(url=meta["dependent_on"]))
    return {"ok": True, **meta}


@router.delete("/csl/styles/{style_id}")
def delete_style(style_id: str) -> dict:
    f = _user_styles_dir() / f"{style_id}.csl"
    if not f.exists():
        raise HTTPException(400, "only user-installed styles can be removed")
    f.unlink()
    return {"ok": True}


@router.get("/csl/locales/{lang}")
def get_locale(lang: str):
    if not re.fullmatch(r"[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,4})?", lang):
        raise HTTPException(400, "bad locale")
    f = BUNDLED_LOCALES / f"locales-{lang}.xml"
    if not f.exists():
        base = lang.split("-")[0]
        f = next(iter(sorted(BUNDLED_LOCALES.glob(f"locales-{base}-*.xml"))), BUNDLED_LOCALES / "locales-en-US.xml")
    return FileResponse(str(f), media_type="application/xml")


# ======================================================================================
# Report
# ======================================================================================

@router.get("/report", response_class=HTMLResponse)
def report(ids: Optional[str] = None, collection_id: Optional[int] = None, tag: list[str] = Query(default=[])) -> str:
    items = _select_items(ids, collection_id, tag, None, None)
    e = html.escape
    rows = []
    for it in items:
        meta = []
        for fid in catalog.ITEM_TYPES[it["item_type"]]["fields"]:
            v = it["field_values"].get(fid)
            if v and fid not in ("title", "abstractNote"):
                meta.append(f"<tr><th>{e(catalog.FIELD_DEFS[fid][0])}</th><td>{e(str(v))}</td></tr>")
        creators = "".join(f"<tr><th>{e(catalog.CREATOR_LABELS.get(c['creatorType'], c['creatorType']))}</th>"
                           f"<td>{e(catalog.creator_display(c))}</td></tr>" for c in it["creators"])
        notes = "".join(f'<div class="note">{n["html"]}</div>' for n in it["notes_list"])
        tags = ", ".join(e(t["name"]) for t in it["tags"])
        rows.append(f"""<article><h2>{e(it['title'])}</h2><table>
          <tr><th>Item Type</th><td>{e(it['type_label'])}</td></tr>{creators}{meta and ''.join(meta)}
          {f'<tr><th>Tags</th><td>{tags}</td></tr>' if tags else ''}
          <tr><th>Date Added</th><td>{e(it.get('added_at') or '')}</td></tr></table>
          {f'<h3>Abstract</h3><p>{e(it["abstract"])}</p>' if it.get('abstract') else ''}
          {f'<h3>Notes</h3>{notes}' if notes else ''}</article>""")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>ReviewHunter Report</title><style>
      body{{font:14px/1.5 "Source Serif 4",Georgia,serif;max-width:860px;margin:32px auto;padding:0 20px;color:#1b1d29}}
      article{{border-bottom:1px solid #dde1ea;padding:18px 0;break-inside:avoid}} h2{{font-size:19px;margin:0 0 8px}}
      table{{border-collapse:collapse;font:12.5px Inter,system-ui,sans-serif}} th{{text-align:right;color:#5d6479;
      font-weight:600;padding:2px 12px 2px 0;vertical-align:top;white-space:nowrap}} td{{padding:2px 0}}
      h3{{font:600 12px Inter,system-ui,sans-serif;text-transform:uppercase;letter-spacing:.06em;color:#5d6479}}
      .note{{background:#f8f9fb;border-left:3px solid #0ea5e9;padding:6px 12px;margin:6px 0}}
      @media print{{body{{margin:0}}}}</style></head><body><h1>Report ({len(items)} items)</h1>{''.join(rows)}</body></html>"""


# ======================================================================================
# Google Drive & backups
# ======================================================================================

@router.get("/drive/status")
def drive_status() -> dict:
    return gdrive.status()


@router.post("/drive/desktop/install")
def drive_install() -> dict:
    return gdrive.launch_desktop_installer()


@router.post("/drive/desktop/enable")
def drive_enable(body: DesktopEnableIn) -> dict:
    try:
        return gdrive.enable_desktop(body.root, papers_in_drive=body.papers_in_drive)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/drive/disable")
def drive_disable() -> dict:
    config.update_settings({"drive_mode": "none"})
    return gdrive.status()


@router.post("/drive/api/client-secret")
async def drive_client_secret(file: UploadFile = File(...)) -> dict:
    try:
        return gdrive.save_client_secret(await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/drive/api/connect")
def drive_connect() -> dict:
    try:
        return gdrive.start_oauth()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/drive/api/disconnect")
def drive_disconnect() -> dict:
    return gdrive.disconnect_api()


@router.get("/drive/api/account")
def drive_account() -> dict:
    try:
        return gdrive.api_account()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/drive/api/sync")
async def drive_sync() -> dict:
    try:
        return await asyncio.to_thread(gdrive.sync_up)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/drive/api/pull-files")
async def drive_pull_files() -> dict:
    try:
        return await asyncio.to_thread(gdrive.download_missing_files)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/drive/api/backups")
def drive_remote_backups() -> list[dict]:
    try:
        return gdrive.list_remote_backups()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/backups")
async def backup_now() -> dict:
    return await asyncio.to_thread(gdrive.create_backup)


@router.get("/backups")
def backups() -> list[dict]:
    return gdrive.list_backups()


@router.post("/backups/restore")
async def restore(body: RestoreIn) -> dict:
    try:
        path = body.path
        if body.drive_file_id:
            path = (await asyncio.to_thread(gdrive.download_remote_backup, body.drive_file_id))["path"]
        if not path:
            raise ValueError("path or drive_file_id required")
        allowed = [str(config.backups_dir().resolve()).casefold(), str(gdrive.backup_target_dir().resolve()).casefold()]
        if not any(str(Path(path).resolve()).casefold().startswith(a) for a in allowed):
            raise ValueError("backups can only be restored from the ReviewHunter backup folders")
        return await asyncio.to_thread(gdrive.stage_restore, path)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ======================================================================================
# System / integrations status
# ======================================================================================

@router.get("/system/info")
def system_info(request: Request) -> dict:
    from . import addin

    root = Path(__file__).resolve().parents[1]
    return {
        "version": "2.0.0",
        "library_root": str(config.library_root()),
        "papers_dir": str(config.papers_dir()),
        "extension_dir": str(root / "browser_extension"),
        "word_addin": addin.status(),
        "https_port": config.get_settings().get("https_port"),
    }


@router.post("/system/open-path")
def open_path(body: OpenPathIn) -> dict:
    """Open a folder in Explorer. Restricted to ReviewHunter's own folders."""
    from .library import _open_path

    root = Path(__file__).resolve().parents[1]
    target = Path(body.path).resolve()
    allowed = [config.library_root().resolve(), config.papers_dir().resolve(), root]
    s = config.get_settings()
    if s.get("drive_desktop_root"):
        allowed.append(Path(s["drive_desktop_root"]).resolve())
    if not any(str(target).casefold().startswith(str(a).casefold()) for a in allowed):
        raise HTTPException(403, "path outside ReviewHunter folders")
    if not target.exists():
        raise HTTPException(404, "path not found")
    _open_path(target)
    return {"ok": True}


@router.post("/system/word-addin/setup")
def word_addin_setup() -> dict:
    """Prepare certificate + manifest and open the setup script for the user to run."""
    from . import addin

    return addin.prepare_and_launch_setup()
