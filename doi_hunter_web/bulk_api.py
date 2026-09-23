"""HTTP boundary for bounded imports; the app's local-origin guard covers this router."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from . import bulk_import

router = APIRouter(prefix="/api/bulk-import", tags=["bulk import"])


class BulkRequest(BaseModel):
    """The destination and options are immutable once a job is accepted."""
    model_config = ConfigDict(extra="forbid")
    text: str = ""
    folder_path: str = ""
    recursive: bool = False
    collection_id: int | None = None
    new_collection_name: str = ""
    auto_search: bool = True
    auto_download: bool = True


def _start(options: BulkRequest, paths: list, temp_dir: str | None = None) -> dict:
    """Parse the whole batch before creating its collection or starting a worker."""
    try:
        entries, warnings = bulk_import.parse_sources(options.text, paths, folder_path=options.folder_path,
                                                      recursive=options.recursive)
        return bulk_import.start_job(entries, collection_id=options.collection_id,
                                     new_collection_name=options.new_collection_name,
                                     auto_search=options.auto_search, auto_download=options.auto_download,
                                     warnings=warnings, temp_dir=temp_dir)
    except bulk_import.BulkInputError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/text", status_code=202)
def import_text(options: BulkRequest) -> dict:
    """Accept pasted references or a local folder without requiring multipart."""
    return _start(options, [])


@router.post("", status_code=202)
async def import_uploads(request: Request) -> dict:
    """Stage bounded uploads; transfer ownership to the worker only after validation."""
    length = request.headers.get("content-length")
    if length is not None and (not length.isascii() or not length.isdigit()):
        raise HTTPException(400, "Invalid Content-Length header.")
    request_limit = bulk_import.MAX_TOTAL_BYTES + 1024 * 1024
    if length and int(length) > request_limit:
        raise HTTPException(413, "Upload request exceeds 1 GB.")
    received, exceeded = 0, False

    async def bounded_receive():
        """Count actual bytes, including chunked bodies, before the parser spools them."""
        nonlocal received, exceeded
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > request_limit:
                exceeded = True
                # Starlette closes its partial upload files on MultiPartException.
                raise MultiPartException("Combined upload request exceeds the size limit.")
        return message

    bounded_request = Request(request.scope, receive=bounded_receive)
    temp_dir = tempfile.mkdtemp(prefix="reviewhunter-bulk-")
    accepted = False
    try:
        async with bounded_request.form(max_files=bulk_import.MAX_ENTRIES, max_fields=16,
                                max_part_size=bulk_import.MAX_TEXT_BYTES) as form:
            fields = {key: value for key, value in form.items() if not isinstance(value, UploadFile)}
            if fields.get("collection_id") == "":
                fields["collection_id"] = None
            try:
                options = BulkRequest.model_validate(fields)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            paths, total = [], 0
            for key, upload in form.multi_items():
                if not isinstance(upload, UploadFile):
                    continue
                if key != "files":
                    raise HTTPException(400, "Upload files using the 'files' field.")
                name = Path((upload.filename or "source.txt").replace("\\", "/")).name
                suffix = Path(name).suffix.lower()
                limit = bulk_import.MAX_PDF_BYTES if suffix == ".pdf" else bulk_import.MAX_DOCUMENT_BYTES
                path = Path(temp_dir) / f"{len(paths):04d}{suffix}"
                size = 0
                with path.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > limit or total > bulk_import.MAX_TOTAL_BYTES:
                            raise HTTPException(413, f"{name}: file or combined upload size limit exceeded.")
                        stream.write(chunk)
                paths.append((path, name))
            result = await run_in_threadpool(_start, options, paths, temp_dir)
            accepted = True
            return result
    except (MultiPartException, StarletteHTTPException) as exc:
        if exceeded:
            raise HTTPException(413, "Combined upload request exceeds the size limit.") from exc
        if isinstance(exc, MultiPartException):
            raise HTTPException(400, str(exc)) from exc
        raise
    finally:
        if not accepted:
            shutil.rmtree(temp_dir, ignore_errors=True)


@router.get("/{job_id}")
def get_job(job_id: str) -> dict:
    """Poll progress for an active or recently completed job."""
    result = bulk_import.snapshot(job_id)
    if result is None:
        raise HTTPException(404, "Import job not found or expired.")
    return result


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Request cancellation after the current entry finishes."""
    result = bulk_import.cancel(job_id)
    if result is None:
        raise HTTPException(404, "Import job not found or expired.")
    return result
