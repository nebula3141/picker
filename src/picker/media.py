"""Media-type helpers — image vs. video classification + ffmpeg discovery.

PICker is image-first; videos are added as a second media kind that flows
through the same scanners, mosaics, and slideshow. Keeping the type test in
one place avoids extension-set drift across modules.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .image_manager import SUPPORTED_EXTENSIONS as IMAGE_EXTENSIONS


VIDEO_EXTENSIONS: set[str] = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi",
    ".mts", ".m2ts", ".wmv", ".3gp", ".ogv",
}

# Combined set used by the scanner + index. Keep image set authoritative for
# anything that is "purely image"; use MEDIA_EXTENSIONS for scan filters.
MEDIA_EXTENSIONS: set[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_image(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def media_type(path: str) -> str:
    """Return 'video' / 'image' / 'other'."""
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "other"


# ── ffmpeg / ffprobe discovery ────────────────────────────────────────────────
# We shell out to the binaries (no Python bindings) — small, robust, and means
# users can drop a portable ffmpeg.exe next to PICker.exe and have it picked up.

@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    p = _find_binary("ffmpeg")
    try:
        from . import log
        log.info("media.ffmpeg_path", path=p)
    except Exception:
        pass
    return p


@lru_cache(maxsize=1)
def ffprobe_path() -> str | None:
    p = _find_binary("ffprobe")
    try:
        from . import log
        log.info("media.ffprobe_path", path=p)
    except Exception:
        pass
    return p


def _find_binary(name: str) -> str | None:
    # 1) Bundled next to the executable / repo root.
    candidates: list[Path] = []
    try:
        # When frozen by PyInstaller, sys.executable is the .exe location.
        import sys
        if getattr(sys, "frozen", False):
            # Bundled binaries live in the PyInstaller extraction dir (_MEIPASS):
            # onefile → a temp dir; onedir → the "_internal" folder. The spec
            # ships them under a "bin/" subfolder.
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "bin" / f"{name}.exe")
                candidates.append(Path(meipass) / f"{name}.exe")
            # Also honor a binary the user drops next to the executable.
            exe_dir = Path(sys.executable).parent
            candidates.append(exe_dir / f"{name}.exe")
            candidates.append(exe_dir / "bin" / f"{name}.exe")
        else:
            # Dev runs: repo-root bin/ (bundled ffmpeg/ffprobe) then src/.
            here = Path(__file__).resolve()
            src_dir = here.parent.parent
            repo_root = here.parents[2]
            candidates.append(repo_root / "bin" / f"{name}.exe")
            candidates.append(repo_root / "bin" / name)
            candidates.append(src_dir / f"{name}.exe")
            candidates.append(src_dir / name)
    except Exception:
        pass
    for c in candidates:
        if c.is_file():
            return str(c)
    # 2) PATH lookup (handles user-installed ffmpeg).
    found = shutil.which(name) or shutil.which(f"{name}.exe")
    return found


def have_ffmpeg() -> bool:
    return ffmpeg_path() is not None


def have_ffprobe() -> bool:
    return ffprobe_path() is not None


# Subprocess defaults for Windows: no console flash on PyInstaller --windowed.
def _no_window_flags() -> dict:
    flags: dict = {}
    if os.name == "nt":
        flags["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return flags


def run_ffprobe_json(path: str, *, timeout: float = 5.0) -> dict | None:
    """Return ffprobe's JSON for a file, or None on any error."""
    binp = ffprobe_path()
    if not binp:
        return None
    try:
        proc = subprocess.run(
            [
                binp, "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                path,
            ],
            capture_output=True,
            timeout=timeout,
            **_no_window_flags(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        import json
        return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def probe_video(path: str) -> dict:
    """Summarized video metadata. Returns {} if unavailable.

    Keys: duration_ms, width, height, fps, codec, bitrate, size.
    """
    data = run_ffprobe_json(path)
    if not data:
        return {}
    out: dict = {}
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)

    try:
        if "duration" in fmt:
            out["duration_ms"] = int(float(fmt["duration"]) * 1000)
    except (TypeError, ValueError):
        pass
    try:
        if "size" in fmt:
            out["size"] = int(fmt["size"])
    except (TypeError, ValueError):
        pass
    try:
        if "bit_rate" in fmt:
            out["bitrate"] = int(fmt["bit_rate"])
    except (TypeError, ValueError):
        pass

    if video_stream:
        out["codec"] = video_stream.get("codec_name") or ""
        try:
            out["width"] = int(video_stream.get("width") or 0) or None
            out["height"] = int(video_stream.get("height") or 0) or None
        except (TypeError, ValueError):
            pass
        # fps from r_frame_rate "30000/1001"
        rate = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate")
        if rate and "/" in rate:
            try:
                num, den = rate.split("/", 1)
                if int(den):
                    out["fps"] = round(int(num) / int(den), 3)
            except (TypeError, ValueError):
                pass
    return out


def fmt_duration(ms: int | None) -> str:
    """'1:23' / '1:02:45'. Empty if None."""
    if not ms or ms <= 0:
        return ""
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"
