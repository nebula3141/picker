#!/usr/bin/env bash
# PICker — one-shot macOS setup.
# Installs ffmpeg via Homebrew, creates a virtualenv, installs Python deps.
# Re-runnable. Run from the mac/ folder:  ./setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="$REPO/.venv"

echo "==> PICker macOS setup"
echo "    repo : $REPO"
echo "    venv : $VENV"

# ── 1. ffmpeg via Homebrew (optional — video thumbnails/metadata) ─────────────
if command -v brew >/dev/null 2>&1; then
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "==> Installing ffmpeg via Homebrew…"
    brew install ffmpeg
  else
    echo "==> ffmpeg already installed."
  fi
else
  echo "!! Homebrew not found — install it from https://brew.sh to get ffmpeg"
  echo "   (video thumbnails/metadata are skipped without it; images work fine)."
fi

# ── 2. Python (3.10+) ─────────────────────────────────────────────────────────
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "!! python3 not found. Install it (brew install python) and re-run." >&2
  exit 1
fi

# ── 3. Virtualenv + Python deps ───────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "==> Creating virtualenv…"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "==> Installing Python dependencies…"
pip install --upgrade pip
pip install -r "$HERE/requirements.txt"

echo
echo "==> Done. Launch with:   ./run.sh"
echo "    or manually:         source $VENV/bin/activate && cd $REPO/src && python main.py"
