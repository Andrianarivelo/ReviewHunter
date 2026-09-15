"""PDF source resolvers (v3).

Resolution order (``DEFAULT_OPTION_RESOLVERS``):

1. legal open-access copies: OpenAlex, Unpaywall (when an e-mail is configured), Semantic Scholar,
   Europe PMC / NCBI PMC, bioRxiv / medRxiv, arXiv;
2. the publisher's own page (doi.org redirect, ``citation_pdf_url`` and PDF links);
3. Sci-Hub mirrors, including Cloudflare-fronted proxies whose PDF host needs the article page as
   ``Referer``;
4. Anna's Archive SciDB.

Every request goes through :mod:`transport` (Chrome TLS impersonation, OS trust store, optional
proxy). A resolver yields :class:`Resolved` candidates; ``needs_browser`` marks copies the server
cannot fetch (JavaScript checks, institutional access) but the Chrome extension can, with the user's
session. Walls, dead mirrors and block pages are reported through :func:`collect_notes` so the user
learns *why* nothing was found, and recorded as browser steps through :func:`collect_browser_steps`
so the extension knows *where* to try next. No CAPTCHA or proof-of-work is ever solved here.
"""
from __future__ import annotations

import contextvars
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import quote, urljoin, urlsplit, urldefrag

import requests
from bs4 import BeautifulSoup

from . import transport
from .transport import Challenge, TransportError, classify_challenge

log = logging.getLogger("doihunter.sources")

# ============================================================================ PARAMETERS
# Sci-Hub: the official domain (sci-hub.fr; .ru and .box redirect to it) shows an ALTCHA human check to
# scripts. sci-hub.ren is a Cloudflare-fronted proxy that serves article pages to a browser-like client;
# its PDF host (sci.bban.top) requires the article page as Referer. .st presents DDoS-Guard's self-signed
# certificate; .se no longer exists (removed). Order = best chance first for a script.
SCIHUB_MIRRORS = [
    "https://sci-hub.ren",
    "https://sci-hub.fr",
    "https://sci-hub.ru",
    "https://sci-hub.box",
    "https://sci-hub.wf",
    "https://sci-hub.st",
]
# Anna's Archive: .org and .se no longer resolve anywhere (removed); .gs was a hijacked domain (removed).
ANNAS_BASES = [
    "https://annas-archive.li",
    "https://annas-archive.gl",
    "https://annas-archive.pk",
]
PAGE_TIMEOUT_S = 20        # landing pages
API_TIMEOUT_S = 15         # JSON APIs (OpenAlex, Unpaywall, Semantic Scholar, Europe PMC, bioRxiv)
MAX_BROWSER_STEPS = 10     # steps handed to the extension per item
OA_LABEL = "Open access"
# ============================================================================


@dataclass
class Resolved:
    """One candidate copy of the paper.

    url:           direct PDF URL (kind "pdf") or an article page to parse (kind "page").
    source:        human-readable label ("Open access (Europe PMC)", "Sci-Hub (sci-hub.ren)").
    referer:       Referer the PDF host requires (hot-link protection), "" when none.
    needs_browser: the server cannot fetch it (JavaScript cookie, institutional access); the
                   extension tries it with the user's browser session.
    note:          why the server stepped aside (shown in the extension progress).
    """

    url: str
    source: str
    referer: str = ""
    kind: str = "pdf"
    needs_browser: bool = False
    note: str = ""


# ---------------- Diagnostics (why a source gave nothing, and what the browser should try) ----------------

_NOTES: contextvars.ContextVar[Optional[list[str]]] = contextvars.ContextVar("rh_source_notes", default=None)
_STEPS: contextvars.ContextVar[Optional[list[dict]]] = contextvars.ContextVar("rh_browser_steps", default=None)


def _note(message: str) -> None:
    """Record a human-readable diagnostic for the current resolution (no-op outside collect_notes)."""
    notes = _NOTES.get()
    if notes is not None and message not in notes:
        notes.append(message)
    log.info(message)


def browser_step(url: str, source: str, *, kind: str = "page", referer: str = "", wall: str = "", note: str = "") -> None:
    """Record a page / PDF the browser extension should try with the user's session."""
    steps = _STEPS.get()
    if steps is None:
        return
    if any(s["url"] == url for s in steps) or len(steps) >= MAX_BROWSER_STEPS:
        return
    steps.append({"url": url, "source": source, "kind": kind, "referer": referer, "wall": wall, "note": note})


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


class collect_browser_steps:
    """Context manager collecting the steps the extension should run (dicts, see browser_step)."""

    def __enter__(self) -> list[dict]:
        self._steps: list[dict] = []
        self._token = _STEPS.set(self._steps)
        return self._steps

    def __exit__(self, *exc) -> None:
        _STEPS.reset(self._token)


def is_bot_challenge(html: str) -> bool:
    """True when an HTML page is a verification wall / block page instead of real content."""
    return classify_challenge(html) is not None


def shadow_library_pages(doi: str) -> list[dict]:
    """Landing pages of the configured Sci-Hub mirrors and SciDB bases for a DOI.

    Used by the browser extension, which opens / fetches them with the user's browser session.
    """
    doi = doi.strip()
    path = quote(doi, safe="/")
    pages = [{"source": f"Sci-Hub ({b.split('//', 1)[-1]})", "url": f"{b}/{path}", "kind": "page"} for b in _scihub_bases()]
    pages += [{"source": f"Anna's Archive SciDB ({b.split('//', 1)[-1]})", "url": f"{b}/scidb/{path}", "kind": "page"}
              for b in _scidb_bases()]
    return pages


_PDF_RX = re.compile(r"\.pdf(?:[/?#~]|$)", re.IGNORECASE)
_PDF_URLISH_RX = re.compile(r"""(?i)(?:(?:https?:)?//|/)[^'"<>\s]+?\.pdf(?:[#?~][^'"<>\s]*)?""")


# ---------------- Helpers ----------------

def _settings() -> dict:
    try:
        from . import config

        return config.get_settings()
    except Exception:
        return {}


def _fetch_page(url: str, label: str = "", *, shadow: bool = False, referer: str = "") -> Optional[transport.Response]:
    """GET that keeps non-200 responses (bot walls often answer 403).

    Returns None on network errors after recording why (``label`` prefixes the note). Certificate
    errors are never bypassed: the certificate is only inspected to name the culprit (web filter,
    self-signed mirror, expired certificate).
    """
    try:
        return transport.get(url, timeout=PAGE_TIMEOUT_S, shadow=shadow, referer=referer or None)
    except TransportError as e:
        _note(f"{label}: {transport.describe_failure(e, url, PAGE_TIMEOUT_S)}")
        log.debug(f"GET {url} failed: {e.detail}")
    except Exception as e:  # pragma: no cover - defensive
        _note(f"{label}: request failed ({type(e).__name__})")
        log.debug(f"GET {url} failed: {e}")
    return None


def _report_wall(label: str, page: transport.Response, challenge: Challenge, *, source_url: str) -> None:
    """Explain a wall and, when a browser can pass it, hand the page to the extension."""
    if challenge.kind == "blocked":
        _note(f"{label}: {challenge.label} (a web filter on this computer or network blocks the site)")
        return
    if challenge.kind == "interactive":
        _note(f"{label}: human verification required (the page opens in Chrome; save with the extension once solved)")
    else:
        _note(f"{label}: {challenge.label}, which only a real browser passes (handed to the Chrome extension)")
    browser_step(source_url, label, kind="page", wall=challenge.kind, note=challenge.label)


def _absolutize(href: str, base: str) -> str:
    return urljoin(base, href.strip())


def _configured_bases(env_name: str, setting_key: str, defaults: list[str]) -> list[str]:
    """Mirror list: environment variable, then Settings (list or comma string), then the built-in list."""
    raw = os.environ.get(env_name, "")
    configured = [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]
    if configured:
        return configured
    value = _settings().get(setting_key)
    if isinstance(value, str):
        value = [p for p in value.split(",")]
    configured = [str(p).strip().rstrip("/") for p in (value or []) if str(p).strip()]
    return [c if "://" in c else f"https://{c}" for c in configured] or defaults


def _scihub_bases() -> list[str]:
    env = os.environ.get("REVIEWHUNTER_SCIHUB_MIRRORS") or os.environ.get("DOIHUNTER_SCIHUB_MIRRORS") or ""
    if env.strip():
        return [p.strip().rstrip("/") for p in env.split(",") if p.strip()]
    return _configured_bases("REVIEWHUNTER_SCIHUB_MIRRORS", "scihub_mirrors", SCIHUB_MIRRORS)


def _scidb_bases() -> list[str]:
    env = os.environ.get("REVIEWHUNTER_SCIDB_BASES") or os.environ.get("DOIHUNTER_SCIDB_BASES") or ""
    if env.strip():
        return [p.strip().rstrip("/") for p in env.split(",") if p.strip()]
    return _configured_bases("REVIEWHUNTER_SCIDB_BASES", "scidb_bases", ANNAS_BASES)


def _validated_pdf_url(url: str, referer: str = "") -> Optional[str]:
    """Return the final URL if it serves a PDF (reads at most the first 8 bytes)."""
    url = urldefrag(url.strip()).url
    try:
        r = transport.get(url, referer=referer or None, accept="application/pdf,*/*", timeout=PAGE_TIMEOUT_S,
                          max_bytes=8, headers={"Range": "bytes=0-7"})
    except TransportError as e:
        log.debug(f"PDF probe {url} failed: {e.detail}")
        return None
    if r.status_code in (200, 206) and r.is_pdf():
        return r.url
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
    """PDF URLs in a shadow-library article page: viewer objects first, then links, then raw URLs."""
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
        raw = match.group(0)
        if "\\" in raw:
            # JavaScript-escaped URL ("\/sci.bban.top\/pdf\/..."): unescape it instead of absolutizing garbage.
            raw = raw.replace("\\/", "/")
            if not raw.startswith(("http", "//")):
                continue
        _add_pdf_candidate_variants(candidates, seen, 70, raw, page_url, same_host_base)

    return sorted(candidates, key=lambda t: -t[0])


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url


# ---------------- Legal open-access copies ----------------

def _api_get(url: str, params: dict, label: str, timeout: float = API_TIMEOUT_S) -> Optional[dict]:
    """JSON GET against a scholarly API (plain requests: these APIs welcome scripts)."""
    email = (_settings().get("unpaywall_email") or "").strip()
    ua = f"ReviewHunter/2.2 (mailto:{email})" if email else "ReviewHunter/2.2"
    try:
        r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": ua, "Accept": "application/json"})
        if r.status_code == 404:
            return None
        if not r.ok:
            log.debug(f"{label}: HTTP {r.status_code}")
            return None
        return r.json()
    except (requests.RequestException, ValueError) as e:
        log.debug(f"{label} lookup failed: {e}")
        return None


def _pmc_candidates(pmcid: str, seen: set[str], out: list[Resolved]) -> None:
    """Europe PMC render (works with Chrome impersonation) and NCBI PMC (JavaScript cookie: browser)."""
    pmcid = pmcid.upper()
    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    epmc = f"https://europepmc.org/articles/{pmcid}?pdf=render"
    if epmc not in seen:
        seen.add(epmc)
        out.append(Resolved(url=epmc, source=f"{OA_LABEL} (Europe PMC)"))
    ncbi = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/"
    if ncbi not in seen:
        seen.add(ncbi)
        out.append(Resolved(url=ncbi, source=f"{OA_LABEL} (PubMed Central)", needs_browser=True,
                            note="PubMed Central serves PDFs only to a browser (JavaScript cookie)"))


def _open_access_candidates(doi: str) -> list[Resolved]:
    """Open-access PDF locations from OpenAlex, Unpaywall, Semantic Scholar, Europe PMC, bioRxiv, arXiv.

    Tried first because these copies are legal. Direct repository links are fetched by the server;
    PubMed Central is marked ``needs_browser``. No API key is needed (Unpaywall wants an e-mail).
    """
    doi = doi.strip().lower()
    out: list[Resolved] = []
    seen: set[str] = set()
    pmcids: list[str] = []

    def add(url: Optional[str], label: str, **kw) -> None:
        url = (url or "").strip()
        key = url.lower()  # indexes disagree on case (pmc5688002 vs PMC5688002): one request is enough
        if url and key not in seen and url.startswith("http"):
            seen.add(key)
            out.append(Resolved(url=url, source=label, **kw))

    # 1. OpenAlex (Unpaywall-derived OA locations, free, no key)
    email = (_settings().get("unpaywall_email") or "").strip()
    params = {"select": "best_oa_location,locations,ids"}
    if email:
        params["mailto"] = email
    data = _api_get(f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}", params, "OpenAlex")
    if data:
        best = data.get("best_oa_location") or {}
        add(best.get("pdf_url"), f"{OA_LABEL} (OpenAlex)")
        for loc in data.get("locations") or []:
            if loc.get("is_oa"):
                name = ((loc.get("source") or {}).get("display_name") or "repository")
                add(loc.get("pdf_url"), f"{OA_LABEL} ({name})")
        pmcid = ((data.get("ids") or {}).get("pmcid") or "").rsplit("/", 1)[-1]
        if pmcid:
            pmcids.append(pmcid)

    # 2. Unpaywall (only with the user's e-mail, as its terms require)
    if email:
        data = _api_get(f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}", {"email": email}, "Unpaywall")
        if data:
            best = data.get("best_oa_location") or {}
            add(best.get("url_for_pdf"), f"{OA_LABEL} (Unpaywall)")
            for loc in data.get("oa_locations") or []:
                add(loc.get("url_for_pdf"), f"{OA_LABEL} ({loc.get('repository_institution') or loc.get('host_type') or 'Unpaywall'})")

    # 3. Semantic Scholar (openAccessPdf + arXiv / PMC identifiers)
    data = _api_get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='/')}",
                    {"fields": "openAccessPdf,externalIds"}, "Semantic Scholar")
    if data:
        add(((data.get("openAccessPdf") or {}).get("url")), f"{OA_LABEL} (Semantic Scholar)")
        ext = data.get("externalIds") or {}
        if ext.get("ArXiv"):
            add(f"https://arxiv.org/pdf/{ext['ArXiv']}", f"{OA_LABEL} (arXiv)")
        if ext.get("PubMedCentral"):
            pmcids.append(str(ext["PubMedCentral"]))

    # 4. Europe PMC (author manuscripts + PMC ids)
    data = _api_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"}, "Europe PMC")
    if data:
        for res in (data.get("resultList") or {}).get("result", [])[:1]:
            if res.get("pmcid"):
                pmcids.append(res["pmcid"])
            for ft in (res.get("fullTextUrlList") or {}).get("fullTextUrl", []):
                if ft.get("documentStyle") == "pdf" and ft.get("availabilityCode") in ("OA", "F"):
                    add(ft.get("url"), f"{OA_LABEL} (Europe PMC)")

    # 5. bioRxiv / medRxiv preprints
    if doi.startswith("10.1101/"):
        for server in ("biorxiv", "medrxiv"):
            data = _api_get(f"https://api.biorxiv.org/details/{server}/{doi}", {}, server)
            versions = (data or {}).get("collection") or []
            if versions:
                latest = versions[-1]
                add(f"https://www.{server}.org/content/{doi}v{latest.get('version', 1)}.full.pdf", f"{OA_LABEL} ({server})")
                break

    for pmcid in dict.fromkeys(pmcids):
        _pmc_candidates(pmcid, seen, out)
    return out


# ---------------- Publisher landing page ----------------

_PDF_META_NAMES = {"citation_pdf_url", "bepress_citation_pdf_url", "eprints.document_url"}
_PDF_LINK_TEXT_RX = re.compile(r"\b(download\s+)?pdf\b", re.IGNORECASE)
_SUPPLEMENT_RX = re.compile(r"(supplement|supplementary|supporting|appendix|esm|mediaobjects)", re.IGNORECASE)
_PII_RX = re.compile(r"/pii/([A-Z0-9]{16,20})", re.IGNORECASE)


def _publisher_pdf_candidates(doi: str) -> list[Resolved]:
    """PDF links on the publisher page reached through doi.org.

    The page is fetched with Chrome impersonation, which is enough for many publishers' landing pages.
    The PDF itself usually needs institutional access: candidates that fail on the server are handed to
    the browser (the user's session) by the library. An optional EZproxy prefix (Settings) adds a
    browser-only variant of each PDF link.
    """
    label = "Publisher PDF"
    doi_url = f"https://doi.org/{quote(doi.strip(), safe='/')}"
    page = _fetch_page(doi_url, "Publisher")
    if page is None:
        return []
    if page.is_pdf():
        return [Resolved(url=page.url, source=label)]
    challenge = classify_challenge(page.text, page.status_code)
    if challenge:
        _report_wall("Publisher", page, challenge, source_url=page.url)
        return []
    if page.status_code != 200:
        _note(f"Publisher: HTTP {page.status_code} on {urlsplit(page.url).netloc}")
        browser_step(page.url, "Publisher page", kind="page", note=f"HTTP {page.status_code} for the server")
        return []

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

    # Elsevier: doi.org lands on linkinghub, which redirects with JavaScript to ScienceDirect.
    host = urlsplit(page.url).netloc.lower()
    pii = _PII_RX.search(page.url) or _PII_RX.search(page.text[:200000])
    if "elsevier" in host and pii:
        _add_candidate(candidates, seen, 60,
                       f"https://www.sciencedirect.com/science/article/pii/{pii.group(1).upper()}/pdfft?isDTMRedir=true&download=true",
                       page.url)

    out = [Resolved(url=url, source=label, referer=page.url) for _, url in sorted(candidates, key=lambda t: -t[0])]
    prefix = (_settings().get("ezproxy_prefix") or "").strip()
    if prefix:
        for r in list(out)[:2]:
            out.append(Resolved(url=f"{prefix}{r.url}", source="Publisher PDF (via EZproxy)", needs_browser=True,
                                note="institutional proxy: sign in once in Chrome"))
    if not out:
        _note(f"Publisher: no PDF link on {host} for the server")
        browser_step(page.url, "Publisher page", kind="page", note="no PDF link for the server (institutional access may apply)")
    return out


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
        page_url = f"{base}/{quote(doi, safe='/')}"
        label = f"Sci-Hub ({base_host})"
        r = _fetch_page(page_url, label, shadow=True)
        if r is None:
            continue
        final_host = urlsplit(r.url).netloc.lower()
        if final_host != base_host and final_host in visited_hosts:
            continue  # alias redirecting to a mirror already tried
        visited_hosts.update({base_host, final_host})
        mirror = final_host or base_host
        label = f"Sci-Hub ({mirror})"
        if r.is_pdf():
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                resolved.append(Resolved(url=r.url, source=label))
            continue
        challenge = classify_challenge(r.text, r.status_code)
        if challenge:
            _report_wall(label, r, challenge, source_url=r.url)
            continue
        if r.status_code != 200:
            _note(f"{label}: HTTP {r.status_code}")
            continue
        found = _pdf_candidates_from_html(r.text, r.url, _origin(r.url))
        if not found:
            _note(f"{label}: article not available")
        for _, url in found:
            clean_url = urldefrag(url).url
            if clean_url and clean_url not in seen_urls:
                seen_urls.add(clean_url)
                resolved.append(Resolved(url=clean_url, source=label, referer=r.url))
    return resolved


def _scihub(doi: str) -> Optional[Resolved]:
    return next(iter(_scihub_candidates(doi)), None)


# ---------------- Anna's Archive SciDB ----------------

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


def _scidb_candidates(doi: str) -> list[Resolved]:
    doi = doi.strip()
    resolved: list[Resolved] = []
    seen_urls: set[str] = set()
    for base in _scidb_bases():
        host = urlsplit(base).netloc.lower()
        label = f"SciDB ({host})"
        page_url = f"{base}/scidb/{quote(doi, safe='/')}"
        page = _fetch_page(page_url, label, shadow=True)
        if page is None:
            continue
        challenge = classify_challenge(page.text, page.status_code)
        if challenge:
            _report_wall(label, page, challenge, source_url=page.url)
            continue
        if page.status_code != 200:
            _note(f"{label}: HTTP {page.status_code}")
            continue
        if urlsplit(page.url).path.startswith("/search"):
            _note(f"{label}: article not available")
            continue
        soup = BeautifulSoup(page.text, "html.parser")
        body_text = (soup.get_text(" ") or "").lower()
        if "not in our database" in body_text or "no scimag" in body_text:
            _note(f"{label}: article not available")
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
        for tag in soup.find_all(["embed", "iframe", "object"]):
            href = (tag.get("src") or tag.get("data") or "").strip()
            if href:
                _add_candidate(candidates, seen, 9, href, page.url)
        candidates.sort(key=lambda t: -t[0])
        found_before = len(resolved)

        for _, url in candidates:
            if _PDF_RX.search(url):
                clean_url = urldefrag(url).url
                if clean_url and clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    resolved.append(Resolved(url=clean_url, source="Anna's Archive (SciDB)", referer=page.url))

        # Otherwise follow the top-scoring links one hop and try again.
        for _, url in candidates[:5]:
            if _PDF_RX.search(url):
                continue
            try:
                r = transport.get(url, timeout=PAGE_TIMEOUT_S, shadow=True, referer=page.url)
            except TransportError:
                continue
            if r.status_code != 200:
                continue
            if r.is_pdf():
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    resolved.append(Resolved(url=r.url, source="Anna's Archive (SciDB)", referer=page.url))
                continue
            for _, sub_url in _pdf_candidates_from_html(r.text, r.url, _origin(r.url)):
                clean_url = urldefrag(sub_url).url
                if clean_url and clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    resolved.append(Resolved(url=clean_url, source="Anna's Archive (SciDB)", referer=r.url))
        if len(resolved) == found_before:
            _note(f"{label}: page reached, but no PDF link found (article missing or page layout changed)")
    return resolved


def _scidb(doi: str) -> Optional[Resolved]:
    return next(iter(_scidb_candidates(doi)), None)


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
            log.info(f"{doi} -> candidate {r.source}{' (browser)' if r.needs_browser else ''}")
            yield r


def resolve_pdf_url(doi: str, resolvers: Iterable[Callable[[str], Optional[Resolved]]] | None = None) -> Optional[Resolved]:
    """Try each resolver in order; return the first hit."""
    return next(iter(resolve_pdf_urls(doi, resolvers)), None)


def browser_plan(doi: str, steps: list[dict], skipped: list[Resolved], failed: list[tuple[Resolved, str]]) -> list[dict]:
    """Ordered steps for the Chrome extension after the server gave up on a DOI.

    1. copies the server could not fetch by design (``needs_browser``: PubMed Central, EZproxy);
    2. candidates that failed on the server for reasons a browser session may fix (HTTP 403 / login
       page / not a PDF) - never those on a dead domain or behind a web-filter block;
    3. walls met on landing pages (Cloudflare / DDoS-Guard checks clear on their own in a tab, human
       checks are opened for the user);
    4. the remaining shadow-library landing pages not yet tried.
    """
    plan: list[dict] = []
    seen: set[str] = set()

    def add(url: str, source: str, *, kind: str = "pdf", referer: str = "", wall: str = "", note: str = "") -> None:
        url = urldefrag(url or "").url
        if not url or url in seen or len(plan) >= MAX_BROWSER_STEPS:
            return
        seen.add(url)
        plan.append({"url": url, "source": source, "kind": kind, "referer": referer, "wall": wall, "note": note})

    for r in skipped:
        add(r.url, r.source, kind=r.kind, referer=r.referer, note=r.note)
    retryable = re.compile(r"(HTTP 40[13]|HTTP 429|not a PDF|login|browser check|browser passes|JavaScript|human verification)", re.I)
    for r, reason in failed:
        if retryable.search(reason or ""):
            add(r.url, r.source, kind=r.kind, referer=r.referer, note=reason[:160])
    blocked_hosts = {urlsplit(s["url"]).netloc.lower() for s in steps if s.get("wall") == "blocked"}
    for s in steps:
        if s.get("wall") == "blocked":
            continue
        add(s["url"], s["source"], kind=s.get("kind", "page"), referer=s.get("referer", ""), wall=s.get("wall", ""), note=s.get("note", ""))
    for p in shadow_library_pages(doi):
        if urlsplit(p["url"]).netloc.lower() in blocked_hosts:
            continue  # the web filter blocks it in Chrome too
        add(p["url"], p["source"], kind="page")
    return plan
