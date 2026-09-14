"""Launch ReviewHunter as a local web app.

Usage:
    python -m doi_hunter_web                  # default port 8765
    python -m doi_hunter_web --port 8080
    python -m doi_hunter_web --no-browser     # don't auto-open the browser

Env overrides:
    REVIEWHUNTER_HOME    library directory (default: ~/ReviewHunter)
    REVIEWHUNTER_HOST    bind address       (default: 127.0.0.1)
    REVIEWHUNTER_PORT    port               (default: 8765)
    REVIEWHUNTER_MAILTO  email for the CrossRef polite pool (optional, faster)
"""
from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
import webbrowser

from . import config


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _reviewhunter_already_running(host: str, port: int) -> bool:
    """The browser extension and Word add-in rely on fixed ports, so never silently move."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as r:
            return json.loads(r.read().decode()).get("ok") is True
    except Exception:
        return False


def _open_when_ready(url: str, host: str, port: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            try:
                s.connect((host, port))
                webbrowser.open(url)
                return
            except OSError:
                time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser(prog="review-hunter-web", description="ReviewHunter local library app")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-https", action="store_true", help="do not start the Word add-in HTTPS listener")
    parser.add_argument("--reload", action="store_true", help="dev: auto-reload on code change")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "Missing dependencies. Install with:\n"
            "    pip install fastapi uvicorn[standard]"
        )

    # A restore staged from the Backups panel is applied before any DB connection opens.
    from .gdrive import apply_pending_restore

    restored = apply_pending_restore()
    if restored:
        print(f"  Restored library database from backup -> {restored}")

    port = args.port
    url = f"http://{args.host}:{port}"
    if not _port_free(args.host, port):
        if _reviewhunter_already_running(args.host, port):
            print(f"\n  ReviewHunter is already running at {url} - opening it.")
            if not args.no_browser:
                webbrowser.open(url)
            return
        raise SystemExit(f"Port {port} is used by another program. Close it or set REVIEWHUNTER_PORT.")

    print(f"\n  ReviewHunter - {url}")
    print(f"      library: {config.library_root()}")

    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url, args.host, port), daemon=True).start()

    servers = [uvicorn.Server(uvicorn.Config("doi_hunter_web.server:app", host=args.host, port=port,
                                             reload=False, log_level="info" if args.verbose else "warning"))]

    # HTTPS listener for the Word add-in (Office requires TLS even on localhost).
    https_port = int(config.get_settings().get("https_port") or 8766)
    if not args.no_https and _port_free("127.0.0.1", https_port):
        try:
            from .addin import ensure_certificates, write_manifest

            certs = ensure_certificates()
            write_manifest()
            servers.append(uvicorn.Server(uvicorn.Config(
                "doi_hunter_web.server:app", host="127.0.0.1", port=https_port, log_level="warning",
                ssl_certfile=str(certs["cert"]), ssl_keyfile=str(certs["key"]))))
            print(f"      Word add-in: https://localhost:{https_port}/addin/taskpane.html")
        except Exception as e:  # never block the main app because of the add-in listener
            print(f"      Word add-in listener disabled: {e}")
    print()

    if args.reload:
        uvicorn.run("doi_hunter_web.server:app", host=args.host, port=port, reload=True)
        return

    import asyncio

    async def serve_all() -> None:
        await asyncio.gather(*(srv.serve() for srv in servers))

    asyncio.run(serve_all())


if __name__ == "__main__":
    main()
