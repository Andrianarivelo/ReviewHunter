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


def _find_free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((config.HOST, preferred))
            return preferred
        except OSError:
            s.bind((config.HOST, 0))
            return s.getsockname()[1]


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

    port = _find_free_port(args.port)
    url = f"http://{args.host}:{port}"
    print(f"\n  ReviewHunter - http://{args.host}:{port}")
    print(f"      library: {config.library_root()}\n")

    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url, args.host, port), daemon=True).start()

    uvicorn.run(
        "doi_hunter_web.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="info" if args.verbose else "warning",
    )


if __name__ == "__main__":
    main()
