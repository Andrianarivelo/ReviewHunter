"""Local-server hardening.

A web app listening on 127.0.0.1 is reachable by *any* website the user visits
(the browser happily sends ``fetch('http://127.0.0.1:8765/api/...')``). Without a
guard, a malicious page could add, trash or export library items (CSRF), or read
data through DNS rebinding. This middleware enforces:

1. Host header must be a loopback name (defeats DNS rebinding).
2. State-changing requests (and WebSocket upgrades) must come from a trusted Origin:
   the app itself (http 8765 / https 8766), a browser extension
   (chrome-extension://, moz-extension://), or no Origin at all (curl, Python, Office
   runtime for same-origin navigations).
3. Cross-site ``Sec-Fetch-Site`` navigations that try to mutate state are rejected.

Browser extensions bypass CORS via host_permissions, so no CORS headers are emitted:
ordinary websites therefore cannot read any API response either.
"""
from __future__ import annotations

import re
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
EXTENSION_ORIGIN = re.compile(r"^(chrome-extension|moz-extension|safari-web-extension)://[a-z0-9\-]+$", re.I)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _host_only(host_header: str) -> str:
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


class LocalOriginGuard:
    """Pure ASGI middleware (works for HTTP and WebSocket scopes)."""

    def __init__(self, app: ASGIApp, ports: Iterable[int]) -> None:
        self.app = app
        self.ports = set(ports)

    def _trusted_origin(self, origin: str) -> bool:
        if not origin:
            return True
        if EXTENSION_ORIGIN.match(origin):
            return True
        m = re.match(r"^(https?)://(127\.0\.0\.1|localhost|\[::1\])(?::(\d+))?$", origin, re.I)
        if not m:
            return False
        port = int(m.group(3)) if m.group(3) else (443 if m.group(1).lower() == "https" else 80)
        return port in self.ports

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = _host_only(headers.get("host", ""))
        if host and host not in LOOPBACK_HOSTS:
            return await self._reject(scope, receive, send, "invalid host")
        origin = headers.get("origin", "")
        method = scope.get("method", "GET").upper()
        mutating = scope["type"] == "websocket" or method not in SAFE_METHODS
        if mutating and not self._trusted_origin(origin):
            return await self._reject(scope, receive, send, "untrusted origin")
        if mutating and not origin and headers.get("sec-fetch-site") in ("cross-site",):
            return await self._reject(scope, receive, send, "cross-site request")
        return await self.app(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send, reason: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        response = JSONResponse({"error": "forbidden", "reason": reason}, status_code=403)
        await response(scope, receive, send)


class NoStoreApi(BaseHTTPMiddleware):
    """API responses carry library data: never cache them on disk."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/", "/connector/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
