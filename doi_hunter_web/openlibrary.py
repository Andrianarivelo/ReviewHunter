"""Open Library book metadata fallback.

This source does not provide PDFs. It keeps book discovery useful when LibGen
mirrors or the FreeBooksAPI host are blocked/unreachable.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from doi_hunter.netinfo import get_headers

log = logging.getLogger("doihunter.openlibrary")

SEARCH_URL = "https://openlibrary.org/search.json"


def _first(values, default=""):
    if isinstance(values, list) and values:
        return values[0]
    return default


def _coerce_year(raw) -> Optional[int]:
    try:
        return int(raw) if raw not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def _normalize(doc: dict) -> dict:
    isbn = doc.get("isbn") or []
    if isinstance(isbn, str):
        isbn = [isbn]
    authors = doc.get("author_name") or []
    if isinstance(authors, str):
        authors = [authors]
    languages = doc.get("language") or []
    return {
        "title": (doc.get("title") or "").strip(),
        "authors": [str(a).strip() for a in authors if str(a).strip()],
        "year": _coerce_year(doc.get("first_publish_year")),
        "publisher": str(_first(doc.get("publisher")) or "").strip(),
        "edition": "",
        "series": "",
        "isbn": [str(x).strip() for x in isbn if str(x).strip()][:8],
        "pages": str(doc.get("number_of_pages_median") or "").strip(),
        "language": ", ".join(str(x) for x in languages[:3]) if isinstance(languages, list) else "",
        "extension": "",
        "size": "",
        "mirrors": [],
        "cover": f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg" if doc.get("cover_i") else "",
        "item_type": "book",
        "_source": "Open Library",
    }


def search(query: str, limit: int = 10, timeout: float = 15) -> list[dict]:
    if not query or not query.strip():
        return []
    try:
        r = requests.get(
            SEARCH_URL,
            params={"q": query, "limit": limit, "fields": "title,author_name,first_publish_year,publisher,isbn,language,number_of_pages_median,cover_i"},
            headers=get_headers(),
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.info(f"Open Library search failed: {e}")
        return []
    docs = data.get("docs") if isinstance(data, dict) else []
    out = []
    seen: set[tuple[str, str, Optional[int]]] = set()
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        norm = _normalize(doc)
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
            params={"q": "test", "limit": 1, "fields": "key"},
            headers=get_headers(),
            timeout=timeout,
        )
        return r.status_code == 200
    except Exception:
        return False
