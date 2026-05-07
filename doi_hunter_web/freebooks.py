"""FreeBooksAPI client — book/publication search across LibGen, libgen.lc, planet-ebooks.

Wraps the public hosted instance at ``freebooksapi.pyaesonemyo.dev`` (the upstream
project is archived; instances may be self-hosted by the user). Override the
endpoint with the ``REVIEWHUNTER_FREEBOOKS_URL`` env var.

Public surface:
    search(query, library='libgen', limit=10) -> list[dict]
    resolve_book_url(mirror_url) -> Optional[str]
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from doi_hunter.netinfo import get_headers

log = logging.getLogger("doihunter.freebooks")

DEFAULT_BASE = "https://freebooksapi.pyaesonemyo.dev/api/latest"
LIBRARIES = ("libgen", "libgen.lc", "planetebooks")


def base_url() -> str:
    return (os.environ.get("REVIEWHUNTER_FREEBOOKS_URL") or os.environ.get("DOIHUNTER_FREEBOOKS_URL", DEFAULT_BASE)).rstrip("/")


# ---------------- Search ----------------

def _normalize_publication(pub: dict) -> dict:
    """Map a FreeBooksAPI Publication dict to our DB-friendly shape.

    Source fields (per upstream Publication dataclass):
        id, authors, isbn, edition, series, title, publisher, year, pages,
        lang, size, extension, mirrors
    """
    authors_raw = pub.get("authors") or ""
    if isinstance(authors_raw, str):
        # FreeBooksAPI returns a single comma/semicolon-joined string; split it.
        parts = [p.strip() for p in re.split(r"[;,]", authors_raw) if p.strip()]
    else:
        parts = list(authors_raw)

    year = pub.get("year")
    try:
        year = int(year) if year not in (None, "", "0") else None
    except (TypeError, ValueError):
        year = None

    isbn = pub.get("isbn") or []
    if isinstance(isbn, str):
        isbn = [x.strip() for x in isbn.split(",") if x.strip()]

    mirrors = pub.get("mirrors") or []
    if isinstance(mirrors, str):
        mirrors = [mirrors]

    return {
        "title": (pub.get("title") or "").strip(),
        "authors": parts,
        "year": year,
        "publisher": (pub.get("publisher") or "").strip(),
        "edition": (pub.get("edition") or "").strip(),
        "series": (pub.get("series") or "").strip(),
        "isbn": isbn,
        "pages": str(pub.get("pages") or "").strip(),
        "language": (pub.get("lang") or "").strip(),
        "extension": (pub.get("extension") or "").strip().lower(),
        "size": (pub.get("size") or "").strip(),
        "mirrors": mirrors,
        "item_type": "book",
    }


def search(query: str, library: str = "libgen", limit: int = 10, timeout: float = 15) -> list[dict]:
    """Search FreeBooksAPI. Returns [] on any error so the UI can degrade gracefully."""
    if not query or not query.strip():
        return []
    if library not in LIBRARIES:
        library = "libgen"
    url = f"{base_url()}/{library}/search"
    try:
        r = requests.get(url, params={"q": query, "limit": limit},
                         headers=get_headers(), timeout=timeout)
        if r.status_code != 200:
            log.info(f"FreeBooksAPI {url} -> HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        log.info(f"FreeBooksAPI request failed: {e}")
        return []

    pubs = data.get("publications") if isinstance(data, dict) else None
    if not isinstance(pubs, list):
        # Some self-hosted instances may return the array directly
        pubs = data if isinstance(data, list) else []

    out = []
    for p in pubs:
        if not isinstance(p, dict):
            continue
        norm = _normalize_publication(p)
        if norm["title"]:
            out.append(norm)
    return out


def is_available() -> bool:
    """Quick reachability check — used by the UI to show/hide the books panel."""
    try:
        r = requests.get(f"{base_url()}/libgen/search",
                         params={"q": "test", "limit": 1},
                         headers=get_headers(), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ---------------- Mirror resolver ----------------

# LibGen-style mirrors (library.lol, libgen.li, libgen.is, etc.) typically link
# to a landing page with a "GET" button. We score links and pick the best.

_FILE_EXT_RX = re.compile(r"\.(pdf|epub|mobi|djvu|azw3|fb2|rtf|cbz|cbr)(\?|#|$)", re.IGNORECASE)
_GET_BUTTON_TEXTS = {"get", "download", "direct download", "cloudflare"}


def _absolutize(href: str, base: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return urljoin(base, href)
    return href


def resolve_book_url(mirror_url: str) -> Optional[str]:
    """Follow a LibGen-style mirror landing page and return the actual file URL.

    Strategy:
      1. GET the landing page.
      2. If the response *is* the file (Content-Type matches), return its URL.
      3. Else parse for `<a href="...ext">` or buttons labeled GET / Download.
      4. Return the highest-scoring candidate.
    """
    try:
        r = requests.get(mirror_url, headers=get_headers(), timeout=20, allow_redirects=True)
    except Exception as e:
        log.debug(f"mirror fetch failed {mirror_url}: {e}")
        return None
    if r.status_code != 200:
        return None

    ctype = r.headers.get("Content-Type", "").lower()
    if any(t in ctype for t in ("application/pdf", "application/epub", "application/octet-stream",
                                "application/x-mobipocket-ebook", "image/vnd.djvu")):
        return r.url

    soup = BeautifulSoup(r.text, "html.parser")
    candidates: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        url = _absolutize(href, r.url)
        text = (a.get_text(strip=True) or "").lower()
        score = 0
        if _FILE_EXT_RX.search(url):
            score += 5
        if text in _GET_BUTTON_TEXTS:
            score += 4
        if "download" in text:
            score += 2
        if "get.php" in url or "/main/" in url or "/scimag/" in url:
            score += 2
        if score > 0:
            candidates.append((score, url))

    candidates.sort(key=lambda t: -t[0])
    return candidates[0][1] if candidates else None
