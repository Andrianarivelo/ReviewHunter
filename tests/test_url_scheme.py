"""reviewhunter:// links: parsing, registration (Windows registry / Linux desktop entry), launcher handling."""
import os
from pathlib import Path

import pytest

from doi_hunter_web import url_scheme as us

ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = r"Software\ReviewHunterTests\Classes\reviewhunter"


@pytest.mark.parametrize("url, expected", [
    ("reviewhunter://start", {"action": "start", "item": None}),
    ("reviewhunter://open", {"action": "open", "item": None}),
    ("reviewhunter://open?item=12", {"action": "open", "item": 12}),
    ("reviewhunter:open?item=7", {"action": "open", "item": 7}),
    ("REVIEWHUNTER://START/", {"action": "start", "item": None}),
    ("reviewhunter://open?item=../../x", {"action": "open", "item": None}),      # never anything but an id
    ("https://evil.example/?item=3", {"action": "open", "item": None}),          # other schemes: just open
    ("", {"action": "open", "item": None}),
])
def test_parse(url, expected):
    assert us.parse(url) == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows registry")
def test_windows_registration_round_trip(monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    try:
        assert not us.status(ROOT, TEST_KEY)["registered"]
        report = us.register(ROOT, TEST_KEY)
        assert report["ok"] and report["linked"], report
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as k:
            assert winreg.QueryValueEx(k, "URL Protocol")[0] == ""          # marks it as a URL handler
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{TEST_KEY}\shell\open\command") as k:
            command = winreg.QueryValueEx(k, "")[0]
        assert "pythonw.exe" in command.lower() and command.endswith('"%1"')
        script = us.handler_script(ROOT).read_text(encoding="utf-8")
        assert "main(['--url'" in script and repr(str(ROOT)) in script
        assert us.register(ROOT, TEST_KEY)["changed"] is False              # idempotent
        assert us.unregister(ROOT, TEST_KEY)
    finally:
        us.unregister(ROOT, TEST_KEY)
        import winreg

        for key in (r"Software\ReviewHunterTests\Classes", r"Software\ReviewHunterTests"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass


@pytest.mark.skipif(os.name == "nt" or os.uname().sysname == "Darwin", reason="Linux desktop entry")
def test_linux_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    assert us.register(ROOT)["linked"]
    text = (tmp_path / "applications" / "reviewhunter-url.desktop").read_text()
    assert "MimeType=x-scheme-handler/reviewhunter;" in text and "%u" in text


def test_never_for_temporary_copies(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    (tmp_path / "doi_hunter_web").mkdir()
    assert us.register(tmp_path)["skipped"] == "temporary folder"
