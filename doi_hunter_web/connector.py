"""Browser connector API (used by the ReviewHunter Chrome extension).

All routes live under ``/connector``. The extension talks to
``http://127.0.0.1:8765`` from its service worker (host_permissions), so these
routes need no CORS; ``security.LocalOriginGuard`` only admits the app itself and
browser-extension origins for state-changing calls.

Contract (JSON unless stated)
-----------------------------
GET  /connector/ping                     -> {app, version, library: {items}, default_collection_id}
GET  /connector/collections              -> [{id, name, parent_id, depth, path}]
GET  /connector/tags                     -> [{name, color, count}]
POST /connector/lookup   {doi?, url?, title?}   -> {found: bool, items: [{id, title}]}
POST /connector/save     {items: [payload], collection_id?, tags?: [str], note?: str,
                          enrich?: bool, pdf_url?: str}
                         -> {saved: [{id, title, existing, needs_pdf, pdf_url}]}
POST /connector/attachment?item_id=..&title=..&url=..&kind=file|snapshot
     body: raw bytes (Content-Type application/pdf, text/html ...)
                         -> {ok, attachment}
POST /connector/identifiers {text, collection_id?, tags?}
                         -> {saved: [{id, title, existing, needs_pdf, pdf_url}], failed: [...]}

``payload`` is the catalog create payload: item_type, title, creators
([{creatorType, lastName, firstName} | {creatorType, name}]), journal, date, doi,
url, abstract, publisher, volume, issue, pages, issn, isbn[], language, pmid,
arxiv_id, short_title, access_date, tags[].
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import __version__, catalog, identifiers
from .db import get_conn

log = logging.getLogger("doihunter.connector")

router = APIRouter(prefix="/connector", tags=["connector"])

APP_VERSION = __version__
MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024
# Seconds to wait after a connector save before the server looks for a legal open-access copy.
# The extension tries the publisher PDF with the user's session first; if that upload lands
# within this delay the fallback does nothing.
OA_FALLBACK_DELAY_S = 40


class SaveIn(BaseModel):
    items: list[dict[str, Any]]
    collection_id: Optional[int] = None
    tags: list[str] = []
    note: Optional[str] = None
    enrich: bool = True
    pdf_url: Optional[str] = None


class LookupIn(BaseModel):
    doi: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None


class IdentifiersIn(BaseModel):
    text: str
    collection_id: Optional[int] = None
    tags: list[str] = []


def _collection_tree() -> list[dict]:
    cols = catalog.list_collections()
    by_parent: dict[Optional[int], list[dict]] = {}
    for c in cols:
        by_parent.setdefault(c["parent_id"], []).append(c)
    out: list[dict] = []

    def walk(parent: Optional[int], depth: int, prefix: str) -> None:
        for c in sorted(by_parent.get(parent, []), key=lambda x: x["name"].casefold()):
            path = f"{prefix}{c['name']}"
            out.append({"id": c["id"], "name": c["name"], "parent_id": c["parent_id"], "depth": depth, "path": path,
                        "count": c["count"]})
            walk(c["id"], depth + 1, path + " / ")

    walk(None, 0, "")
    return out


@router.get("/ping")
def ping() -> dict:
    stats = catalog.stats()
    return {"app": "ReviewHunter", "version": APP_VERSION, "library": {"items": stats["items"]}}


@router.get("/collections")
def collections() -> list[dict]:
    return _collection_tree()


@router.get("/tags")
def tags() -> list[dict]:
    return [{"name": t["name"], "color": t["color"], "count": t["count"]} for t in catalog.list_tags()]


@router.post("/lookup")
def lookup(body: LookupIn) -> dict:
    conn = get_conn()
    rows = []
    if body.doi:
        rows = conn.execute("SELECT id, title FROM items WHERE doi=? AND deleted_at IS NULL",
                            (catalog.normalize_doi(body.doi),)).fetchall()
    if not rows and body.url:
        rows = conn.execute("SELECT id, title FROM items WHERE url=? AND deleted_at IS NULL", (body.url,)).fetchall()
    if not rows and body.title and len(body.title) > 12:
        rows = conn.execute("SELECT id, title FROM items WHERE title=? COLLATE NOCASE AND deleted_at IS NULL",
                            (body.title.strip(),)).fetchall()
    return {"found": bool(rows), "items": [dict(r) for r in rows]}


def _merge_enriched(payload: dict) -> dict:
    """Fill gaps from CrossRef/arXiv/PubMed when the page exposed an identifier."""
    resolved = None
    try:
        if payload.get("doi"):
            resolved = identifiers.resolve_doi(payload["doi"])
        elif payload.get("arxiv_id"):
            resolved = identifiers.resolve_arxiv(payload["arxiv_id"])
        elif payload.get("pmid"):
            resolved = identifiers.resolve_pmid(payload["pmid"])
    except Exception as e:  # enrichment is best effort
        log.info(f"enrichment failed: {e}")
    if not resolved:
        return payload
    merged = dict(resolved)
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        # Page-provided values win for these (publisher pages are authoritative for their own URL/abstract).
        if key in ("url", "access_date", "tags") or not merged.get(key):
            merged[key] = value
    if not payload.get("creators") and resolved.get("creators"):
        merged["creators"] = resolved["creators"]
    return merged


@router.post("/save")
async def save(body: SaveIn, request: Request) -> dict:
    if not body.items:
        raise HTTPException(400, "items required")
    loop = asyncio.get_running_loop()
    saved = []
    for raw in body.items[:200]:
        payload = dict(raw)
        payload.setdefault("access_date", date.today().isoformat())
        payload["tags"] = list(dict.fromkeys([*(payload.get("tags") or []), *body.tags]))
        if body.collection_id:
            payload["collections"] = [body.collection_id]
        if body.note:
            payload["notes"] = [f"<p>{_escape(body.note)}</p>"]
        if body.enrich:
            payload = await loop.run_in_executor(None, _merge_enriched, payload)
        pdf_url = payload.pop("_pdf_url", None) or body.pdf_url or raw.get("pdf_url")
        payload.pop("pdf_url", None)
        payload["source_query"] = f"connector:{payload.get('url') or payload.get('doi') or ''}"[:500]
        item = await loop.run_in_executor(None, catalog.create_item, payload)
        # Re-saving an existing item still applies the chosen folder / tags / note: catalog.create_item
        # calls _attach_links on its existing-DOI path, so no second call here (it would duplicate notes).
        existing = bool(item.get("_existing"))
        has_file = any(a["kind"] != "link" for a in catalog.list_attachments(item["id"]))
        saved.append({"id": item["id"], "title": item["title"], "existing": existing,
                      "needs_pdf": not has_file, "pdf_url": pdf_url})
        _publish(request, "item_added", {"id": item["id"], "title": item["title"], "source": "connector"})
        if not has_file and item.get("doi"):
            _schedule_open_access_fallback(request, item["id"])
    return {"saved": saved}


def _schedule_open_access_fallback(request: Request, item_id: int, delay: float = OA_FALLBACK_DELAY_S) -> None:
    """After ``delay`` seconds, attach a legal open-access PDF if the item still has no file."""
    import threading

    from .library import fetch_open_access_pdf

    bus = getattr(request.app.state, "bus", None)

    def run() -> None:
        try:
            result = fetch_open_access_pdf(item_id)
        except Exception as e:  # background best effort: never crash the server
            log.warning(f"open-access fallback failed for item {item_id}: {e}")
            return
        log.info(f"open-access fallback item {item_id}: {result}")
        if result.get("ok") and bus is not None:
            bus.publish("item_changed", {"id": item_id})

    timer = threading.Timer(delay, run)
    timer.daemon = True
    timer.start()


@router.post("/attachment")
async def attachment(request: Request, item_id: int, title: Optional[str] = None, url: Optional[str] = None,
                     kind: str = "file", filename: Optional[str] = None) -> dict:
    item = catalog.get_item(item_id, full=False)
    if not item:
        raise HTTPException(404, "item not found")
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "attachment too large")
    if ctype == "application/pdf" or data[:5] == b"%PDF-":
        if data[:5] != b"%PDF-":
            raise HTTPException(415, "body is not a PDF (the site probably returned a login page)")
        ext = "pdf"
    elif ctype in ("text/html", "application/xhtml+xml"):
        ext = "html"
        kind = "snapshot"
    elif ctype == "application/epub+zip":
        ext = "epub"
    else:
        ext = (filename or "file.bin").rsplit(".", 1)[-1][:8] or "bin"
    loop = asyncio.get_running_loop()
    att = await loop.run_in_executor(
        None, lambda: catalog.store_file_bytes(item_id, data, f"file.{ext}", title=title, kind=kind, url=url)
    )
    from .library import _index_fulltext_async

    _index_fulltext_async(item_id)
    _publish(request, "item_changed", {"id": item_id})
    return {"ok": True, "attachment": att}


@router.post("/identifiers")
async def add_identifiers(body: IdentifiersIn, request: Request) -> dict:
    found = identifiers.extract_identifiers(body.text)
    if not found:
        kind, value = identifiers.classify(body.text)
        if kind != "title":
            found = [(kind, value)]
    loop = asyncio.get_running_loop()
    saved, failed = [], []
    for kind, value in found[:100]:
        payload = await loop.run_in_executor(None, identifiers.resolve, value if kind != "arxiv" else f"arXiv:{value}")
        if not payload:
            failed.append(value)
            continue
        pdf_url = payload.pop("_pdf_url", None)
        payload["tags"] = list(body.tags)
        if body.collection_id:
            payload["collections"] = [body.collection_id]
        item = await loop.run_in_executor(None, catalog.create_item, payload)
        existing = bool(item.get("_existing"))
        has_file = any(a["kind"] != "link" for a in catalog.list_attachments(item["id"]))
        # needs_pdf / pdf_url let the extension download an open-access copy with the browser session.
        saved.append({"id": item["id"], "title": item["title"], "existing": existing,
                      "needs_pdf": not has_file, "pdf_url": pdf_url})
        _publish(request, "item_added", {"id": item["id"], "title": item["title"], "source": "connector"})
    return {"saved": saved, "failed": failed}


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def _publish(request: Request, event: str, data: dict) -> None:
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        bus.publish(event, data)
