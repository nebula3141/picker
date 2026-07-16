#!/usr/bin/env bash
# PICker — launch on Linux. Activates the venv created by setup.sh and runs the app.
# Pass a file or folder to open it directly:  ./run.sh /path/to/photos
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="$REPO/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "No virtualenv found at $VENV — run ./setup.sh first." >&2
  exit 1
fi

# Uncomment to force X11/XWayland if Wayland gives you trouble:
# export QT_QPA_PLATFORM=xcb

# Use the cross-platform launcher: it sets the platform flag and disables
# Windows-only features before starting the app.
exec "$VENV/bin/python" "$HERE/launch.py" "$@"
