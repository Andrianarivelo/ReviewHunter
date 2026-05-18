"""Google Drive OAuth/API helpers for ReviewHunter.

This module stays independent from library.py so the API layer can use it
without creating import cycles. The app stores OAuth tokens under the user's
ReviewHunter data folder, never in the repository.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import config

FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DEFAULT_WEB_OAUTH_PORT = 53682
DEFAULT_OAUTH_TIMEOUT_SECONDS = 180


class DriveUnavailable(RuntimeError):
    pass


class DriveNotConnected(RuntimeError):
    pass


class DriveOAuthConfigError(ValueError):
    pass


def _scopes() -> list[str]:
    raw = os.environ.get("REVIEWHUNTER_GOOGLE_DRIVE_SCOPES", "").strip()
    if not raw:
        return DEFAULT_SCOPES
    return [part.strip() for part in raw.replace(",", " ").split() if part.strip()]


def _imports() -> dict[str, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except Exception as exc:
        raise DriveUnavailable(
            "Google Drive API dependencies are missing. Run pip install -r requirements.txt."
        ) from exc
    return {
        "Request": Request,
        "Credentials": Credentials,
        "InstalledAppFlow": InstalledAppFlow,
        "build": build,
        "HttpError": HttpError,
        "MediaFileUpload": MediaFileUpload,
    }


def _settings() -> dict:
    return config.read_settings()


def _drive_settings() -> dict:
    settings = _settings()
    return {
        "client_config_path": settings.get("google_drive_client_config_path") or "",
        "folder_id": settings.get("google_drive_api_folder_id") or "",
        "folder_name": settings.get("google_drive_api_folder_name") or "",
        "folder_path": settings.get("google_drive_api_folder_path") or "",
        "auto_upload": bool(settings.get("google_drive_api_auto_upload", False)),
    }


def dependencies_available() -> bool:
    try:
        _imports()
        return True
    except DriveUnavailable:
        return False


def client_config_path() -> Path | None:
    raw = (
        os.environ.get("REVIEWHUNTER_GOOGLE_CLIENT_SECRET")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or _drive_settings().get("client_config_path")
    )
    if raw:
        p = Path(raw).expanduser().resolve()
        if p.exists():
            return p
    stored = config.google_drive_client_config_path()
    return stored if stored.exists() else None


def _oauth_port() -> int:
    raw = os.environ.get("REVIEWHUNTER_GOOGLE_OAUTH_PORT", "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
    return DEFAULT_WEB_OAUTH_PORT


def _oauth_timeout_seconds() -> int:
    raw = os.environ.get("REVIEWHUNTER_GOOGLE_OAUTH_TIMEOUT", "").strip()
    if raw:
        try:
            seconds = int(raw)
            if seconds >= 30:
                return seconds
        except ValueError:
            pass
    return DEFAULT_OAUTH_TIMEOUT_SECONDS


def _project_number_from_client_id(client_id: str) -> str:
    prefix = (client_id or "").split("-", 1)[0]
    return prefix if prefix.isdigit() else ""


def _drive_api_url(project_number: str = "") -> str:
    if project_number:
        return f"https://console.developers.google.com/apis/api/drive.googleapis.com/overview?project={project_number}"
    return "https://console.developers.google.com/apis/api/drive.googleapis.com/overview"


def _friendly_drive_error(exc: Exception, project_number: str = "") -> str:
    text = str(exc)
    if "accessNotConfigured" in text or "drive.googleapis.com" in text and "disabled" in text:
        url = _drive_api_url(project_number)
        return (
            f"Google Drive API is disabled for project {project_number or 'this OAuth project'}. "
            f"Enable it here, wait a few minutes, then refresh ReviewHunter: {url}"
        )
    if "invalid_grant" in text:
        return "Google OAuth token is invalid or expired. Disconnect Google Drive, then reconnect."
    return text


def _read_client_config(path: str | Path) -> dict:
    p = Path(path).expanduser().resolve()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OAuth client JSON is invalid") from exc
    except OSError as exc:
        raise FileNotFoundError(f"OAuth client JSON not found: {p}") from exc
    if "installed" not in data and "web" not in data:
        raise ValueError("OAuth client JSON must contain an installed or web client")
    return data


def _local_root_redirects(redirect_uris: list[str]) -> list[str]:
    out: list[str] = []
    for uri in redirect_uris:
        parsed = urlparse(uri)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            continue
        if parsed.path not in {"", "/"}:
            continue
        if not parsed.port:
            continue
        out.append(uri)
    return out


def oauth_client_info(path: str | Path | None = None) -> dict:
    p = Path(path).expanduser().resolve() if path else client_config_path()
    if not p or not p.exists():
        return {
            "client_type": "",
            "oauth_redirect_uri": "",
            "oauth_redirect_ready": False,
            "oauth_message": "OAuth client JSON is not configured",
            "redirect_uris": [],
        }

    data = _read_client_config(p)
    client_type = "installed" if "installed" in data else "web"
    section = data.get(client_type) or {}
    redirect_uris = list(section.get("redirect_uris") or [])
    project_number = _project_number_from_client_id(section.get("client_id") or "")
    project_id = section.get("project_id") or ""

    if client_type == "installed":
        return {
            "client_type": client_type,
            "project_id": project_id,
            "project_number": project_number,
            "drive_api_url": _drive_api_url(project_number),
            "oauth_redirect_uri": "dynamic localhost callback",
            "oauth_redirect_ready": True,
            "oauth_message": (
                "Desktop OAuth client: no redirect URI setup needed. "
                "If Google shows 403 access_denied, add your Google account as a Test user "
                "in the OAuth consent screen or publish/verify the app."
            ),
            "redirect_uris": redirect_uris,
        }

    local_redirects = _local_root_redirects(redirect_uris)
    redirect_uri = local_redirects[0] if local_redirects else f"http://localhost:{_oauth_port()}/"
    ready = redirect_uri in redirect_uris
    if ready:
        message = (
            f"Web OAuth client ready. ReviewHunter will use {redirect_uri}. "
            "If Google shows 403 access_denied, add your Google account as a Test user "
            "in the OAuth consent screen or publish/verify the app."
        )
    else:
        message = (
            "This is a Web OAuth client. Add this exact Authorized redirect URI "
            f"in Google Cloud, redownload/reselect the JSON, then retry: {redirect_uri}. "
            "Alternatively create a Desktop app OAuth client and use that JSON."
        )
    return {
        "client_type": client_type,
        "project_id": project_id,
        "project_number": project_number,
        "drive_api_url": _drive_api_url(project_number),
        "oauth_redirect_uri": redirect_uri,
        "oauth_redirect_ready": ready,
        "oauth_message": message,
        "redirect_uris": redirect_uris,
    }


def store_client_config(source: str | Path) -> Path:
    src = Path(source).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"OAuth client JSON not found: {src}")
    _read_client_config(src)
    target = config.google_drive_client_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if src != target:
        shutil.copy2(src, target)
    config.update_settings(google_drive_client_config_path=str(target))
    return target


def _save_credentials(creds) -> None:
    p = config.google_drive_token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(creds.to_json(), encoding="utf-8")


def credentials():
    libs = _imports()
    token = config.google_drive_token_path()
    if not token.exists():
        raise DriveNotConnected("Google Drive is not connected")
    creds = libs["Credentials"].from_authorized_user_file(str(token), _scopes())
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(libs["Request"]())
            _save_credentials(creds)
        else:
            raise DriveNotConnected("Google Drive token is expired; reconnect Drive")
    return creds


def service():
    libs = _imports()
    return libs["build"]("drive", "v3", credentials=credentials(), cache_discovery=False)


def connect(client_secret_path: str | None = None) -> dict:
    libs = _imports()
    if client_secret_path:
        secret_path = store_client_config(client_secret_path)
    else:
        secret_path = client_config_path()
    if not secret_path:
        raise FileNotFoundError("OAuth client JSON is not configured")

    info = oauth_client_info(secret_path)
    host = "localhost"
    port = 0
    trailing_slash = True
    if info["client_type"] == "web":
        if not info["oauth_redirect_ready"]:
            raise DriveOAuthConfigError(info["oauth_message"])
        parsed = urlparse(info["oauth_redirect_uri"])
        host = parsed.hostname or "localhost"
        port = parsed.port or _oauth_port()
        trailing_slash = info["oauth_redirect_uri"].endswith("/")

    flow = libs["InstalledAppFlow"].from_client_secrets_file(str(secret_path), _scopes())
    try:
        creds = flow.run_local_server(
            host=host,
            port=port,
            open_browser=True,
            redirect_uri_trailing_slash=trailing_slash,
            timeout_seconds=_oauth_timeout_seconds(),
            success_message="ReviewHunter is connected to Google Drive. You can close this tab.",
            access_type="offline",
            prompt="consent",
        )
    except OSError as exc:
        raise DriveOAuthConfigError(
            f"OAuth callback port {port} is already in use. Close the old Google OAuth tab, "
            "restart ReviewHunter if needed, then try Connect again."
        ) from exc
    _save_credentials(creds)
    config.update_settings(google_drive_client_config_path=str(secret_path))
    if not _drive_settings()["folder_id"]:
        try:
            ensure_folder_path("ReviewHunter")
        except Exception:
            pass
    return status()


def disconnect() -> dict:
    token = config.google_drive_token_path()
    if token.exists():
        token.unlink()
    return status(check_remote=False)


def _quota_dict(raw: dict | None) -> dict:
    raw = raw or {}
    out = {}
    for key in ("limit", "usage", "usageInDrive"):
        try:
            out[key] = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


def status(*, check_remote: bool = True) -> dict:
    available = dependencies_available()
    settings = _drive_settings()
    token_exists = config.google_drive_token_path().exists()
    client_path = client_config_path()
    project_number = ""
    out = {
        "available": available,
        "connected": False,
        "client_configured": bool(client_path),
        "token_exists": token_exists,
        "client_config_path": str(client_path) if client_path else "",
        "token_path": str(config.google_drive_token_path()),
        "project_id": "",
        "project_number": "",
        "drive_api_url": _drive_api_url(""),
        "scopes": _scopes(),
        **settings,
        "user": {},
        "quota": {},
        "error": "",
        "error_detail": "",
    }
    if client_path:
        try:
            info = oauth_client_info(client_path)
            project_number = info.get("project_number") or ""
            out.update(info)
        except Exception as exc:
            out.update({
                "client_type": "",
                "project_id": "",
                "project_number": "",
                "drive_api_url": _drive_api_url(""),
                "oauth_redirect_uri": "",
                "oauth_redirect_ready": False,
                "oauth_message": str(exc),
                "redirect_uris": [],
                "error": str(exc),
                "error_detail": str(exc),
            })
    if not available or not token_exists or not check_remote:
        return out
    try:
        about = service().about().get(
            fields="user(displayName,emailAddress),storageQuota(limit,usage,usageInDrive)"
        ).execute()
        out["connected"] = True
        out["user"] = about.get("user") or {}
        out["quota"] = _quota_dict(about.get("storageQuota"))
    except Exception as exc:
        out["error"] = _friendly_drive_error(exc, project_number)
        out["error_detail"] = str(exc)
    return out


def auto_upload_enabled() -> bool:
    s = _drive_settings()
    return bool(s["auto_upload"] and s["folder_id"] and config.google_drive_token_path().exists())


# ----------- Author-subfolder helpers -----------

_FOLDER_UNSAFE_RE = re.compile(r'[\\/*?:"<>|\r\n\t]')


def _author_subfolder_name(authors) -> str:
    """Return a Drive-folder-safe first-author last name from an authors field.

    *authors* may be a JSON-encoded list (as stored in the DB) or a plain list.
    Format expected per entry: ``"Lastname, Firstname"`` or ``"Firstname Lastname"``.
    Only the part before the first comma is used so results stay concise.
    """
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except (json.JSONDecodeError, ValueError):
            authors = [a.strip() for a in authors.split(";") if a.strip()] or [authors]
    if not authors:
        return "Unknown Author"
    first_entry = str(authors[0]).strip()
    # "Smith, John" → "Smith"
    last_name = first_entry.split(",")[0].strip()
    safe = _FOLDER_UNSAFE_RE.sub("", last_name).strip().rstrip(". ")
    return safe or "Unknown Author"


def ensure_author_subfolder(authors, *, root_folder_id: str = "") -> dict:
    """Ensure a per-first-author subfolder exists inside the configured Drive folder.

    Returns the Drive folder resource dict (``id``, ``name``, ``webViewLink``, …).
    The folder is created if it does not already exist but is never renamed.
    Pass *root_folder_id* to override the configured ReviewHunter root.
    """
    name = _author_subfolder_name(authors)
    parent = root_folder_id or _default_folder_id()
    return ensure_folder_path(name, parent_id=parent, persist=False)


def ensure_named_subfolder(name: str, *, root_folder_id: str = "") -> dict:
    parent = root_folder_id or _default_folder_id()
    return ensure_folder_path(name, parent_id=parent, persist=False)


def set_auto_upload(enabled: bool) -> dict:
    config.update_settings(google_drive_api_auto_upload=bool(enabled))
    return status()


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_folders(query: str = "", *, limit: int = 20) -> list[dict]:
    q = f"mimeType='{FOLDER_MIME}' and trashed=false"
    if query.strip():
        q += f" and name contains '{_q(query.strip())}'"
    res = service().files().list(
        q=q,
        pageSize=max(1, min(limit, 100)),
        spaces="drive",
        fields="files(id,name,parents,webViewLink,createdTime,modifiedTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return res.get("files", [])


def get_folder(folder_id: str) -> dict:
    f = service().files().get(
        fileId=folder_id,
        fields="id,name,mimeType,parents,webViewLink",
        supportsAllDrives=True,
    ).execute()
    if f.get("mimeType") != FOLDER_MIME:
        raise ValueError("Selected Drive item is not a folder")
    return f


def _find_child_folder(svc, parent_id: str, name: str) -> dict | None:
    q = (
        f"mimeType='{FOLDER_MIME}' and trashed=false "
        f"and name='{_q(name)}' and '{_q(parent_id)}' in parents"
    )
    res = svc.files().list(
        q=q,
        pageSize=1,
        spaces="drive",
        fields="files(id,name,parents,webViewLink)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files") or []
    return files[0] if files else None


def rename_file(file_id: str, name: str) -> dict:
    if not file_id.strip():
        raise ValueError("file_id required")
    return service().files().update(
        fileId=file_id.strip(),
        body={"name": name.strip()},
        fields="id,name,parents,webViewLink",
        supportsAllDrives=True,
    ).execute()


def ensure_folder_path(path: str = "ReviewHunter", *, parent_id: str = "root", persist: bool = True) -> dict:
    svc = service()
    current = parent_id or "root"
    current_file: dict = {"id": current, "name": "My Drive"}
    parts = [p.strip() for p in (path or "ReviewHunter").replace("\\", "/").split("/") if p.strip()]
    if not parts:
        parts = ["ReviewHunter"]
    for part in parts:
        found = _find_child_folder(svc, current, part)
        if not found:
            found = svc.files().create(
                body={"name": part, "mimeType": FOLDER_MIME, "parents": [current]},
                fields="id,name,parents,webViewLink",
                supportsAllDrives=True,
            ).execute()
        current = found["id"]
        current_file = found
    if persist:
        config.update_settings(
            google_drive_api_folder_id=current_file["id"],
            google_drive_api_folder_name=current_file.get("name") or parts[-1],
            google_drive_api_folder_path="/".join(parts),
        )
    return current_file


def configure_folder(*, folder_id: str = "", folder_path: str = "ReviewHunter", auto_upload: bool | None = None) -> dict:
    if folder_id.strip():
        folder = get_folder(folder_id.strip())
        updates = {
            "google_drive_api_folder_id": folder["id"],
            "google_drive_api_folder_name": folder.get("name") or "Drive folder",
            "google_drive_api_folder_path": folder.get("name") or "",
        }
        config.update_settings(**updates)
    else:
        folder = ensure_folder_path(folder_path or "ReviewHunter")
    if auto_upload is not None:
        config.update_settings(google_drive_api_auto_upload=bool(auto_upload))
    return {"folder": folder, **status()}


def _default_folder_id() -> str:
    folder_id = _drive_settings().get("folder_id")
    if folder_id:
        return folder_id
    return ensure_folder_path("ReviewHunter")["id"]


def _find_uploaded_file(svc, folder_id: str, name: str, item_id: int | None = None) -> dict | None:
    parent = _q(folder_id)
    clauses = [f"trashed=false", f"'{parent}' in parents"]
    if item_id is not None:
        clauses.append(f"appProperties has {{ key='reviewhunter_item_id' and value='{int(item_id)}' }}")
    else:
        clauses.append(f"name='{_q(name)}'")
    res = svc.files().list(
        q=" and ".join(clauses),
        pageSize=1,
        spaces="drive",
        fields="files(id,name,parents,webViewLink,webContentLink,size,md5Checksum)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files") or []
    return files[0] if files else None


def upload_file(path: str | Path, *, folder_id: str = "", existing_file_id: str = "", item_id: int | None = None) -> dict:
    libs = _imports()
    src = Path(path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"file not found: {src}")
    svc = service()
    folder = folder_id or _default_folder_id()
    if not existing_file_id:
        existing = _find_uploaded_file(svc, folder, src.name, item_id=item_id)
        if existing:
            existing_file_id = existing["id"]
    mime, _ = mimetypes.guess_type(str(src))
    media = libs["MediaFileUpload"](
        str(src),
        mimetype=mime or "application/octet-stream",
        resumable=src.stat().st_size > 5 * 1024 * 1024,
    )
    body: dict[str, Any] = {"name": src.name}
    if item_id is not None:
        body["appProperties"] = {"reviewhunter_item_id": str(item_id)}
    fields = "id,name,parents,webViewLink,webContentLink,size,md5Checksum"
    if existing_file_id:
        try:
            return svc.files().update(
                fileId=existing_file_id,
                body=body,
                media_body=media,
                fields=fields,
                supportsAllDrives=True,
            ).execute()
        except libs["HttpError"] as exc:
            if getattr(exc.resp, "status", None) not in (404, 410):
                raise
    body["parents"] = [folder]
    return svc.files().create(
        body=body,
        media_body=media,
        fields=fields,
        supportsAllDrives=True,
    ).execute()


def upload_directory(path: str | Path, *, parent_id: str = "") -> dict:
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"folder not found: {root}")
    parent = parent_id or _default_folder_id()
    drive_root = ensure_folder_path(root.name, parent_id=parent, persist=False)
    uploaded = 0
    folders = 1

    def walk(local_dir: Path, drive_parent: str) -> None:
        nonlocal uploaded, folders
        for child in sorted(local_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.is_dir():
                folder = ensure_folder_path(child.name, parent_id=drive_parent, persist=False)
                folders += 1
                walk(child, folder["id"])
            elif child.is_file():
                upload_file(child, folder_id=drive_parent)
                uploaded += 1

    walk(root, drive_root["id"])
    return {"ok": True, "root": drive_root, "files": uploaded, "folders": folders}
