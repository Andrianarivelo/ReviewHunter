#!/usr/bin/env bash
# ReviewHunter launcher - bootstraps a local venv and starts the web app.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if command -v python3 >/dev/null 2>&1; then
    exec python3 run.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python run.py "$@"
else
    echo "ReviewHunter needs Python 3.10+. Install it from https://www.python.org/downloads/" >&2
    exit 1
fi
