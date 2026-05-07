"""Book/publication search orchestrator.

The download-oriented sources are useful when reachable, but they are optional
and often blocked by DNS, mirror outages, or local network policy. Metadata
providers keep the Books tab useful for bibliographic review even when no
direct book download source is available.
"""
from __future__ import annotations

import asyncio
import logging

from . import freebooks, googlebooks, libgen, openlibrary

log = logging.getLogger("doihunter.books")

DOWNLOAD_SOURCE_TIMEOUT = 3.0


async def _search_openlibrary(query: str, limit: int) -> list[dict]:
    try:
        return await asyncio.to_thread(openlibrary.search, query, limit)
    except Exception as e:
        log.warning(f"Open Library fallback failed: {e}")
        return []


async def _search_googlebooks(query: str, limit: int) -> list[dict]:
    try:
        return await asyncio.to_thread(googlebooks.search, query, limit)
    except Exception as e:
        log.warning(f"Google Books search failed: {e}")
        return []


async def _search_libgen(query: str, limit: int) -> list[dict]:
    if not libgen.available():
        return []
    try:
        return await asyncio.wait_for(libgen.search_books(query, limit=limit), timeout=DOWNLOAD_SOURCE_TIMEOUT)
    except asyncio.TimeoutError:
        log.info("libgen-api-modern search timed out")
    except Exception as e:
        log.warning(f"libgen-api-modern failed: {e}")
    return []


async def _search_freebooks(query: str, limit: int) -> list[dict]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(freebooks.search, query, "libgen", limit),
            timeout=DOWNLOAD_SOURCE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.info("FreeBooksAPI fallback timed out")
    except Exception as e:
        log.warning(f"FreeBooksAPI fallback failed: {e}")
    return []


async def search(query: str, limit: int = 10) -> dict:
    """Return ``{"available": bool, "results": [...], "source": str}``.

    ``available`` is False only when *no* backend is reachable, so the UI can
    surface a single "books search unavailable" hint.
    """
    if not query or not query.strip():
        return {"available": True, "results": [], "source": ""}

    # Use reliable metadata sources first. Direct-download sources are queried
    # only when metadata providers have no matches; this keeps the Books tab
    # responsive when LibGen mirrors are blocked or down.
    google_results = await _search_googlebooks(query, limit)
    if google_results:
        return {"available": True, "results": google_results, "source": "Google Books"}

    openlibrary_results = await _search_openlibrary(query, limit)
    if openlibrary_results:
        return {"available": True, "results": openlibrary_results, "source": "Open Library"}

    results = await _search_libgen(query, limit)
    if results:
        return {"available": True, "results": results, "source": "libgen-api-modern"}

    results = await _search_freebooks(query, limit)
    if results:
        return {"available": True, "results": results, "source": "FreeBooksAPI"}

    # No backend returned anything; check whether either download source was even
    # reachable so the UI can distinguish "no matches" from "service down".
    gb_up, fb_up, ol_up = await asyncio.gather(
        asyncio.to_thread(googlebooks.is_available),
        asyncio.to_thread(freebooks.is_available),
        asyncio.to_thread(openlibrary.is_available),
    )
    return {"available": libgen.available() or gb_up or fb_up or ol_up, "results": [], "source": ""}


async def search_articles(query: str, limit: int = 10) -> list[dict]:
    """Sci-Mag (scientific articles) search via libgen-api-modern only."""
    if libgen.available():
        return await libgen.search_articles(query, limit=limit)
    return []
