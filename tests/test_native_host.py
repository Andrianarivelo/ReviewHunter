"""Starting ReviewHunter from the browser extension: native messaging helper and its registration."""
import io
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from doi_hunter_web import native_host as nh

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows path encoding (UTF-16LE)")
def test_path_id_matches_ids_chromium_assigned():
    """IDs observed in a real Chromium for unpacked extensions loaded from these folders."""
    assert nh.extension_path_id(r"C:\Users\andry\AppData\Local\Temp\rh-upgrade-xf5xl8_4\browser_extension") \
        == "cjoiccnkgddcgohgpeogbikedgamkefa"
    assert nh.extension_path_id(r"c:\Users\andry\AppData\Local\Temp\rh-upgrade-ry62i1ay\browser_extension") \
        == "hapodgeofmpkkdhpailnofaanhmdpcme"                                # drive letter case does not matter


def test_helper_trusts_the_bundled_connector_and_reported_ones(library, monkeypatch):
    manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert "nativeMessaging" in manifest["permissions"]
    assert "key" not in manifest     # a key would change installed connectors' IDs (Chrome disables the update)
    own = nh.extension_path_id(ROOT / "browser_extension")
    assert nh.host_manifest(ROOT)["allowed_origins"] == [f"chrome-extension://{own}/"]
    monkeypatch.setattr(nh, "register", lambda root=ROOT, **kw: {"ok": True})
    other = "a" * 32
    assert nh.remember_extension_id(other)["added"] and not nh.remember_extension_id(other)["added"]
    assert nh.host_manifest(ROOT)["allowed_origins"][1] == f"chrome-extension://{other}/"
    with pytest.raises(ValueError):
        nh.remember_extension_id("../../etc")


def test_message_framing_round_trip():
    buf = io.BytesIO()
    nh.write_message(buf, {"cmd": "hello", "é": 1})
    buf.seek(0)
    assert nh.read_message(buf) == {"cmd": "hello", "é": 1}
    assert nh.read_message(io.BytesIO(b"")) is None
    with pytest.raises(ValueError):
        nh.read_message(io.BytesIO(struct.pack("<I", nh.MAX_MESSAGE + 1)))


def _ask(env, message, timeout=150):
    data = json.dumps(message).encode()
    out = subprocess.run([sys.executable, "-m", "doi_hunter_web.native_host", "chrome-extension://x/"],
                         input=struct.pack("<I", len(data)) + data, capture_output=True, cwd=ROOT, env=env,
                         timeout=timeout)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    size = struct.unpack("<I", out.stdout[:4])[0]
    assert len(out.stdout) == 4 + size, "the helper wrote something else than its one answer to stdout"
    return json.loads(out.stdout[4:])


def test_helper_starts_the_server_once_and_it_keeps_running(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "REVIEWHUNTER_HOME": str(tmp_path / "lib"), "REVIEWHUNTER_PORT": str(port),
           "REVIEWHUNTER_NO_SHORTCUTS": "1", "REVIEWHUNTER_NO_HTTPS": "1", "REVIEWHUNTER_NO_DIALOGS": "1"}
    try:
        assert _ask(env, {"cmd": "hello"})["running"] is False
        first = _ask(env, {"cmd": "start"})
        assert first["ok"] and first["state"] == "started", first
        # The helper has exited: the server it started must still answer.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/instance", timeout=5) as r:
            info = json.loads(r.read())
        assert info["root"] == str(ROOT) and info["launched_by"] == "launcher"
        second = _ask(env, {"cmd": "start"})
        assert second["ok"] and second["state"] == "running"                 # single instance
        assert _ask(env, {"cmd": "rm -rf /"})["ok"] is False                 # only known commands
    finally:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/system/shutdown", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except OSError:
            pass
        time.sleep(1.5)


def test_registration_is_written_detected_and_removed(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    if os.name == "nt":
        where = {"keys": {"test": r"Software\ReviewHunterTests\NativeMessagingHosts"}}
    else:
        (tmp_path / "browser").mkdir()
        where = {"dirs": {"test": tmp_path / "browser" / "NativeMessagingHosts"}}
    try:
        assert not nh.status(ROOT, **where)[0]["registered"]
        report = nh.register(ROOT, **where)
        assert report["ok"] and report["browsers"][0]["linked"], report
        program = nh.host_program(ROOT).read_text(encoding="utf-8")
        assert "-m doi_hunter_web.native_host" in program and str(ROOT) in program
        assert nh.register(ROOT, **where)["changed"] == []                  # idempotent: nothing rewritten
        assert nh.unregister(ROOT, **where)
        assert not nh.status(ROOT, **where)[0]["registered"]
    finally:
        nh.unregister(ROOT, **where)
        if os.name == "nt":
            import winreg

            for key in (r"Software\ReviewHunterTests\NativeMessagingHosts", r"Software\ReviewHunterTests"):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                except OSError:
                    pass


def test_never_registered_for_temporary_copies(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    (tmp_path / "doi_hunter_web").mkdir()
    assert nh.register(tmp_path)["skipped"] == "temporary folder"
