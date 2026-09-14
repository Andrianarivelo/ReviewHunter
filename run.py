#!/usr/bin/env python3
"""Self-contained launcher for ReviewHunter. No `pip install` required.

On first run this script:
  1. Creates a local virtual environment at ``./.venv/``.
  2. Installs the runtime dependencies into it.
  3. Re-executes itself inside the venv and starts the web app.

On every later run it just re-uses the existing venv. Delete ``.venv/`` to
force a clean re-install, or pass ``--reinstall``.

Usage::

    python run.py                  # launch the web UI (auto-opens browser)
    python run.py --reinstall      # rebuild the venv from scratch
    python run.py -- --port 9000   # forward args to the web app
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQS = ROOT / "requirements.txt"
MIN_PY = (3, 10)
SENTINEL = "REVIEWHUNTER_BOOTSTRAPPED"

REQUIRED_MODULES = ("fastapi", "uvicorn", "requests", "bs4", "pydantic", "libgen", "multipart", "pypdf",
                    "bibtexparser", "cryptography", "google_auth_oauthlib", "googleapiclient")


def _venv_python() -> Path:
    return VENV_DIR / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def _check_python_version() -> None:
    if sys.version_info < MIN_PY:
        sys.exit(f"ReviewHunter needs Python {'.'.join(map(str, MIN_PY))}+; you have {sys.version.split()[0]}.")


def _ensure_venv(reinstall: bool) -> None:
    if reinstall and VENV_DIR.exists():
        print(f"[setup] Removing existing venv at {VENV_DIR}...")
        shutil.rmtree(VENV_DIR)
    if VENV_DIR.exists():
        return
    print(f"[setup] Creating local venv at {VENV_DIR} (one-time, ~10s)...")
    builder = venv.EnvBuilder(with_pip=True, upgrade_deps=False, clear=False)
    builder.create(str(VENV_DIR))


def _modules_present(py: Path) -> bool:
    probe = "; ".join(f"import {m}" for m in REQUIRED_MODULES)
    return subprocess.run([str(py), "-c", probe], capture_output=True).returncode == 0


def _ensure_deps() -> None:
    py = _venv_python()
    if _modules_present(py):
        return
    print("[setup] Installing dependencies into the venv (one-time)...")
    subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"])
    subprocess.check_call([str(py), "-m", "pip", "install", "--quiet", "-r", str(REQS)])


def _split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on a literal ``--`` so users can forward args to the app."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def main() -> None:
    _check_python_version()

    bootstrap_args, app_args = _split_passthrough(sys.argv[1:])
    reinstall = "--reinstall" in bootstrap_args

    if os.environ.get(SENTINEL) != "1":
        try:
            _ensure_venv(reinstall=reinstall)
            _ensure_deps()
        except subprocess.CalledProcessError as e:
            sys.exit(f"[setup] Failed: {e}\nTry deleting {VENV_DIR} and re-running.")

        env = {**os.environ, SENTINEL: "1"}
        cmd = [str(_venv_python()), str(Path(__file__).resolve()), *(["--"] + app_args if app_args else [])]
        try:
            sys.exit(subprocess.call(cmd, env=env))
        except KeyboardInterrupt:
            sys.exit(130)

    # Inside the venv now - start the app.
    sys.path.insert(0, str(ROOT))
    from doi_hunter_web.__main__ import main as app_main

    # Forward post-`--` args to argparse inside the app.
    sys.argv = [sys.argv[0], *app_args]
    app_main()


if __name__ == "__main__":
    main()
