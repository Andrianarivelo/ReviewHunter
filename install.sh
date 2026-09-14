#!/usr/bin/env bash
# =============================================================================
#  ReviewHunter one-click installer for Linux
#
#  Run from a terminal (or "Run as program" in your file manager):
#      bash install.sh                 # graphical installer when a display is available
#      bash install.sh --cli --yes     # unattended terminal install
#  One-line install from the internet:
#      curl -fsSL https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.sh | bash
#
#  Steps: 1) make sure Python 3.10+, venv, pip and tkinter exist (installs the distro packages
#            with sudo after asking)  2) locate or download the app  3) run the installer
# =============================================================================
set -euo pipefail

ZIP_URL="https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/ReviewHunter.zip"
MIN_MINOR=10                       # Python 3.10+
ASSUME_YES=0
for arg in "$@"; do [[ "$arg" == "--yes" || "$arg" == "-y" ]] && ASSUME_YES=1; done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n  \033[31m%s\033[0m\n' "$*" >&2; exit 1; }

confirm() {                         # confirm "question" -> 0 for yes
    [[ $ASSUME_YES == 1 ]] && return 0
    if [[ -r /dev/tty ]]; then
        local reply
        read -r -p "  $1 [Y/n] " reply </dev/tty || true
        [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
    else
        return 1
    fi
}

as_root() {                         # run a command as root (sudo asks for the user's password in the terminal)
    if [[ $(id -u) == 0 ]]; then "$@"; elif command -v sudo >/dev/null; then sudo "$@"; else die "Please run as root or install sudo."; fi
}

echo
bold "  ReviewHunter Setup"
echo "  ------------------"

# ------------------------------------------------------------------ 1) Python
find_python() {
    PY=""
    for cand in python3 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$cand" >/dev/null 2>&1 &&
           "$cand" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $MIN_MINOR) else 1)" 2>/dev/null; then
            PY=$(command -v "$cand"); return 0
        fi
    done
    return 1
}

python_ready() {                    # venv + ensurepip (Debian splits them out) and tkinter for the window
    "$PY" -c "import venv, ensurepip" 2>/dev/null || return 1
    [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && return 0
    "$PY" -c "import tkinter" 2>/dev/null
}

install_system_packages() {
    local cmd=()
    if command -v apt-get >/dev/null; then
        cmd=(sh -c "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip python3-tk ca-certificates")
    elif command -v dnf >/dev/null; then
        cmd=(dnf install -y python3 python3-pip python3-tkinter)
    elif command -v zypper >/dev/null; then
        cmd=(zypper --non-interactive install python3 python3-pip python3-tk)
    elif command -v pacman >/dev/null; then
        cmd=(pacman -S --needed --noconfirm python python-pip tk)
    elif command -v apk >/dev/null; then
        cmd=(apk add python3 py3-pip py3-tkinter)
    else
        die "Unknown package manager. Install Python 3.10+ with venv and tkinter, then run this script again."
    fi
    info "Missing system packages. Command to run:"
    info "  ${cmd[*]}"
    confirm "Install them now (you may be asked for your password)?" || die "Cancelled. Install the packages above, then run this script again."
    as_root "${cmd[@]}"
}

if ! find_python || ! python_ready; then
    install_system_packages
    find_python || die "Python 3.10+ is still not available (your distribution may ship an older Python). See https://www.python.org/downloads/"
    python_ready || die "Python's venv module is still missing. Install python3-venv (or equivalent) and retry."
fi
info "Using Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# ------------------------------------------------------------------ 2) app files
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi
INSTALLER="${SCRIPT_DIR:+$SCRIPT_DIR/installer/reviewhunter_installer.py}"
if [[ -z "$INSTALLER" || ! -f "$INSTALLER" ]]; then
    DL=$(mktemp -d -t reviewhunter-setup-XXXXXX)
    info "Downloading the latest ReviewHunter release..."
    if ! "$PY" - "$ZIP_URL" "$DL" <<'PYEOF'
import sys, urllib.request, zipfile, io
url, dest = sys.argv[1], sys.argv[2]
data = urllib.request.urlopen(url, timeout=120).read()
zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
PYEOF
    then
        # Private repository: the public URL answers 404; the GitHub CLI downloads with the user's login.
        command -v gh >/dev/null || die "Download failed. If the repository is private: install the GitHub CLI and run 'gh auth login', or download ReviewHunter.zip from the Releases page while signed in."
        info "Public download refused: trying the GitHub CLI with your login..."
        gh release download --repo Andrianarivelo/ReviewHunter --pattern ReviewHunter.zip --dir "$DL" --clobber || die "Download failed."
        "$PY" -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$DL/ReviewHunter.zip" "$DL"
    fi
    INSTALLER=$(find "$DL" -name reviewhunter_installer.py -print -quit)
    [[ -n "$INSTALLER" ]] || die "The downloaded release does not contain the installer."
fi

# ------------------------------------------------------------------ 3) run the installer
info "Starting the installer..."
echo
# When piped (curl | bash) stdin is the script itself: reattach the keyboard if a terminal exists.
if { : </dev/tty; } 2>/dev/null; then
    exec "$PY" "$INSTALLER" "$@" </dev/tty
else
    exec "$PY" "$INSTALLER" "$@"
fi
