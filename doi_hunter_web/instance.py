"""Identity of the running server, and a clean way to stop it.

The launcher (``launcher.py``) and every shortcut rely on this: before opening the browser they ask the
server on the fixed port *who it is* (``GET /api/system/instance``). An instance started from another
install folder, or still running an older version, is asked to stop (``POST /api/system/shutdown``) and
replaced, so a click on the shortcut always shows the code of the installation the shortcut belongs to.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__

ROOT = Path(__file__).resolve().parents[1]          # the install (or checkout) folder of this code


def code_stamp(root: Path = ROOT) -> str:
    """Fingerprint of the Python code on disk (paths, sizes, modification times).

    The version number alone misses in-place updates (git pull, reinstalling the same version): the
    launcher compares this stamp with the one the running server recorded when it started.
    """
    import hashlib

    h = hashlib.sha1()
    for f in sorted((Path(root) / "doi_hunter_web").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


CODE_STAMP = code_stamp()                           # what this process is running
STARTED_AT = datetime.now().isoformat(timespec="seconds")
SERVERS: list[Any] = []                             # uvicorn.Server objects registered by __main__


def info() -> dict:
    """What a launcher needs to decide whether this instance is the right one."""
    return {"app": "ReviewHunter", "version": __version__, "code": CODE_STAMP, "root": str(ROOT), "pid": os.getpid(),
            "started_at": STARTED_AT, "python": sys.executable,
            "launched_by": os.environ.get("REVIEWHUNTER_LAUNCHED_BY", "terminal"),
            "can_shutdown": bool(SERVERS)}


def register(servers: list[Any]) -> None:
    SERVERS[:] = list(servers)


def request_shutdown(delay: float = 0.4) -> bool:
    """Ask every listener to exit after ``delay`` seconds (so the HTTP answer still reaches the caller).

    Returns False when the app was not started by ``python -m doi_hunter_web`` (tests, ``--reload``):
    there is then nothing this process may safely stop.
    """
    if not SERVERS:
        return False

    def stop() -> None:
        time.sleep(delay)
        for server in SERVERS:
            server.should_exit = True

    threading.Thread(target=stop, daemon=True, name="shutdown").start()
    return True
