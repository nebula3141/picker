"""Video thumbnail extraction via ffmpeg subprocess.

Pulls one frame ~10% into the video at THUMB_MAX_DIM long-edge JPEG quality 4
and writes it to the same shared cache directory used by image thumbnails. The
slideshow's filmstrip + the album-browser mosaic can then read them with the
exact same code path as image thumbs.

ffmpeg is invoked as a subprocess (no Python binding). Detection is lazy via
`media.have_ffmpeg()`; if it's missing, the loader returns None and callers
fall back to the placeholder tile.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PyQt6.QtGui import QImage

from . import media
from .gallery_view import THUMB_MAX_DIM, cache_file as image_cache_file


# Suffix appended to the cache key for video thumbs so they don't collide with
# any future image-of-the-same-path entry. (Practically images and videos can't
# share a path, but cache_key only hashes path/mtime/size — keep them disjoint.)
_VIDEO_CACHE_SUFFIX = ".v.jpg"


def cache_path_for_video(source_folder: str, video_path: str) -> Path:
    """Return the on-disk JPEG cache path for a video's thumbnail."""
    base = image_cache_file(source_folder, video_path)
    # base ends in .jpg — swap to .v.jpg so the video cache file is distinct.
    return base.with_name(base.stem + _VIDEO_CACHE_SUFFIX)


def _seek_position_seconds(video_path: str) -> float:
    """Pick a sensible seek-to time. We don't probe duration here (slow);
    1.0s is past most title cards / black frames and works on shorts too.
    For longer videos a 10% offset would be nicer, but probing doubles the
    cost — stick with the cheap default."""
    return 1.0


def extract_thumb(
    video_path: str,
    cache_file: Path,
    *,
    long_edge: int = THUMB_MAX_DIM,
    seek: float | None = None,
    timeout: float = 30.0,
) -> QImage | None:
    """Run ffmpeg → write JPEG to cache_file → return as QImage.

    Returns None if ffmpeg is missing, the file isn't decodable, or the
    subprocess failed. The cache file is written atomically (tmp file + rename)
    so partial writes don't poison the cache on crash.
    """
    from . import log
    binp = media.ffmpeg_path()
    if not binp:
        log.warn("video_thumb: ffmpeg binary not found", video=video_path)
        return None

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warn("video_thumb: cannot create cache dir",
                 dir=str(cache_file.parent), err=str(e))
        return None

    seek_pos = seek if seek is not None else _seek_position_seconds(video_path)
    tmp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")

    cmd = [
        binp,
        "-loglevel", "error",
        "-y",
        "-threads", "1",            # one ffmpeg per CPU core, leaves room for app
        # -ss BEFORE -i is the fast seek (keyframe-accurate enough for a thumb).
        "-ss", f"{seek_pos:.3f}",
        "-i", video_path,
        "-an",                      # skip audio decode entirely (we want one video frame)
        "-frames:v", "1",
        # Letterbox-fit to long_edge while preserving aspect; even dims for JPEG.
        "-vf", (
            f"scale='if(gt(iw,ih),{long_edge},-2)':"
            f"'if(gt(iw,ih),-2,{long_edge})'"
        ),
        "-q:v", "4",
        # Force image2 muxer; the tmp file ends in .jpg.tmp and ffmpeg refuses
        # to infer JPEG from the .tmp suffix. Without this: rc=-22 EINVAL.
        "-f", "image2",
        str(tmp_file),
    ]

    flags: dict = {}
    if os.name == "nt":
        flags["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **flags)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warn("video_thumb: ffmpeg launch failed",
                 video=video_path, err=str(e))
        _try_unlink(tmp_file)
        return None

    if proc.returncode != 0 or not tmp_file.exists():
        _try_unlink(tmp_file)
        # Some clips fail at -ss=1.0 (very short videos). Retry from the start.
        if seek_pos > 0:
            return extract_thumb(video_path, cache_file,
                                 long_edge=long_edge, seek=0.0, timeout=timeout)
        try:
            err_tail = (proc.stderr or b"")[-400:].decode("utf-8", "replace")
        except Exception:
            err_tail = ""
        log.warn("video_thumb: ffmpeg returned non-zero",
                 video=video_path, rc=proc.returncode, stderr=err_tail)
        return None

    try:
        # Atomic-ish rename. On Windows replace handles overwrite.
        os.replace(str(tmp_file), str(cache_file))
    except OSError as e:
        log.warn("video_thumb: rename to cache failed",
                 src=str(tmp_file), dst=str(cache_file), err=str(e))
        _try_unlink(tmp_file)
        return None

    img = QImage(str(cache_file))
    if img.isNull():
        log.warn("video_thumb: ffmpeg wrote unreadable JPEG",
                 cache=str(cache_file))
        return None
    log.info("video_thumb: extracted",
             video=video_path, cache=str(cache_file),
             w=img.width(), h=img.height())
    return img


def _try_unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
