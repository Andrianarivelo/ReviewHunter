"""Adapter for the ``libgen-api-modern`` PyPI package.

Why this is the primary book/publication source:
  - Direct download links (``Direct_Download_Link``) — no mirror-page scraping.
  - Three corpora: non-fiction/sci-tech, fiction, and scientific articles.
  - Async-native, plays well with FastAPI.

The package is imported lazily so the rest of ReviewHunter still loads (and the
fallback to FreeBooksAPI still works) when it's missing.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("doihunter.libgen")


def available() -> bool:
    try:
        import libgen_api_modern  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------- Normalization ----------------

def _split_authors(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(a).strip() for a in raw if str(a).strip()]
    return [p.strip() for p in re.split(r"[;,]", str(raw)) if p.strip()]


def _coerce_year(raw) -> Optional[int]:
    if raw in (None, "", "0"):
        return None
    try:
        return int(str(raw)[:4])
    except (TypeError, ValueError):
        return None


def _normalize_record(rec: dict, *, kind: str) -> dict:
    """Map a libgen-api-modern result dict to our DB-friendly book shape.

    Field names vary by ``kind``:
      - sci-tech: Title, Author(s), Series, Publisher, Year, Edition, Language,
                  Pages, ISBN, Size, Extension, Cover, Direct_Download_Link
      - fiction:  Title, Authors, Language, Year, Publisher, Format, Cover,
                  Direct_Download_Link
      - sci-mag:  Title, Authors, DOI, Journal, Publisher, Year, Volume, Issue,
                  Pages, Direct_Download_Link_1, Direct_Download_Link_2
    """
    title = (rec.get("Title") or "").strip()
    authors = _split_authors(rec.get("Authors") or rec.get("Author(s)"))

    # Both fiction (`Format`) and sci-tech (`Extension`) name the file format.
    extension = (rec.get("Extension") or rec.get("Format") or "").strip().lower().lstrip(".")

    isbn = rec.get("ISBN") or []
    if isinstance(isbn, str):
        isbn = [x.strip() for x in re.split(r"[;,]", isbn) if x.strip()]

    mirrors: list[str] = []
    for key in ("Direct_Download_Link", "Direct_Download_Link_1", "Direct_Download_Link_2"):
        v = rec.get(key)
        if v and isinstance(v, str):
            mirrors.append(v)

    return {
        "title": title,
        "authors": authors,
        "year": _coerce_year(rec.get("Year")),
        "publisher": (rec.get("Publisher") or "").strip(),
        "edition": (rec.get("Edition") or "").strip(),
        "series": (rec.get("Series") or "").strip(),
        "isbn": isbn,
        "pages": str(rec.get("Pages") or "").strip(),
        "language": (rec.get("Language") or "").strip(),
        "extension": extension,
        "size": (rec.get("Size") or "").strip(),
        "mirrors": mirrors,
        "cover": (rec.get("Cover") or "").strip(),
        "doi": (rec.get("DOI") or "").strip().lower() or None,
        "journal": (rec.get("Journal") or "").strip(),
        "kind": kind,            # 'scitech' | 'fiction' | 'scimag'
        "item_type": "paper" if kind == "scimag" else "book",
        "_source": "libgen-api-modern",
    }


# ---------------- Search entry points ----------------

async def search_books(query: str, limit: int = 10) -> list[dict]:
    """Run sci-tech and fiction searches concurrently, merge, dedupe, and trim."""
    if not available() or not query.strip():
        return []
    import asyncio
    from libgen_api_modern import LibgenSearch  # type: ignore

    async def _scitech() -> list[dict]:
        try:
            res = await LibgenSearch.search(query)
        except Exception as e:
            log.info(f"LibgenSearch.search failed: {e}")
            return []
        return [_normalize_record(r, kind="scitech") for r in (res or [])]

    async def _fiction() -> list[dict]:
        try:
            res = await LibgenSearch.search_fiction(query)
        except Exception as e:
            log.info(f"LibgenSearch.search_fiction failed: {e}")
            return []
        return [_normalize_record(r, kind="fiction") for r in (res or [])]

    sci, fic = await asyncio.gather(_scitech(), _fiction())

    # De-duplicate by (title, first author) — same book can appear in both
    # corpora with slightly different metadata.
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for rec in sci + fic:
        if not rec["title"]:
            continue
        key = (rec["title"].lower(), (rec["authors"][0].lower() if rec["authors"] else ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
        if len(out) >= limit:
            break
    return out


async def search_articles(query: str, limit: int = 10) -> list[dict]:
    """Search Sci-Mag (scientific articles). Returns paper-shaped records with DOI."""
    if not available() or not query.strip():
        return []
    from libgen_api_modern import LibgenSearch  # type: ignore
    try:
        res = await LibgenSearch.search_scientific_articles(query)
    except Exception as e:
        log.info(f"LibgenSearch.search_scientific_articles failed: {e}")
        return []
    return [_normalize_record(r, kind="scimag") for r in (res or [])][:limit]
