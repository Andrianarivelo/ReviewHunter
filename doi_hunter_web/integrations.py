"""One-click installs for the browser connectors and the Word add-in (shortcuts, Settings buttons, installer).

    python -m doi_hunter_web.integrations firefox | safari | chrome | edge | brave | word | status

What each one can honestly do (browser vendors decide what an outside program may install):
    chrome / edge / brave  open the browser's extensions page and the connector folder, path copied:
                           the person enables Developer mode and clicks "Load unpacked" (Chromium does
                           not allow other installs of extensions outside its stores)
    firefox                downloads the Mozilla-signed connector of the latest release and opens it in
                           Firefox, which asks "Add ReviewHunter Connector?" (one click). Without a signed
                           build, opens about:debugging to load it until Firefox restarts.
    safari (macOS)         downloads the connector app of the latest release, opens it and Safari's
                           Extensions settings (tick ReviewHunter). Unsigned builds also need Safari's
                           Develop > Allow Unsigned Extensions.
    word                   Windows: the add-in setup, quiet (Windows asks once to trust the local
                           certificate). macOS: keychain trust (password prompt) + manifest sideloading.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Optional

from . import config

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
RELEASE = "https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download"
FIREFOX_ASSET = "ReviewHunter-Firefox.xpi"
SAFARI_ASSET = "ReviewHunter-Safari.zip"
EXTENSION_PAGES = {"chrome": "chrome://extensions/", "edge": "edge://extensions/", "brave": "brave://extensions/"}


# ======================================================================================
# Finding browsers
# ======================================================================================

def _first(paths) -> Optional[str]:
    return next((str(p) for p in paths if p and Path(p).is_file()), None)


def find_browser(name: str) -> Optional[str]:
    """Executable of an installed browser (None when absent)."""
    env = os.environ
    if IS_WINDOWS:
        bases = [env.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        rel = {"chrome": r"Google\Chrome\Application\chrome.exe", "edge": r"Microsoft\Edge\Application\msedge.exe",
               "brave": r"BraveSoftware\Brave-Browser\Application\brave.exe", "firefox": r"Mozilla Firefox\firefox.exe"}
        if name not in rel:
            return None
        return _first(Path(b) / rel[name] for b in bases if b)
    if IS_MAC:
        app = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "brave": "Brave Browser", "firefox": "Firefox",
               "safari": "Safari"}.get(name)
        if not app:
            return None
        exe = {"Firefox": "firefox", "Safari": "Safari"}.get(app, app)
        return _first(Path(d) / f"{app}.app" / "Contents" / "MacOS" / exe
                      for d in ("/Applications", str(Path.home() / "Applications")))
    names = {"chrome": ["google-chrome", "google-chrome-stable"], "edge": ["microsoft-edge"],
             "brave": ["brave-browser", "brave"], "firefox": ["firefox"]}.get(name, [])
    return next((shutil.which(n) for n in names if shutil.which(n)), None)


def status() -> dict:
    from . import addin

    browsers = {b: bool(find_browser(b)) for b in ("chrome", "edge", "brave", "firefox")}
    if IS_MAC:
        browsers["safari"] = True
    word = addin.status()
    return {"browsers": browsers, "platform": "windows" if IS_WINDOWS else "macos" if IS_MAC else "linux",
            "word": {"installed": word.get("registered") and word.get("ca_trusted"), **word}}


# ======================================================================================
# Downloads
# ======================================================================================

def connectors_dir() -> Path:
    d = config.library_root() / "connectors"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(asset: str) -> Optional[Path]:
    target = connectors_dir() / asset
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        opener = urllib.request.build_opener()
        with opener.open(f"{RELEASE}/{asset}", timeout=60) as r, open(tmp, "wb") as out:
            shutil.copyfileobj(r, out)
        tmp.replace(target)
        return target
    except OSError:
        tmp.unlink(missing_ok=True)
        return target if target.exists() else None


def xpi_is_signed(xpi: Path) -> bool:
    try:
        with zipfile.ZipFile(xpi) as z:
            names = set(z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    return "META-INF/mozilla.rsa" in names or "META-INF/cose.sig" in names


def _open(path_or_url: str, browser: Optional[str] = None) -> None:
    if browser:
        subprocess.Popen([browser, path_or_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif IS_WINDOWS:
        os.startfile(path_or_url)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", path_or_url])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path_or_url])
    else:
        webbrowser.open(path_or_url)


def _copy_to_clipboard(text: str) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif IS_MAC:
            subprocess.run(["pbcopy"], input=text.encode(), check=False)
    except OSError:
        pass


# ======================================================================================
# Installs
# ======================================================================================

def install_chromium(browser: str) -> dict:
    exe = find_browser(browser)
    folder = ROOT / "browser_extension"
    if not exe:
        return {"ok": False, "message": f"{browser.title()} was not found on this computer."}
    _copy_to_clipboard(str(folder))
    _open(EXTENSION_PAGES[browser], exe)
    _open(str(folder))
    return {"ok": True, "message": f"In {browser.title()}: turn on Developer mode, click Load unpacked and paste the "
                                   f"folder path (copied): {folder}. Already installed? Click Reload on its card.",
            "folder": str(folder)}


def install_firefox() -> dict:
    exe = find_browser("firefox")
    if not exe:
        _open("https://www.mozilla.org/firefox/")
        return {"ok": False, "message": "Firefox was not found on this computer."}
    xpi = _download(FIREFOX_ASSET)
    if xpi and xpi_is_signed(xpi):
        _open(str(xpi), exe)   # Firefox shows "Add ReviewHunter Connector?"
        return {"ok": True, "mode": "signed", "message": "Firefox asks to add ReviewHunter Connector: click Add."}
    # No Mozilla-signed build available: build one locally and load it until Firefox restarts.
    sys.path.insert(0, str(ROOT / "tools"))
    import build_extensions

    built = build_extensions.build(connectors_dir() / "build")
    _copy_to_clipboard(built["xpi"])
    _open("about:debugging#/runtime/this-firefox", exe)
    return {"ok": True, "mode": "temporary", "xpi": built["xpi"],
            "message": "Firefox only installs add-ons signed by Mozilla permanently, and no signed build is "
                       "available yet. In the page that opened, click 'Load Temporary Add-on' and choose "
                       f"{built['xpi']} (path copied). It stays until Firefox restarts."}


def install_safari() -> dict:
    if not IS_MAC:
        return {"ok": False, "message": "Safari is only available on macOS."}
    archive = _download(SAFARI_ASSET)
    if not archive:
        return {"ok": False, "message": "The Safari connector could not be downloaded (check the internet connection)."}
    target = Path.home() / "Applications"
    target.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="rh-safari-"))
    subprocess.run(["ditto", "-x", "-k", str(archive), str(tmp)], check=True)
    app = next(tmp.glob("*.app"), None)
    if not app:
        return {"ok": False, "message": "The downloaded Safari connector is incomplete."}
    dest = target / app.name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(app), str(dest))
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True)
    subprocess.Popen(["open", str(dest)])
    return {"ok": True, "app": str(dest),
            "message": "In Safari: Settings > Extensions > tick ReviewHunter Connector, and allow it on all websites. "
                       "If it is not listed: Safari > Settings > Advanced > Show features for web developers, then "
                       "Develop > Allow Unsigned Extensions (needed until the connector is signed by Apple)."}


def install_word() -> dict:
    from . import addin

    if IS_WINDOWS:
        result = addin.prepare_and_launch_setup(quiet=True)
        return {"ok": result.get("launched", False), "message": "Windows asks once to trust the ReviewHunter "
                "certificate (it only covers this computer's localhost): click Yes. Then restart Word: "
                "References > ReviewHunter."}
    if IS_MAC:
        r = addin.install_mac()
        return {"ok": r["ok"], "message": ("Restart Word, then Home > Add-ins > ReviewHunter." if r["ok"]
                                           else f"The certificate was not trusted: {r['error']}")}
    return {"ok": False, "message": "Microsoft Word for desktop exists only on Windows and macOS."}


INSTALLERS = {"firefox": install_firefox, "safari": install_safari, "word": install_word,
              "chrome": lambda: install_chromium("chrome"), "edge": lambda: install_chromium("edge"),
              "brave": lambda: install_chromium("brave")}


def install(name: str) -> dict:
    if name not in INSTALLERS:
        raise ValueError(f"unknown integration {name!r}")
    return INSTALLERS[name]()


def _notify(title: str, message: str) -> None:
    """Shortcuts run without a console: show the outcome."""
    try:
        if IS_WINDOWS:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
        elif IS_MAC:
            esc = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display dialog "{esc}" with title "{title}" buttons {{"OK"}}'],
                           capture_output=True, timeout=600)
        else:
            print(f"{title}: {message}")
    except Exception:
        print(f"{title}: {message}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    name = (argv or ["status"])[0]
    if name == "status":
        print(json.dumps(status(), indent=2))
        return 0
    result = install(name)
    if name != "word" or not IS_WINDOWS:   # the Word setup shows its own final dialog
        _notify(f"ReviewHunter for {name.title()}", result.get("message", ""))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
