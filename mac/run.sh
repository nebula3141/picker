#!/usr/bin/env bash
# PICker — launch on macOS. Activates the venv created by setup.sh and runs the app.
# Pass a file or folder to open it directly:  ./run.sh /path/to/photos
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="$REPO/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "No virtualenv found at $VENV — run ./setup.sh first." >&2
  exit 1
fi

# Reuse the shared cross-platform launcher (detects macOS, sets PICKER_PLATFORM,
# disables Windows-only integrations). It lives in linux/ but is OS-agnostic.
exec "$VENV/bin/python" "$REPO/linux/launch.py" "$@"
