#!/usr/bin/env bash
# =============================================================================
#  ReviewHunter one-click installer for macOS and Linux
#
#  One line in Terminal (nothing else to install first, no administrator password):
#      curl -fsSL https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.sh | bash
#  Or from the unzipped folder:
#      bash install.sh                 # setup window when possible, terminal otherwise
#      bash install.sh --cli --yes     # unattended terminal install
#  macOS: double-clicking "Install ReviewHunter.command" runs this script.
#
#  Steps: 1) find Python 3.10+ with venv; if missing, install a private Python for this user with
#            uv (https://docs.astral.sh/uv, no sudo); Linux falls back to the distro packages
#         2) locate or download the app      3) run the installer (window or terminal)
#  Environment: RH_FORCE_UV=1 always uses the private Python (tests).
# =============================================================================
set -euo pipefail

ZIP_URL="https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/ReviewHunter.zip"
UV_INSTALLER="https://astral.sh/uv/install.sh"
PRIVATE_PYTHON="3.12"
MIN_MINOR=10                       # Python 3.10+
ASSUME_YES=0
for arg in "$@"; do [[ "$arg" == "--yes" || "$arg" == "-y" ]] && ASSUME_YES=1; done
OS=$(uname -s)

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
usable() {                          # usable /path/to/python -> 0 when Python >= 3.10 with venv + ensurepip
    "$1" -c "import sys, venv, ensurepip; sys.exit(0 if sys.version_info[:2] >= (3, $MIN_MINOR) else 1)" 2>/dev/null
}

find_python() {
    PY=""
    [[ "${RH_FORCE_UV:-0}" == 1 ]] && return 1
    local cands=(python3.13 python3.12 python3.11 python3.10 python3 python)
    if [[ $OS == Darwin ]]; then    # Homebrew and python.org installs are often not on PATH in a new Terminal
        cands+=(/opt/homebrew/bin/python3 /usr/local/bin/python3)
        for f in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3; do [[ -x "$f" ]] && cands+=("$f"); done
    fi
    for cand in "${cands[@]}"; do
        local path
        path=$(command -v "$cand" 2>/dev/null) || continue
        # macOS' /usr/bin/python3 is a stub: running it without the developer tools opens an install
        # dialog (and it is Python 3.9 anyway). Only probe it when the tools are already there.
        if [[ $OS == Darwin && "$path" == /usr/bin/python3 ]] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
        if usable "$path"; then PY="$path"; return 0; fi
    done
    return 1
}

private_python() {                  # a Python for this user only, in ~/.local/share/uv (no sudo, no Xcode)
    local uv
    uv=$(command -v uv 2>/dev/null || true)
    [[ -z "$uv" && -x "$HOME/.local/bin/uv" ]] && uv="$HOME/.local/bin/uv"
    if [[ -z "$uv" ]]; then
        command -v curl >/dev/null || return 1
        info "Downloading uv (a small tool that installs Python for your user account)..."
        # UV_NO_MODIFY_PATH: leave the user's shell profile untouched.
        curl -LsSf "$UV_INSTALLER" | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || return 1
        uv="$HOME/.local/bin/uv"
        [[ -x "$uv" ]] || return 1
    fi
    info "Installing a private Python $PRIVATE_PYTHON (one time, about 30 MB)..."
    "$uv" python install "$PRIVATE_PYTHON" >/dev/null 2>&1 || return 1
    PY=$("$uv" python find "$PRIVATE_PYTHON" 2>/dev/null) || return 1
    usable "$PY"
}

install_system_packages() {         # Linux fallback when uv cannot be used (offline mirror, no curl...)
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
        die "Unknown package manager. Install Python 3.10+ with venv, then run this script again."
    fi
    info "Command to run:"
    info "  ${cmd[*]}"
    confirm "Install them now (you may be asked for your password)?" || die "Cancelled. Install the packages above, then run this script again."
    as_root "${cmd[@]}"
}

if ! find_python; then
    info "Python $MIN_MINOR+ was not found: installing a private Python (no administrator password needed)."
    if ! private_python; then
        [[ $OS == Darwin ]] && die "Could not install Python automatically. Install it from https://www.python.org/downloads/macos/ and run this script again."
        info "The private Python could not be installed: using your distribution's packages instead."
        install_system_packages
        find_python || die "Python 3.10+ is still not available. See https://www.python.org/downloads/"
    fi
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
    # curl uses the system certificate store (Python builds on macOS may not have one configured yet).
    if command -v curl >/dev/null && curl -fsSL "$ZIP_URL" -o "$DL/ReviewHunter.zip"; then
        :
    elif ! "$PY" -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$ZIP_URL" "$DL/ReviewHunter.zip"; then
        command -v gh >/dev/null || die "Download failed. Check your internet connection."
        gh release download --repo Andrianarivelo/ReviewHunter --pattern ReviewHunter.zip --dir "$DL" --clobber || die "Download failed."
    fi
    "$PY" -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$DL/ReviewHunter.zip" "$DL"
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
