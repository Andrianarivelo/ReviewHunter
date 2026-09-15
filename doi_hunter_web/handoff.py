"""Browser hand-off registry.

When the server cannot fetch a PDF (JavaScript checks, institutional access, human verification), it
registers a *plan* here: the ordered pages / PDF URLs the Chrome extension should try with the user's
own browser session. The web app forwards the plan to the extension immediately (window.postMessage
to the connector's content script) and the extension also polls ``GET /connector/handoffs`` once a
minute, so a plan is not lost when the app tab was closed. The extension reports progress through
``POST /connector/handoffs/{item_id}/status``; the server relays it to the UI as WebSocket events.

The registry lives in memory only (plans are cheap to rebuild) and forgets entries after ``TTL_S``.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

TTL_S = 6 * 3600
MAX_ENTRIES = 200
_LOCK = threading.Lock()
_ITEMS: dict[int, dict] = {}


def register(item_id: int, doi: str, title: str, plan: list[dict], reason: str = "") -> dict:
    """Store (or replace) the plan for an item and return the public record."""
    with _LOCK:
        _prune_locked()
        rec = {
            "item_id": int(item_id),
            "doi": doi or "",
            "title": (title or "")[:300],
            "plan": list(plan or [])[:20],
            "reason": (reason or "")[:500],
            "created_at": time.time(),
            "state": "pending",     # pending | running | waiting | done | failed
            "message": "",
            "updated_at": time.time(),
        }
        _ITEMS[int(item_id)] = rec
        return dict(rec)


def get(item_id: int) -> Optional[dict]:
    with _LOCK:
        rec = _ITEMS.get(int(item_id))
        return dict(rec) if rec else None


def pending(include_failed: bool = False) -> list[dict]:
    """Plans the extension should (still) run: pending / running / waiting, oldest first."""
    with _LOCK:
        _prune_locked()
        states = {"pending", "running", "waiting"} | ({"failed"} if include_failed else set())
        return [dict(r) for r in sorted(_ITEMS.values(), key=lambda r: r["created_at"]) if r["state"] in states]


def update(item_id: int, state: str, message: str = "") -> Optional[dict]:
    """Record progress reported by the extension."""
    with _LOCK:
        rec = _ITEMS.get(int(item_id))
        if not rec:
            return None
        if state in ("pending", "running", "waiting", "done", "failed"):
            rec["state"] = state
        rec["message"] = (message or "")[:500]
        rec["updated_at"] = time.time()
        return dict(rec)


def clear(item_id: int) -> None:
    with _LOCK:
        _ITEMS.pop(int(item_id), None)


def _prune_locked() -> None:
    now = time.time()
    for key in [k for k, r in _ITEMS.items() if now - r["created_at"] > TTL_S]:
        _ITEMS.pop(key, None)
    while len(_ITEMS) > MAX_ENTRIES:
        oldest = min(_ITEMS.values(), key=lambda r: r["created_at"])
        _ITEMS.pop(oldest["item_id"], None)
