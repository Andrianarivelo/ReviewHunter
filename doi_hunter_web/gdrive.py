"""Google Drive integration: two complementary modes + backups.

Desktop mode (recommended, zero API setup)
    Google Drive for Desktop exposes Drive as a local folder (e.g. ``G:\\My Drive``).
    ReviewHunter can store PDFs there and writes periodic library backups there.
    The live SQLite database is deliberately kept LOCAL: a database file that is being
    written while a sync client uploads it can be corrupted. Backups are consistent
    snapshots made with SQLite's online backup API.

API mode (no Drive app needed, works across machines)
    OAuth "installed app" flow with the ``drive.file`` scope: the app only sees files it
    created itself (non-sensitive scope, no Google verification needed). The user
    supplies ``client_secret.json`` once; tokens are stored in ``<library>/google``.
    Push: PDFs/attachments + backup archives. Pull: list/download backups and files.

Backups (both modes, also without Drive)
    ``create_backup`` -> ``ReviewHunter-backup-YYYYmmdd-HHMMSS.zip`` containing
    ``library.db`` (snapshot), ``settings.json`` and ``manifest.json``. Restoring is staged:
    the archive is verified, the current DB is itself backed up, and the swap happens at the
    next start (no open connections can be corrupted).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config
from .db import get_conn

log = logging.getLogger("doihunter.gdrive")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_ROOT_FOLDER = "ReviewHunter"
BACKUP_PREFIX = "ReviewHunter-backup-"
PENDING_RESTORE = "library.db.restore-pending"

_state_lock = threading.Lock()
_oauth_state: dict[str, Any] = {"running": False, "error": "", "finished_at": ""}
_sync_state: dict[str, Any] = {"running": False, "last_result": None, "error": ""}


# ======================================================================================
# Desktop mode
# ======================================================================================

def desktop_installed() -> bool:
    if os.name != "nt":
        return bool(config.google_drive_candidates())
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google" / "Drive File Stream"]
    return any(r.exists() for r in roots)


def desktop_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq GoogleDriveFS.exe", "/NH"], capture_output=True,
                             text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "GoogleDriveFS.exe" in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def launch_desktop_installer() -> dict:
    """Open a console running winget so the user sees and answers every prompt themselves."""
    if os.name != "nt":
        return {"ok": False, "url": "https://www.google.com/drive/download/"}
    if shutil.which("winget"):
        subprocess.Popen(
            ["cmd", "/c", "start", "Install Google Drive", "cmd", "/k",
             "winget install --id Google.GoogleDrive -e && echo. && echo Done. Sign in to Google Drive, then return to ReviewHunter."],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return {"ok": True, "method": "winget"}
    os.startfile("https://www.google.com/drive/download/")  # type: ignore[attr-defined]
    return {"ok": True, "method": "browser"}


def enable_desktop(root: str, *, papers_in_drive: bool) -> dict:
    from .library import configure_papers_directory

    root_path = Path(root).expanduser()
    if not root_path.exists():
        raise ValueError(f"folder not found: {root}")
    (root_path / DRIVE_ROOT_FOLDER / "backups").mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"ok": True, "root": str(root_path)}
    if papers_in_drive:
        result["papers"] = configure_papers_directory(root_path / DRIVE_ROOT_FOLDER / "papers", migrate_existing=True)
    config.update_settings({"drive_mode": "desktop", "drive_desktop_root": str(root_path),
                            "drive_papers_in_drive": papers_in_drive})
    result["backup"] = create_backup()
    return result


# ======================================================================================
# Backups
# ======================================================================================

def backup_target_dir() -> Path:
    s = config.get_settings()
    if s.get("drive_mode") == "desktop" and s.get("drive_desktop_root") and Path(s["drive_desktop_root"]).exists():
        p = Path(s["drive_desktop_root"]) / DRIVE_ROOT_FOLDER / "backups"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return config.backups_dir()


def create_backup(destination: Optional[Path] = None) -> dict:
    dest_dir = Path(destination) if destination else backup_target_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"{BACKUP_PREFIX}{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "library.db"
        dst = sqlite3.connect(str(snap))
        try:
            get_conn().backup(dst)
        finally:
            dst.close()
        counts = sqlite3.connect(str(snap))
        n_items = counts.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        counts.close()
        manifest = {"app": "ReviewHunter", "created_at": datetime.now().isoformat(timespec="seconds"),
                    "items": n_items, "library_root": str(config.library_root()),
                    "papers_dir": str(config.papers_dir()), "schema_version": 2}
        tmp_zip = target.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "library.db")
            if config.settings_path().exists():
                z.write(config.settings_path(), "settings.json")
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
        tmp_zip.replace(target)
    config.update_settings({"last_backup_at": datetime.now().isoformat(timespec="seconds")})
    _prune_backups(dest_dir)
    return {"ok": True, "path": str(target), "items": n_items, "size": target.stat().st_size}


def _prune_backups(folder: Path) -> None:
    keep = int(config.get_settings().get("backup_keep") or 10)
    backups = sorted(folder.glob(f"{BACKUP_PREFIX}*.zip"))
    for old in backups[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups() -> list[dict]:
    seen: set[str] = set()
    out = []
    for folder in {backup_target_dir(), config.backups_dir()}:
        for f in sorted(folder.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True):
            if str(f) in seen:
                continue
            seen.add(str(f))
            info: dict[str, Any] = {"path": str(f), "name": f.name, "size": f.stat().st_size,
                                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
            try:
                with zipfile.ZipFile(f) as z:
                    info.update(json.loads(z.read("manifest.json")))
            except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
                info["corrupt"] = True
            out.append(info)
    return sorted(out, key=lambda b: b["name"], reverse=True)


def stage_restore(backup_path: str) -> dict:
    """Verify an archive and stage it; it replaces the DB at the next application start."""
    path = Path(backup_path)
    if not path.exists():
        raise ValueError("backup not found")
    root = config.library_root()
    with zipfile.ZipFile(path) as z:
        if "library.db" not in z.namelist():
            raise ValueError("archive does not contain library.db")
        staged = root / PENDING_RESTORE
        with z.open("library.db") as src, open(staged, "wb") as dst:
            shutil.copyfileobj(src, dst)
    check = sqlite3.connect(str(staged))
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        n = check.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        staged.unlink(missing_ok=True)
        raise ValueError(f"backup failed integrity check: {ok}")
    safety = create_backup(config.backups_dir())
    return {"ok": True, "staged_items": n, "safety_backup": safety["path"], "restart_required": True}


def apply_pending_restore() -> Optional[str]:
    """Called at startup BEFORE any DB connection is opened."""
    root = config.library_root()
    staged = root / PENDING_RESTORE
    if not staged.exists():
        return None
    db = root / "library.db"
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    staged.replace(db)
    log.info("restored library database from staged backup")
    return str(db)


# ======================================================================================
# API mode
# ======================================================================================

def _client_secret_path() -> Path:
    return config.google_dir() / "client_secret.json"


def _token_path() -> Path:
    return config.google_dir() / "token.json"


def save_client_secret(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("not a JSON file") from e
    if "installed" not in data:
        kind = next(iter(data), "unknown")
        raise ValueError(f"expected a 'Desktop app' OAuth client (key 'installed'), got '{kind}'. "
                         "In Google Cloud Console choose Application type: Desktop app.")
    _client_secret_path().write_text(json.dumps(data), encoding="utf-8")
    return {"ok": True, "client_id": data["installed"].get("client_id", "")[:24] + "..."}


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tp = _token_path()
    if not tp.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tp.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            log.warning(f"token refresh failed: {e}")
            return None
    return creds if creds and creds.valid else None


def start_oauth() -> dict:
    """Open the Google consent page; a local loopback server receives the code."""
    if not _client_secret_path().exists():
        raise ValueError("upload client_secret.json first")
    with _state_lock:
        if _oauth_state["running"]:
            return {"ok": True, "running": True}
        _oauth_state.update(running=True, error="")

    def run() -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret_path()), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300,
                                          success_message="ReviewHunter is connected to Google Drive. "
                                                          "You can close this tab.")
            _token_path().write_text(creds.to_json(), encoding="utf-8")
            config.update_settings({"drive_mode": "api"})
        except Exception as e:  # user closed the tab, timeout, network...
            _oauth_state["error"] = str(e) or e.__class__.__name__
        finally:
            _oauth_state.update(running=False, finished_at=datetime.now().isoformat(timespec="seconds"))

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "running": True}


def disconnect_api() -> dict:
    creds = None
    try:
        creds = _credentials()
    except Exception:
        pass
    if creds is not None:
        try:
            import requests

            requests.post("https://oauth2.googleapis.com/revoke", params={"token": creds.token}, timeout=10)
        except Exception:
            pass
    _token_path().unlink(missing_ok=True)
    if config.get_settings().get("drive_mode") == "api":
        config.update_settings({"drive_mode": "none"})
    return {"ok": True}


def _service():
    from googleapiclient.discovery import build

    creds = _credentials()
    if not creds:
        raise ValueError("Google Drive API is not connected")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _ensure_folder(svc, name: str, parent: Optional[str] = None) -> str:
    q = [f"name = '{name}'", "mimeType = 'application/vnd.google-apps.folder'", "trashed = false"]
    if parent:
        q.append(f"'{parent}' in parents")
    res = svc.files().list(q=" and ".join(q), fields="files(id)", spaces="drive").execute()
    if res.get("files"):
        return res["files"][0]["id"]
    body: dict[str, Any] = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    return svc.files().create(body=body, fields="id").execute()["id"]


def api_account() -> dict:
    svc = _service()
    about = svc.about().get(fields="user(displayName,emailAddress),storageQuota(limit,usage)").execute()
    return about


def sync_up(*, include_files: bool = True) -> dict:
    """Upload a fresh backup and new/changed attachment files."""
    from googleapiclient.http import MediaFileUpload

    with _state_lock:
        if _sync_state["running"]:
            return {"ok": False, "reason": "sync already running"}
        _sync_state.update(running=True, error="")
    try:
        svc = _service()
        root = _ensure_folder(svc, DRIVE_ROOT_FOLDER)
        backups = _ensure_folder(svc, "backups", root)
        papers = _ensure_folder(svc, "papers", root)
        backup = create_backup(config.backups_dir())
        media = MediaFileUpload(backup["path"], mimetype="application/zip", resumable=True)
        svc.files().create(body={"name": Path(backup["path"]).name, "parents": [backups]}, media_body=media,
                           fields="id").execute()
        uploaded = skipped = 0
        conn = get_conn()
        if include_files:
            rows = conn.execute("SELECT id, path, mime FROM attachments WHERE kind IN ('file','snapshot') "
                                "AND path IS NOT NULL").fetchall()
            for row in rows:
                p = Path(row["path"])
                if not p.exists():
                    continue
                st = p.stat()
                known = conn.execute("SELECT drive_id, size, mtime FROM drive_files WHERE local_path=?",
                                     (str(p),)).fetchone()
                if known and known["size"] == st.st_size and abs((known["mtime"] or 0) - st.st_mtime) < 1:
                    skipped += 1
                    continue
                media = MediaFileUpload(str(p), mimetype=row["mime"] or "application/octet-stream", resumable=True)
                if known:
                    svc.files().update(fileId=known["drive_id"], media_body=media).execute()
                    drive_id = known["drive_id"]
                else:
                    drive_id = svc.files().create(body={"name": p.name, "parents": [papers]}, media_body=media,
                                                  fields="id").execute()["id"]
                conn.execute("INSERT OR REPLACE INTO drive_files (local_path, drive_id, size, mtime) VALUES (?,?,?,?)",
                             (str(p), drive_id, st.st_size, st.st_mtime))
                conn.execute("UPDATE attachments SET drive_file_id=? WHERE id=?", (drive_id, row["id"]))
                uploaded += 1
        # Keep only the N most recent remote backups.
        keep = int(config.get_settings().get("backup_keep") or 10)
        remote = svc.files().list(q=f"'{backups}' in parents and trashed = false", orderBy="name desc",
                                  fields="files(id,name)", pageSize=200).execute().get("files", [])
        for f in remote[keep:]:
            svc.files().delete(fileId=f["id"]).execute()
        result = {"ok": True, "uploaded": uploaded, "unchanged": skipped, "backup": Path(backup["path"]).name}
        config.update_settings({"last_drive_sync_at": datetime.now().isoformat(timespec="seconds")})
        _sync_state["last_result"] = result
        return result
    except Exception as e:
        _sync_state["error"] = str(e)
        raise
    finally:
        _sync_state["running"] = False


def list_remote_backups() -> list[dict]:
    svc = _service()
    root = _ensure_folder(svc, DRIVE_ROOT_FOLDER)
    backups = _ensure_folder(svc, "backups", root)
    return svc.files().list(q=f"'{backups}' in parents and trashed = false", orderBy="name desc",
                            fields="files(id,name,size,modifiedTime)", pageSize=100).execute().get("files", [])


def download_remote_backup(file_id: str) -> dict:
    from googleapiclient.http import MediaIoBaseDownload

    svc = _service()
    meta = svc.files().get(fileId=file_id, fields="name").execute()
    target = config.backups_dir() / meta["name"]
    with open(target, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, svc.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return {"ok": True, "path": str(target)}


def download_missing_files() -> dict:
    """On a new machine: fetch Drive copies of attachments whose local file is missing."""
    from googleapiclient.http import MediaIoBaseDownload

    svc = _service()
    conn = get_conn()
    fetched = 0
    rows = conn.execute("SELECT id, path, drive_file_id FROM attachments WHERE drive_file_id IS NOT NULL").fetchall()
    for row in rows:
        p = Path(row["path"]) if row["path"] else None
        if p and p.exists():
            continue
        target = config.papers_dir() / (p.name if p else f"attachment-{row['id']}.pdf")
        with open(target, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, svc.files().get_media(fileId=row["drive_file_id"]))
            done = False
            while not done:
                _, done = downloader.next_chunk()
        conn.execute("UPDATE attachments SET path=?, size=? WHERE id=?", (str(target), target.stat().st_size,
                                                                        row["id"]))
        conn.execute("UPDATE items SET file_path=? WHERE file_path=?", (str(target), row["path"]))
        fetched += 1
    return {"ok": True, "downloaded": fetched}


# ======================================================================================
# Status + scheduler
# ======================================================================================

def status() -> dict:
    s = config.get_settings()
    candidates = [str(p) for p in config.google_drive_candidates()]
    api_connected = False
    api_error = ""
    if _token_path().exists():
        try:
            api_connected = _credentials() is not None
        except Exception as e:
            api_error = str(e)
    return {
        "mode": s.get("drive_mode"),
        "desktop": {
            "installed": desktop_installed(),
            "running": desktop_running(),
            "candidates": candidates,
            "root": s.get("drive_desktop_root"),
            "papers_in_drive": s.get("drive_papers_in_drive"),
        },
        "api": {
            "client_secret": _client_secret_path().exists(),
            "connected": api_connected,
            "oauth": dict(_oauth_state),
            "sync": {k: v for k, v in _sync_state.items()},
            "auto_sync": s.get("drive_api_auto_sync"),
            "error": api_error,
            "last_sync_at": s.get("last_drive_sync_at"),
        },
        "backups": {
            "target": str(backup_target_dir()),
            "interval_hours": s.get("backup_interval_hours"),
            "keep": s.get("backup_keep"),
            "last_backup_at": s.get("last_backup_at"),
        },
        "papers_dir": str(config.papers_dir()),
        "library_root": str(config.library_root()),
    }


def _due(last_iso: str, hours: float) -> bool:
    if hours <= 0:
        return False
    if not last_iso:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(last_iso)).total_seconds() >= hours * 3600
    except ValueError:
        return True


def scheduler_loop(stop: threading.Event, interval_s: int = 600) -> None:
    """Background thread: automatic backups (+ API sync when enabled)."""
    time.sleep(30)  # let the app start first
    while not stop.is_set():
        try:
            s = config.get_settings()
            hours = float(s.get("backup_interval_hours") or 0)
            if s.get("drive_mode") == "api" and s.get("drive_api_auto_sync"):
                if _due(s.get("last_drive_sync_at", ""), hours or 24):
                    sync_up()
            elif _due(s.get("last_backup_at", ""), hours):
                create_backup()
        except Exception as e:
            log.warning(f"scheduled backup/sync failed: {e}")
        stop.wait(interval_s)
