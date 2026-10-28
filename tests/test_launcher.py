"""Launcher: single instance that is always THIS install's code; quit / restart from the app.

Integration tests start real server processes on a free port with a throw-away library, exactly as a
shortcut does (``python -m doi_hunter_web.launcher``). Shortcuts are never touched by these tests.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def env(tmp_path):
    port = _free_port()
    e = {**os.environ, "REVIEWHUNTER_HOME": str(tmp_path / "lib"), "REVIEWHUNTER_PORT": str(port),
         "REVIEWHUNTER_NO_SHORTCUTS": "1", "REVIEWHUNTER_NO_HTTPS": "1", "REVIEWHUNTER_NO_DIALOGS": "1",
         "PYTHONIOENCODING": "utf-8"}
    e.pop("REVIEWHUNTER_PAPERS_DIR", None)
    yield {"env": e, "port": port, "home": tmp_path / "lib"}
    _post(port, "/api/system/shutdown")   # never leave a server behind
    _wait_free(port)


def _launch(env, *args, timeout=150):
    return subprocess.run([sys.executable, "-m", "doi_hunter_web.launcher", "--no-browser", "--no-shortcuts", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def _instance(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/instance", timeout=3) as r:
            return json.loads(r.read())
    except OSError:
        return None


def _post(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None


def _wait_free(port, timeout=30):
    from doi_hunter_web.launcher import port_free

    end = time.time() + timeout
    while time.time() < end:
        if port_free("127.0.0.1", port):
            return True
        time.sleep(0.2)
    return False


def test_classify():
    from doi_hunter_web import __version__
    from doi_hunter_web.launcher import classify

    assert classify(None) == "none"
    assert classify({"app": "other"}) == "foreign"
    assert classify({"app": "ReviewHunter", "version": __version__, "root": str(ROOT)}) == "same"
    assert classify({"app": "ReviewHunter", "version": "2.7.0", "root": str(ROOT)}) == "stale"          # old code in memory
    assert classify({"app": "ReviewHunter", "version": __version__, "root": "C:/elsewhere"}) == "stale"  # other install
    assert classify({"app": "ReviewHunter", "version": __version__, "root": None, "legacy": True}) == "stale"


def test_single_instance_restart_and_quit(env):
    port = env["port"]
    first = _launch(env["env"])
    assert first.returncode == 0, first.stderr + first.stdout
    info = _instance(port)
    assert info["root"] == str(ROOT) and info["launched_by"] == "launcher" and info["can_shutdown"]
    assert (env["home"] / "logs" / "server.log").exists()

    again = _launch(env["env"])                                  # second click: same instance, no new server
    assert again.returncode == 0 and "already running" in again.stdout
    assert _instance(port)["pid"] == info["pid"]

    # Extensions may not stop the app; the app page and local programs may.
    assert _post(port, "/api/system/shutdown", {"Origin": "chrome-extension://abcdefghijklmnop"}) == 403
    assert _post(port, "/api/system/restart", {"Origin": f"http://127.0.0.1:{port}"}) == 200
    end = time.time() + 90
    while time.time() < end:
        now = _instance(port)
        if now and now["pid"] != info["pid"]:
            break
        time.sleep(0.3)
    assert now and now["pid"] != info["pid"], "restart did not bring up a new process"

    assert _post(port, "/api/system/shutdown") == 200
    assert _wait_free(port)


def test_stale_old_version_is_replaced(env, tmp_path):
    """An old ReviewHunter (no /api/system/instance, no shutdown endpoint) holding the port is replaced."""
    port = env["port"]
    fake = tmp_path / "old_doi_hunter_web_2_7.py"   # command line contains doi_hunter_web like the real thing
    fake.write_text(
        "import http.server, json, socketserver, sys\n"
        # HTTPServer.server_bind calls socket.getfqdn() between bind() and listen(), which can stall for
        # seconds on macOS: the port then looks taken but nothing answers. Skip the name lookup.
        "class S(http.server.ThreadingHTTPServer):\n"
        "    def server_bind(self):\n"
        "        socketserver.TCPServer.server_bind(self)\n"
        "        self.server_name, self.server_port = 'localhost', self.server_address[1]\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        if self.path == '/connector/ping':\n"
        "            body = json.dumps({'app': 'ReviewHunter', 'version': '2.7.0'}).encode()\n"
        "            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()\n"
        "            self.wfile.write(body)\n"
        "        else:\n"
        "            self.send_response(404); self.end_headers()\n"
        "    do_POST = do_GET\n"
        "    def log_message(self, *a): pass\n"
        "S(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n", encoding="utf-8")
    old = subprocess.Popen([sys.executable, str(fake), str(port)])
    try:
        end = time.time() + 15                                      # wait until the old version ANSWERS
        while time.time() < end:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/connector/ping", timeout=2) as r:
                    if json.loads(r.read()).get("version") == "2.7.0":
                        break
            except OSError:
                time.sleep(0.2)
        result = _launch(env["env"])
        assert result.returncode == 0, result.stderr + result.stdout
        assert "Replacing" in result.stdout
        assert old.wait(timeout=20) is not None                    # the old process was stopped
        assert _instance(port)["root"] == str(ROOT)
    finally:
        if old.poll() is None:
            old.kill()


def test_foreign_program_is_never_killed(env):
    port = env["port"]
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", port))
        blocker.listen()
        env["env"]["REVIEWHUNTER_LAUNCHER_SETTLE"] = "1"
        result = _launch(env["env"], timeout=60)
    assert result.returncode == 1 and "another program" in (result.stderr + result.stdout)


def test_port_in_time_wait_after_quit_counts_as_free():
    """Quit then reopen at once: the server's closed connections leave the port in TIME_WAIT, which must
    not be reported as "used by another program" (it was on macOS/Linux before SO_REUSEADDR)."""
    from doi_hunter_web.launcher import port_free

    port = _free_port()
    server = socket.socket()
    if os.name != "nt":
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as uvicorn binds
    server.bind(("127.0.0.1", port))
    server.listen()
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()          # the server side closes first -> its end of the connection enters TIME_WAIT
    server.close()
    client.close()
    assert port_free("127.0.0.1", port)
