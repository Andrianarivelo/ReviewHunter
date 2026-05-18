"""Library paths and runtime config. Single source of truth for where things live."""
import json
import os
import string
from pathlib import Path

APP_NAME = "ReviewHunter"

def library_root() -> Path:
    """User-overridable via REVIEWHUNTER_HOME env var; defaults to ~/ReviewHunter."""
    override = os.environ.get("REVIEWHUNTER_HOME") or os.environ.get("DOIHUNTER_HOME")
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = Path.home() / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return library_root() / "library.db"


def _read_settings() -> dict:
    p = settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_settings(settings: dict) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def read_settings() -> dict:
    return _read_settings()


def update_settings(**updates) -> dict:
    settings = _read_settings()
    settings.update(updates)
    _write_settings(settings)
    return settings


def set_papers_dir(path: str | Path) -> Path:
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    settings = _read_settings()
    settings["papers_dir"] = str(p)
    _write_settings(settings)
    return p


def papers_dir() -> Path:
    override = os.environ.get("REVIEWHUNTER_PAPERS_DIR") or os.environ.get("DOIHUNTER_PAPERS_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    else:
        configured = _read_settings().get("papers_dir")
        p = Path(configured).expanduser().resolve() if configured else library_root() / "papers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return library_root() / "settings.json"


def google_drive_token_path() -> Path:
    return library_root() / "google_drive_token.json"


def google_drive_client_config_path() -> Path:
    return library_root() / "google_oauth_client.json"


def google_drive_candidates() -> list[Path]:
    """Likely local Google Drive sync roots.

    This targets Google Drive for desktop / legacy Backup and Sync folders. The
    app writes to the local sync folder; Google Drive handles cloud upload.
    """
    candidates: list[Path] = []
    for env_name in ("REVIEWHUNTER_GOOGLE_DRIVE", "GOOGLE_DRIVE", "GOOGLEDRIVE", "GDRIVE_ROOT"):
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    home = Path.home()
    candidates.extend([
        home / "Google Drive",
        home / "Google Drive" / "My Drive",
        home / "My Drive",
    ])

    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:/")
            candidates.extend([
                root / "My Drive",
                root / "Google Drive",
                root / "Mon Drive",
            ])

    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen or not resolved.exists() or not resolved.is_dir():
            continue
        seen.add(key)
        out.append(resolved)
    return out


def default_google_drive_root() -> Path | None:
    candidates = google_drive_candidates()
    return candidates[0] if candidates else None


HOST = os.environ.get("REVIEWHUNTER_HOST") or os.environ.get("DOIHUNTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("REVIEWHUNTER_PORT") or os.environ.get("DOIHUNTER_PORT", "8765"))
