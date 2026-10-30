#!/usr/bin/env python3
"""ReviewHunter installer (Windows, macOS and Linux) - standard library only.

What it does, step by step (every step reports progress to a window or to the terminal):

    1. Check the system   Python >= 3.10, venv/pip available, write access, free disk, internet
    2. Get the app files  copy from the folder this installer ships in, or download the latest release
    3. Python environment create (or reuse) a private virtual environment ``<install dir>/.venv``
    4. Dependencies       pip install -r requirements.txt, progress parsed from pip's output
    5. Optional extras    semantic clustering (sentence-transformers, CPU build of PyTorch)
    6. Self-test          imports + starts the app on a free port with a throw-away library and
                          waits for ``/connector/ping`` to answer (a real end-to-end check)
    7. Chrome extension   validate bundled connector, prepare setup launcher and instructions
    8. Shortcuts          Windows: Desktop + Start menu (.lnk); macOS: ~/Applications/ReviewHunter.app;
                          Linux: application menu + Desktop (.desktop). All run the launcher, which
                          keeps them pointing at this install (doi_hunter_web/shortcuts.py)
    9. Finish             writes ``install.json`` and opens Chrome setup when requested

Your library (``~/ReviewHunter``: database, PDFs, settings) is never created, modified or deleted by
the installer or the uninstaller: only the program folder and the shortcuts are managed here.

Usage
    python reviewhunter_installer.py                 # graphical installer (falls back to terminal mode)
    python reviewhunter_installer.py --cli --yes     # unattended terminal install with defaults
    python reviewhunter_installer.py --uninstall     # remove program folder + shortcuts (keeps your library)
    python reviewhunter_installer.py --help          # all options
"""
from __future__ import annotations

# ============================================================================ PARAMETERS
APP_NAME = "ReviewHunter"
GITHUB_REPO = "Andrianarivelo/ReviewHunter"
RELEASE_ZIP_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/ReviewHunter.zip"
MIN_PYTHON = (3, 10)
MIN_FREE_GB = 1.5                  # base install (venv + dependencies) is ~350 MB; keep a margin
MIN_FREE_GB_SEMANTIC = 4.0         # PyTorch CPU + sentence-transformers models
SELFTEST_TIMEOUT_S = 90            # first start compiles bytecode and creates the database
PIP_EXPECTED_PACKAGES = 60         # rough count of resolved packages, only used to scale the progress bar
REQUIRED_MODULES = ("fastapi", "uvicorn", "requests", "bs4", "pydantic", "libgen", "multipart", "pypdf",
                    "fontTools", "curl_cffi", "bibtexparser", "cryptography", "google_auth_oauthlib", "googleapiclient")
# Files and folders never copied into the install folder (dev artefacts, secrets, local data).
COPY_EXCLUDE = {".git", ".venv", "venv", "secret", "build", "dist", "downloads", "__pycache__", ".pytest_cache",
                "certs", "PROJECT_MEMORY.md", "error.log", "failed_downloads.txt", "paper_titles.txt",
                ".claude", "shots", "node_modules", "native_host"}
COPY_EXCLUDE_SUFFIXES = (".egg-info", ".pyc", ".log", ".pem")
# Program sub-folders replaced on upgrade (so files deleted upstream do not linger). .venv is kept.
CODE_DIRS = ("doi_hunter_web", "doi_hunter", "browser_extension", "word_addin", "scripts", "tools", "installer")

# Window styling (matches the app: slate text, sky-blue accent)
UI = {
    "bg": "#f8fafc", "panel": "#ffffff", "text": "#0f172a", "muted": "#64748b", "accent": "#0284c7",
    "accent_dark": "#0369a1", "ok": "#059669", "error": "#dc2626", "border": "#e2e8f0",
    "font": ("Segoe UI", 10), "font_title": ("Segoe UI Semibold", 18), "font_step": ("Segoe UI", 10),
    "font_mono": ("Consolas", 9), "width": 760, "height": 650,
}
# ============================================================================

import argparse
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
HERE = Path(__file__).resolve().parent
LOG_FILE = Path(tempfile.gettempdir()) / "reviewhunter-install.log"


# ---------------------------------------------------------------------------- locations

def default_install_dir() -> Path:
    """Per-user program folder: no administrator rights needed on any OS.

    macOS: ~/Library/ReviewHunter (no space in the path, like Linux' ~/.local/share); the small
    ReviewHunter.app launcher goes to ~/Applications, where Launchpad and Spotlight find it.
    """
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / APP_NAME
    if IS_MAC:
        return Path.home() / "Library" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "reviewhunter"


def urlopen_verified(url, timeout: float):
    """urlopen that still verifies certificates when this Python has no CA store configured.

    python.org builds on macOS ship without one until "Install Certificates.command" is run, and some
    standalone builds look in OpenSSL's default folder. Retry with the bundles that exist instead of
    ever disabling verification.
    """
    import ssl

    try:
        return urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            raise
        candidates = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]
        for module in ("certifi", "pip._vendor.certifi"):
            try:
                candidates.insert(0, __import__(module, fromlist=["where"]).where())
            except Exception:
                pass
        for cafile in candidates:
            if os.path.isfile(cafile):
                context = ssl.create_default_context(cafile=cafile)
                return urllib.request.urlopen(url, timeout=timeout, context=context)
        raise


def desktop_session() -> bool:
    """True when a browser / window can be opened (not a headless server or CI container)."""
    return IS_WINDOWS or IS_MAC or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def bundled_source() -> Optional[Path]:
    """The app folder this installer ships in (``<app>/installer/this_file.py``), if complete."""
    root = HERE.parent
    if (root / "run.py").exists() and (root / "doi_hunter_web").is_dir() and (root / "requirements.txt").exists():
        return root
    return None


def read_version(root: Path) -> str:
    """Version string from ``doi_hunter_web/__init__.py`` (single source of truth)."""
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "doi_hunter_web" / "__init__.py").read_text("utf-8"))
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def venv_python(target: Path, windowless: bool = False) -> Path:
    if IS_WINDOWS:
        return target / ".venv" / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return target / ".venv" / "bin" / "python"


def console_python() -> str:
    """The console interpreter (python.exe) even when the installer runs under pythonw.exe.

    A GUI-subsystem parent has no console, so every console child it starts (venv -> ensurepip)
    would pop up a black window; python.exe started with CREATE_NO_WINDOW keeps them hidden.
    """
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return sys.executable


def _powershell(script: str, env: Optional[dict] = None) -> str:
    """Run a PowerShell snippet without a visible window; returns stdout."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=60, env={**os.environ, **(env or {})},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout


# ---------------------------------------------------------------------------- progress reporting

@dataclass
class Options:
    """Choices made on the welcome page (or with command-line flags)."""
    target: Path = field(default_factory=default_install_dir)
    source: Optional[Path] = None          # None -> download the latest release
    semantic: bool = False                 # optional semantic clustering extra (large download)
    desktop_shortcut: bool = True
    menu_shortcut: bool = True
    word_addin: bool = False               # Windows only: opens the add-in setup window at the end
    chrome_extension: bool = True          # bundle/setup now, final Load unpacked action belongs to Chrome
    launch: bool = True


class Reporter:
    """Receives progress events. The GUI and the terminal UI both subclass it."""

    def step(self, index: int, title: str) -> None: ...
    def progress(self, fraction: float, detail: str = "") -> None: ...
    def log(self, line: str) -> None: ...
    def step_done(self, index: int, ok: bool, note: str = "") -> None: ...
    def finished(self, ok: bool, message: str) -> None: ...


class InstallError(RuntimeError):
    """A failure with a human-readable explanation and, when known, how to fix it."""


def _log_to_file(line: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------- installer engine

class Installer:
    """Runs the installation steps in order and reports weighted overall progress."""

    def __init__(self, options: Options, reporter: Reporter) -> None:
        self.o = options
        self.r = reporter
        self.cancelled = threading.Event()
        self.version = "unknown"
        self.created_shortcuts: list[str] = []
        self.in_place = False              # True when installing inside the downloaded/cloned folder itself
        self.warnings: list[str] = []
        self.extension: dict = {"status": "not_selected"}
        # (title, weight in the overall bar, function)
        self.steps: list[tuple[str, float, Callable[[], Optional[str]]]] = [
            ("Check the system", 3, self.check_system),
            ("Get the application files", 7, self.get_files),
            ("Create the Python environment", 8, self.create_venv),
            ("Install dependencies", 52, self.install_dependencies),
            ("Install optional extras", 10, self.install_extras),
            ("Test that ReviewHunter starts", 14, self.self_test),
            ("Set up the Chrome extension", 3, self.prepare_chrome_extension),
            ("Create shortcuts", 4, self.create_shortcuts),
            ("Finish", 2, self.finish),
        ]
        self._weights = [w for _, w, _ in self.steps]
        self._current = 0

    # -- helpers -----------------------------------------------------------------------

    def log(self, line: str) -> None:
        _log_to_file(line)
        self.r.log(line)

    def sub_progress(self, fraction: float, detail: str = "") -> None:
        """Progress inside the current step, converted to the overall 0..1 bar."""
        done = sum(self._weights[: self._current])
        overall = (done + self._weights[self._current] * max(0.0, min(1.0, fraction))) / sum(self._weights)
        self.r.progress(overall, detail)

    def run_cmd(self, cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None,
                on_line: Optional[Callable[[str], None]] = None, timeout: Optional[float] = None) -> None:
        """Run a command, streaming each output line to the log (and to ``on_line``)."""
        self.log("$ " + " ".join(str(c) for c in cmd))
        proc = subprocess.Popen(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", **(env or {})},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        tail: list[str] = []
        started = time.time()
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log(line)
                tail = (tail + [line])[-15:]
                if on_line:
                    on_line(line)
            if self.cancelled.is_set():
                proc.kill()
                raise InstallError("Installation cancelled.")
            if timeout and time.time() - started > timeout:
                proc.kill()
                raise InstallError(f"Command timed out after {timeout:.0f} s: {cmd[0]}")
        if proc.wait() != 0:
            raise InstallError(f"Command failed (exit code {proc.returncode}):\n" + "\n".join(tail[-8:]))

    # -- orchestration -----------------------------------------------------------------

    def run(self) -> bool:
        _log_to_file(f"\n===== {APP_NAME} install {datetime.now().isoformat(timespec='seconds')} =====")
        self.log(f"Python {platform.python_version()} ({sys.executable}) on {platform.platform()}")
        for i, (title, _w, fn) in enumerate(self.steps):
            self._current = i
            self.r.step(i, title)
            self.sub_progress(0.0, title)
            try:
                note = fn() or ""
            except InstallError as e:
                self.log(f"ERROR: {e}")
                self.r.step_done(i, False, str(e).splitlines()[0])
                self.r.finished(False, f"{title} failed.\n\n{e}\n\nFull log: {LOG_FILE}")
                return False
            except Exception as e:  # unexpected: keep the traceback in the log for bug reports
                self.log(traceback.format_exc())
                self.r.step_done(i, False, str(e))
                self.r.finished(False, f"{title} failed with an unexpected error:\n{e}\n\nFull log: {LOG_FILE}")
                return False
            self.sub_progress(1.0, title)
            self.r.step_done(i, True, note)
        msg = f"{APP_NAME} {self.version} is installed in\n{self.o.target}"
        if self.warnings:
            msg += "\n\nNotes:\n- " + "\n- ".join(self.warnings)
        self.r.finished(True, msg)
        return True

    # -- steps -------------------------------------------------------------------------

    def check_system(self) -> str:
        if sys.version_info < MIN_PYTHON:
            raise InstallError(f"Python {'.'.join(map(str, MIN_PYTHON))} or newer is required "
                               f"(this is {platform.python_version()}). Use install.sh (macOS, Linux) or "
                               "Install-ReviewHunter.bat (Windows): they install a private Python for you.")
        self.sub_progress(0.2, "Python version OK")
        try:
            import ensurepip  # noqa: F401  (missing on Debian/Ubuntu without python3-venv)
            import venv  # noqa: F401
        except ImportError:
            raise InstallError("Python's 'venv' module is missing.\n"
                               "Ubuntu/Debian: sudo apt install python3-venv\n"
                               "(the install.sh launcher does this automatically)")
        target = self.o.target.expanduser().resolve()
        self.o.target = target
        is_app_folder = (target / "run.py").exists() and (target / "doi_hunter_web").is_dir()
        if target.is_dir() and any(target.iterdir()) and not is_app_folder:
            raise InstallError(f"{target} already contains other files. Choose an empty folder or a new one "
                               f"(for example {target / APP_NAME}).")
        parent = next((p for p in [target, *target.parents] if p.exists()), None)
        if parent is None or not os.access(parent, os.W_OK):
            raise InstallError(f"No permission to write in {parent or target}. Choose another install folder.")
        free_gb = shutil.disk_usage(parent).free / 1e9
        need = MIN_FREE_GB_SEMANTIC if self.o.semantic else MIN_FREE_GB
        if free_gb < need:
            raise InstallError(f"Not enough free disk space on {parent}: {free_gb:.1f} GB free, {need:.1f} GB needed.")
        self.sub_progress(0.6, f"{free_gb:.0f} GB free")
        try:
            urlopen_verified(urllib.request.Request("https://pypi.org/simple/pip/", method="HEAD"), timeout=15)
        except Exception as e:
            raise InstallError(f"Cannot reach the Python package index (pypi.org): {e}\n"
                               "Check your internet connection or proxy settings.")
        return f"Python {platform.python_version()}, {free_gb:.0f} GB free"

    def get_files(self) -> str:
        source = self.o.source
        tmp: Optional[Path] = None
        if source is None:
            tmp = Path(tempfile.mkdtemp(prefix="reviewhunter-dl-"))
            archive = tmp / "ReviewHunter.zip"
            self.log(f"Downloading {RELEASE_ZIP_URL}")
            try:
                with urlopen_verified(RELEASE_ZIP_URL, timeout=60) as resp, archive.open("wb") as out:
                    total = int(resp.headers.get("Content-Length") or 0)
                    got = 0
                    while chunk := resp.read(1 << 16):
                        out.write(chunk)
                        got += len(chunk)
                        if total:
                            self.sub_progress(0.7 * got / total, f"Downloading {got / 1e6:.1f} / {total / 1e6:.1f} MB")
            except urllib.error.HTTPError as e:
                # Private repository: anonymous downloads get 404. The GitHub CLI uses the user's own login.
                if e.code not in (401, 403, 404) or not shutil.which("gh"):
                    raise InstallError(f"Could not download {RELEASE_ZIP_URL} ({e}). If the repository is private, "
                                       "install the GitHub CLI and run 'gh auth login', or download the zip "
                                       "from the Releases page while signed in.")
                self.log("Public download refused: trying the GitHub CLI with your login")
                self.run_cmd(["gh", "release", "download", "--repo", GITHUB_REPO, "--pattern", "ReviewHunter.zip",
                              "--dir", str(tmp), "--clobber"], timeout=600)
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp / "src")
            source = next((p.parent for p in (tmp / "src").rglob("run.py") if (p.parent / "doi_hunter_web").is_dir()),
                          None)
            if source is None:
                raise InstallError("The downloaded release does not contain ReviewHunter.")
        source = source.resolve()
        self.version = read_version(source)
        target = self.o.target
        if source == target:
            self.in_place = True
            self.log("Installing in place (source folder is the install folder).")
            return f"version {self.version}, in place"

        target.mkdir(parents=True, exist_ok=True)
        for d in CODE_DIRS:  # upgrade: replace code folders, keep .venv
            if (target / d).is_dir():
                shutil.rmtree(target / d)
        files = [p for p in source.rglob("*") if p.is_file() and not self._excluded(p.relative_to(source))]
        for n, f in enumerate(files, 1):
            rel = f.relative_to(source)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            if n % 25 == 0 or n == len(files):
                self.sub_progress(0.7 + 0.3 * n / len(files), f"Copying files {n}/{len(files)}")
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        if not IS_WINDOWS:   # zip extraction drops the executable bit
            for script in ("run.sh", "install.sh", "Install ReviewHunter.command"):
                if (target / script).exists():
                    (target / script).chmod(0o755)
        return f"version {self.version}, {len(files)} files"

    @staticmethod
    def _excluded(rel: Path) -> bool:
        for part in rel.parts:
            if part in COPY_EXCLUDE or part.endswith(COPY_EXCLUDE_SUFFIXES[:1]):
                return True
        return rel.name.endswith(COPY_EXCLUDE_SUFFIXES) or rel.name.startswith("client_secret")

    def create_venv(self) -> str:
        py = venv_python(self.o.target)
        if py.exists():
            ok = subprocess.run([str(py), "-c", "import sys; assert sys.version_info >= (3, 10)"],
                                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if ok.returncode == 0:
                return "reusing the existing environment"
            self.log("Existing environment is broken: recreating it.")
            shutil.rmtree(self.o.target / ".venv", ignore_errors=True)
        self.sub_progress(0.2, "Creating the virtual environment")
        self.run_cmd([console_python(), "-m", "venv", str(self.o.target / ".venv")])
        self.sub_progress(0.6, "Updating pip")
        self.run_cmd([str(py), "-m", "pip", "install", "--upgrade", "pip"], timeout=600)
        return "new environment"

    def install_dependencies(self) -> str:
        py = venv_python(self.o.target)
        seen = 0

        def on_line(line: str) -> None:
            nonlocal seen
            m = re.match(r"(Collecting|Requirement already satisfied:|Using cached|Downloading)\s+([^\s(]+)", line)
            if m and m.group(1) in ("Collecting", "Requirement already satisfied:"):
                seen += 1
                name = m.group(2).split("/")[-1][:48]
                self.sub_progress(0.85 * min(1.0, seen / PIP_EXPECTED_PACKAGES), f"{seen} packages: {name}")
            elif line.startswith("Installing collected packages"):
                self.sub_progress(0.9, "Installing the downloaded packages (may take a minute)")
            elif line.startswith("Successfully installed"):
                self.sub_progress(1.0, "Dependencies installed")

        self.run_cmd([str(py), "-m", "pip", "install", "--progress-bar", "off", "-r",
                      str(self.o.target / "requirements.txt")], cwd=self.o.target, on_line=on_line, timeout=3600)
        return f"{seen} packages checked"

    def install_extras(self) -> str:
        if not self.o.semantic:
            return "none selected"
        py = venv_python(self.o.target)
        has_nvidia = bool(shutil.which("nvidia-smi"))
        if not has_nvidia:  # the default PyTorch wheel on Linux bundles ~3 GB of CUDA libraries
            self.sub_progress(0.1, "PyTorch (CPU build)")
            self.run_cmd([str(py), "-m", "pip", "install", "--progress-bar", "off", "torch",
                          "--index-url", "https://download.pytorch.org/whl/cpu"], timeout=3600)
        self.sub_progress(0.6, "sentence-transformers")
        self.run_cmd([str(py), "-m", "pip", "install", "--progress-bar", "off", "sentence-transformers>=2.5",
                      "numpy>=1.24"], timeout=3600)
        return "semantic clustering" + ("" if has_nvidia else " (CPU)")

    def self_test(self) -> str:
        py = venv_python(self.o.target)
        probe = "; ".join(f"import {m}" for m in REQUIRED_MODULES) + "; import doi_hunter_web.server; import doi_hunter_web.pdf_ingest; import doi_hunter_web.insight"
        self.sub_progress(0.1, "Checking imports")
        self.run_cmd([str(py), "-c", probe], cwd=self.o.target, timeout=300)

        # Start the real app on a free port with a temporary, throw-away library.
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        home = Path(tempfile.mkdtemp(prefix="reviewhunter-selftest-"))
        env = {**os.environ, "REVIEWHUNTER_PORT": str(port), "REVIEWHUNTER_HOME": str(home), "PYTHONUNBUFFERED": "1"}
        self.log(f"Starting the app on port {port} with a temporary library {home}")
        proc = subprocess.Popen([str(py), "-m", "doi_hunter_web", "--no-browser", "--no-https"], cwd=str(self.o.target),
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.time() + SELFTEST_TIMEOUT_S
            while time.time() < deadline:
                if proc.poll() is not None:
                    out = proc.stdout.read() if proc.stdout else ""
                    raise InstallError("ReviewHunter exited during the self-test:\n" + out[-1500:])
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/connector/ping", timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    if data.get("app") == APP_NAME and data.get("version") == self.version:
                        return f"app answered (version {data.get('version')})"
                except Exception:
                    pass
                self.sub_progress(0.2 + 0.8 * (1 - (deadline - time.time()) / SELFTEST_TIMEOUT_S), "Waiting for the app")
                time.sleep(1)
            raise InstallError(f"ReviewHunter did not answer within {SELFTEST_TIMEOUT_S} s.")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            shutil.rmtree(home, ignore_errors=True)

    def prepare_chrome_extension(self) -> str:
        """Prepare a stable, validated unpacked extension without editing browser preferences."""
        if not self.o.chrome_extension:
            return "not selected"
        self.extension = validate_chrome_extension(self.o.target)
        help_path = self.o.target / "ChromeExtensionSetup.txt"
        help_path.write_text(
            f"ReviewHunter Chrome connector {self.extension['version']}\n\n"
            + EXTENSION_HELP.format(folder=self.extension["folder"])
            + "\n\nChrome requires you to confirm loading this local extension.\n"
            + "On upgrades, click Reload on the existing ReviewHunter extension.\n"
            + "If Chrome requests approval for new permissions, review and accept them in Chrome.\n"
            + "Keep this installed folder in place; Chrome loads the extension from it.\n", encoding="utf-8")
        if IS_WINDOWS:
            # Relative paths keep this launcher usable even when the installation moves.
            launcher = self.o.target / "Install Chrome Extension.cmd"
            launcher.write_text('@echo off\nsetlocal\ncd /d "%~dp0"\n'
                '".venv\\Scripts\\python.exe" "installer\\reviewhunter_installer.py" --setup-chrome --target "%~dp0."\n'
                'if errorlevel 1 pause\n', encoding="utf-8")
        self.log(f"Chrome connector {self.extension['version']} prepared in {self.extension['folder']}")
        # Let the extension start ReviewHunter (Chrome native messaging helper, per user, no admin).
        lines: list[str] = []
        py = venv_python(self.o.target)
        try:
            if not py.exists():
                raise InstallError("the program's Python environment is missing")
            self.run_cmd([str(py), "-m", "doi_hunter_web.native_host", "--register"],
                         cwd=self.o.target, on_line=lines.append, timeout=120)
            report = json.loads("\n".join(lines[lines.index("{"):] if "{" in lines else lines) or "{}")
            self.extension["can_start_app"] = bool(report.get("ok"))
        except (InstallError, OSError, ValueError) as e:   # never fails the install: ReviewHunter repairs it at launch
            self.extension["can_start_app"] = False
            self.warnings.append(f"The extension cannot start ReviewHunter yet ({e}); it is set up at the first launch.")
        return f"v{self.extension['version']} ready; confirm in Chrome"

    def create_shortcuts(self) -> str:
        if not (self.o.desktop_shortcut or self.o.menu_shortcut):
            return "skipped"
        # The app's own shortcut module writes them: the launcher repairs them later with the exact same
        # rules, so installer-made and repaired shortcuts can never disagree.
        kinds = [k for k, wanted in (("menu", self.o.menu_shortcut), ("desktop", self.o.desktop_shortcut)) if wanted]
        lines: list[str] = []
        self.run_cmd([str(venv_python(self.o.target)), "-m", "doi_hunter_web.shortcuts", "--create", *kinds],
                     cwd=self.o.target, on_line=lines.append, timeout=180)
        try:
            report = json.loads("\n".join(lines[lines.index("{"):] if "{" in lines else lines))
        except ValueError:
            raise InstallError("The shortcut helper did not answer.")
        if report.get("skipped"):
            self.warnings.append(f"Shortcuts were not created ({report['skipped']}).")
            return f"skipped ({report['skipped']})"
        self.created_shortcuts += [s["path"] for s in report.get("shortcuts", []) if s.get("exists")]
        if report.get("errors"):
            self.warnings.append("Some shortcuts could not be written: " + "; ".join(report["errors"]))
        if IS_WINDOWS:
            icon_png = self.o.target / "browser_extension" / "icons" / "icon-128.png"
            icon = self.o.target / "installer" / "reviewhunter.ico"
            if icon_png.exists() and not icon.exists():
                write_ico_from_png(icon_png, icon)
            if self.o.menu_shortcut:
                places = [Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"]
                if self.o.chrome_extension:
                    setup_link = places[-1] / f"{APP_NAME} Chrome Extension.lnk"
                    make_windows_shortcut(setup_link, venv_python(self.o.target),
                        f'"{self.o.target / "installer" / "reviewhunter_installer.py"}" --setup-chrome --target "{self.o.target}"',
                        self.o.target, icon, "Install or update the ReviewHunter Chrome extension", minimized=True)
                    self.created_shortcuts.append(str(setup_link))
                base = Path(getattr(sys, "_base_executable", sys.executable))
                lnk = places[-1] / f"Uninstall {APP_NAME}.lnk"
                make_windows_shortcut(lnk, base, f'"{self.o.target / "installer" / "reviewhunter_installer.py"}" --uninstall',
                                      Path.home(), icon, "Remove ReviewHunter (your library is kept)")
                self.created_shortcuts.append(str(lnk))
        elif IS_LINUX and shutil.which("update-desktop-database"):
            apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
            subprocess.run(["update-desktop-database", str(apps)], capture_output=True)
        return f"{len(self.created_shortcuts)} ready"

    def finish(self) -> str:
        manifest = {
            "app": APP_NAME, "version": self.version, "installed_at": datetime.now().isoformat(timespec="seconds"),
            "target": str(self.o.target), "python": sys.executable, "shortcuts": self.created_shortcuts,
            "semantic": self.o.semantic, "platform": platform.platform(), "in_place": self.in_place,
            "chrome_extension": self.extension,
        }
        (self.o.target / "install.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if self.o.word_addin and IS_WINDOWS:
            try:
                self.run_cmd([str(venv_python(self.o.target)), "-c",
                              "from doi_hunter_web import addin; print(addin.prepare_and_launch_setup())"],
                             cwd=self.o.target, timeout=120)
            except InstallError as e:
                self.warnings.append(f"Word add-in setup could not start ({e}); use Settings > Word Add-in later.")
        return "install.json written"


# ---------------------------------------------------------------------------- platform helpers

def write_ico_from_png(png: Path, ico: Path) -> None:
    """Wrap a PNG in an .ico container (PNG-compressed icons are valid since Windows Vista)."""
    data = png.read_bytes()
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    header = (0).to_bytes(2, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    entry = bytes([width % 256, height % 256, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little") \
        + len(data).to_bytes(4, "little") + (6 + 16).to_bytes(4, "little")
    ico.parent.mkdir(parents=True, exist_ok=True)
    ico.write_bytes(header + entry + data)


def make_windows_shortcut(lnk: Path, target: Path, arguments: str, workdir: Path, icon: Path, description: str,
                          minimized: bool = False) -> None:
    """Create a .lnk with WScript.Shell. Paths travel through environment variables (no quoting bugs)."""
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:RH_LNK);"
        "$s.TargetPath = $env:RH_TARGET; $s.Arguments = $env:RH_ARGS; $s.WorkingDirectory = $env:RH_WORKDIR;"
        "$s.Description = $env:RH_DESC; if (Test-Path $env:RH_ICON) { $s.IconLocation = $env:RH_ICON };"
        f"$s.WindowStyle = {7 if minimized else 1}; $s.Save()"
    )
    _powershell(script, env={"RH_LNK": str(lnk), "RH_TARGET": str(target), "RH_ARGS": arguments,
                             "RH_WORKDIR": str(workdir), "RH_DESC": description, "RH_ICON": str(icon)})
    if not lnk.exists():
        raise InstallError(f"Could not create the shortcut {lnk}")


def open_in_browser(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


def launch_app(target: Path) -> None:
    """Start the installed app exactly like its shortcut does (the launcher: single, current instance)."""
    py = venv_python(target, windowless=True)
    if IS_WINDOWS:
        subprocess.Popen([str(py), "-m", "doi_hunter_web.launcher"], cwd=str(target),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        subprocess.Popen([str(py), "-m", "doi_hunter_web.launcher"], cwd=str(target), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_running_app(target: Path) -> None:
    """Before deleting a program folder, stop the server it is running (if any)."""
    for port in (8765,):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/instance", timeout=2) as r:
                info = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        if info.get("app") == APP_NAME and Path(info.get("root") or "").resolve() == target.resolve():
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/system/shutdown", data=b"{}",
                                             method="POST", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
                time.sleep(2)
            except Exception:
                pass


def validate_chrome_extension(target: Path) -> dict:
    """Check the complete bundled runtime before offering it for installation in Chrome."""
    folder = (target / "browser_extension").resolve()
    manifest_path = folder / "manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise InstallError(f"The bundled Chrome extension is missing or unreadable: {exc}") from exc
    if manifest.get("manifest_version") != 3 or manifest.get("name") != "ReviewHunter Connector":
        raise InstallError("The installer requires the ReviewHunter Manifest V3 Chrome connector.")
    version = manifest.get("version", "")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise InstallError("The bundled Chrome extension has an invalid version.")
    paths = ["src/core/pdf.js", "src/core/save.js", "src/core/handoff.js", "src/api.js"]
    paths += list((manifest.get("icons") or {}).values())
    paths += [manifest.get("background", {}).get("service_worker", ""),
              manifest.get("action", {}).get("default_popup", ""),
              manifest.get("options_ui", {}).get("page", "")]
    for script in manifest.get("content_scripts", []):
        paths += script.get("js", []) + script.get("css", [])
    # Imports, HTML scripts and CSS are all shipped from these runtime folders.
    # Verifying local JS imports catches an archive that forgot a newly added module.
    for script in (folder / "src").rglob("*.js"):
        for imported in re.findall(r'(?:from\s*|import\s*)[\'\"]([^\'\"]+)[\'\"]', script.read_text("utf-8")):
            if imported.startswith("."):
                dependency = (script.parent / imported).resolve()
                if not dependency.is_relative_to(folder) or not dependency.is_file():
                    raise InstallError(f"Chrome extension dependency is missing: {script.name}: {imported}")
    for relative in paths:
        candidate = (folder / relative).resolve()
        if not relative or not candidate.is_relative_to(folder) or not candidate.is_file():
            raise InstallError(f"Chrome extension file is missing: {relative or '(manifest entry)'}")
    return {"version": version, "folder": str(folder), "status": "ready_for_chrome",
            "requires_confirmation": True, "manifest_sha256": hashlib.sha256(raw).hexdigest()}


def find_chrome() -> Optional[str]:
    """Find Chrome explicitly; chrome:// URLs must not be sent to a different default browser."""
    if IS_WINDOWS:
        for base in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(base):
                path = Path(os.environ[base]) / "Google/Chrome/Application/chrome.exe"
                if path.is_file():
                    return str(path)
    if IS_MAC:
        for app in (Path("/Applications"), Path.home() / "Applications"):
            path = app / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
            if path.is_file():
                return str(path)
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def open_extension_setup(target: Path) -> bool:
    """Open the supported Chrome loading flow, without changing policies or profile files."""
    folder = Path(validate_chrome_extension(target)["folder"])
    chrome = find_chrome()
    if IS_WINDOWS:
        _powershell("Set-Clipboard -Value $env:RH_EXTENSION_FOLDER", {"RH_EXTENSION_FOLDER": str(folder)})
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.run(["pbcopy"], input=str(folder).encode("utf-8"), capture_output=True)   # paste in "Load unpacked"
        subprocess.Popen(["open", str(folder)])
    else:
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(folder)])
    if chrome:
        subprocess.Popen([chrome, "chrome://extensions/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return bool(chrome)


EXTENSION_HELP = (
    "1. In Chrome, open chrome://extensions\n"
    "2. Switch on 'Developer mode' (top right)\n"
    "3. Click 'Load unpacked' and choose the folder:\n   {folder}\n"
    "4. Pin the ReviewHunter button next to the address bar\n"
    "Already installed? Click Reload on its card to apply this update."
)


# ---------------------------------------------------------------------------- uninstall

def uninstall(target: Optional[Path], confirm: Callable[[str], bool], say: Callable[[str], None]) -> bool:
    """Remove shortcuts and the program folder. The user library (~/ReviewHunter) is never touched."""
    target = (target or default_install_dir()).expanduser().resolve()
    manifest_file = target / "install.json"
    if not manifest_file.exists():
        say(f"No ReviewHunter installation found in {target}.")
        return False
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    library = Path.home() / APP_NAME
    if not confirm(f"Remove ReviewHunter {manifest.get('version', '')} from\n{target}\n\n"
                   f"Your library (database, PDFs, settings) in\n{library}\nis kept."):
        return False
    stop_running_app(target)
    py = venv_python(target)
    if py.exists():   # browser launcher registrations (registry keys / manifest files outside the folder)
        try:
            subprocess.run([str(py), "-m", "doi_hunter_web.native_host", "--unregister"], cwd=str(target),
                           capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    for s in manifest.get("shortcuts", []):
        try:
            p = Path(s)
            if p.is_dir() and not p.is_symlink():   # macOS ReviewHunter.app bundle
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        except OSError as e:
            say(f"Could not remove shortcut {s}: {e}")
    if manifest.get("in_place"):  # a git clone or unzipped folder the user manages: keep it
        manifest_file.unlink(missing_ok=True)
        say(f"Shortcuts removed. {target} was installed in place and is kept (delete it yourself if you want).")
        return True
    # Safety: only delete a folder that really is a ReviewHunter program folder.
    if not ((target / "run.py").exists() and (target / "doi_hunter_web").is_dir()):
        say(f"{target} does not look like a ReviewHunter program folder; it was not deleted.")
        return False
    if IS_WINDOWS and Path(sys.executable).resolve().is_relative_to(target):
        say("Run the uninstaller with the system Python, not the app's own environment.")
        return False
    shutil.rmtree(target, ignore_errors=True)
    say("ReviewHunter was removed." if not target.exists() else f"Some files could not be removed from {target}.")
    return not target.exists()


# ---------------------------------------------------------------------------- terminal UI

class TerminalReporter(Reporter):
    """Single-line progress bar on a TTY, plain lines otherwise (CI, Docker logs)."""

    def __init__(self, verbose: bool = False) -> None:
        self.tty = sys.stdout.isatty()
        self.verbose = verbose
        self.total = 9
        self._last = ""

    def step(self, index: int, title: str) -> None:
        self._clear()
        print(f"[{index + 1}/{self.total}] {title}", flush=True)

    def progress(self, fraction: float, detail: str = "") -> None:
        if not self.tty:
            return
        width = 30
        filled = int(width * fraction)
        line = f"  [{'#' * filled}{'-' * (width - filled)}] {fraction * 100:5.1f}%  {detail[:60]}"
        sys.stdout.write("\r" + line.ljust(len(self._last)))
        sys.stdout.flush()
        self._last = line

    def log(self, line: str) -> None:
        if self.verbose:
            self._clear()
            print("    " + line, flush=True)

    def step_done(self, index: int, ok: bool, note: str = "") -> None:
        self._clear()
        print(f"      {'OK ' if ok else 'FAILED'} {note}", flush=True)

    def finished(self, ok: bool, message: str) -> None:
        self._clear()
        print(("\nDone. " if ok else "\nInstallation failed. ") + message, flush=True)

    def _clear(self) -> None:
        if self.tty and self._last:
            sys.stdout.write("\r" + " " * len(self._last) + "\r")
            self._last = ""


def ask_yes_no(question: str, default: bool = True) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return default
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def run_cli(args: argparse.Namespace, options: Options) -> int:
    print(f"{APP_NAME} installer ({platform.system()}, Python {platform.python_version()})")
    if not args.yes:
        entered = input(f"Install folder [{options.target}]: ").strip() if sys.stdin.isatty() else ""
        if entered:
            options.target = Path(entered)
        options.semantic = options.semantic or ask_yes_no("Also install semantic clustering (~2 GB)?", False)
    ok = Installer(options, TerminalReporter(verbose=args.verbose)).run()
    if ok:
        if options.chrome_extension:
            print("\nChrome extension ready. Confirm loading it in Chrome:\n" + EXTENSION_HELP.format(folder=options.target / "browser_extension"))
            if options.launch and desktop_session():
                if not open_extension_setup(options.target):
                    print("Chrome was not found. Install Chrome, then run the Chrome extension setup shortcut.")
        if options.launch and desktop_session():
            launch_app(options.target)
            print("\nReviewHunter is starting in your browser.")
    return 0 if ok else 1


# ---------------------------------------------------------------------------- graphical UI

def run_gui(options: Options) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} Setup")
    root.geometry(f"{UI['width']}x{UI['height']}")
    root.minsize(640, 500)
    root.configure(bg=UI["bg"])
    try:
        icon_png = (options.source or HERE.parent) / "browser_extension" / "icons" / "icon-128.png"
        if icon_png.exists():
            root.iconphoto(True, tk.PhotoImage(file=str(icon_png)))
    except tk.TclError:
        pass
    if IS_MAC:  # native macOS families (Segoe UI / Consolas do not exist there)
        UI.update(font=("Helvetica Neue", 13), font_title=("Helvetica Neue", 22, "bold"), font_step=("Helvetica Neue", 13),
                  font_mono=("Menlo", 11), width=820, height=700)
    elif not IS_WINDOWS:  # Segoe UI / Consolas do not exist on Linux: use Tk's portable families
        UI.update(font=("DejaVu Sans", 10), font_title=("DejaVu Sans", 17, "bold"), font_step=("DejaVu Sans", 10),
                  font_mono=("DejaVu Sans Mono", 9))

    style = ttk.Style(root)
    style.configure("Accent.Horizontal.TProgressbar", background=UI["accent"])
    style.configure("TCheckbutton", background=UI["panel"], foreground=UI["text"], font=UI["font"])
    events: "queue.Queue[tuple]" = queue.Queue()

    # ---- shared header
    header = tk.Frame(root, bg=UI["panel"], highlightthickness=1, highlightbackground=UI["border"])
    header.pack(fill="x")
    badge = tk.Label(header, text="R", bg=UI["accent"], fg="white", font=(UI["font_title"][0], 16, "bold"), width=2)
    badge.pack(side="left", padx=(20, 12), pady=16)
    title_box = tk.Frame(header, bg=UI["panel"])
    title_box.pack(side="left", pady=12)
    version = read_version(options.source) if options.source else "latest release"
    tk.Label(title_box, text=f"{APP_NAME} Setup", bg=UI["panel"], fg=UI["text"], font=UI["font_title"]).pack(anchor="w")
    tk.Label(title_box, text=f"Version {version}  |  {platform.system()}  |  Python {platform.python_version()}",
             bg=UI["panel"], fg=UI["muted"], font=UI["font"]).pack(anchor="w")

    body = tk.Frame(root, bg=UI["bg"])
    body.pack(fill="both", expand=True, padx=20, pady=16)
    footer = tk.Frame(root, bg=UI["bg"])
    footer.pack(fill="x", padx=20, pady=(0, 16))

    def clear() -> None:
        for w in (*body.winfo_children(), *footer.winfo_children()):
            w.destroy()

    def button(parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, relief="flat", cursor="hand2", font=UI["font"],
                         bg=UI["accent"] if primary else UI["panel"], fg="white" if primary else UI["text"],
                         activebackground=UI["accent_dark"] if primary else UI["border"],
                         activeforeground="white" if primary else UI["text"], padx=18, pady=7, bd=0,
                         highlightthickness=0 if primary else 1, highlightbackground=UI["border"])

    # ---- page 1: welcome / options
    def page_welcome() -> None:
        clear()
        card = tk.Frame(body, bg=UI["panel"], highlightthickness=1, highlightbackground=UI["border"])
        card.pack(fill="both", expand=True)
        tk.Label(card, text="This will install ReviewHunter and everything it needs.\n"
                            "Your existing library is never modified.",
                 bg=UI["panel"], fg=UI["text"], font=UI["font"], justify="left").pack(anchor="w", padx=20, pady=(18, 12))

        tk.Label(card, text="Install folder", bg=UI["panel"], fg=UI["muted"], font=UI["font"]).pack(anchor="w", padx=20)
        row = tk.Frame(card, bg=UI["panel"])
        row.pack(fill="x", padx=20, pady=(2, 14))
        target_var = tk.StringVar(value=str(options.target))
        tk.Entry(row, textvariable=target_var, font=UI["font"], relief="solid", bd=1).pack(side="left", fill="x",
                                                                                       expand=True, ipady=4)

        def browse() -> None:
            chosen = filedialog.askdirectory(initialdir=str(Path(target_var.get()).parent))
            if chosen:
                target_var.set(str(Path(chosen) / APP_NAME) if Path(chosen).name != APP_NAME else chosen)

        button(row, "Browse...", browse).pack(side="left", padx=(8, 0))

        vars_ = {
            "desktop_shortcut": tk.BooleanVar(value=options.desktop_shortcut),
            "menu_shortcut": tk.BooleanVar(value=options.menu_shortcut),
            "semantic": tk.BooleanVar(value=options.semantic),
            "word_addin": tk.BooleanVar(value=options.word_addin),
            "chrome_extension": tk.BooleanVar(value=options.chrome_extension),
            "launch": tk.BooleanVar(value=options.launch),
        }
        labels = [
            ("desktop_shortcut", "Desktop shortcut"),
            ("menu_shortcut", "Start menu entry" if IS_WINDOWS else "Applications menu entry"),
            ("chrome_extension", "Set up the Chrome extension (finish by confirming Load unpacked in Chrome)"),
            ("semantic", "Semantic clustering and similarity (optional, about 2 GB download)"),
        ]
        if IS_WINDOWS:
            labels.append(("word_addin", "Set up the Word citation add-in at the end (asks you to confirm a certificate)"))
        labels.append(("launch", "Start ReviewHunter when setup is finished"))
        for key, text in labels:
            ttk.Checkbutton(card, text=text, variable=vars_[key]).pack(anchor="w", padx=20, pady=3)

        tk.Label(card, text=f"Google Drive and Zotero import are configured inside the app.\n"
                            f"Setup log: {LOG_FILE}",
                 bg=UI["panel"], fg=UI["muted"], font=(UI["font"][0], 9), justify="left").pack(anchor="w", padx=20,
                                                                                            pady=(14, 16))

        def start() -> None:
            options.target = Path(target_var.get()).expanduser()
            for key, var in vars_.items():
                setattr(options, key, bool(var.get()))
            page_progress()

        button(footer, "Cancel", root.destroy).pack(side="right")
        button(footer, "Install", start, primary=True).pack(side="right", padx=(0, 8))

    # ---- page 2: progress
    state: dict = {"installer": None}

    def page_progress() -> None:
        clear()
        installer = Installer(options, QueueReporter(events))
        state["installer"] = installer
        top = tk.Frame(body, bg=UI["bg"])
        top.pack(fill="x")
        pct = tk.Label(top, text="0%", bg=UI["bg"], fg=UI["text"], font=(UI["font"][0], 12, "bold"))
        pct.pack(side="right")
        detail = tk.Label(top, text="Starting...", bg=UI["bg"], fg=UI["muted"], font=UI["font"], anchor="w")
        detail.pack(side="left", fill="x", expand=True)
        bar = ttk.Progressbar(body, maximum=1000, style="Accent.Horizontal.TProgressbar")
        bar.pack(fill="x", pady=(6, 12))

        steps_card = tk.Frame(body, bg=UI["panel"], highlightthickness=1, highlightbackground=UI["border"])
        steps_card.pack(fill="x")
        step_rows = []
        for title, _w, _fn in installer.steps:
            r = tk.Frame(steps_card, bg=UI["panel"])
            r.pack(fill="x", padx=14, pady=2)
            mark = tk.Label(r, text="\u25cb", width=2, bg=UI["panel"], fg=UI["muted"], font=UI["font_step"])
            mark.pack(side="left")
            tk.Label(r, text=title, bg=UI["panel"], fg=UI["text"], font=UI["font_step"]).pack(side="left")
            note = tk.Label(r, text="", bg=UI["panel"], fg=UI["muted"], font=(UI["font"][0], 9))
            note.pack(side="right")
            step_rows.append((mark, note))

        log_frame = tk.Frame(body, bg=UI["bg"])
        log_text = tk.Text(log_frame, height=7, font=UI["font_mono"], bg="#0f172a", fg="#cbd5e1", relief="flat",
                           wrap="none", padx=8, pady=6)
        log_text.pack(fill="both", expand=True)
        shown = {"log": False}

        def toggle_log() -> None:
            shown["log"] = not shown["log"]
            if shown["log"]:
                log_frame.pack(fill="both", expand=True, pady=(10, 0))
                toggle.config(text="Hide details")
            else:
                log_frame.pack_forget()
                toggle.config(text="Show details")

        toggle = button(footer, "Show details", toggle_log)
        toggle.pack(side="left")
        cancel = button(footer, "Cancel", lambda: installer.cancelled.set())
        cancel.pack(side="right")

        def pump() -> None:
            try:
                while True:
                    kind, *payload = events.get_nowait()
                    if kind == "step":
                        i, _t = payload
                        step_rows[i][0].config(text="\u25cf", fg=UI["accent"])
                    elif kind == "progress":
                        frac, text = payload
                        bar["value"] = int(frac * 1000)
                        pct.config(text=f"{frac * 100:.0f}%")
                        if text:
                            detail.config(text=text)
                    elif kind == "log":
                        log_text.insert("end", payload[0] + "\n")
                        if int(log_text.index("end-1c").split(".")[0]) > 2000:
                            log_text.delete("1.0", "500.0")
                        log_text.see("end")
                    elif kind == "step_done":
                        i, ok, note = payload
                        step_rows[i][0].config(text="\u2713" if ok else "\u2717", fg=UI["ok"] if ok else UI["error"])
                        step_rows[i][1].config(text=note[:60])
                    elif kind == "finished":
                        ok, message = payload
                        page_done(ok, message)
                        return
            except queue.Empty:
                pass
            root.after(80, pump)

        threading.Thread(target=installer.run, daemon=True).start()
        root.after(80, pump)

    # ---- page 3: done
    def page_done(ok: bool, message: str) -> None:
        clear()
        card = tk.Frame(body, bg=UI["panel"], highlightthickness=1, highlightbackground=UI["border"])
        card.pack(fill="both", expand=True)
        tk.Label(card, text="\u2713  Installation complete" if ok else "\u2717  Installation failed", bg=UI["panel"],
                 fg=UI["ok"] if ok else UI["error"], font=(UI["font_title"][0], 15, "bold")).pack(anchor="w", padx=20,
                                                                                                 pady=(18, 8))
        msg = tk.Text(card, height=min(8, message.count("\n") + 2), font=UI["font"], bg=UI["panel"], fg=UI["text"], relief="flat", wrap="word")
        msg.insert("1.0", message)
        msg.config(state="disabled")
        msg.pack(fill="x", padx=20)
        if ok and options.chrome_extension:
            def show_chrome_setup() -> None:
                """Keep setup instructions available if Chrome or the desktop shell cannot open."""
                try:
                    if not open_extension_setup(options.target):
                        messagebox.showinfo("Chrome extension setup", "Chrome was not found. Install Chrome, then reopen "
                                            "the extension setup shortcut and follow the instructions shown here.")
                except (OSError, InstallError) as exc:
                    messagebox.showinfo("Chrome extension setup", f"Could not open Chrome setup automatically: {exc}\n\n"
                                        "Follow the instructions shown here to load the extension manually.")

            tk.Label(card, text="Save references from Chrome", bg=UI["panel"], fg=UI["text"],
                     font=(UI["font"][0], 10, "bold")).pack(anchor="w", padx=20, pady=(12, 2))
            tk.Label(card, text=EXTENSION_HELP.format(folder=options.target / "browser_extension"), bg=UI["panel"],
                     fg=UI["muted"], font=UI["font"], justify="left", wraplength=UI["width"] - 90).pack(anchor="w", padx=20)
            button(card, "Open the extension folder and Chrome", show_chrome_setup).pack(
                anchor="w", padx=20, pady=(8, 16))
            tk.Label(card, text="Chrome needs your final confirmation. On Windows, the folder path is copied for you.",
                     bg=UI["panel"], fg=UI["muted"], font=(UI["font"][0], 9)).pack(anchor="w", padx=20, pady=(0, 8))
            if options.launch:
                root.after(150, show_chrome_setup)
        if ok:
            if options.launch:
                launch_app(options.target)
            button(footer, "Close", root.destroy, primary=True).pack(side="right")
            button(footer, "Open ReviewHunter", lambda: launch_app(options.target)).pack(side="right", padx=(0, 8))
        else:
            button(footer, "Close", root.destroy).pack(side="right")
            button(footer, "Open the log", lambda: open_in_browser(LOG_FILE.as_uri())).pack(side="right", padx=(0, 8))
            button(footer, "Try again", page_welcome, primary=True).pack(side="right", padx=(0, 8))

    page_welcome()
    if os.environ.get("RH_INSTALLER_AUTOSTART") == "1":  # used by automated screenshots / tests
        root.after(500, lambda: [w.invoke() for w in footer.winfo_children() if getattr(w, "cget", None)
                                 and w.cget("text") == "Install"])
    root.mainloop()
    return 0


class QueueReporter(Reporter):
    """Thread-safe bridge: the installer thread puts events, the Tk loop reads them."""

    def __init__(self, q: "queue.Queue[tuple]") -> None:
        self.q = q

    def step(self, index, title): self.q.put(("step", index, title))
    def progress(self, fraction, detail=""): self.q.put(("progress", fraction, detail))
    def log(self, line): self.q.put(("log", line))
    def step_done(self, index, ok, note=""): self.q.put(("step_done", index, ok, note))
    def finished(self, ok, message): self.q.put(("finished", ok, message))


def run_gui_uninstall(target: Optional[Path]) -> int:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    ok = uninstall(target, lambda q: messagebox.askyesno(f"Uninstall {APP_NAME}", q),
                   lambda m: messagebox.showinfo(f"Uninstall {APP_NAME}", m))
    root.destroy()
    return 0 if ok else 1


# ---------------------------------------------------------------------------- entry point

def gui_available() -> bool:
    if not desktop_session() or (IS_MAC and os.environ.get("SSH_CONNECTION")):
        return False
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=f"Install {APP_NAME} on Windows, macOS or Linux.")
    parser.add_argument("--cli", action="store_true", help="terminal mode (no window)")
    parser.add_argument("--yes", "-y", action="store_true", help="accept defaults, no questions (implies --cli)")
    parser.add_argument("--target", type=Path, help=f"install folder (default: {default_install_dir()})")
    parser.add_argument("--source", type=Path, help="app folder to install from (default: this bundle, else download)")
    parser.add_argument("--download", action="store_true", help="always download the latest release")
    parser.add_argument("--with-semantic", action="store_true", help="also install semantic clustering (~2 GB)")
    parser.add_argument("--no-shortcuts", action="store_true")
    parser.add_argument("--no-launch", action="store_true", help="do not start the app or open Chrome setup at the end")
    parser.add_argument("--no-chrome-extension", action="store_true", help="skip the Chrome setup step and shortcut")
    parser.add_argument("--setup-chrome", action="store_true", help="set up the bundled Chrome extension without reinstalling the app")
    parser.add_argument("--uninstall", action="store_true", help="remove the program folder and shortcuts")
    parser.add_argument("--verbose", "-v", action="store_true", help="print every command output line")
    args = parser.parse_args(argv)

    if args.setup_chrome:
        target = (args.target or default_install_dir()).expanduser().resolve()
        try:
            print(EXTENSION_HELP.format(folder=target / "browser_extension"))
            if not open_extension_setup(target):
                print("Chrome was not found. Install Chrome and run this setup again.")
                return 1
            return 0
        except (OSError, InstallError) as exc:
            print(f"Chrome setup could not open: {exc}")
            return 1

    if args.uninstall:
        if args.cli or args.yes or not gui_available():
            ok = uninstall(args.target, (lambda q: True) if args.yes else (lambda q: ask_yes_no(q + "\nContinue?", False)),
                           print)
            return 0 if ok else 1
        return run_gui_uninstall(args.target)

    options = Options(
        target=args.target or default_install_dir(),
        source=None if args.download else (args.source or bundled_source()),
        semantic=args.with_semantic,
        desktop_shortcut=not args.no_shortcuts,
        menu_shortcut=not args.no_shortcuts,
        launch=not args.no_launch,
        chrome_extension=not args.no_chrome_extension,
    )
    if args.cli or args.yes or not gui_available():
        if not (args.cli or args.yes):
            print("No graphical display or tkinter available: using terminal mode.")
        return run_cli(args, options)
    try:
        return run_gui(options)
    except Exception as e:   # e.g. a Tk build that cannot open a window on this Mac: same install, in the terminal
        if "tcl" not in type(e).__module__.lower() and "display" not in str(e).lower():
            raise
        print(f"The setup window could not open ({e}): continuing in terminal mode.")
        return run_cli(args, options)


if __name__ == "__main__":
    sys.exit(main())
