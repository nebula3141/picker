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
    """An image file directly inside the currently-browsed folder."""
    path: str
    name: str


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

def _walk_count_and_cover(
    folder: str,
    exts: set[str],
    exclude_hidden: bool,
) -> tuple[int, str]:
    """Recursive count + best cover-file path under ``folder``.

    Cover preference: an image (cheap to decode via QImageReader) over a video
    (requires ffmpeg). Falls back to a video only if the subtree contains no
    image at all.
    """
    from .image_manager import SUPPORTED_EXTENSIONS as _IMAGE_EXTS
    count = 0
    image_cover = ""
    video_cover = ""
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        if exclude_hidden:
            dirnames[:] = [
                n for n in dirnames
                if not _is_hidden(n) and n != ".picker_cache"
            ]
            filenames = [n for n in filenames if not n.startswith(".")]
        files = sorted(n for n in filenames if Path(n).suffix.lower() in exts)
        if not files:
            continue
        count += len(files)
        if not image_cover:
            for n in files:
                if Path(n).suffix.lower() in _IMAGE_EXTS:
                    image_cover = os.path.join(dirpath, n)
                    break
        if not video_cover:
            video_cover = os.path.join(dirpath, files[0])
    return count, (image_cover or video_cover)


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
                    images.append(ImageItem(path=e.path, name=name))
        except OSError:
            continue

    return folders, images


def folder_stat(
    folder_path: str,
    extensions: set[str] | None = None,
    exclude_hidden: bool | None = None,
) -> tuple[int, str]:
    """Recursive image count + best cover for a single folder. Runs off the UI
    thread to fill in tiles produced by ``scan_path(deep=False)``."""
    extensions, exclude_hidden = _resolve_scan_opts(extensions, exclude_hidden)
    return _walk_count_and_cover(folder_path, extensions, exclude_hidden)
