"""Installer component checks without installing into a user's profile or opening Chrome."""
import argparse
import json
import shutil
from pathlib import Path

import pytest

from installer import reviewhunter_installer as setup

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle(tmp_path):
    """Copy the actual connector runtime so missing-file tests exercise shipped assets."""
    shutil.copytree(ROOT / "browser_extension", tmp_path / "browser_extension")
    return tmp_path


def test_current_pdf_dependencies_are_required():
    assert {"fontTools", "curl_cffi", "pypdf"}.issubset(setup.REQUIRED_MODULES)


def test_extension_prepared_with_current_version_and_honest_status(bundle, monkeypatch):
    monkeypatch.setattr(setup, "IS_WINDOWS", True)
    installer = setup.Installer(setup.Options(target=bundle), setup.Reporter())
    result = installer.prepare_chrome_extension()
    manifest = json.loads((bundle / "browser_extension/manifest.json").read_text())
    assert f"v{manifest['version']}" in result
    assert installer.extension["version"] == manifest["version"]
    assert installer.extension["status"] == "ready_for_chrome" and installer.extension["requires_confirmation"]
    assert "Load unpacked" in (bundle / "ChromeExtensionSetup.txt").read_text()
    command = (bundle / "Install Chrome Extension.cmd").read_text()
    assert '--setup-chrome --target "%~dp0."' in command


def test_missing_extension_dependency_stops_install(bundle):
    (bundle / "browser_extension/src/core/pdf.js").unlink()
    with pytest.raises(setup.InstallError, match="missing"):
        setup.validate_chrome_extension(bundle)


def test_manifest_path_cannot_escape_bundle(bundle):
    path = bundle / "browser_extension/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["background"]["service_worker"] = "../elsewhere.js"
    path.write_text(json.dumps(manifest))
    (bundle / "elsewhere.js").write_text("// outside extension")
    with pytest.raises(setup.InstallError, match="missing"):
        setup.validate_chrome_extension(bundle)


def test_skip_chrome_setup(bundle):
    installer = setup.Installer(setup.Options(target=bundle, chrome_extension=False), setup.Reporter())
    assert installer.prepare_chrome_extension() == "not selected"
    assert not (bundle / "ChromeExtensionSetup.txt").exists()
    assert installer.extension == {"status": "not_selected"}


def test_manifest_records_prepared_extension(bundle):
    installer = setup.Installer(setup.Options(target=bundle, launch=False), setup.Reporter())
    installer.version = "2.2.1"
    installer.prepare_chrome_extension()
    installer.finish()
    installed = json.loads((bundle / "install.json").read_text())
    assert installed["chrome_extension"]["status"] == "ready_for_chrome"
    assert installed["chrome_extension"]["folder"] == str((bundle / "browser_extension").resolve())


def test_no_launch_never_opens_chrome_or_app(bundle, monkeypatch):
    monkeypatch.setattr(setup.Installer, "run", lambda self: True)
    monkeypatch.setattr(setup, "open_extension_setup", lambda *a: pytest.fail("Chrome must stay closed"))
    monkeypatch.setattr(setup, "launch_app", lambda *a: pytest.fail("App must stay closed"))
    assert setup.run_cli(argparse.Namespace(yes=True, verbose=False), setup.Options(target=bundle, launch=False)) == 0


def test_chrome_setup_opens_correct_browser_and_preserves_path(bundle, monkeypatch):
    called = []
    monkeypatch.setattr(setup, "IS_WINDOWS", True)
    monkeypatch.setattr(setup, "find_chrome", lambda: r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    monkeypatch.setattr(setup, "_powershell", lambda script, env: called.append((script, env)))
    monkeypatch.setattr(setup.os, "startfile", lambda path: called.append(path), raising=False)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda cmd, **kwargs: called.append(cmd))
    assert setup.open_extension_setup(bundle)
    assert called[-1][-1] == "chrome://extensions/"
    assert called[0][1]["RH_EXTENSION_FOLDER"] == str((bundle / "browser_extension").resolve())
    assert not any("--load-extension" in str(call) for call in called)


def test_chrome_detection_supports_x86_install(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "IS_WINDOWS", True)
    for name in ("LOCALAPPDATA", "ProgramFiles"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    chrome = tmp_path / "Google/Chrome/Application/chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"fixture")
    assert setup.find_chrome() == str(chrome)


def test_setup_command_uses_installed_target_without_reinstalling(bundle, monkeypatch):
    called = []
    monkeypatch.setattr(setup, "open_extension_setup", lambda target: called.append(target) or True)
    monkeypatch.setattr(setup.Installer, "run", lambda self: pytest.fail("Must not reinstall"))
    assert setup.main(["--setup-chrome", "--target", str(bundle)]) == 0
    assert called == [bundle.resolve()]
