"""Serve the extension folder for the browser test harness, with caching disabled.

Usage (from the repository root):
    .venv/Scripts/python.exe browser_extension/test/serve.py [port]
Then open http://127.0.0.1:8799/test/test.html
"""
from __future__ import annotations

import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # browser_extension/
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799


class NoCacheHandler(SimpleHTTPRequestHandler):
    """Static handler that forbids caching so edited ES modules reload immediately."""

    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript"}

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        """Static files plus two generated routes used by test/pdf_test.js.

        /__gen/pdf?size=N      deterministic N-byte body starting with %PDF- (byte i = i % 251 after the header)
        /__gen/redirect?to=URL 302 redirect (exercises redirect following)
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/__gen/pdf":
            size = max(16, min(int(query.get("size", ["1024"])[0]), 50 * 1024 * 1024))
            header = b"%PDF-1.4\n"
            body = header + bytes(i % 251 for i in range(len(header), size))
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/__gen/redirect":
            self.send_response(302)
            self.send_header("Location", query.get("to", ["/"])[0])
            self.end_headers()
            return
        super().do_GET()


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    print(f"Serving {ROOT} at http://127.0.0.1:{PORT}/test/test.html")
    ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
