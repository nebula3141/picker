#!/usr/bin/env bash
# PICker — one-shot Linux setup (Debian/Ubuntu).
# Installs system packages (apt), creates a virtualenv, installs Python deps.
# Re-runnable. Run from the linux/ folder:  ./setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="$REPO/.venv"

echo "==> PICker Linux setup"
echo "    repo : $REPO"
echo "    venv : $VENV"

# ── 1. System packages ────────────────────────────────────────────────────────
if command -v apt >/dev/null 2>&1; then
  echo "==> Installing system packages via apt (sudo)…"
  sudo apt update
  sudo apt install -y \
    python3 python3-venv python3-pip \
    libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
    ffmpeg \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav
else
  echo "!! apt not found — install the equivalent packages for your distro"
  echo "   (see README.md: Qt xcb libs, ffmpeg, gstreamer1.0 plugins)."
fi

# ── 2. Virtualenv + Python deps ───────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "==> Creating virtualenv…"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "==> Installing Python dependencies…"
pip install --upgrade pip
pip install -r "$HERE/requirements.txt"

echo
echo "==> Done. Launch with:   ./run.sh"
echo "    or manually:         source $VENV/bin/activate && cd $REPO/src && python main.py"
