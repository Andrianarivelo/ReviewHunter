"""HTTP transport for PDF sources: look like a browser, trust what the machine trusts, explain failures.

Why this module exists (measured on 2026-09-15, see PROJECT_MEMORY.md section 7):

* Europe PMC, NCBI PMC, Cloudflare-fronted Sci-Hub proxies and their PDF hosts answer
  ``python-requests`` (and even ``curl.exe``) with a bot check, but serve a client whose TLS
  handshake looks like Chrome. ``curl_cffi`` provides that impersonation; it is optional and
  ``requests`` remains the fallback.
* Some PDF hosts only serve a file when the request carries the ``Referer`` of the article page
  (hot-link protection); every function here accepts a ``referer``.
* Python trusts only the ``certifi`` bundle, while Chrome trusts the operating-system store. On
  managed machines the OS store carries the corporate CA, so the bundle used here is
  certifi + OS store (setting ``use_system_certificates``). Verification is never disabled for
  content: an untrusted certificate is only *inspected* to tell the user who issued it.
* An optional proxy (setting ``network_proxy``, e.g. ``socks5h://127.0.0.1:9150`` for Tor Browser or
  ``http://proxy.example:3128``) can be applied to shadow-library requests only or to everything.
* Failures are explained precisely: a web-filter block page, a Cloudflare / DDoS-Guard check that a
  real browser passes on its own, a human check (ALTCHA, CAPTCHA), a dead domain (checked against
  DNS-over-HTTPS) versus a domain filtered by the local resolver, an expired or self-signed certificate.

Nothing here solves a CAPTCHA or a proof-of-work challenge: those pages are classified so that the
browser extension can take over with the user's own session.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import ssl
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urlsplit

import certifi
import requests

try:  # optional: Chrome TLS/HTTP2 impersonation
    from curl_cffi import requests as curl_requests
    from curl_cffi.requests.errors import RequestsError as CurlRequestsError

    HAVE_CURL = True
except Exception:  # pragma: no cover - depends on the environment
    curl_requests = None
    CurlRequestsError = ()  # type: ignore[assignment]
    HAVE_CURL = False

log = logging.getLogger("doihunter.transport")

# ============================================================================ PARAMETERS
IMPERSONATE = "chrome"  # curl_cffi target (latest Chrome fingerprint known to the installed version)
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")
DEFAULT_TIMEOUT_S = 20
CONNECT_TIMEOUT_S = 12
PEEK_TIMEOUT_S = 6
DOH_TIMEOUT_S = 6
CA_BUNDLE_MAX_AGE_S = 24 * 3600
DOH_RESOLVERS = [
    ("Cloudflare", "https://cloudflare-dns.com/dns-query"),
    ("Google", "https://dns.google/resolve"),
]
# ============================================================================


@dataclass
class Response:
    """Uniform response for both transports (headers keyed in lower case)."""

    url: str
    status_code: int
    headers: dict
    content: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    @property
    def text(self) -> str:
        charset = re.search(r"charset=([\w-]+)", self.header("content-type"))
        for enc in ((charset.group(1) if charset else None), "utf-8", "latin-1"):
            if not enc:
                continue
            try:
                return self.content.decode(enc, errors="replace")
            except LookupError:
                continue
        return self.content.decode("utf-8", errors="replace")

    def is_pdf(self) -> bool:
        return is_pdf_content(self.content, self.header("content-type"), self.header("content-disposition"))


class TransportError(Exception):
    """Network failure with a coarse ``kind``: tls | dns | connect | timeout | proxy | other."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass
class Challenge:
    """A page that is not the content asked for.

    kind: ``auto``        JavaScript check a real browser passes without the user (Cloudflare "Just a
                          moment", DDoS-Guard, NCBI "Preparing to download", invisible reCAPTCHA).
          ``interactive`` the user must act (ALTCHA "are you a robot", hCaptcha, Turnstile checkbox).
          ``blocked``     a web-filter block page (FortiGuard "Site bloque", "Web Page Blocked").
    """

    kind: str
    label: str


# ---------------------------------------------------------------------------- settings access

def _settings() -> dict:
    try:
        from . import config

        return config.get_settings()
    except Exception:  # config unavailable (benchmarks importing the module alone)
        return {}


def is_pdf_content(content: bytes, content_type: str = "", disposition: str = "") -> bool:
    """A response is a PDF when it says so (type / attachment name) or its bytes start with %PDF-."""
    if content[:5] == b"%PDF-":
        return True
    return "application/pdf" in (content_type or "").lower() or ".pdf" in (disposition or "").lower()


# ---------------------------------------------------------------------------- certificates

def _os_store_certificates() -> list[bytes]:
    """PEM certificates of the operating-system trust store (Windows: ROOT + CA; else common bundles)."""
    pems: list[bytes] = []
    if hasattr(ssl, "enum_certificates"):
        for store in ("ROOT", "CA"):
            try:
                for der, encoding, _trust in ssl.enum_certificates(store):
                    if encoding == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_cert(der).encode())
            except Exception as e:  # pragma: no cover - platform specific
                log.debug(f"cannot read the {store} certificate store: {e}")
        return pems
    for candidate in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                      "/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem"):
        p = Path(candidate)
        if p.exists():
            pems.append(p.read_bytes())
            break
    return pems


def ca_bundle() -> str:
    """Path of the CA bundle to verify servers with: certifi alone, or certifi + OS store when enabled.

    The merged bundle is cached under the library's ``certs`` folder and rebuilt once a day (or when
    the setting changes), so the OS store is not enumerated on every request.
    """
    if not _settings().get("use_system_certificates", True):
        return certifi.where()
    try:
        from .config import certs_dir

        target = certs_dir() / "ca-bundle-system.pem"
    except Exception:
        return certifi.where()
    try:
        if target.exists() and time.time() - target.stat().st_mtime < CA_BUNDLE_MAX_AGE_S:
            return str(target)
        extra = _os_store_certificates()
        data = Path(certifi.where()).read_bytes()
        if extra:
            data += b"\n" + b"\n".join(extra)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        log.info(f"CA bundle rebuilt with {len(extra)} certificate(s) from the system store")
        return str(target)
    except Exception as e:
        log.debug(f"system CA bundle unavailable, using certifi only: {e}")
        return certifi.where()


# ---------------------------------------------------------------------------- proxy

def proxy_for(shadow: bool) -> Optional[str]:
    """Proxy URL to use for a request, honouring ``proxy_scope`` (shadow | all) and REVIEWHUNTER_PROXY."""
    s = _settings()
    url = (os.environ.get("REVIEWHUNTER_PROXY") or s.get("network_proxy") or "").strip()
    if not url:
        return None
    scope = (os.environ.get("REVIEWHUNTER_PROXY_SCOPE") or s.get("proxy_scope") or "shadow").strip().lower()
    if scope == "all" or shadow:
        return url
    return None


# ---------------------------------------------------------------------------- requests

def browser_headers(referer: Optional[str] = None, accept: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    """Headers of a normal Chrome navigation (the impersonating transport adds its own fingerprint headers)."""
    h = {
        "User-Agent": USER_AGENT,
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        h["Referer"] = referer
        h["Sec-Fetch-Site"] = "cross-site"
    if extra:
        h.update(extra)
    return h


def _map_curl_error(e: Exception) -> TransportError:
    text = str(e)
    code = getattr(e, "code", None)
    if code in (6,) or "resolve host" in text.lower():
        return TransportError("dns", text)
    if code in (60, 51, 35, 58, 77) or "certificate" in text.lower() or "SSL" in text:
        return TransportError("tls", text)
    if code == 28 or "timed out" in text.lower() or "timeout" in text.lower():
        return TransportError("timeout", text)
    if code in (5, 97) or "proxy" in text.lower():
        return TransportError("proxy", text)
    if code == 7 or "connect" in text.lower():
        return TransportError("connect", text)
    return TransportError("other", text)


def _map_requests_error(e: Exception) -> TransportError:
    text = str(e)
    if isinstance(e, requests.exceptions.SSLError):
        return TransportError("tls", text)
    if isinstance(e, requests.exceptions.ProxyError):
        return TransportError("proxy", text)
    if isinstance(e, requests.exceptions.ConnectionError):
        if "NameResolution" in text or "getaddrinfo" in text or "Name or service not known" in text:
            return TransportError("dns", text)
        return TransportError("connect", text)
    if isinstance(e, requests.exceptions.Timeout):
        return TransportError("timeout", text)
    return TransportError("other", text)


def get(
    url: str,
    *,
    referer: Optional[str] = None,
    accept: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    allow_redirects: bool = True,
    impersonate: bool = True,
    verify: bool | str | None = None,
    shadow: bool = False,
    max_bytes: Optional[int] = None,
) -> Response:
    """GET with a browser fingerprint (curl_cffi when installed, requests otherwise).

    ``shadow`` marks shadow-library traffic for the proxy scope. ``max_bytes`` stops reading a body
    beyond that size (probe requests). Raises ``TransportError`` on network failure; HTTP errors
    are returned as responses (bot walls often answer 403 with a page worth classifying).
    """
    verify_arg: bool | str = ca_bundle() if verify is None else verify
    proxy = proxy_for(shadow)
    hdrs = browser_headers(referer, accept, headers)
    if HAVE_CURL and impersonate:
        try:
            kw = dict(headers=hdrs, timeout=timeout, allow_redirects=allow_redirects, verify=verify_arg,
                      impersonate=IMPERSONATE)
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            if max_bytes:
                with curl_requests.Session() as s:
                    r = s.get(url, stream=True, **kw)
                    chunks, size = [], 0
                    for chunk in r.iter_content():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= max_bytes:
                            break
                    r.close()
                    content = b"".join(chunks)[:max_bytes]
            else:
                r = curl_requests.get(url, **kw)
                content = r.content
            return Response(url=str(r.url), status_code=int(r.status_code),
                            headers={k.lower(): v for k, v in r.headers.items()}, content=content)
        except CurlRequestsError as e:
            raise _map_curl_error(e) from e
        except Exception as e:  # curl_cffi raises plain exceptions for a few conditions
            raise _map_curl_error(e) from e
    try:
        kw = dict(headers=hdrs, timeout=(CONNECT_TIMEOUT_S, timeout), allow_redirects=allow_redirects, verify=verify_arg)
        if proxy:
            kw["proxies"] = {"http": proxy, "https": proxy}
        if max_bytes:
            with requests.get(url, stream=True, **kw) as r:
                chunks, size = [], 0
                for chunk in r.iter_content(64 * 1024):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                content = b"".join(chunks)[:max_bytes]
                return Response(url=r.url, status_code=r.status_code,
                                headers={k.lower(): v for k, v in r.headers.items()}, content=content)
        r = requests.get(url, **kw)
        return Response(url=r.url, status_code=r.status_code,
                        headers={k.lower(): v for k, v in r.headers.items()}, content=r.content)
    except requests.RequestException as e:
        raise _map_requests_error(e) from e


def download(
    url: str,
    target: Path,
    *,
    referer: Optional[str] = None,
    timeout: float = 45,
    shadow: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
    max_bytes: int = 200 * 1024 * 1024,
) -> Response:
    """Stream a URL into ``target``; returns a Response whose ``content`` holds only the first 1 kB.

    The caller checks the magic bytes and size. Raises ``TransportError`` on network failure and
    ``ValueError`` when the body exceeds ``max_bytes``.
    """
    verify_arg = ca_bundle()
    proxy = proxy_for(shadow)
    hdrs = browser_headers(referer, "application/pdf,application/octet-stream,*/*;q=0.8")
    target.parent.mkdir(parents=True, exist_ok=True)

    def _write(iterator: Iterator[bytes], total: int) -> bytes:
        head = b""
        written = 0
        with open(target, "wb") as f:
            for chunk in iterator:
                if not chunk:
                    continue
                if len(head) < 1024:
                    head += chunk[: 1024 - len(head)]
                f.write(chunk)
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(f"file larger than {max_bytes // (1024 * 1024)} MB")
                if progress:
                    progress(written, total)
        return head

    if HAVE_CURL:
        try:
            kw = dict(headers=hdrs, timeout=timeout, allow_redirects=True, verify=verify_arg, impersonate=IMPERSONATE)
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            with curl_requests.Session() as s:
                r = s.get(url, stream=True, **kw)
                try:
                    total = int(r.headers.get("content-length") or 0)
                    head = _write(r.iter_content(), total)
                finally:
                    r.close()
                return Response(url=str(r.url), status_code=int(r.status_code),
                                headers={k.lower(): v for k, v in r.headers.items()}, content=head)
        except (ValueError, TransportError):
            raise
        except Exception as e:
            raise _map_curl_error(e) from e
    try:
        kw = dict(headers=hdrs, timeout=(CONNECT_TIMEOUT_S, timeout), allow_redirects=True, verify=verify_arg)
        if proxy:
            kw["proxies"] = {"http": proxy, "https": proxy}
        with requests.get(url, stream=True, **kw) as r:
            total = int(r.headers.get("Content-Length") or 0)
            head = _write(r.iter_content(64 * 1024), total)
            return Response(url=r.url, status_code=r.status_code,
                            headers={k.lower(): v for k, v in r.headers.items()}, content=head)
    except requests.RequestException as e:
        raise _map_requests_error(e) from e


# ---------------------------------------------------------------------------- page classification

_BLOCK_RX = re.compile(r"(site bloqu|web page blocked|fortiguard|fortinet|access to this site is blocked|"
                       r"this website is blocked|url blocked|blocked by your (?:organization|administrator))", re.I)
_INTERACTIVE_RX = re.compile(r"(altcha|êtes-vous un robot|etes-vous un robot|are you a robot|hcaptcha|"
                             r"verify you are human|confirm you are human|press and hold|slide to verify)", re.I)
_AUTO_PATTERNS = [
    (re.compile(r"(just a moment\.\.\.|cf-chl|challenge-platform|cf-challenge|_cf_chl_opt)", re.I), "Cloudflare browser check"),
    (re.compile(r"ddos-guard", re.I), "DDoS-Guard browser check"),
    (re.compile(r"preparing to download", re.I), "NCBI download page (JavaScript cookie)"),
    (re.compile(r"(recaptcha/challengepage|checking your browser - recaptcha)", re.I), "reCAPTCHA browser check"),
    (re.compile(r"(antibot solution|<title[^>]*>\s*loading\.\.\.\s*</title>)", re.I), "antibot loading page"),
    (re.compile(r"(checking your browser|checking if the site connection is secure)", re.I), "browser check"),
]


def classify_challenge(html: str, status: Optional[int] = None) -> Optional[Challenge]:
    """Classify an HTML page as a wall, or None when it looks like real content.

    Only the first 20 kB are inspected: markers live in the head / first screen, while a real
    article page is long and could mention "robot" or "blocked" anywhere in its references.
    """
    head = (html or "")[:20000]
    if not head.strip():
        return None
    title_match = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    if _BLOCK_RX.search(title) or (_BLOCK_RX.search(head) and len(html) < 120000 and "fortinet" in head.lower()):
        return Challenge("blocked", f"web filter block page{f' ({title})' if title else ''}")
    if _INTERACTIVE_RX.search(head):
        return Challenge("interactive", "human verification (ALTCHA / CAPTCHA)")
    for rx, label in _AUTO_PATTERNS:
        if rx.search(head):
            return Challenge("auto", label)
    return None


# ---------------------------------------------------------------------------- failure diagnosis

def peek_certificate(host: str, port: int = 443, timeout: float = PEEK_TIMEOUT_S) -> Optional[dict]:
    """Read the certificate a host presents WITHOUT trusting it (diagnostic only, no content is read)."""
    try:
        from cryptography import x509

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            der = s.getpeercert(True)
        if not der:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cert = x509.load_der_x509_certificate(der)
        subject = cert.subject.rfc4514_string()
        issuer = cert.issuer.rfc4514_string()
        not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        return {"subject": subject, "issuer": issuer, "not_after": not_after.date().isoformat(),
                "self_signed": subject == issuer, "expired": time.time() > not_after.timestamp()}
    except Exception as e:
        log.debug(f"certificate peek failed for {host}: {e}")
        return None


def _block_page_title(url: str) -> str:
    """Title of the page behind an untrusted certificate, used only to name a web-filter block page."""
    try:
        r = get(url, verify=False, timeout=8, allow_redirects=False, max_bytes=60000)
    except Exception:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", r.text[:20000], re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def describe_tls_failure(url: str) -> str:
    """Human-readable reason for a certificate failure on ``url``."""
    host = urlsplit(url).hostname or url
    cert = peek_certificate(host)
    if not cert:
        return "HTTPS handshake refused (the server or a network filter dropped the connection)"
    who = f"{cert['subject']} / {cert['issuer']}".lower()
    if "fortinet" in who or "forticlient" in who or "fortigate" in who:
        title = _block_page_title(url)
        page = f' ("{title}")' if title else ""
        return (f"blocked by the FortiClient web filter{page}: the site is replaced by a block page "
                "(organisation policy; use another network or ask IT)")
    if "ddos-guard" in who:
        return "the mirror presents DDoS-Guard's self-signed certificate (mirror misconfigured, not trusted)"
    if cert["expired"]:
        return f"the site's certificate expired on {cert['not_after']}"
    if cert["self_signed"]:
        return f"the site presents a self-signed certificate ({cert['subject'][:60]})"
    return f"certificate not trusted (issued by {cert['issuer'][:80]})"


def doh_resolves(host: str) -> Optional[bool]:
    """Does the domain exist according to public DNS-over-HTTPS? True/False, None when unknown."""
    for name, endpoint in DOH_RESOLVERS:
        try:
            r = requests.get(endpoint, params={"name": host, "type": "A"},
                             headers={"Accept": "application/dns-json", "User-Agent": USER_AGENT}, timeout=DOH_TIMEOUT_S)
            if not r.ok:
                continue
            data = r.json()
            if data.get("Status") == 3:  # NXDOMAIN
                return False
            answers = [a for a in data.get("Answer") or [] if a.get("type") in (1, 5, 28)]
            return bool(answers)
        except (requests.RequestException, ValueError) as e:
            log.debug(f"DoH {name} failed for {host}: {e}")
    return None


def describe_dns_failure(url: str) -> str:
    host = urlsplit(url).hostname or url
    exists = doh_resolves(host)
    if exists is False:
        return "domain no longer exists (no DNS record at public resolvers either)"
    if exists:
        return "blocked by your DNS resolver (the domain exists at public resolvers; Chrome's secure DNS may reach it)"
    return "domain does not resolve (could not check public resolvers)"


def _failing_host_url(err: TransportError, url: str) -> str:
    """The URL whose certificate failed: after a redirect, curl names the final host in its message."""
    m = re.search(r"target hostname '([^']+)'", err.detail) or re.search(r"host='([^']+)'", err.detail)
    if m and m.group(1) and m.group(1).lower() != (urlsplit(url).hostname or "").lower():
        return f"https://{m.group(1)}/"
    return url


def describe_failure(err: TransportError, url: str, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """One clear sentence for a TransportError on ``url``."""
    if err.kind == "tls":
        target = _failing_host_url(err, url)
        text = describe_tls_failure(target)
        if target != url:
            text = f"redirects to {urlsplit(target).hostname}, which is {text}"
        return text
    if err.kind == "dns":
        return describe_dns_failure(url)
    if err.kind == "timeout":
        return f"no answer within {int(timeout)} s"
    if err.kind == "proxy":
        return "the configured proxy refused the connection (check Settings > PDF sources > proxy)"
    if err.kind == "connect":
        return "not reachable (connection refused or reset)"
    return f"request failed ({err.detail[:80]})"


def transport_name() -> str:
    """Which HTTP client is active (shown in Settings)."""
    if HAVE_CURL:
        try:
            import curl_cffi

            return f"curl_cffi {curl_cffi.__version__} (Chrome impersonation)"
        except Exception:  # pragma: no cover
            return "curl_cffi (Chrome impersonation)"
    return "requests (no TLS impersonation: install curl_cffi for Cloudflare-protected sources)"
