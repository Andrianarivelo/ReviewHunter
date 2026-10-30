"""Shortcuts that always start THIS installation (Windows, macOS, Linux).

Every shortcut runs the same command, the launcher of the install folder it belongs to::

    <install>/.venv/(Scripts/pythonw.exe | bin/python) -m doi_hunter_web.launcher     (working dir <install>)

    Windows  Desktop\\ReviewHunter.lnk, Start Menu\\Programs\\ReviewHunter.lnk
    macOS    ~/Applications/ReviewHunter.app (Launchpad, Spotlight, Dock) + optional Desktop link to it
    Linux    ~/.local/share/applications/reviewhunter.desktop + optional Desktop copy

``ensure()`` is called by every launch (and by the installer and Settings > Advanced): shortcuts that
point to another folder, an old launch method or a deleted interpreter are rewritten, and if no
shortcut exists at all the application-menu one is created again. Shortcuts the user deleted while
another one still exists are not recreated. Nothing is done for throw-away installs (temporary
folders, tests) or when ``REVIEWHUNTER_NO_SHORTCUTS=1``.

Command line (used by the installer)::

    python -m doi_hunter_web.shortcuts --create menu desktop     # prints a JSON report
    python -m doi_hunter_web.shortcuts --status
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from . import __version__

APP_NAME = "ReviewHunter"
ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
LAUNCH_ARGS = ["-m", "doi_hunter_web.launcher"]
BUNDLE_ID = "org.reviewhunter.app"
ICON_PNG = Path("browser_extension") / "icons" / "icon-128.png"
ICON_PNG_LARGE = Path("doi_hunter_web") / "static" / "icons" / "icon-512.png"   # sharp macOS app icon


# ======================================================================================
# The command every shortcut runs
# ======================================================================================

def launcher_python(root: Path = ROOT) -> Path:
    """Interpreter of the install's private environment (window-less on Windows: no console flashes)."""
    if IS_WINDOWS:
        for cand in (root / ".venv" / "Scripts" / "pythonw.exe", Path(sys.executable).with_name("pythonw.exe")):
            if cand.exists():
                return cand
        return Path(sys.executable)
    cand = root / ".venv" / "bin" / "python"
    return cand if cand.exists() else Path(sys.executable)


def skip_reason(root: Path = ROOT) -> Optional[str]:
    """Shortcuts must never be pointed at tests, installer self-tests or temporary copies."""
    if os.environ.get("REVIEWHUNTER_NO_SHORTCUTS") == "1":
        return "disabled by REVIEWHUNTER_NO_SHORTCUTS"
    try:
        Path(root).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        return "temporary folder"
    except ValueError:
        pass
    if not (Path(root) / "doi_hunter_web").is_dir():
        return "not a ReviewHunter folder"
    return None


def _same(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (TypeError, ValueError):
        return False


# ======================================================================================
# Icons (pure Python: no Pillow, no iconutil)
# ======================================================================================

def _png_size(data: bytes) -> tuple[int, int]:
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def write_ico(png: Path, ico: Path) -> Path:
    """Wrap a PNG in an .ico container (PNG-compressed icons are valid since Windows Vista)."""
    data = png.read_bytes()
    w, h = _png_size(data)
    header = (0).to_bytes(2, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    entry = bytes([w % 256, h % 256, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little") \
        + len(data).to_bytes(4, "little") + (6 + 16).to_bytes(4, "little")
    ico.parent.mkdir(parents=True, exist_ok=True)
    ico.write_bytes(header + entry + data)
    return ico


def write_icns(png: Path, icns: Path) -> Path:
    """Apple icon file holding the PNG as-is ('ic07' = 128x128 PNG, supported since macOS 10.7)."""
    data = png.read_bytes()
    kind = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07", 256: b"ic08", 512: b"ic09"}.get(_png_size(data)[0], b"ic07")
    element = kind + (8 + len(data)).to_bytes(4, "big") + data
    icns.parent.mkdir(parents=True, exist_ok=True)
    icns.write_bytes(b"icns" + (8 + len(element)).to_bytes(4, "big") + element)
    return icns


# ======================================================================================
# Locations
# ======================================================================================

def _powershell(script: str, env: Optional[dict] = None) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
        capture_output=True, timeout=60, env={**os.environ, **(env or {})},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout.decode("utf-8", "replace")


def _linux_desktop_dir() -> Path:
    if shutil.which("xdg-user-dir"):
        try:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                return Path(out)
        except (OSError, subprocess.SubprocessError):
            pass
    return Path.home() / "Desktop"


def default_places() -> dict[str, Path]:
    """Folder of each shortcut kind ("menu" = Start menu / Applications / application menu)."""
    if IS_WINDOWS:
        raw = _powershell("@{desktop=[Environment]::GetFolderPath('Desktop'); "
                          "menu=[Environment]::GetFolderPath('Programs')} | ConvertTo-Json -Compress")
        try:
            data = json.loads(raw)
            return {"menu": Path(data["menu"]), "desktop": Path(data["desktop"])}
        except (ValueError, KeyError, TypeError):
            appdata = Path(os.environ.get("APPDATA", Path.home()))
            return {"menu": appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                    "desktop": Path.home() / "Desktop"}
    if IS_MAC:
        return {"menu": Path.home() / "Applications", "desktop": Path.home() / "Desktop"}
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return {"menu": data_home / "applications", "desktop": _linux_desktop_dir()}


def shortcut_path(kind: str, place: Path) -> Path:
    if IS_WINDOWS:
        return place / f"{APP_NAME}.lnk"
    if IS_MAC:
        return place / f"{APP_NAME}.app"
    return place / "reviewhunter.desktop"


# ======================================================================================
# Reading what a shortcut points to
# ======================================================================================

def _read_windows_links(paths: list[Path]) -> dict[str, dict]:
    existing = [str(p) for p in paths if p.exists()]
    if not existing:
        return {}
    script = ("$sh = New-Object -ComObject WScript.Shell; $out = @{}; "
              "foreach ($f in ($env:RH_LINKS -split '\\|')) { $s = $sh.CreateShortcut($f); "
              "$out[$f] = @{target=$s.TargetPath; args=$s.Arguments; workdir=$s.WorkingDirectory} }; "
              "$out | ConvertTo-Json -Compress -Depth 3")
    try:
        return json.loads(_powershell(script, {"RH_LINKS": "|".join(existing)}) or "{}")
    except ValueError:
        return {}


def _mac_bundle_value(bundle: Path, name: str) -> Optional[str]:
    """A RH_ROOT / RH_PY / RH_ARCH value of the bundle script (single-quoted shell string)."""
    script = bundle / "Contents" / "MacOS" / APP_NAME
    try:
        m = re.search(rf"^{name}='((?:[^']|'\\'')*)'$", script.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1).replace("'\\''", "'") if m else None


def _mac_bundle_root(bundle: Path) -> Optional[str]:
    return _mac_bundle_value(bundle, "RH_ROOT")


def _linux_entry_fields(entry: Path) -> dict[str, str]:
    try:
        text = entry.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^(Exec|Path)=(.*)$", text, re.M)}


def describe(kind: str, path: Path, root: Path = ROOT, windows_info: Optional[dict] = None) -> dict:
    """Status of one shortcut: does it exist, and does it start the launcher of ``root``?"""
    out = {"kind": kind, "path": str(path), "exists": path.exists() or path.is_symlink(), "linked": False,
           "points_to": ""}
    if not out["exists"]:
        return out
    if IS_WINDOWS:
        link = (windows_info or {}).get(str(path)) or {}
        out["points_to"] = f"{link.get('target', '')} {link.get('args', '')}".strip()
        out["linked"] = (_same(link.get("target", ""), launcher_python(root)) and Path(link.get("target", "")).exists()
                         and "doi_hunter_web.launcher" in (link.get("args") or "")
                         and _same(link.get("workdir", ""), root))
    elif IS_MAC:
        bundle = path.resolve() if path.is_symlink() else path
        bundle_root = _mac_bundle_root(bundle)
        bundle_py = _mac_bundle_value(bundle, "RH_PY")
        out["points_to"] = bundle_root or ""
        # Linked = this folder, the interpreter the other platforms would use, and the right architecture
        # (bundles written by 2.9.0 before RH_PY / RH_ARCH existed are rewritten once).
        out["linked"] = (bool(bundle_root) and _same(bundle_root, root) and bool(bundle_py)
                         and _same(bundle_py, launcher_python(root)) and Path(bundle_py).exists()
                         and (_mac_bundle_value(bundle, "RH_ARCH") or "") == (venv_arch(root) or ""))
        if kind == "desktop" and path.is_symlink():   # the Desktop item is a link to the Applications bundle
            out["linked"] = out["linked"] and path.resolve().exists()
    else:
        fields = _linux_entry_fields(path)
        out["points_to"] = fields.get("Exec", "")
        out["linked"] = (str(launcher_python(root)) in fields.get("Exec", "")
                         and "doi_hunter_web.launcher" in fields.get("Exec", "")
                         and _same(fields.get("Path", ""), root))
    return out


def status(root: Path = ROOT, places: Optional[dict[str, Path]] = None) -> list[dict]:
    places = places or default_places()
    paths = {kind: shortcut_path(kind, place) for kind, place in places.items()}
    info = _read_windows_links(list(paths.values())) if IS_WINDOWS else None
    return [describe(kind, p, root, info) for kind, p in paths.items()]


# ======================================================================================
# Writing shortcuts
# ======================================================================================

def _write_windows(lnk: Path, root: Path) -> None:
    icon = root / "installer" / "reviewhunter.ico"
    if not icon.exists() and (root / ICON_PNG).exists():
        write_ico(root / ICON_PNG, icon)
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:RH_LNK);"
              "$s.TargetPath = $env:RH_TARGET; $s.Arguments = $env:RH_ARGS; $s.WorkingDirectory = $env:RH_WORKDIR;"
              "$s.Description = 'ReviewHunter reference manager'; if (Test-Path $env:RH_ICON) { $s.IconLocation = $env:RH_ICON };"
              "$s.WindowStyle = 1; $s.Save()")
    _powershell(script, {"RH_LNK": str(lnk), "RH_TARGET": str(launcher_python(root)), "RH_ARGS": " ".join(LAUNCH_ARGS),
                         "RH_WORKDIR": str(root), "RH_ICON": str(icon)})
    if not lnk.exists():
        raise OSError(f"could not create {lnk}")


def macho_arch(path: Path) -> Optional[str]:
    """CPU architecture of a Mach-O file: 'arm64', 'x86_64', or None (universal / unknown)."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if len(head) < 8 or int.from_bytes(head[:4], "little") != 0xFEEDFACF:   # thin 64-bit Mach-O only
        return None
    return {0x0100000C: "arm64", 0x01000007: "x86_64"}.get(int.from_bytes(head[4:8], "little"))


def venv_arch(root: Path) -> Optional[str]:
    """Architecture the installed packages were compiled for (from a compiled module in the venv).

    Why it matters: Finder / LaunchServices may run a script-based .app under Rosetta (x86_64). A
    universal python.org Python then starts as x86_64 and cannot load the arm64 packages pip installed.
    """
    for so in sorted((Path(root) / ".venv").glob("lib/python3*/site-packages/pydantic_core/_pydantic_core*.so")):
        return macho_arch(so)
    return None


def _sh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def mac_bundle_files(root: Path, python: Optional[Path] = None, arch: Optional[str] = None) -> dict[str, str]:
    """Text files of the macOS .app (kept separate so tests can check them on any OS)."""
    python = python or launcher_python(root)
    arch = arch if arch is not None else (venv_arch(root) or "")
    script = (
        "#!/bin/bash\n"
        "# ReviewHunter launcher, generated by ReviewHunter (repaired automatically, do not edit).\n"
        # Started from Finder / Launchpad there is no terminal: keep a trace of every launch and its errors.
        'mkdir -p "$HOME/ReviewHunter/logs" 2>/dev/null && exec >>"$HOME/ReviewHunter/logs/launcher.log" 2>&1\n'
        'echo "$(date \'+%Y-%m-%d %H:%M:%S\') ReviewHunter.app $*"\n'
        f"RH_ROOT={_sh_quote(root)}\n"
        f"RH_PY={_sh_quote(python)}\n"
        f"RH_ARCH={_sh_quote(arch)}\n"
        'cd "$RH_ROOT" || { osascript -e \'display alert "ReviewHunter" message "The program folder was moved or '
        "deleted. Install ReviewHunter again.\"'; exit 1; }\n"
        # Run Python in the architecture its packages were installed for (never Rosetta by accident).
        'if [ -n "$RH_ARCH" ] && [ -x /usr/bin/arch ]; then\n'
        f'    exec /usr/bin/arch -"$RH_ARCH" "$RH_PY" {" ".join(LAUNCH_ARGS)} "$@"\n'
        "fi\n"
        f'exec "$RH_PY" {" ".join(LAUNCH_ARGS)} "$@"\n'
    )
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>CFBundleName</key><string>{APP_NAME}</string>\n"
        f"  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>\n"
        f"  <key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>\n"
        f"  <key>CFBundleExecutable</key><string>{APP_NAME}</string>\n"
        f"  <key>CFBundleIconFile</key><string>{APP_NAME}.icns</string>\n"
        "  <key>CFBundlePackageType</key><string>APPL</string>\n"
        f"  <key>CFBundleShortVersionString</key><string>{__version__}</string>\n"
        "  <key>LSMinimumSystemVersion</key><string>10.13</string>\n"
        # Native on Apple silicon: Rosetta would start a universal Python as x86_64 (see venv_arch).
        "  <key>LSRequiresNativeExecution</key><true/>\n"
        "  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>\n"
        # The launcher starts the server in the background and exits within seconds: no Dock icon that
        # would bounce and then be reported as "not responding".
        "  <key>LSUIElement</key><true/>\n"
        "  <key>NSHighResolutionCapable</key><true/>\n"
        "</dict></plist>\n"
    )
    return {"Contents/MacOS/" + APP_NAME: script, "Contents/Info.plist": plist}


def _write_mac_bundle(bundle: Path, root: Path) -> None:
    tmp = bundle.with_name(f".{bundle.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, text in mac_bundle_files(root).items():
        f = tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
    (tmp / "Contents" / "MacOS" / APP_NAME).chmod(0o755)
    icon_src = next((root / p for p in (ICON_PNG_LARGE, ICON_PNG) if (root / p).exists()), None)
    if icon_src:
        write_icns(icon_src, tmp / "Contents" / "Resources" / f"{APP_NAME}.icns")
    if bundle.is_symlink():
        bundle.unlink()
    shutil.rmtree(bundle, ignore_errors=True)
    tmp.rename(bundle)
    if IS_MAC:   # make Finder / Launchpad pick up the (new) icon and executable
        subprocess.run(["touch", str(bundle)], capture_output=True)


def linux_entry(root: Path) -> str:
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME}\nComment=Reference manager: library, PDFs, citations\n"
            f'Exec="{launcher_python(root)}" {" ".join(LAUNCH_ARGS)}\n'
            f"Path={root}\nIcon={root / ICON_PNG}\nTerminal=false\n"
            "Categories=Office;Education;Science;\nStartupNotify=false\n")


def _write_linux(entry: Path, root: Path) -> None:
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(linux_entry(root), encoding="utf-8")
    entry.chmod(0o755)
    if shutil.which("gio"):   # GNOME: trusted launcher, starts on double-click
        subprocess.run(["gio", "set", str(entry), "metadata::trusted", "true"], capture_output=True)


def write(kind: str, path: Path, root: Path, places: dict[str, Path]) -> None:
    if IS_WINDOWS:
        _write_windows(path, root)
    elif IS_MAC:
        if kind == "desktop":
            # A link to the Applications bundle: one bundle, two places, never out of sync.
            bundle = shortcut_path("menu", places["menu"])
            if not describe("menu", bundle, root)["linked"]:
                _write_mac_bundle(bundle, root)
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(bundle)
        else:
            _write_mac_bundle(path, root)
    else:
        _write_linux(path, root)


def ensure(root: Path = ROOT, *, create: Iterable[str] = (), places: Optional[dict[str, Path]] = None,
           keep_one: bool = True) -> dict:
    """Repair every existing shortcut that does not start ``root``; create the kinds listed in ``create``.

    ``keep_one``: when no shortcut exists at all, create the application-menu one, so the app can
    always be found again. Returns {"skipped" | "repaired", "created", "ok", "shortcuts"}.
    """
    root = Path(root).resolve()
    reason = skip_reason(root)
    if reason:
        return {"skipped": reason, "repaired": [], "created": [], "shortcuts": []}
    places = places or default_places()
    if IS_WINDOWS:
        # Linked .lnk files are not rewritten, but they show installer/reviewhunter.ico: keep that file in
        # step with the logo (new logo, deleted file) so every existing shortcut shows the current icon.
        png, ico = root / ICON_PNG, root / "installer" / "reviewhunter.ico"
        if png.exists() and (not ico.exists() or ico.stat().st_mtime < png.stat().st_mtime):
            write_ico(png, ico)
    current = status(root, places)
    create = set(create)
    if keep_one and not any(s["exists"] for s in current):
        create.add("menu")
    repaired, created, errors = [], [], []
    # The application-menu entry first: on macOS the Desktop item links to it.
    for s in sorted(current, key=lambda s: s["kind"] != "menu"):
        if IS_MAC and s["kind"] == "desktop" and s["exists"]:
            s = describe("desktop", Path(s["path"]), root)   # links to the bundle repaired just before
        if s["exists"] and s["linked"]:
            continue
        if not s["exists"] and s["kind"] not in create:
            continue
        try:
            write(s["kind"], Path(s["path"]), root, places)
            (repaired if s["exists"] else created).append(s["path"])
        except OSError as e:
            errors.append(f"{s['path']}: {e}")
    final = status(root, places)
    return {"repaired": repaired, "created": created, "errors": errors, "shortcuts": final,
            "ok": not errors and all(s["linked"] for s in final if s["exists"])}


def remove(places: Optional[dict[str, Path]] = None) -> list[str]:
    """Delete ReviewHunter shortcuts (uninstaller)."""
    removed = []
    for kind, place in (places or default_places()).items():
        p = shortcut_path(kind, place)
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
        else:
            continue
        removed.append(str(p))
    return removed


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create, check or repair ReviewHunter shortcuts.")
    parser.add_argument("--create", nargs="*", default=None, choices=["menu", "desktop"])
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(status(), indent=2))
        return 0
    result = ensure(create=args.create or (), keep_one=True)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") or result.get("skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
