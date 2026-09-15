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
        # Credentialed CORS so shadow_test can fetch http://localhost:PORT from a page on 127.0.0.1:PORT
        # (two host names are needed to test mirror-alias de-duplication).
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        """Static files plus two generated routes used by test/pdf_test.js.

        /__gen/pdf?size=N      deterministic N-byte body starting with %PDF- (byte i = i % 251 after the header)
        /__gen/redirect?to=URL 302 redirect (exercises redirect following)

        Routes used by test/shadow_test.js (Sci-Hub / SciDB fallback):
        /__shadow/<scenario>/connector/pdf-sources  JSON mirror list, like ReviewHunter's endpoint
        /__shadow/wall                              human-verification page (fixtures/scihub_challenge.html)
        /__shadow/article                           article page embedding /__gen/pdf in an <object>
        /__shadow/alias                             302 to the wall on 127.0.0.1 (mirror alias)

        Routes used by test/handoff_test.js (browser hand-off): /__handoff/pdf, /__handoff/article,
        /__handoff/ddg?key=K (wall twice, then article), /__handoff/wall, /__handoff/block.
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
        if parsed.path.startswith("/__shadow/"):
            self._shadow(parsed.path)
            return
        if parsed.path.startswith("/__handoff/"):
            self._handoff(parsed.path, query)
            return
        if parsed.path == "/__gen/redirect":
            self.send_response(302)
            self.send_header("Location", query.get("to", ["/"])[0])
            self.end_headers()
            return
        super().do_GET()

    # Hits per DDoS-Guard key: the wall is shown twice, then the article (a JavaScript check that "clears").
    ddg_hits: dict[str, int] = {}

    def _handoff(self, path: str, query: dict) -> None:
        """Fake sources and a fake connector for the browser hand-off tests (test/handoff_test.js)."""
        import json

        if path == "/__handoff/pdf":
            body = b"%PDF-1.4\n" + bytes(i % 251 for i in range(9, 4096))
            ctype, status = "application/pdf", 200
        elif path == "/__handoff/article":
            body = b'<html><head><title>Sci-Hub | Deep learning</title></head><body><embed type="application/pdf" src="/__gen/pdf?size=4096#view=FitH" id="pdf"></body></html>'
            ctype, status = "text/html; charset=utf-8", 200
        elif path == "/__handoff/ddg":
            key = query.get("key", ["k"])[0]
            n = NoCacheHandler.ddg_hits.get(key, 0) + 1
            NoCacheHandler.ddg_hits[key] = n
            if n <= 2:
                body = b'<!doctype html><html><head><title>DDoS-Guard</title></head><body data-ddg-origin="true"><h1>Checking your browser before accessing</h1></body></html>'
                ctype, status = "text/html; charset=utf-8", 403
            else:
                body = b'<html><head><title>SciDB</title></head><body><object type="application/pdf" data="/__gen/pdf?size=4096"></object></body></html>'
                ctype, status = "text/html; charset=utf-8", 200
        elif path == "/__handoff/wall":
            body = (ROOT / "test" / "fixtures" / "scihub_challenge.html").read_bytes()
            ctype, status = "text/html; charset=utf-8", 200
        elif path == "/__handoff/block":
            body = b'<html><head><title>UNIGE - Site bloque</title></head><body><div>Fortinet FortiGuard Web Filter</div></body></html>'
            ctype, status = "text/html; charset=utf-8", 200
        elif path == "/__handoff/connector/ping":
            body = json.dumps({"app": "ReviewHunter", "version": "test", "library": {"items": 0}}).encode()
            ctype, status = "application/json", 200
        else:
            body, ctype, status = b"not found", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _shadow(self, path: str) -> None:
        """Fake Sci-Hub mirrors for the shadow-library fallback tests."""
        import json

        port = self.server.server_address[1]
        a, b = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
        scenarios = {
            # wall on the first mirror, real article on the second one
            "wall-then-article": [{"source": "Sci-Hub (a)", "url": f"{a}/__shadow/wall"},
                                  {"source": "Sci-Hub (b)", "url": f"{b}/__shadow/article"}],
            # wall only; the second mirror redirects to the first (alias) and must be skipped
            "wall-and-alias": [{"source": "Sci-Hub (a)", "url": f"{a}/__shadow/wall"},
                               {"source": "Sci-Hub (alias)", "url": f"{b}/__shadow/alias"}],
        }
        if path.endswith("/connector/pdf-sources"):
            name = path.split("/")[2]
            body = json.dumps({"doi": "10.1177/0269881107082286", "pages": scenarios.get(name, [])}).encode()
            ctype, status = "application/json", 200
        elif path == "/__shadow/wall":
            body = (ROOT / "test" / "fixtures" / "scihub_challenge.html").read_bytes()
            ctype, status = "text/html; charset=utf-8", 200
        elif path == "/__shadow/article":
            body = b'<html><body><div class="pdf"><object type="application/pdf" data="/__gen/pdf?size=4096#view=FitH"></object></div></body></html>'
            ctype, status = "text/html; charset=utf-8", 200
        elif path == "/__shadow/alias":
            self.send_response(302)
            self.send_header("Location", f"{a}/__shadow/wall")
            self.end_headers()
            return
        else:
            body, ctype, status = b"not found", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    print(f"Serving {ROOT} at http://127.0.0.1:{PORT}/test/test.html")
    ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
