"""Book/publication search orchestrator.

Tries ``libgen-api-modern`` first (direct download links, fastest, multiple
corpora), falls back to FreeBooksAPI when the package isn't installed or
returns nothing useful. Both backends produce the same normalized shape
already, so callers don't care which one served the result.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from . import freebooks, libgen

log = logging.getLogger("doihunter.books")


async def search(query: str, limit: int = 10) -> dict:
    """Return ``{"available": bool, "results": [...], "source": str}``.

    ``available`` is False only when *no* backend is reachable, so the UI can
    surface a single "books search unavailable" hint.
    """
    if not query or not query.strip():
        return {"available": True, "results": [], "source": ""}

    if libgen.available():
        try:
            results = await libgen.search_books(query, limit=limit)
        except Exception as e:
            log.warning(f"libgen-api-modern failed: {e}")
            results = []
        if results:
            return {"available": True, "results": results, "source": "libgen-api-modern"}

    # Fallback: FreeBooksAPI (sync HTTP client, run in a thread to keep the loop free)
    try:
        results = await asyncio.to_thread(freebooks.search, query, "libgen", limit)
    except Exception as e:
        log.warning(f"FreeBooksAPI fallback failed: {e}")
        results = []

    if results:
        return {"available": True, "results": results, "source": "FreeBooksAPI"}

    # Neither backend returned anything; check whether either was even reachable
    # so the UI can distinguish "no matches" from "service down".
    fb_up = await asyncio.to_thread(freebooks.is_available)
    return {"available": libgen.available() or fb_up, "results": [], "source": ""}


async def search_articles(query: str, limit: int = 10) -> list[dict]:
    """Sci-Mag (scientific articles) search via libgen-api-modern only."""
    if libgen.available():
        return await libgen.search_articles(query, limit=limit)
    return []
