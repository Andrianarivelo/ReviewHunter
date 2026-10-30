"""Start ReviewHunter from the Chrome extension (Chrome native messaging).

A browser extension cannot start programs. Chrome's *native messaging* lets it ask one registered
helper: Chrome itself runs the helper, sends it one JSON message on stdin and reads one answer on
stdout (each prefixed with its length as a 4-byte little-endian integer), then the helper exits.

    extension  --{"cmd": "start"}-->  Chrome  --runs-->  <install>/native_host/reviewhunter_host(.bat|.sh)
                                                            = python -m doi_hunter_web.native_host
               <--{"ok": true, "state": "started", "url": ...}--

``start`` uses the launcher's rules (launcher.py): an instance of this install and code is reused, a
stale one is replaced, and the server is started detached, so it keeps running when the helper exits
(verified with a real Chromium: the server survives the helper and Chrome quitting).

Registration ("host manifest") for Google Chrome, Microsoft Edge, Brave and Chromium, per user, no
administrator rights:
    Windows  HKCU\\Software\\<browser>\\NativeMessagingHosts\\org.reviewhunter.launcher -> manifest path
    macOS    ~/Library/Application Support/<browser>/NativeMessagingHosts/org.reviewhunter.launcher.json
    Linux    ~/.config/<browser>/NativeMessagingHosts/org.reviewhunter.launcher.json
Only the ReviewHunter connector may use it (``allowed_origins``). Its ID is known without asking the
browser: an unpacked extension's ID is derived from its folder path (``extension_path_id``, the same
computation as Chromium, verified against IDs Chromium assigned). IDs the connector reports to the app
page (loaded from another folder) are added too. No fixed key in the extension manifest: tested in
Chromium (Developer mode on), reloading a connector 1.2.2 whose files became 1.3.0 WITH a key installed
a second extension (new ID, settings lost) and left the old one disabled; WITHOUT a key it was upgraded
in place. Like the shortcuts, the registration is checked at every launch and repaired when it points
to another install.

Command line:  python -m doi_hunter_web.native_host --register | --unregister | --status
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
import time
from pathlib import Path
from typing import Optional

from . import __version__

HOST_NAME = "org.reviewhunter.launcher"
ROOT = Path(__file__).resolve().parents[1]
_ID_RE = re.compile(r"^[a-p]{32}$")
MAX_KNOWN_IDS = 8
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
MAX_MESSAGE = 64 * 1024

# Per-browser locations of native messaging host registrations (user level).
WINDOWS_KEYS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "chromium": r"Software\Chromium\NativeMessagingHosts",
}
MAC_DIRS = {"chrome": "Google/Chrome", "edge": "Microsoft Edge", "brave": "BraveSoftware/Brave-Browser",
            "chromium": "Chromium"}
LINUX_DIRS = {"chrome": "google-chrome", "edge": "microsoft-edge", "brave": "BraveSoftware/Brave-Browser",
              "chromium": "chromium"}


# ======================================================================================
# Protocol (stdin / stdout)
# ======================================================================================

def read_message(stream) -> Optional[dict]:
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    size = struct.unpack("<I", raw)[0]
    if size > MAX_MESSAGE:
        raise ValueError("message too large")
    return json.loads(stream.read(size).decode("utf-8"))


def write_message(stream, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def _log(line: str) -> None:
    try:
        from . import config

        folder = config.library_root() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "native_host.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def handle(message: dict) -> dict:
    """Answer one request. Never raises: errors are answers too."""
    from . import config, launcher

    cmd = (message or {}).get("cmd")
    url = f"http://{config.HOST}:{config.PORT}"
    base = {"host": HOST_NAME, "host_version": __version__, "root": str(ROOT), "url": url}
    try:
        if cmd == "hello":
            found = launcher.probe(config.HOST, config.PORT)
            return {**base, "ok": True, "running": launcher.classify(found) == "same"}
        if cmd != "start":
            return {**base, "ok": False, "error": f"unknown command {cmd!r}"}
        found = launcher.probe(config.HOST, config.PORT)
        state = launcher.classify(found)
        if state == "same":
            return {**base, "ok": True, "state": "running", "version": found.get("version")}
        if state == "foreign":
            return {**base, "ok": False, "error": f"port {config.PORT} is used by another program"}
        replaced = state == "stale"
        if replaced and not launcher.stop_instance(config.HOST, config.PORT, found):
            return {**base, "ok": False, "error": "an older ReviewHunter could not be stopped"}
        proc = launcher.start_background()
        ok, why = launcher.wait_ready(config.HOST, config.PORT, proc)
        _log(f"start: ok={ok} replaced={replaced} {why}")
        if not ok:
            return {**base, "ok": False, "error": f"ReviewHunter did not start: {why}",
                    "log": str(launcher.log_path())}
        return {**base, "ok": True, "state": "replaced" if replaced else "started", "version": __version__}
    except Exception as e:   # the extension shows this text
        _log(f"error: {e!r}")
        return {**base, "ok": False, "error": str(e) or e.__class__.__name__}


def serve() -> int:
    """One request per run, as Chrome's sendNativeMessage does."""
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr            # nothing else may ever write to the protocol stream
    try:
        message = read_message(stdin)
    except (ValueError, json.JSONDecodeError) as e:
        write_message(stdout, {"ok": False, "error": f"bad request: {e}"})
        return 1
    if message is None:
        return 0
    write_message(stdout, handle(message))
    return 0


# ======================================================================================
# Registration
# ======================================================================================

def host_dir(root: Path = ROOT) -> Path:
    return Path(root) / "native_host"


def host_program(root: Path = ROOT) -> Path:
    return host_dir(root) / ("reviewhunter_host.bat" if IS_WINDOWS else "reviewhunter_host.sh")


def extension_path_id(folder: str | Path) -> str:
    """ID Chromium gives an unpacked extension loaded from ``folder`` (crx_file::id_util::GenerateIdForPath).

    sha256 of the absolute path (Windows: UTF-16LE with an upper-case drive letter; elsewhere: the UTF-8
    bytes), first 16 bytes, each hex digit mapped 0-f -> a-p.
    """
    path = os.path.abspath(str(folder))
    if IS_WINDOWS:
        if len(path) > 1 and path[1] == ":":
            path = path[0].upper() + path[1:]
        data = path.encode("utf-16-le")
    else:
        data = path.encode("utf-8")
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(data).hexdigest()[:32])


def known_extension_ids() -> list[str]:
    """IDs the connector itself reported to the app page (connector loaded from another folder)."""
    try:
        from . import config

        return [i for i in (config.get_settings().get("connector_extension_ids") or []) if _ID_RE.match(str(i))]
    except Exception:
        return []


def allowed_ids(root: Path = ROOT) -> list[str]:
    ids = [extension_path_id(Path(root) / "browser_extension")]
    return ids + [i for i in known_extension_ids() if i not in ids]


def remember_extension_id(extension_id: str, root: Path = ROOT) -> dict:
    """Trust a connector ID reported by the app page, then rewrite the registration if it changed."""
    from . import config

    if not _ID_RE.match(extension_id or ""):
        raise ValueError("not a Chrome extension id")
    ids = known_extension_ids()
    if extension_id in allowed_ids(root):
        return {"ok": True, "added": False}
    config.update_settings({"connector_extension_ids": ([extension_id] + ids)[:MAX_KNOWN_IDS]})
    return {"ok": True, "added": True, "registration": register(root)}


def host_manifest(root: Path = ROOT) -> dict:
    return {"name": HOST_NAME, "description": "Starts ReviewHunter for its browser extension",
            "path": str(host_program(root)), "type": "stdio",
            "allowed_origins": [f"chrome-extension://{i}/" for i in allowed_ids(root)]}


def _program_text(root: Path) -> str:
    from .shortcuts import launcher_python, venv_arch

    if IS_WINDOWS:
        # Console python.exe: native messaging needs stdin/stdout (Chrome hides the window).
        py = Path(root) / ".venv" / "Scripts" / "python.exe"
        py = py if py.exists() else Path(sys.executable).with_name("python.exe")
        return f'@echo off\r\ncd /d "{root}"\r\n"{py}" -m doi_hunter_web.native_host %*\r\n'
    arch = venv_arch(root) if IS_MAC else None
    run = f'/usr/bin/arch -{arch} ' if arch else ""
    q = lambda v: "'" + str(v).replace("'", "'\\''") + "'"  # noqa: E731
    return (f"#!/bin/bash\n# ReviewHunter native messaging host (generated, repaired automatically).\n"
            f"cd {q(root)} || exit 1\nexec {run}{q(launcher_python(root))} -m doi_hunter_web.native_host \"$@\"\n")


def _manifest_dirs(home: Optional[Path] = None) -> dict[str, Path]:
    home = home or Path.home()
    if IS_MAC:
        base = home / "Library" / "Application Support"
        return {b: base / d / "NativeMessagingHosts" for b, d in MAC_DIRS.items()}
    base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return {b: base / d / "NativeMessagingHosts" for b, d in LINUX_DIRS.items()}


def _write_files(root: Path) -> Path:
    """Host program + manifest inside the install (the manifest is copied / referenced per browser)."""
    folder = host_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    program = host_program(root)
    program.write_bytes(_program_text(root).encode("utf-8"))
    if not IS_WINDOWS:
        program.chmod(0o755)
    manifest = folder / f"{HOST_NAME}.json"
    manifest.write_text(json.dumps(host_manifest(root), indent=2), encoding="utf-8")
    return manifest


def status(root: Path = ROOT, *, keys: Optional[dict] = None, dirs: Optional[dict] = None) -> list[dict]:
    """Per browser: is the host registered, and for this install?"""
    out = []
    expected = host_manifest(root)
    program_ok = host_program(root).exists() and host_program(root).read_bytes() == _program_text(root).encode("utf-8")
    if IS_WINDOWS:
        import winreg

        own = host_dir(root) / f"{HOST_NAME}.json"
        for browser, key in (keys or WINDOWS_KEYS).items():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}") as k:
                    value = winreg.QueryValueEx(k, "")[0]
            except OSError:
                value = ""
            try:
                content = json.loads(Path(value).read_text(encoding="utf-8")) if value else None
            except (OSError, ValueError):
                content = None
            out.append({"browser": browser, "where": rf"HKCU\{key}\{HOST_NAME}", "registered": bool(value),
                        "linked": bool(value) and os.path.normcase(value) == os.path.normcase(str(own))
                        and content == expected and program_ok})
        return out
    for browser, folder in (dirs or _manifest_dirs()).items():
        f = folder / f"{HOST_NAME}.json"
        try:
            content = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            content = None
        out.append({"browser": browser, "where": str(f), "registered": content is not None,
                    "linked": content == expected and program_ok})
    return out


def register(root: Path = ROOT, *, keys: Optional[dict] = None, dirs: Optional[dict] = None) -> dict:
    """Write / repair the registration for every supported browser. Idempotent."""
    from .shortcuts import skip_reason

    root = Path(root).resolve()
    reason = skip_reason(root)
    if reason:
        return {"skipped": reason, "browsers": []}
    if all(s["linked"] for s in status(root, keys=keys, dirs=dirs)):
        return {"ok": True, "changed": [], "browsers": status(root, keys=keys, dirs=dirs)}
    manifest = _write_files(root)
    changed, errors = [], []
    if IS_WINDOWS:
        import winreg

        for browser, key in (keys or WINDOWS_KEYS).items():
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}") as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(manifest))
                changed.append(browser)
            except OSError as e:
                errors.append(f"{browser}: {e}")
    else:
        text = json.dumps(host_manifest(root), indent=2)
        for browser, folder in (dirs or _manifest_dirs()).items():
            # Only browsers that exist (their profile folder): do not create folders for the others.
            if not folder.parent.exists():
                continue
            try:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{HOST_NAME}.json").write_text(text, encoding="utf-8")
                changed.append(browser)
            except OSError as e:
                errors.append(f"{browser}: {e}")
    final = status(root, keys=keys, dirs=dirs)
    return {"ok": not errors, "changed": changed, "errors": errors, "browsers": final}


def unregister(root: Path = ROOT, *, keys: Optional[dict] = None, dirs: Optional[dict] = None) -> list[str]:
    """Remove the registrations that point to ``root`` (uninstaller)."""
    removed = []
    for s in status(root, keys=keys, dirs=dirs):
        if not s["registered"]:
            continue
        if IS_WINDOWS:
            import winreg

            key = (keys or WINDOWS_KEYS)[s["browser"]]
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}")
                removed.append(s["where"])
            except OSError:
                pass
        else:
            try:
                Path(s["where"]).unlink()
                removed.append(s["where"])
            except OSError:
                pass
    return removed


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--register" in argv:
        result = register()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") or result.get("skipped") else 1
    if "--unregister" in argv:
        print(json.dumps({"removed": unregister()}, indent=2))
        return 0
    if "--status" in argv:
        print(json.dumps(status(), indent=2))
        return 0
    # Chrome passes the extension origin (and on Windows --parent-window=...) as arguments.
    return serve()


if __name__ == "__main__":
    sys.exit(main())
