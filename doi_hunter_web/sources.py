"""PDF source resolvers.

Resolution order: publisher/open-access PDF -> Sci-Hub mirrors -> Anna's
Archive SciDB. The library calls ``iter_pdf_urls`` to test candidates lazily
until a real PDF is saved.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import quote, urljoin, urlsplit, urldefrag

import requests
from bs4 import BeautifulSoup

from doi_hunter.netinfo import get_headers

log = logging.getLogger("doihunter.sources")

SCIHUB_MIRRORS = [
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.se",
]

ANNAS_BASES = [
    "https://annas-archive.pk",
    "https://annas-archive.gs",
    "https://annas-archive.org",
    "https://annas-archive.se",
]

_PDF_RX = re.compile(r"\.pdf(?:[/?#~]|$)", re.IGNORECASE)
_PDF_URLISH_RX = re.compile(r"""(?i)(?:(?:https?:)?//|/)[^'"<>\s]+?\.pdf(?:[#?~][^'"<>\s]*)?""")
_FIGSHARE_ARTICLE_ID_RX = re.compile(r"(?:^|[./_-])(\d{5,})(?:\.v\d+)?$", re.IGNORECASE)


@dataclass
class Resolved:
    url: str
    source: str   # human-readable label, e.g. "Sci-Hub (.ru)" or "Anna's Archive SciDB"


# ---------------- Helpers ----------------

def _get(url: str, **kw) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=get_headers(), timeout=20, **kw)
        return r if r.status_code == 200 else None
    except Exception as e:
        log.debug(f"GET {url} failed: {e}")
        return None


def _absolutize(href: str, base: str) -> str:
    return urljoin(base, href.strip())


def _configured_bases(env_name: str, defaults: list[str]) -> list[str]:
    raw = os.environ.get(env_name, "")
    configured = [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]
    return configured or defaults


def _is_pdf_response(r: requests.Response) -> bool:
    ctype = r.headers.get("Content-Type", "").lower()
    dispo = r.headers.get("Content-Disposition", "").lower()
    return "application/pdf" in ctype or ".pdf" in dispo


def _validated_pdf_url(url: str) -> Optional[str]:
    """Return the final URL if it appears to serve a PDF without downloading it."""
    url = urldefrag(url.strip()).url
    headers = {**get_headers(), "Accept": "application/pdf,*/*"}
    try:
        head = requests.head(url, headers=headers, timeout=15, allow_redirects=True)
        if head.status_code in (200, 206) and _is_pdf_response(head):
            return head.url
    except Exception as e:
        log.debug(f"HEAD {url} failed: {e}")

    try:
        probe_headers = {**headers, "Range": "bytes=0-7"}
        with requests.get(url, headers=probe_headers, timeout=20, stream=True, allow_redirects=True) as r:
            if r.status_code not in (200, 206):
                return None
            if _is_pdf_response(r):
                return r.url
            first = next(r.iter_content(8), b"")
            if first.startswith(b"%PDF"):
                return r.url
    except Exception as e:
        log.debug(f"PDF probe {url} failed: {e}")
    return None


def _add_candidate(candidates: list[tuple[int, str]], seen: set[str], score: int, href: str, base: str) -> None:
    if not href or href.startswith("#"):
        return
    url = _absolutize(href, base)
    if url and url not in seen:
        seen.add(url)
        candidates.append((score, url))


def _add_pdf_candidate_variants(
    candidates: list[tuple[int, str]],
    seen: set[str],
    score: int,
    href: str,
    page_url: str,
    same_host_base: str,
) -> None:
    _add_candidate(candidates, seen, score, href, page_url)
    url = _absolutize(href, page_url)
    path = urlsplit(url).path
    if "/storage/" in path or "/downloads/" in path:
        _add_candidate(candidates, seen, score - 1, path, same_host_base)


def _pdf_candidates_from_html(html: str, page_url: str, same_host_base: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()

    for tag in soup.find_all(["embed", "iframe", "object"]):
        for attr in ("src", "data"):
            href = (tag.get(attr) or "").strip()
            if href:
                _add_pdf_candidate_variants(candidates, seen, 100, href, page_url, same_host_base)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        url = _absolutize(href, page_url)
        text = a.get_text(" ", strip=True).lower()
        score = 0
        if _PDF_RX.search(url):
            score += 80
        if any(word in text for word in ("download", "pdf", "save")):
            score += 10
        if score:
            _add_pdf_candidate_variants(candidates, seen, score, href, page_url, same_host_base)

    for match in _PDF_URLISH_RX.finditer(html):
        _add_pdf_candidate_variants(candidates, seen, 70, match.group(0), page_url, same_host_base)

    return sorted(candidates, key=lambda t: -t[0])


# ---------------- Publisher / open-access landing pages ----------------

_PDF_META_NAMES = {
    "citation_pdf_url",
    "bepress_citation_pdf_url",
    "eprints.document_url",
}
_PDF_LINK_TEXT_RX = re.compile(r"\b(download\s+)?pdf\b", re.IGNORECASE)
_SUPPLEMENT_RX = re.compile(r"(supplement|supplementary|supporting|appendix|esm|mediaobjects)", re.IGNORECASE)


def _publisher_pdf_candidates(doi: str) -> list[Resolved]:
    page = _get(f"https://doi.org/{quote(doi.strip(), safe='/')}", allow_redirects=True)
    if not page:
        return []
    if _is_pdf_response(page):
        return [Resolved(url=page.url, source="Publisher PDF")]

    soup = BeautifulSoup(page.text, "html.parser")
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()

    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or meta.get("itemprop") or "").strip().lower()
        content = (meta.get("content") or "").strip()
        if key in _PDF_META_NAMES:
            _add_candidate(candidates, seen, 100, content, page.url)

    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        typ = (link.get("type") or "").lower()
        if "pdf" in typ or "pdf" in rel:
            _add_candidate(candidates, seen, 80, link["href"], page.url)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        url = _absolutize(href, page.url)
        text = a.get_text(" ", strip=True)
        score = 0
        if _PDF_RX.search(url):
            score += 40
        if _PDF_LINK_TEXT_RX.search(text) or _PDF_LINK_TEXT_RX.search(" ".join(a.get("class") or [])):
            score += 20
        if a.get("download") is not None or a.get("data-article-pdf") == "true":
            score += 20
        if _SUPPLEMENT_RX.search(url) or _SUPPLEMENT_RX.search(text):
            continue
        if score > 0:
            _add_candidate(candidates, seen, score, href, page.url)

    return [Resolved(url=url, source="Publisher PDF") for _, url in sorted(candidates, key=lambda t: -t[0])]


def _publisher_pdf(doi: str) -> Optional[Resolved]:
    return next(iter(_publisher_pdf_candidates(doi)), None)


# ---------------- Figshare / institutional Figshare repositories --------

def _figshare_article_id_from_doi(doi: str) -> str:
    match = _FIGSHARE_ARTICLE_ID_RX.search((doi or "").strip())
    return match.group(1) if match else ""


def _figshare_candidates(doi: str) -> list[Resolved]:
    article_id = _figshare_article_id_from_doi(doi)
    if not article_id:
        return []
    try:
        r = requests.get(
            f"https://api.figshare.com/v2/articles/{quote(article_id, safe='')}",
            headers=get_headers(),
            timeout=20,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as e:
        log.debug(f"Figshare API lookup failed for {doi}: {e}")
        return []

    resolved: list[Resolved] = []
    seen: set[str] = set()
    for f in data.get("files") or []:
        url = (f.get("download_url") or "").strip()
        name = (f.get("name") or "").lower()
        mimetype = (f.get("mimetype") or "").lower()
        if not url or url in seen:
            continue
        if "pdf" not in mimetype and not name.endswith(".pdf"):
            continue
        seen.add(url)
        resolved.append(Resolved(url=url, source="Figshare API PDF"))
    return resolved


def _figshare_pdf(doi: str) -> Optional[Resolved]:
    return next(iter(_figshare_candidates(doi)), None)


# ---------------- Sci-Hub ----------------

def _scihub_candidates(doi: str) -> list[Resolved]:
    doi = doi.strip()
    resolved: list[Resolved] = []
    seen_urls: set[str] = set()
    for base in _configured_bases("REVIEWHUNTER_SCIHUB_MIRRORS", _configured_bases("DOIHUNTER_SCIHUB_MIRRORS", SCIHUB_MIRRORS)):
        r = _get(f"{base}/{quote(doi, safe='/')}", allow_redirects=True)
        if not r:
            continue
        mirror = base.split("//", 1)[-1]
        if _is_pdf_response(r):
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                resolved.append(Resolved(url=r.url, source=f"Sci-Hub ({mirror})"))
            continue
        for _, url in _pdf_candidates_from_html(r.text, r.url, base):
            clean_url = urldefrag(url).url
            if clean_url and clean_url not in seen_urls:
                seen_urls.add(clean_url)
                resolved.append(Resolved(url=clean_url, source=f"Sci-Hub ({mirror})"))
    return resolved


def _scihub(doi: str) -> Optional[Resolved]:
    return next(iter(_scihub_candidates(doi)), None)


# ---------------- Anna's Archive SciDB ----------------

# The SciDB landing page links to several mirrors (LibGen, Sci-Hub-cached,
# direct PDFs). The structure changes; we score candidates and pick the
# strongest. We *also* tolerate the page returning a "not found" template.

_LIBGEN_RX = re.compile(r"(libgen|library\.lol|library\.bz|libgen\.rs|libgen\.li|libgen\.is|sci-hub)", re.IGNORECASE)
_SLOW_FAST_RX = re.compile(r"(slow_download|fast_download|/scimag|/get\.php\?)", re.IGNORECASE)


def _score_link(href: str, text: str) -> int:
    score = 0
    if _PDF_RX.search(href):
        score += 5
    if _LIBGEN_RX.search(href):
        score += 4
    if _SLOW_FAST_RX.search(href):
        score += 3
    if "download" in (text or "").lower():
        score += 1
    return score


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url


def _scidb_candidates(doi: str) -> list[Resolved]:
    doi = doi.strip()
    resolved: list[Resolved] = []
    seen_urls: set[str] = set()
    for base in _configured_bases("REVIEWHUNTER_SCIDB_BASES", _configured_bases("DOIHUNTER_SCIDB_BASES", ANNAS_BASES)):
        page = _get(f"{base}/scidb/{quote(doi, safe='/')}", allow_redirects=True)
        if not page:
            continue
        if urlsplit(page.url).path.startswith("/search"):
            continue
        soup = BeautifulSoup(page.text, "html.parser")

        # Quick negative/anti-bot filters.
        body_text = (soup.get_text(" ") or "").lower()
        if "not in our database" in body_text or "no scimag" in body_text:
            continue
        if "antibot solution" in body_text or (soup.title and soup.title.get_text(strip=True).lower() == "loading..."):
            continue

        candidates: list[tuple[int, str]] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#"):
                continue
            url = _absolutize(href, page.url)
            score = _score_link(url, a.get_text(strip=True))
            if score > 0:
                _add_candidate(candidates, seen, score, href, page.url)

        candidates.sort(key=lambda t: -t[0])

        for _, url in candidates:
            if _PDF_RX.search(url):
                clean_url = urldefrag(url).url
                if clean_url and clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    resolved.append(Resolved(url=clean_url, source="Anna's Archive (SciDB)"))

        # Otherwise follow the top-scoring links one hop and try again.
        for _, url in candidates[:5]:
            if _PDF_RX.search(url):
                continue
            r = _get(url, allow_redirects=True)
            if not r:
                continue
            if _is_pdf_response(r):
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    resolved.append(Resolved(url=r.url, source="Anna's Archive (SciDB)"))
                continue
            for _, sub_url in _pdf_candidates_from_html(r.text, r.url, _origin(r.url)):
                clean_url = urldefrag(sub_url).url
                if clean_url and clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    resolved.append(Resolved(url=clean_url, source="Anna's Archive (SciDB)"))
    return resolved


def _scidb(doi: str) -> Optional[Resolved]:
    return next(iter(_scidb_candidates(doi)), None)


# ---------------- Public API ----------------

DEFAULT_RESOLVERS: list[Callable[[str], Optional[Resolved]]] = [_figshare_pdf, _publisher_pdf, _scihub, _scidb]
DEFAULT_OPTION_RESOLVERS: list[Callable[[str], Iterable[Resolved]]] = [
    _figshare_candidates,
    _publisher_pdf_candidates,
    _scihub_candidates,
    _scidb_candidates,
]


def _as_resolved_list(result) -> list[Resolved]:
    if not result:
        return []
    if isinstance(result, Resolved):
        return [result]
    return [r for r in result if r]


def resolve_pdf_urls(
    doi: str,
    resolvers: Iterable[Callable[[str], Iterable[Resolved] | Optional[Resolved]]] | None = None,
) -> list[Resolved]:
    """Return unique candidate PDF URLs in preferred attempt order."""
    return list(iter_pdf_urls(doi, resolvers))


def iter_pdf_urls(
    doi: str,
    resolvers: Iterable[Callable[[str], Iterable[Resolved] | Optional[Resolved]]] | None = None,
) -> Iterable[Resolved]:
    """Yield unique candidate PDF URLs lazily in preferred attempt order."""
    seen_urls: set[str] = set()
    for fn in (resolvers or DEFAULT_OPTION_RESOLVERS):
        try:
            found = _as_resolved_list(fn(doi))
        except Exception as e:
            log.warning(f"resolver {fn.__name__} crashed for {doi}: {e}")
            continue
        for r in found:
            if r.url in seen_urls:
                continue
            seen_urls.add(r.url)
            log.info(f"{doi} -> candidate {r.source}")
            yield r


def resolve_pdf_url(doi: str, resolvers: Iterable[Callable[[str], Optional[Resolved]]] | None = None) -> Optional[Resolved]:
    """Try each resolver in order; return the first hit."""
    return next(iter(resolve_pdf_urls(doi, resolvers)), None)
