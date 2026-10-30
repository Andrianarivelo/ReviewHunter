"""One-click installs ("ReviewHunter for Firefox / Word ..."): what each one opens, and their shortcuts.

Nothing here opens a browser or touches a real registration: the opener, the clipboard and the
download are replaced, and shortcuts are written to temporary folders.
"""
import os
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from doi_hunter_web import integrations as ig
from doi_hunter_web import shortcuts as sc

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def sandbox(library, tmp_path, monkeypatch):
    """Record what would be opened; never touch the clipboard or the internet."""
    opened = []
    monkeypatch.setattr(ig, "_open", lambda target, browser=None: opened.append((target, browser)))
    monkeypatch.setattr(ig, "_copy_to_clipboard", lambda text: opened.append(("clipboard", text)))
    monkeypatch.setattr(ig, "connectors_dir", lambda: (tmp_path / "connectors").mkdir(exist_ok=True) or tmp_path / "connectors")
    fake = tmp_path / "firefox.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(ig, "find_browser", lambda name: str(fake) if name in ("firefox", "chrome") else None)
    return {"opened": opened, "exe": str(fake), "tmp": tmp_path}


def _xpi(path: Path, signed: bool) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", "{}")
        if signed:
            z.writestr("META-INF/mozilla.rsa", b"sig")
    return path


def test_signed_xpi_detection(tmp_path):
    assert ig.xpi_is_signed(_xpi(tmp_path / "s.xpi", True))
    assert not ig.xpi_is_signed(_xpi(tmp_path / "u.xpi", False))
    (tmp_path / "broken.xpi").write_bytes(b"not a zip")
    assert not ig.xpi_is_signed(tmp_path / "broken.xpi")


def test_firefox_signed_release_opens_in_firefox(sandbox, monkeypatch):
    xpi = _xpi(sandbox["tmp"] / "ReviewHunter-Firefox.xpi", True)
    monkeypatch.setattr(ig, "_download", lambda asset: xpi)
    r = ig.install_firefox()
    assert r["ok"] and r["mode"] == "signed"
    assert sandbox["opened"] == [(str(xpi), sandbox["exe"])]          # Firefox itself asks "Add ...?"


def test_firefox_without_signed_build_loads_a_temporary_copy(sandbox, monkeypatch):
    monkeypatch.setattr(ig, "_download", lambda asset: None)            # offline, or no release asset yet
    r = ig.install_firefox()
    assert r["ok"] and r["mode"] == "temporary" and Path(r["xpi"]).exists()
    with zipfile.ZipFile(r["xpi"]) as z:
        import json

        manifest = json.loads(z.read("manifest.json"))
    assert manifest["browser_specific_settings"]["gecko"]["id"] == "connector@reviewhunter.app"
    assert ("about:debugging#/runtime/this-firefox", sandbox["exe"]) in sandbox["opened"]
    assert ("clipboard", r["xpi"]) in sandbox["opened"]
    assert "until Firefox restarts" in r["message"]                      # honest about the limit


def test_chromium_opens_extensions_page_and_folder(sandbox):
    r = ig.install_chromium("chrome")
    assert r["ok"] and ("chrome://extensions/", sandbox["exe"]) in sandbox["opened"]
    assert (str(ROOT / "browser_extension"), None) in sandbox["opened"]
    assert not ig.install_chromium("edge")["ok"]                         # not installed: says so


def test_safari_only_on_macos(sandbox, monkeypatch):
    monkeypatch.setattr(ig, "IS_MAC", False)
    assert not ig.install_safari()["ok"]


def test_api_lists_and_guards_installs(library, monkeypatch):
    from fastapi.testclient import TestClient

    from doi_hunter_web.server import app

    monkeypatch.setattr(ig, "INSTALLERS", {**ig.INSTALLERS, "firefox": lambda: {"ok": True, "message": "m"}})
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        st = c.get("/api/system/integrations").json()
        assert set(st["browsers"]) >= {"chrome", "edge", "brave", "firefox"} and "word" in st
        app_origin = {"Origin": "http://127.0.0.1:8765"}
        assert c.post("/api/system/integrations/firefox", headers=app_origin).json() == {"ok": True, "message": "m"}
        assert c.post("/api/system/integrations/nope", headers=app_origin).status_code == 404
        for ext in ("chrome-extension://abc", "moz-extension://abc", "safari-web-extension://abc"):
            assert c.post("/api/system/integrations/firefox", headers={"Origin": ext}).status_code == 403


# ---------------------------------------------------------------- add-on shortcuts

@pytest.fixture()
def places(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    return {"menu": tmp_path / "menu", "desktop": tmp_path / "desktop"}


def _as(monkeypatch, system):
    monkeypatch.setattr(sc, "IS_WINDOWS", system == "windows")
    monkeypatch.setattr(sc, "IS_MAC", system == "mac")


def test_linux_addon_entries_created_repaired_removed(places, monkeypatch):
    _as(monkeypatch, "linux")
    report = sc.ensure_addons(ROOT, create=True, places=places, names=["firefox", "chrome"])
    assert report["ok"] and len(report["created"]) == 2
    entry = places["menu"] / "reviewhunter-firefox.desktop"
    assert "-m doi_hunter_web.integrations firefox" in entry.read_text() and "Name=ReviewHunter for Firefox" in entry.read_text()

    entry.write_text('[Desktop Entry]\nExec="/opt/old/.venv/bin/python" -m doi_hunter_web.integrations firefox\nPath=/opt/old\n')
    (places["menu"] / "reviewhunter-chrome.desktop").unlink()            # deleted by the user
    report = sc.ensure_addons(ROOT, places=places, names=["firefox", "chrome"])
    assert report["repaired"] == [str(entry)] and report["created"] == []  # repaired; deleted stays deleted
    assert sorted(sc.remove_addons(places)) == [str(entry)]


def test_mac_addon_bundles_do_not_claim_links(places, monkeypatch):
    _as(monkeypatch, "mac")
    report = sc.ensure_addons(ROOT, create=True, places=places, names=["safari", "word"])
    assert report["ok"]
    bundle = places["menu"] / "ReviewHunter Add-ons" / "ReviewHunter for Safari.app"
    script = (bundle / "Contents" / "MacOS" / "ReviewHunter").read_text(encoding="utf-8")
    plist = (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "-m doi_hunter_web.integrations safari" in script
    assert "org.reviewhunter.app.addon.safari" in plist and "CFBundleURLTypes" not in plist
    assert "CFBundleURLTypes" in sc.mac_bundle_files(PurePosixPath("/x"), PurePosixPath("/x/py"), "")["Contents/Info.plist"]
    sc.remove_addons(places)
    assert not (places["menu"] / "ReviewHunter Add-ons").exists()


@pytest.mark.skipif(os.name != "nt", reason="real .lnk files need Windows")
def test_windows_addon_links(places):
    report = sc.ensure_addons(ROOT, create=True, places=places, names=["firefox", "word"])
    assert report["ok"] and len(report["created"]) == 2
    word = places["menu"] / "ReviewHunter Add-ons" / "ReviewHunter for Word.lnk"
    assert word.exists() and all(a["linked"] for a in report["addons"])
    # A link to another install is rewritten.
    sc._powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:L); $s.TargetPath = $env:T;"
                   "$s.Arguments = '-m doi_hunter_web.integrations word'; $s.Save()",
                   {"L": str(word), "T": str(ROOT / "ReviewHunter.bat")})
    report = sc.ensure_addons(ROOT, places=places, names=["firefox", "word"])
    assert report["repaired"] == [str(word)] and report["ok"]
    assert len(sc.remove_addons(places)) == 2 and not (places["menu"] / "ReviewHunter Add-ons").exists()
