"""Google Books metadata search.

This source does not provide PDFs. It gives fast book metadata for discovery,
with Open Library and download-oriented sources as fallbacks.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from doi_hunter.netinfo import get_headers

log = logging.getLogger("doihunter.googlebooks")

SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"


def _coerce_year(raw) -> Optional[int]:
    if not raw:
        return None
    m = re.search(r"\d{4}", str(raw))
    return int(m.group(0)) if m else None


def _normalize(item: dict) -> dict:
    info = item.get("volumeInfo") or {}
    identifiers = info.get("industryIdentifiers") or []
    isbn = []
    for ident in identifiers:
        if not isinstance(ident, dict):
            continue
        value = str(ident.get("identifier") or "").strip()
        if value:
            isbn.append(value)
    images = info.get("imageLinks") or {}
    cover = images.get("thumbnail") or images.get("smallThumbnail") or ""
    if cover.startswith("http://"):
        cover = "https://" + cover.removeprefix("http://")
    authors = info.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    return {
        "title": str(info.get("title") or "").strip(),
        "authors": [str(a).strip() for a in authors if str(a).strip()],
        "year": _coerce_year(info.get("publishedDate")),
        "publisher": str(info.get("publisher") or "").strip(),
        "edition": "",
        "series": "",
        "isbn": isbn[:8],
        "pages": str(info.get("pageCount") or "").strip(),
        "language": str(info.get("language") or "").strip(),
        "extension": "",
        "size": "",
        "mirrors": [],
        "cover": cover,
        "item_type": "book",
        "_source": "Google Books",
    }


def search(query: str, limit: int = 10, timeout: float = 8) -> list[dict]:
    if not query or not query.strip():
        return []
    try:
        r = requests.get(
            SEARCH_URL,
            params={"q": query, "maxResults": max(1, min(limit, 40)), "printType": "books"},
            headers=get_headers(),
            timeout=timeout,
        )
        if r.status_code != 200:
            log.info(f"Google Books search returned HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        log.info(f"Google Books search failed: {e}")
        return []

    out = []
    seen: set[tuple[str, str, Optional[int]]] = set()
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        norm = _normalize(item)
        if not norm["title"]:
            continue
        key = (
            norm["title"].casefold(),
            (norm["authors"][0].casefold() if norm["authors"] else ""),
            norm["year"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out[:limit]


def is_available(timeout: float = 3) -> bool:
    try:
        r = requests.get(
            SEARCH_URL,
            params={"q": "test", "maxResults": 1, "printType": "books"},
            headers=get_headers(),
            timeout=timeout,
        )
        return r.status_code == 200
    except Exception:
        return False
