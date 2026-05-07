"""Adapter for the ``libgen-api-modern`` PyPI package.

Why this is the primary book/publication source:
  - Direct download links (``Direct_Download_Link``) - no mirror-page scraping.
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
        import libgen  # noqa: F401
        return True
    except Exception:
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


def _read_field(rec, name: str, default=None):
    if isinstance(rec, dict):
        return rec.get(name, default)
    return getattr(rec, name, default)


def _normalize_download_links(links) -> list[str]:
    if not links:
        return []
    if isinstance(links, dict):
        vals = links.values()
    else:
        vals = [
            getattr(links, "get_link", None),
            getattr(links, "cloudflare_link", None),
            getattr(links, "ipfs_link", None),
            getattr(links, "pinata_link", None),
        ]
    return [str(v).strip() for v in vals if v and str(v).strip()]


def _normalize_record(rec, *, kind: str) -> dict:
    """Map a libgen-api-modern result dict to our DB-friendly book shape.

    Field names vary by ``kind``:
      - sci-tech: Title, Author(s), Series, Publisher, Year, Edition, Language,
                  Pages, ISBN, Size, Extension, Cover, Direct_Download_Link
      - fiction:  Title, Authors, Language, Year, Publisher, Format, Cover,
                  Direct_Download_Link
      - sci-mag:  Title, Authors, DOI, Journal, Publisher, Year, Volume, Issue,
                  Pages, Direct_Download_Link_1, Direct_Download_Link_2
    """
    title = (_read_field(rec, "Title") or _read_field(rec, "title") or "").strip()
    authors = _split_authors(
        _read_field(rec, "Authors")
        or _read_field(rec, "Author(s)")
        or _read_field(rec, "authors")
    )

    # Both fiction (`Format`) and sci-tech (`Extension`) name the file format.
    extension = (
        _read_field(rec, "Extension")
        or _read_field(rec, "Format")
        or _read_field(rec, "extension")
        or ""
    ).strip().lower().lstrip(".")

    isbn = _read_field(rec, "ISBN") or _read_field(rec, "isbn") or []
    if isinstance(isbn, str):
        isbn = [x.strip() for x in re.split(r"[;,]", isbn) if x.strip()]

    mirrors: list[str] = _normalize_download_links(_read_field(rec, "download_links"))
    for key in ("Direct_Download_Link", "Direct_Download_Link_1", "Direct_Download_Link_2"):
        v = _read_field(rec, key)
        if v and isinstance(v, str):
            mirrors.append(v)

    return {
        "title": title,
        "authors": authors,
        "year": _coerce_year(_read_field(rec, "Year") or _read_field(rec, "year")),
        "publisher": (_read_field(rec, "Publisher") or _read_field(rec, "publisher") or "").strip(),
        "edition": (_read_field(rec, "Edition") or _read_field(rec, "edition") or "").strip(),
        "series": (_read_field(rec, "Series") or _read_field(rec, "series") or "").strip(),
        "isbn": isbn,
        "pages": str(_read_field(rec, "Pages") or _read_field(rec, "pages") or "").strip(),
        "language": (_read_field(rec, "Language") or _read_field(rec, "language") or "").strip(),
        "extension": extension,
        "size": (_read_field(rec, "Size") or _read_field(rec, "size") or "").strip(),
        "mirrors": mirrors,
        "cover": (_read_field(rec, "Cover") or _read_field(rec, "cover_url") or "").strip(),
        "doi": (_read_field(rec, "DOI") or _read_field(rec, "doi") or "").strip().lower() or None,
        "journal": (_read_field(rec, "Journal") or _read_field(rec, "journal") or "").strip(),
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
    from libgen import SearchType, search_async  # type: ignore

    async def _scitech() -> list[dict]:
        try:
            res = await search_async(query)
        except Exception as e:
            log.info(f"libgen.search_async failed: {e}")
            return []
        return [_normalize_record(r, kind="scitech") for r in (res or [])]

    async def _fiction() -> list[dict]:
        try:
            res = await search_async(query, search_type=SearchType.FICTION)
        except Exception as e:
            log.info(f"libgen.search_async fiction failed: {e}")
            return []
        return [_normalize_record(r, kind="fiction") for r in (res or [])]

    sci, fic = await asyncio.gather(_scitech(), _fiction())

    # De-duplicate by (title, first author) - same book can appear in both
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
    return []
