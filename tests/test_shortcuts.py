"""Shortcuts always start THIS install: creation, detection of wrong targets and repair, on each OS.

macOS and Linux logic is plain file writing, so it is exercised on any OS by switching the platform
flags. Windows .lnk files are created for real (in temporary folders) when the tests run on Windows.
"""
import os
import sys
from pathlib import Path, PurePosixPath

import pytest

from doi_hunter_web import shortcuts as sc

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def places(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_NO_SHORTCUTS", raising=False)
    return {"menu": tmp_path / "menu", "desktop": tmp_path / "desktop"}


def _as(monkeypatch, system):
    monkeypatch.setattr(sc, "IS_WINDOWS", system == "windows")
    monkeypatch.setattr(sc, "IS_MAC", system == "mac")


def test_never_for_temporary_or_disabled_installs(tmp_path, monkeypatch):
    (tmp_path / "doi_hunter_web").mkdir()
    assert sc.skip_reason(tmp_path) == "temporary folder"
    monkeypatch.setenv("REVIEWHUNTER_NO_SHORTCUTS", "1")
    assert "disabled" in sc.ensure(ROOT)["skipped"]


def test_icns_and_ico_are_valid_containers(tmp_path):
    png = ROOT / sc.ICON_PNG
    icns = sc.write_icns(png, tmp_path / "a.icns").read_bytes()
    assert icns[:4] == b"icns" and int.from_bytes(icns[4:8], "big") == len(icns)
    assert icns[8:12] == b"ic07" and icns[16:24] == png.read_bytes()[:8]          # 128 px PNG element
    ico = sc.write_ico(png, tmp_path / "a.ico").read_bytes()
    assert ico[:6] == b"\x00\x00\x01\x00\x01\x00" and ico[22:30] == png.read_bytes()[:8]


def test_mac_bundle_is_created_detected_and_repaired(places, monkeypatch, tmp_path):
    _as(monkeypatch, "mac")
    root = ROOT
    # The Desktop item is a symlink to the bundle; Windows only allows symlinks with a special privilege.
    try:
        (tmp_path / "probe-link").symlink_to(tmp_path)
        kinds = ["menu", "desktop"]
    except OSError:
        kinds = ["menu"]
    report = sc.ensure(root, create=kinds, places=places)
    bundle = places["menu"] / "ReviewHunter.app"
    assert report["ok"] and set(report["created"]) == {str(sc.shortcut_path(k, places[k])) for k in kinds}
    script = (bundle / "Contents" / "MacOS" / "ReviewHunter").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/bash") and "-m doi_hunter_web.launcher" in script
    plist = (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "<key>LSUIElement</key><true/>" in plist and "org.reviewhunter.app" in plist
    assert (bundle / "Contents" / "Resources" / "ReviewHunter.icns").exists()
    if os.name != "nt":
        assert os.access(bundle / "Contents" / "MacOS" / "ReviewHunter", os.X_OK)
    assert sc._mac_bundle_root(bundle) == str(root)

    # A bundle left by an old install in another folder is detected and rewritten.
    files = sc.mac_bundle_files(PurePosixPath("/Users/old/Library/Review'Hunter"))  # quote in the path survives
    (bundle / "Contents" / "MacOS" / "ReviewHunter").write_text(files["Contents/MacOS/ReviewHunter"], encoding="utf-8")
    assert sc._mac_bundle_root(bundle) == "/Users/old/Library/Review'Hunter"
    assert not sc.describe("menu", bundle, root)["linked"]
    report = sc.ensure(root, places=places)
    assert report["repaired"] == [str(bundle)] and report["ok"]


def test_linux_entries(places, monkeypatch):
    _as(monkeypatch, "linux")
    entry = places["menu"] / "reviewhunter.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text('[Desktop Entry]\nExec="/opt/old/.venv/bin/python" -m doi_hunter_web\nPath=/opt/old\n')
    report = sc.ensure(ROOT, places=places)
    assert report["repaired"] == [str(entry)] and report["ok"]
    text = entry.read_text()
    assert "-m doi_hunter_web.launcher" in text and f"Path={ROOT}" in text
    assert not (places["desktop"] / "reviewhunter.desktop").exists()     # a deleted Desktop entry stays deleted


@pytest.mark.skipif(os.name != "nt", reason="real .lnk files need Windows")
def test_windows_links_repaired_and_one_always_kept(places):
    old = places["desktop"] / "ReviewHunter.lnk"
    old.parent.mkdir(parents=True)
    sc._powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:L); $s.TargetPath = $env:T; $s.Save()",
                   {"L": str(old), "T": str(ROOT / "ReviewHunter.bat")})                    # the old .bat launch
    before = {s["kind"]: s for s in sc.status(ROOT, places)}
    assert before["desktop"]["exists"] and not before["desktop"]["linked"] and not before["menu"]["exists"]

    report = sc.ensure(ROOT, places=places)
    assert report["repaired"] == [str(old)] and report["created"] == [] and report["ok"]
    after = {s["kind"]: s for s in report["shortcuts"]}
    assert after["desktop"]["linked"] and "doi_hunter_web.launcher" in after["desktop"]["points_to"]
    assert "pythonw.exe" in after["desktop"]["points_to"].lower()                         # no console window

    old.unlink()                                                                          # none left at all:
    report = sc.ensure(ROOT, places=places)                                               # the Start menu one returns
    assert report["created"] == [str(places["menu"] / "ReviewHunter.lnk")]
