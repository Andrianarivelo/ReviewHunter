"""PDF source resolvers.

Resolution order: publisher/open-access PDF -> Sci-Hub mirrors -> Anna's
Archive SciDB. The library calls ``iter_pdf_urls`` to test candidates lazily
until a real PDF is saved.

Shadow libraries increasingly answer automated clients with a human check
(ALTCHA "are you a robot?", DDoS-Guard, JavaScript "antibot" pages). Those
pages are detected and reported through ``collect_notes`` so the user learns
*why* nothing was found; they are never solved automatically. The browser
extension handles that case with the user's own session instead.
"""
from __future__ import annotations

import contextvars
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

# sci-hub.fr is the live domain in Sept 2026; .ru and .box redirect to it, .st and .se no longer answer.
# Redirects to an already visited host are skipped, so listing aliases costs one cheap request each.
SCIHUB_MIRRORS = [
    "https://sci-hub.fr",
    "https://sci-hub.ru",
    "https://sci-hub.box",
    "https://sci-hub.st",
    "https://sci-hub.se",
]

# annas-archive.gs was removed: its "Antibot solution" page redirects to an unrelated advertising domain.
ANNAS_BASES = [
    "https://annas-archive.org",
    "https://annas-archive.li",
    "https://annas-archive.se",
    "https://annas-archive.pk",
]

# ---------------- Diagnostics (why a source gave nothing) ----------------

_NOTES: contextvars.ContextVar[Optional[list[str]]] = contextvars.ContextVar("rh_source_notes", default=None)

_CHALLENGE_RX = re.compile(
    r"(altcha|êtes-vous un robot|etes-vous un robot|are you a robot|ddos-guard|antibot solution|"
    r"cf-challenge|challenge-platform|just a moment\.\.\.|verify you are human)",
    re.IGNORECASE,
)


def _note(message: str) -> None:
    """Record a human-readable diagnostic for the current resolution (no-op outside collect_notes)."""
    notes = _NOTES.get()
    if notes is not None and message not in notes:
        notes.append(message)
    log.info(message)


def is_bot_challenge(html: str) -> bool:
    """True when an HTML page is a human-verification wall instead of real content.

    Checks the first 20 kB only: challenge markers live in the head / first screen, while a real
    article page is long and could mention "robot" anywhere in its references.
    """
    head = (html or "")[:20000]
    if re.search(r"<title[^>]*>\s*loading\.\.\.\s*</title>", head, re.IGNORECASE):
        return True
    return bool(_CHALLENGE_RX.search(head))


class collect_notes:
    """Context manager collecting diagnostics emitted by resolvers.

    >>> with collect_notes() as notes:
    ...     list(iter_pdf_urls(doi))
    >>> notes  # ["Sci-Hub (sci-hub.fr): human verification required", ...]
    """

    def __enter__(self) -> list[str]:
        self._notes: list[str] = []
        self._token = _NOTES.set(self._notes)
        return self._notes

    def __exit__(self, *exc) -> None:
        _NOTES.reset(self._token)


def shadow_library_pages(doi: str) -> list[dict]:
    """Landing pages of the configured Sci-Hub mirrors and SciDB bases for a DOI.

    Used by the browser extension, which opens / fetches them with the user's browser session.
    """
    doi = doi.strip()
    path = quote(doi, safe="/")
    pages = [{"source": f"Sci-Hub ({b.split('//', 1)[-1]})", "url": f"{b}/{path}"} for b in _scihub_bases()]
    pages += [{"source": f"Anna's Archive SciDB ({b.split('//', 1)[-1]})", "url": f"{b}/scidb/{path}"} for b in _scidb_bases()]
    return pages

_PDF_RX = re.compile(r"\.pdf(?:[/?#~]|$)", re.IGNORECASE)
_PDF_URLISH_RX = re.compile(r"""(?i)(?:(?:https?:)?//|/)[^'"<>\s]+?\.pdf(?:[#?~][^'"<>\s]*)?""")


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


def _fetch_page(url: str, label: str = "") -> Optional[requests.Response]:
    """GET that keeps non-200 responses (bot walls often answer 403).

    Returns None on network errors after recording why (``label`` prefixes the note). Certificate
    errors are never bypassed: an untrusted certificate usually means a web filter (FortiClient,
    corporate proxy) is intercepting the site, and verification must stay on.
    """
    try:
        return requests.get(url, headers=get_headers(), timeout=20, allow_redirects=True)
    except requests.exceptions.SSLError as e:
        _note(f"{label}: HTTPS certificate not trusted (a web filter or proxy is probably intercepting this site)")
        log.debug(f"GET {url} SSL error: {e}")
    except requests.exceptions.ConnectionError as e:
        text = str(e)
        if "NameResolution" in text or "getaddrinfo" in text:
            _note(f"{label}: domain does not resolve (blocked by DNS filtering, or the mirror is gone)")
        else:
            _note(f"{label}: not reachable (connection refused or reset)")
        log.debug(f"GET {url} failed: {e}")
    except requests.exceptions.Timeout:
        _note(f"{label}: no answer within 20 s")
    except Exception as e:
        _note(f"{label}: request failed ({type(e).__name__})")
        log.debug(f"GET {url} failed: {e}")
    return None


def _absolutize(href: str, base: str) -> str:
    return urljoin(base, href.strip())


def _configured_bases(env_name: str, defaults: list[str]) -> list[str]:
    raw = os.environ.get(env_name, "")
    configured = [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]
    return configured or defaults


def _scihub_bases() -> list[str]:
    return _configured_bases("REVIEWHUNTER_SCIHUB_MIRRORS", _configured_bases("DOIHUNTER_SCIHUB_MIRRORS", SCIHUB_MIRRORS))


def _scidb_bases() -> list[str]:
    return _configured_bases("REVIEWHUNTER_SCIDB_BASES", _configured_bases("DOIHUNTER_SCIDB_BASES", ANNAS_BASES))


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


# ---------------- Sci-Hub ----------------

def _scihub_candidates(doi: str) -> list[Resolved]:
    doi = doi.strip()
    resolved: list[Resolved] = []
    seen_urls: set[str] = set()
    visited_hosts: set[str] = set()
    for base in _scihub_bases():
        base_host = urlsplit(base).netloc.lower()
        if base_host in visited_hosts:
            continue
        r = _fetch_page(f"{base}/{quote(doi, safe='/')}", f"Sci-Hub ({base_host})")
        if r is None:
            continue
        final_host = urlsplit(r.url).netloc.lower()
        if final_host in visited_hosts:
            continue  # alias redirecting to a mirror already tried
        visited_hosts.update({base_host, final_host})
        mirror = final_host or base_host
        if not _is_pdf_response(r) and is_bot_challenge(r.text):
            _note(f"Sci-Hub ({mirror}): human verification required (open the page in Chrome and save with the extension)")
            continue
        if r.status_code != 200:
            _note(f"Sci-Hub ({mirror}): HTTP {r.status_code}")
            continue
        if _is_pdf_response(r):
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                resolved.append(Resolved(url=r.url, source=f"Sci-Hub ({mirror})"))
            continue
        found = _pdf_candidates_from_html(r.text, r.url, _origin(r.url))
        if not found:
            _note(f"Sci-Hub ({mirror}): article not available")
        for _, url in found:
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
    for base in _scidb_bases():
        host = urlsplit(base).netloc.lower()
        page = _fetch_page(f"{base}/scidb/{quote(doi, safe='/')}", f"SciDB ({host})")
        if page is None:
            continue
        if is_bot_challenge(page.text):
            _note(f"SciDB ({host}): human verification required")
            continue
        if page.status_code != 200:
            _note(f"SciDB ({host}): HTTP {page.status_code}")
            continue
        if urlsplit(page.url).path.startswith("/search"):
            _note(f"SciDB ({host}): article not available")
            continue
        soup = BeautifulSoup(page.text, "html.parser")

        body_text = (soup.get_text(" ") or "").lower()
        if "not in our database" in body_text or "no scimag" in body_text:
            _note(f"SciDB ({host}): article not available")
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


# ---------------- Legal open-access copies (OpenAlex + Europe PMC) ----------------

def _open_access_candidates(doi: str) -> list[Resolved]:
    """Open-access PDF locations indexed by OpenAlex and Europe PMC (no API key needed).

    OpenAlex aggregates Unpaywall-style OA data (publisher OA, green repositories,
    preprint servers). Europe PMC adds PubMed Central author manuscripts. Tried first
    because these copies are legal and rarely bot-protected.
    """
    doi = doi.strip().lower()
    out: list[Resolved] = []
    seen: set[str] = set()

    def add(url: Optional[str], label: str) -> None:
        if url and url not in seen:
            seen.add(url)
            out.append(Resolved(url=url, source=label))

    try:
        r = requests.get(f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}",
                         params={"select": "best_oa_location,locations"}, timeout=15,
                         headers={"User-Agent": "ReviewHunter/2.0"})
        if r.ok:
            data = r.json()
            best = data.get("best_oa_location") or {}
            add(best.get("pdf_url"), "Open access (OpenAlex)")
            for loc in data.get("locations") or []:
                if loc.get("is_oa"):
                    name = ((loc.get("source") or {}).get("display_name") or "repository")
                    add(loc.get("pdf_url"), f"Open access ({name})")
    except (requests.RequestException, ValueError) as e:
        log.debug(f"OpenAlex lookup failed for {doi}: {e}")
    try:
        r = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                         params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"}, timeout=15)
        if r.ok:
            for res in (r.json().get("resultList") or {}).get("result", [])[:1]:
                for ft in (res.get("fullTextUrlList") or {}).get("fullTextUrl", []):
                    if ft.get("documentStyle") == "pdf" and ft.get("availabilityCode") in ("OA", "F"):
                        add(ft.get("url"), "Open access (Europe PMC)")
    except (requests.RequestException, ValueError) as e:
        log.debug(f"Europe PMC lookup failed for {doi}: {e}")
    return out


# ---------------- Public API ----------------

DEFAULT_RESOLVERS: list[Callable[[str], Optional[Resolved]]] = [_publisher_pdf, _scihub, _scidb]
DEFAULT_OPTION_RESOLVERS: list[Callable[[str], Iterable[Resolved]]] = [
    _open_access_candidates,
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
