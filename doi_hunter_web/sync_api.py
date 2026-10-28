"""HTTP boundary for the Zotero connector and shared libraries (the local-origin guard covers this router).

Long operations run in a worker thread; progress and completion reach the UI over the existing event
WebSocket (``zotero_progress``, ``zotero_done``, ``shared_synced``, ``library_changed``).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config, fulltext, shared_library, zotero_sync

router = APIRouter(prefix="/api", tags=["zotero connector", "shared libraries"])


def _publish(request: Request, event: str, data: dict) -> None:
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        bus.publish(event, data)


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(400, str(e) or e.__class__.__name__)


# ======================================================================================
# Zotero connector
# ======================================================================================

class ZoteroConfigIn(BaseModel):
    libraries: Optional[dict[str, dict[str, Any]]] = None
    auto_sync: Optional[bool] = None
    sync_interval_minutes: Optional[float] = None
    conflict_policy: Optional[str] = None
    mirror_deletions: Optional[bool] = None
    copy_files: Optional[bool] = None
    install_dir: Optional[str] = None
    data_dir: Optional[str] = None


class ZoteroSyncIn(BaseModel):
    libraries: Optional[list[str]] = None     # None = the libraries enabled in settings


@router.get("/zotero/status")
def zotero_status() -> dict:
    return zotero_sync.detect()


@router.post("/zotero/config")
def zotero_config(body: ZoteroConfigIn) -> dict:
    try:
        return zotero_sync.configure(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise _bad_request(e)


@router.post("/zotero/sync")
async def zotero_sync_now(body: ZoteroSyncIn, request: Request) -> dict:
    """Start a sync in the background; the UI follows zotero_progress / zotero_done events."""
    if zotero_sync.public_state()["running"]:
        return {"queued": False, "reason": "a Zotero sync is already running"}
    libraries = body.libraries if body.libraries is not None else zotero_sync.enabled_libraries()
    if not libraries:
        raise HTTPException(400, "select at least one Zotero library")

    async def run() -> None:
        try:
            report = await asyncio.to_thread(zotero_sync.sync, libraries=libraries,
                                             progress=lambda stage, data: _publish(request, stage, data))
            if report.get("files"):
                await asyncio.to_thread(fulltext.reindex_all)
            _publish(request, "library_changed", {"source": "zotero"})
        except Exception as e:
            _publish(request, "zotero_failed", {"error": f"Zotero sync failed: {e}"})

    asyncio.create_task(run())
    return {"queued": True, "libraries": libraries}


# ======================================================================================
# Shared libraries
# ======================================================================================

class MemberIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


class CreateIn(BaseModel):
    name: str
    parent_dir: str
    collection_id: Optional[int] = None


class JoinIn(BaseModel):
    path: str


class AutoIn(BaseModel):
    auto_sync: bool


@router.get("/shared")
def shared_overview() -> dict:
    return {"member": shared_library.member(), "libraries": shared_library.list_libraries(),
            "default_parent": shared_library.default_parent(),
            "drive_roots": [str(p) for p in config.google_drive_candidates()],
            "interval_seconds": config.get_settings().get("shared_sync_interval_seconds")}


@router.post("/shared/member")
def shared_member(body: MemberIn) -> dict:
    patch = {}
    if body.name is not None and body.name.strip():
        patch["member_name"] = body.name.strip()[:80]
    if body.email is not None:
        patch["member_email"] = body.email.strip()[:200]
    config.update_settings(patch)
    return shared_library.member()


@router.get("/shared/discover")
def shared_discover(root: Optional[str] = None) -> list[dict]:
    return shared_library.discover([root] if root else [])


@router.post("/shared/create")
async def shared_create(body: CreateIn, request: Request) -> dict:
    try:
        report = await asyncio.to_thread(shared_library.create, body.name, body.parent_dir,
                                         collection_id=body.collection_id)
    except (ValueError, OSError) as e:
        raise _bad_request(e)
    _publish(request, "library_changed", {"source": "shared"})
    return report


@router.post("/shared/join")
async def shared_join(body: JoinIn, request: Request) -> dict:
    try:
        report = await asyncio.to_thread(shared_library.join, body.path)
    except (ValueError, OSError) as e:
        raise _bad_request(e)
    _publish(request, "library_changed", {"source": "shared"})
    return report


@router.post("/shared/{library_id}/sync")
async def shared_sync(library_id: str, request: Request) -> dict:
    try:
        report = await asyncio.to_thread(shared_library.sync, library_id)
    except (ValueError, OSError) as e:
        raise _bad_request(e)
    _publish(request, "shared_synced", {"library_id": library_id})
    if report.get("applied") or report.get("items_added") or report.get("items_removed"):
        _publish(request, "library_changed", {"source": "shared"})
    return report


@router.post("/shared/{library_id}/auto")
def shared_auto(library_id: str, body: AutoIn) -> dict:
    from .db import get_conn

    get_conn().execute("UPDATE shared_libraries SET auto_sync=? WHERE library_id=?", (1 if body.auto_sync else 0,
                                                                                      library_id))
    return {"ok": True}


@router.post("/shared/{library_id}/leave")
def shared_leave(library_id: str, request: Request) -> dict:
    try:
        result = shared_library.leave(library_id)
    except ValueError as e:
        raise _bad_request(e)
    _publish(request, "library_changed", {"source": "shared"})
    return result


@router.post("/shared/{library_id}/open")
def shared_open(library_id: str) -> dict:
    """Open the library folder (to share it from Drive's right-click menu). Only registered folders."""
    from .db import get_conn
    from .library import _open_path

    row = get_conn().execute("SELECT root_path FROM shared_libraries WHERE library_id=?", (library_id,)).fetchone()
    if not row or not Path(row[0]).exists():
        raise HTTPException(404, "shared library folder not found (is Google Drive running?)")
    _open_path(Path(row[0]))
    return {"ok": True}
