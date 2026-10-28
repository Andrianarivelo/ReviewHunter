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
STARTED_AT = datetime.now().isoformat(timespec="seconds")
SERVERS: list[Any] = []                             # uvicorn.Server objects registered by __main__


def info() -> dict:
    """What a launcher needs to decide whether this instance is the right one."""
    return {"app": "ReviewHunter", "version": __version__, "root": str(ROOT), "pid": os.getpid(),
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
