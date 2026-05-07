"""Library paths and runtime config. Single source of truth for where things live."""
import os
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


def papers_dir() -> Path:
    p = library_root() / "papers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return library_root() / "settings.json"


HOST = os.environ.get("REVIEWHUNTER_HOST") or os.environ.get("DOIHUNTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("REVIEWHUNTER_PORT") or os.environ.get("DOIHUNTER_PORT", "8765"))
