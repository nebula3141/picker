#!/usr/bin/env python3
"""PICker — cross-platform launcher (Windows + Linux + macOS).

On boot this:
  1. Detects the operating system.
  2. Writes a small **platform flag** file in the launching directory recording
     the detected platform (and a little context).
  3. Configures the environment for that platform:
       - On non-Windows it exports PICKER_DISABLE_WINDOWS_FEATURES=1 so the app
         skips Windows-only integrations (file associations, shell context menu,
         taskbar AppUserModelID). Everything else is cross-platform already.
       - PICKER_PLATFORM is exported for any code that wants to know.
  4. Adds the app's ``src/`` to the import path and starts it, forwarding any CLI
     arguments (e.g. a file/folder to open).

Run it the same way on either OS:

    python3 linux/launch.py [optional file or folder]     # Linux / macOS
    python  linux\\launch.py [optional file or folder]      # Windows

Paths are derived from this file's location, so it works regardless of the
current working directory.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

# ── Paths (resolved from this file, not the CWD) ──────────────────────────────
LAUNCH_DIR = Path(__file__).resolve().parent        # the linux/ directory
REPO_ROOT = LAUNCH_DIR.parent                        # repo root
SRC_DIR = REPO_ROOT / "src"                          # where main.py + picker/ live
FLAG_FILE = LAUNCH_DIR / "platform.flag"             # written on every boot


def detect_platform() -> str:
    """Return 'windows' | 'linux' | 'macos' | <raw lowercased>."""
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        return "windows"
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "macos"
    return sysname or "unknown"


def write_flag(plat: str) -> None:
    """Record the detected platform in the launching directory. Best-effort —
    a read-only directory is not fatal, the app still runs."""
    payload = {
        "platform": plat,
        "windows_features": plat == "windows",
        "python": platform.python_version(),
        "machine": platform.machine(),
        "set_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "launch_dir": str(LAUNCH_DIR),
        "src_dir": str(SRC_DIR),
    }
    try:
        FLAG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[PICker launcher] could not write {FLAG_FILE}: {e}", file=sys.stderr)


def main() -> None:
    plat = detect_platform()
    write_flag(plat)

    # Make the platform visible to the app.
    os.environ["PICKER_PLATFORM"] = plat

    # Off Windows: hard-disable the Windows-only integrations. The app reads this
    # via picker.sysfeatures.windows_features_enabled().
    if plat != "windows":
        os.environ["PICKER_DISABLE_WINDOWS_FEATURES"] = "1"

    if not SRC_DIR.is_dir():
        sys.exit(f"[PICker launcher] cannot find app source at {SRC_DIR}")

    # Run the app in-process so single-instance IPC, signals, and exit code all
    # behave exactly as a normal launch.
    sys.path.insert(0, str(SRC_DIR))
    try:
        from main import main as app_main
    except Exception as e:  # pragma: no cover
        sys.exit(f"[PICker launcher] failed to import the app: {e}")
    app_main()


if __name__ == "__main__":
    main()
