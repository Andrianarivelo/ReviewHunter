#!/bin/bash
# ReviewHunter installer for macOS: double-click this file in Finder.
# The first time, macOS may say it cannot check the developer of a downloaded script:
# right-click the file > Open > Open. (Or paste this in Terminal instead:
#   curl -fsSL https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.sh | bash )
cd "$(dirname "$0")" || exit 1
/bin/bash ./install.sh "$@"
status=$?
echo
if [ $status -eq 0 ]; then echo "  You can close this window."; else echo "  Setup did not finish (log: ${TMPDIR:-/tmp}/reviewhunter-install.log)."; fi
read -r -p "  Press Return to close." _
exit $status
