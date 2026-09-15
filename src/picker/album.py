"""Album discovery for source folders.

An *album* is any folder under the chosen source root that directly contains
image files. Subfolders nested any number of levels are returned individually,
each with a relative-path display name. The source root itself becomes an
album when it has loose images at the top level.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .image_manager import SUPPORTED_EXTENSIONS, active_extensions
from . import settings as settings_mod


@dataclass(frozen=True)
class Album:
    path: str          # absolute folder path
    rel: str           # POSIX-relative from source ("" for source root)
    name: str          # display name
    image_count: int
    cover_path: str    # absolute path of first image (used as tile cover)


@dataclass(frozen=True)
class Folder:
    """Subfolder entry shown as a folder tile in the browser view."""
    path: str
    name: str
    image_count: int   # recursive count under this folder
    cover_path: str    # first image found anywhere inside (cover thumb)


@dataclass(frozen=True)
class ImageItem:
    """An image file directly inside the currently-browsed folder.

    ``mtime``/``size`` come straight from the directory listing (free on Windows,
    where scandir returns them with the name), so caches can validate entries
    without a per-file ``stat`` syscall on the UI thread."""
    path: str
    name: str
    mtime: float | None = None
    size: int = 0

    @property
    def sig(self) -> tuple[int, int] | None:
        return (int(self.mtime), int(self.size)) if self.mtime is not None else None


def _is_hidden(name: str) -> bool:
    return name.startswith(".") or name == "__pycache__"


def scan_albums(
    source_folder: str,
    extensions: set[str] | None = None,
    exclude_hidden: bool | None = None,
) -> list[Album]:
    """Walk source_folder; emit one Album per directory with direct image files."""
    src = Path(source_folder).resolve()
    if not src.is_dir():
        return []

    if extensions is None:
        types = settings_mod.get("file_types") or None
        extensions = active_extensions(types if isinstance(types, list) else None)
    if exclude_hidden is None:
        exclude_hidden = bool(settings_mod.get("exclude_hidden"))

    albums: list[Album] = []

    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        d = Path(dirpath)
        if exclude_hidden:
            dirnames[:] = [n for n in dirnames if not _is_hidden(n)]
            # Also skip cache dirs
            dirnames[:] = [n for n in dirnames if n != ".picker_cache"]

        imgs = sorted(
            d / n for n in filenames
            if Path(n).suffix.lower() in extensions
        )
        if not imgs:
            continue

        try:
            rel = d.relative_to(src).as_posix()
        except ValueError:
            rel = d.name

        if d == src:
            rel = ""
            name = src.name or str(src)
        else:
            name = rel  # full relative path so nested albums are unambiguous

        albums.append(Album(
            path=str(d),
            rel=rel,
            name=name,
            image_count=len(imgs),
            cover_path=str(imgs[0]),
        ))

    # Root album first, then alphabetical by relative path
    albums.sort(key=lambda a: (a.rel != "", a.rel.lower()))
    return albums


# ── Folder-tree (Picasa-style) browsing ───────────────────────────────────────

# Recursive (count, cover) per folder, filled as a side effect of every walk.
# Walking folder X records the result for X *and every folder beneath it*, so
# drilling down the tree never re-walks a subtree that was already counted one
# level up (the old code re-walked the whole subtree at every depth).
import threading as _threading

_STAT_CACHE: dict[str, tuple[int, str]] = {}
_STAT_LOCK = _threading.Lock()
_STAT_CACHE_MAX = 50_000


def _nkey(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def cached_folder_stat(folder: str) -> tuple[int, str] | None:
    with _STAT_LOCK:
        return _STAT_CACHE.get(_nkey(folder))


def invalidate_folder_stats(path: str | None = None) -> None:
    """Forget cached counts for ``path`` and all its ancestors (their recursive
    totals include it), or everything when ``path`` is None."""
    with _STAT_LOCK:
        if path is None:
            _STAT_CACHE.clear()
            return
        cur = _nkey(path)
        while True:
            _STAT_CACHE.pop(cur, None)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent


def _walk_count_and_cover(
    folder: str,
    exts: set[str],
    exclude_hidden: bool,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Recursive count + best cover-file path under ``folder``.

    Cover preference: an image (cheap to decode via QImageReader) over a video
    (requires ffmpeg). Falls back to a video only if the subtree contains no
    image at all. Every sub-folder's own result is memoised on the way back up.
    """
    from .image_manager import SUPPORTED_EXTENSIONS as _IMAGE_EXTS
    results: dict[str, tuple[int, str]] = {}

    def walk(path: str, depth: int) -> tuple[int, str, str]:
        key = _nkey(path)
        with _STAT_LOCK:
            hit = _STAT_CACHE.get(key)
        if hit is not None:
            cov = hit[1]
            is_img = bool(cov) and Path(cov).suffix.lower() in _IMAGE_EXTS
            return hit[0], (cov if is_img else ""), ("" if is_img else cov)
        if (cancelled is not None and cancelled()) or depth > 64:
            return 0, "", ""
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            return 0, "", ""
        count = 0
        image_cover = ""
        video_cover = ""
        subdirs = []
        for e in entries:
            name = e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if exclude_hidden and (_is_hidden(name) or name == ".picker_cache"):
                        continue
                    subdirs.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    if exclude_hidden and name.startswith("."):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in exts:
                        continue
                    count += 1
                    if not image_cover and ext in _IMAGE_EXTS:
                        image_cover = e.path
                    elif not video_cover:
                        video_cover = e.path
            except OSError:
                continue
        for sd in subdirs:
            c, ic, vc = walk(sd, depth + 1)
            count += c
            image_cover = image_cover or ic
            video_cover = video_cover or vc
        if not (cancelled is not None and cancelled()):
            results[key] = (count, image_cover or video_cover)
        return count, image_cover, video_cover

    count, ic, vc = walk(folder, 0)
    if results:
        with _STAT_LOCK:
            if len(_STAT_CACHE) + len(results) > _STAT_CACHE_MAX:
                _STAT_CACHE.clear()
            _STAT_CACHE.update(results)
    return count, (ic or vc)


def _resolve_scan_opts(extensions, exclude_hidden):
    if extensions is None:
        types = settings_mod.get("file_types") or None
        extensions = active_extensions(types if isinstance(types, list) else None)
    if exclude_hidden is None:
        exclude_hidden = bool(settings_mod.get("exclude_hidden"))
    return extensions, exclude_hidden


def scan_path(
    path: str,
    extensions: set[str] | None = None,
    exclude_hidden: bool | None = None,
    progress_cb: Callable[[str], None] | None = None,
    deep: bool = True,
) -> tuple[list[Folder], list[ImageItem]]:
    """Inspect ``path`` non-recursively. Returns (subfolders, images).

    ``images`` are the image files directly inside ``path``. ``subfolders`` are
    the immediate subdirectories.

    **Fast mode (`deep=False`) — the default first paint:** subdirectories are
    listed as-is (``image_count = -1``, ``cover_path = ""``); their real recursive
    count + cover are computed later by :func:`folder_stat` off the UI thread.
    This makes opening any folder instant regardless of subtree size.

    **Deep mode (`deep=True`):** each subfolder is walked recursively for its
    exact image count + cover, and empty ones are dropped. Used to fill in / cache
    the tiles after the fast paint.
    """
    base = Path(path)
    if not base.is_dir():
        return [], []
    extensions, exclude_hidden = _resolve_scan_opts(extensions, exclude_hidden)

    folders: list[Folder] = []
    images: list[ImageItem] = []

    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        return [], []

    for e in entries:
        try:
            name = e.name
            if exclude_hidden and (_is_hidden(name) or name == ".picker_cache"):
                continue
            if e.is_dir(follow_symlinks=False):
                if not deep:
                    folders.append(Folder(path=e.path, name=name,
                                          image_count=-1, cover_path=""))
                    continue
                if progress_cb:
                    progress_cb(e.path)
                count, cover = _walk_count_and_cover(e.path, extensions, exclude_hidden)
                if count > 0:
                    folders.append(Folder(
                        path=e.path,
                        name=name,
                        image_count=count,
                        cover_path=cover,
                    ))
            elif e.is_file(follow_symlinks=False):
                if Path(name).suffix.lower() in extensions:
                    try:
                        st = e.stat(follow_symlinks=False)   # cached by scandir on Windows
                        mtime, size = st.st_mtime, st.st_size
                    except OSError:
                        mtime, size = None, 0
                    images.append(ImageItem(path=e.path, name=name, mtime=mtime, size=size))
        except OSError:
            continue

    return folders, images


def list_images(path: str, extensions: set[str] | None = None,
                exclude_hidden: bool | None = None) -> list[ImageItem]:
    """The media files directly inside ``path`` (sorted, with stat fields)."""
    return scan_path(path, extensions, exclude_hidden, deep=False)[1]


def folder_stat(
    folder_path: str,
    extensions: set[str] | None = None,
    exclude_hidden: bool | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Recursive image count + best cover for a single folder. Runs off the UI
    thread to fill in tiles produced by ``scan_path(deep=False)``."""
    hit = cached_folder_stat(folder_path)
    if hit is not None:
        return hit
    extensions, exclude_hidden = _resolve_scan_opts(extensions, exclude_hidden)
    return _walk_count_and_cover(folder_path, extensions, exclude_hidden, cancelled)
