"""The one way ReviewHunter is started from a shortcut (all platforms).

    python -m doi_hunter_web.launcher [--restart] [--no-browser] [--foreground]

1. Repairs the shortcuts so they keep pointing at this install (``shortcuts.ensure``).
2. Asks the server on the fixed port who it is (``/api/system/instance``):
     * this install, this version  -> just open the browser (single instance)
     * another folder / older code -> stop it cleanly and start this one (``--restart`` forces it)
     * another program on the port -> explain, never kill it
3. Starts the server in the background (no console window), its output in
   ``<library>/logs/server.log``, waits until it answers, opens the browser and exits.
   Quit or restart it from Settings > Advanced; a failed start is reported in a dialog.

``--foreground`` runs the server in this terminal instead (developers, troubleshooting).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

from . import __version__, config

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
START_TIMEOUT_S = 90          # first start after an update compiles bytecode and migrates the database
STOP_TIMEOUT_S = 20
LOG_MAX_BYTES = 2_000_000

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ======================================================================================
# Talking to a running instance
# ======================================================================================

_last_probe_error = ""        # why the port did not answer like ReviewHunter (shown in the error dialog)


def _get_json(url: str, timeout: float = 2.0) -> Optional[dict]:
    global _last_probe_error
    try:
        with _NO_PROXY.open(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code}
    except (OSError, ValueError) as e:
        _last_probe_error = f"{url.split('/', 3)[-1]}: {e.__class__.__name__}: {e}"
        return None


def probe(host: str, port: int) -> Optional[dict]:
    """Identity of whatever answers on the port; None when nothing listens.

    Versions before 2.9 have no /api/system/instance: they are recognised by /connector/ping.
    """
    base = f"http://{host}:{port}"
    data = _get_json(f"{base}/api/system/instance")
    if data and data.get("app") == "ReviewHunter":
        return data
    ping = _get_json(f"{base}/connector/ping")
    if ping and ping.get("app") == "ReviewHunter":
        return {"app": "ReviewHunter", "version": ping.get("version"), "root": None, "pid": None, "legacy": True}
    if data is None and ping is None:
        return None if port_free(host, port) else {"app": "other"}
    return {"app": "other"}


def port_free(host: str, port: int) -> bool:
    """Could a server listen on this port now?

    POSIX: SO_REUSEADDR, exactly as uvicorn binds. Without it a port whose last connections are in
    TIME_WAIT (up to a minute after a server quits) looks busy, and "Quit then reopen" on macOS/Linux
    reported "used by another program". Not on Windows, where SO_REUSEADDR would allow binding over
    a port that another program really is listening on.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if not IS_WINDOWS:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def classify(found: Optional[dict], root: Path = ROOT, version: str = __version__,
             code: Optional[str] = None) -> str:
    """none | same | stale | foreign. Pure function (unit-tested).

    Same = same install folder, same version AND the same code on disk as when that server started
    (``code``: see instance.code_stamp; an in-place update keeps the version but changes the code).
    """
    if not found:
        return "none"
    if found.get("app") != "ReviewHunter":
        return "foreign"
    same_root = bool(found.get("root")) and os.path.normcase(os.path.abspath(found["root"])) == \
        os.path.normcase(os.path.abspath(str(root)))
    if code is None:
        from .instance import code_stamp
        code = code_stamp(root)
    same_code = found.get("code") in (None, code)     # servers before the stamp existed: version decides
    return "same" if same_root and found.get("version") == version and same_code else "stale"


def _pid_on_port(port: int) -> Optional[int]:
    """PID listening on the port (old versions do not report it)."""
    try:
        if IS_WINDOWS:
            # Bytes + lenient decoding: netstat prints in the OEM code page ("État" on French Windows) and a
            # strict text decode fails silently in subprocess' reader thread. The state column is translated
            # too ("ABHÖREN"), so a listening socket is recognised by its empty foreign address instead.
            raw = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or b""
            for line in raw.decode("ascii", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[2] in ("0.0.0.0:0", "[::]:0"):
                    return int(parts[-1])
        else:
            raw = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True,
                                 timeout=10).stdout or b""
            out = raw.decode("ascii", "replace").split()
            return int(out[0]) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _command_line(pid: int) -> str:
    try:
        if IS_WINDOWS:
            raw = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                                 capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        else:
            raw = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, timeout=10).stdout
        return (raw or b"").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def stop_instance(host: str, port: int, found: dict, timeout: float = STOP_TIMEOUT_S) -> bool:
    """Stop a ReviewHunter instance: politely first, then (old versions only) by PID after checking
    that the process really is ReviewHunter. Returns True once the port is free."""
    polite = True
    try:
        req = urllib.request.Request(f"http://{host}:{port}/api/system/shutdown", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        _NO_PROXY.open(req, timeout=5).read()
    except urllib.error.HTTPError as e:
        polite = e.code not in (404, 405, 409)   # older version (no endpoint) or not stoppable: do not wait
    except (OSError, ValueError):
        pass
    deadline = time.time() + (timeout if polite else 1.0)
    while time.time() < deadline:
        if port_free(host, port):
            return True
        time.sleep(0.25)
    pid = found.get("pid") or _pid_on_port(port)
    if pid and re.search(r"doi_hunter_web|run\.py|review.?hunter", _command_line(pid), re.I):
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.kill(int(pid), 15)
        except OSError:
            pass
        end = time.time() + 10
        while time.time() < end:
            if port_free(host, port):
                return True
            time.sleep(0.25)
    return port_free(host, port)


# ======================================================================================
# Starting the server
# ======================================================================================

def log_path() -> Path:
    folder = config.library_root() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "server.log"
    if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
        log.replace(folder / "server.previous.log")
    return log


def server_python() -> str:
    """Console interpreter for the server (its output goes to the log file, never to a window)."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def start_background(extra_args: Optional[list[str]] = None) -> subprocess.Popen:
    log = open(log_path(), "a", encoding="utf-8", errors="replace")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {__version__} from {ROOT} =====\n")
    log.flush()
    cmd = [server_python(), "-m", "doi_hunter_web", "--no-browser", *(extra_args or [])]
    env = {**os.environ, "REVIEWHUNTER_LAUNCHED_BY": "launcher", "PYTHONUNBUFFERED": "1"}
    kwargs: dict = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL, "stdout": log,
                    "stderr": subprocess.STDOUT}
    if IS_WINDOWS:
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:   # outlive the launcher even when it was started inside a job object
            return subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kwargs)  # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            return subprocess.Popen(cmd, creationflags=flags, **kwargs)
    return subprocess.Popen(cmd, start_new_session=True, **kwargs)


def spawn_restart(pid: int) -> None:
    """Called by the running server (Settings > Restart): a detached launcher replaces it once it stops."""
    cmd = [server_python(), "-m", "doi_hunter_web.launcher", "--wait-pid", str(pid), "--no-browser"]
    kwargs: dict = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "env": {**os.environ, "REVIEWHUNTER_LAUNCHED_BY": "launcher"}}
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kwargs)
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, **kwargs)
    else:
        subprocess.Popen(cmd, start_new_session=True, **kwargs)


def wait_ready(host: str, port: int, proc: Optional[subprocess.Popen], timeout: float = START_TIMEOUT_S) -> tuple[bool, str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = probe(host, port)
        if classify(found) == "same":
            return True, ""
        if proc is not None and proc.poll() is not None:
            return False, f"the server stopped during start-up (exit code {proc.returncode})"
        time.sleep(0.3)
    return False, f"no answer after {timeout:.0f} s"


def _log_tail(lines: int = 12) -> str:
    try:
        return "\n".join(log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def notify_error(title: str, message: str) -> None:
    """Show an error even when started from a shortcut without any console."""
    print(f"{title}: {message}", file=sys.stderr)
    if os.environ.get("REVIEWHUNTER_NO_DIALOGS") == "1":   # tests and CI: a modal dialog would block forever
        return
    try:
        if IS_WINDOWS:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        elif IS_MAC:
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            subprocess.run(["osascript", "-e", f'display alert "{esc(title)}" message "{esc(message)}" as critical'],
                           capture_output=True, timeout=600)
        else:
            for tool in (["zenity", "--error", "--title", title, "--text", message],
                         ["kdialog", "--title", title, "--error", message], ["notify-send", title, message]):
                if subprocess.run(["which", tool[0]], capture_output=True).returncode == 0:
                    subprocess.run(tool, capture_output=True, timeout=600)
                    break
    except Exception:
        pass


def _heal_shortcuts() -> None:
    """Shortcuts and the browser-extension launcher registration both follow this install."""
    try:
        from . import shortcuts

        shortcuts.ensure()
    except Exception as e:   # a shortcut problem must never prevent the app from starting
        print(f"shortcut check skipped: {e}", file=sys.stderr)
    try:
        from . import native_host

        native_host.register()
    except Exception as e:
        print(f"browser launcher registration skipped: {e}", file=sys.stderr)


# ======================================================================================
# Entry point
# ======================================================================================

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="reviewhunter", description="Start ReviewHunter (single instance).")
    parser.add_argument("--restart", action="store_true", help="replace a running instance even if it is current")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--foreground", action="store_true", help="run the server in this terminal")
    parser.add_argument("--no-shortcuts", action="store_true", help="do not check the shortcuts")
    parser.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)   # restart requested by the app itself
    args = parser.parse_args(argv)
    try:
        return _run(args)
    finally:
        # After the browser has opened: the check (PowerShell on Windows) must not delay the window.
        if not args.no_shortcuts:
            _heal_shortcuts()


def _run(args: argparse.Namespace) -> int:
    host, port = config.HOST, config.PORT
    url = f"http://{host}:{port}"
    found = probe(host, port)
    state = classify(found)
    settle = time.time() + float(os.environ.get("REVIEWHUNTER_LAUNCHER_SETTLE", "12"))
    while state == "foreign" and time.time() < settle:   # a ReviewHunter that is still booting (double-click)
        time.sleep(0.5)
        found = probe(host, port)
        state = classify(found)
    if args.wait_pid and state != "none":
        state = "stale"   # the app asked to be restarted: wait for it to go, then start fresh
    if state == "same" and not args.restart:
        if not args.no_browser:
            webbrowser.open(url)
        print(f"ReviewHunter {__version__} is already running at {url}")
        return 0
    if state == "foreign":
        notify_error("ReviewHunter cannot start",
                     f"Port {port} is used by another program. Close that program, or set the environment "
                     f"variable REVIEWHUNTER_PORT to another port."
                     + (f"\n\n(Details: {_last_probe_error})" if _last_probe_error else ""))
        return 1
    if state in ("same", "stale"):
        print(f"Replacing the running ReviewHunter {found.get('version')} ({found.get('root') or 'unknown folder'})")
        if not stop_instance(host, port, found):
            notify_error("ReviewHunter cannot restart",
                         f"An older ReviewHunter keeps running on port {port} and could not be stopped. "
                         "Close it (or restart the computer) and try again.")
            return 1

    if args.foreground:
        from .__main__ import main as serve

        sys.argv = [sys.argv[0], *([] if not args.no_browser else ["--no-browser"])]
        serve()
        return 0

    proc = start_background()
    ok, why = wait_ready(host, port, proc)
    if not ok:
        notify_error("ReviewHunter did not start",
                     f"ReviewHunter could not start: {why}.\n\nLast lines of {log_path()}:\n{_log_tail()}")
        return 1
    if not args.no_browser:
        webbrowser.open(url)
    print(f"ReviewHunter {__version__} is running at {url} (log: {log_path()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
