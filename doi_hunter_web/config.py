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


# --------------------------------------------------------------------------------------
# Generic user settings (settings.json). Defaults live here so the UI and the server
# always agree on what a missing key means.
# --------------------------------------------------------------------------------------
DEFAULT_SETTINGS: dict = {
    "citation_style": "apa",                 # CSL style id used by Quick Copy / bibliography
    "citation_locale": "en-US",
    "quick_copy_format": "bibliography",     # bibliography | citation | bibtex | ris
    "rename_pattern": "{author} - {year} - {title}",
    "auto_rename_files": True,
    "auto_tag_keywords": True,               # add keywords from metadata as automatic tags
    "auto_index_fulltext": True,
    "auto_retrieve_pdf_metadata": True,
    "metadata_backfill_attempted": [],   # item ids already tried by the startup metadata backfill
    "backup_interval_hours": 24,             # 0 disables automatic backups
    "backup_keep": 10,
    "drive_mode": "none",                    # none | desktop | api
    "drive_desktop_root": "",
    "drive_papers_in_drive": False,
    "drive_api_auto_sync": False,
    "last_backup_at": "",
    "last_drive_sync_at": "",
    "https_port": 8766,
    # ---- PDF sources & network (see transport.py / sources.py)
    "unpaywall_email": "",                   # enables Unpaywall and OpenAlex's polite pool (any e-mail you own)
    "network_proxy": "",                     # e.g. socks5h://127.0.0.1:9150 (Tor Browser) or http://proxy:3128
    "proxy_scope": "shadow",                 # shadow: Sci-Hub / SciDB requests only | all: every download
    "use_system_certificates": True,         # trust the OS certificate store in addition to certifi (like Chrome)
    "browser_handoff": True,                 # hand failed downloads to the Chrome extension (user's session)
    "scihub_mirrors": [],                    # custom Sci-Hub mirrors (empty = built-in list)
    "scidb_bases": [],                       # custom Anna's Archive bases (empty = built-in list)
    "ezproxy_prefix": "",                    # e.g. https://ezproxy.example.edu/login?url=  (browser-only variant)
    # ---- Journal impact metrics (journal_metrics.py)
    "auto_retrieve_impact_factor": True,     # backfill journal metrics for new / unrated items at startup
    "impact_factor_backfill_attempted": [],  # item ids already tried by the startup backfill (unmatched journals)
}


def get_settings() -> dict:
    return {**DEFAULT_SETTINGS, **_read_settings()}


def update_settings(patch: dict) -> dict:
    current = _read_settings()
    for key, value in (patch or {}).items():
        if key in DEFAULT_SETTINGS or key == "papers_dir":
            current[key] = value
    _write_settings(current)
    return get_settings()


def certs_dir() -> Path:
    p = library_root() / "certs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def google_dir() -> Path:
    p = library_root() / "google"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_dir() -> Path:
    p = library_root() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p
