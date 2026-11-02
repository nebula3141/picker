"""Persistent image-dimension cache.

Remembering each image's pixel size lets the justified mosaic lay out with the
**correct** aspect ratios on its very first paint — skipping the per-image
header-read pass and the re-justify shuffle — every time a folder (or the whole
app) is reopened. Combined with the on-disk thumbnail cache, revisiting a folder
becomes near-instant: the grid is already the right shape and the thumbs decode
straight from disk.

One compact JSON index lives per source folder, next to the thumbnails in
``.picker_cache/dimensions.json``. Entries are validated against each file's
mtime + size on read, so a modified or replaced file simply misses and gets
re-measured (and the thumbnail cache invalidates in lockstep, since it keys on
the same fields). Writes are buffered in memory and flushed atomically.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_CACHE_DIRNAME = ".picker_cache"
# v2: sizes saved before 5.0 ignored EXIF rotation (rotated portraits were
# stored as landscape). A new file name drops them all once, cleanly.
_INDEX_NAME = "dimensions-v2.json"
_LEGACY_INDEX_NAMES = ("dimensions.json",)

_lock = threading.Lock()
_folders: dict[str, dict[str, list]] = {}   # source_folder -> {npath: [w,h,mtime,size]}
_loaded: set[str] = set()
_dirty: set[str] = set()


def _index_path(source_folder: str) -> Path:
    return Path(source_folder) / _CACHE_DIRNAME / _INDEX_NAME


def _npath(path: str) -> str:
    # Memoised, and skips abspath for already-absolute paths — this runs once
    # per image on every folder open. See thumb_store.npath.
    from .thumb_store import npath as _n
    return _n(path)


def _ensure_loaded(source_folder: str) -> dict:
    """Load a folder's index once (call under _lock)."""
    d = _folders.get(source_folder)
    if source_folder in _loaded:
        return d if d is not None else {}
    data: dict = {}
    try:
        p = _index_path(source_folder)
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, ValueError):
        data = {}
    _folders[source_folder] = data
    _loaded.add(source_folder)
    return data


def get(source_folder: str, path: str) -> tuple[int, int] | None:
    """Return the cached ``(width, height)`` for ``path`` if present and still
    valid (mtime + size unchanged), else ``None``."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = _npath(path)
    with _lock:
        v = _ensure_loaded(source_folder).get(key)
    if (isinstance(v, list) and len(v) == 4
            and int(v[2]) == int(st.st_mtime) and int(v[3]) == st.st_size
            and v[0] > 0 and v[1] > 0):
        return int(v[0]), int(v[1])
    return None


def reset() -> None:
    """Drop all in-memory state (after the on-disk caches were wiped)."""
    with _lock:
        _folders.clear()
        _loaded.clear()
        _dirty.clear()


def preload(source_folder: str) -> None:
    """Parse a folder's index now (call from a worker) so the first lookup on
    the UI thread doesn't pay for the JSON read."""
    with _lock:
        _ensure_loaded(source_folder)


def get_many(source_folder: str, entries) -> list[tuple[int, int] | None]:
    """Batch lookup with **no syscalls**: ``entries`` is an iterable of
    ``(path, mtime, size)`` whose stat fields the caller already has (scandir
    provides them for free). One lock acquisition for the whole folder."""
    out: list[tuple[int, int] | None] = []
    with _lock:
        d = _ensure_loaded(source_folder)
        for path, mtime, size in entries:
            v = d.get(_npath(path))
            if (isinstance(v, list) and len(v) == 4 and mtime is not None
                    and int(v[2]) == int(mtime) and int(v[3]) == int(size)
                    and v[0] > 0 and v[1] > 0):
                out.append((int(v[0]), int(v[1])))
            else:
                out.append(None)
    return out


def put(source_folder: str, path: str, w: int, h: int) -> None:
    """Record ``path``'s pixel size for next time. Buffered; see :func:`flush`."""
    if w <= 0 or h <= 0:
        return
    try:
        st = os.stat(path)
    except OSError:
        return
    put_sig(source_folder, path, w, h, (int(st.st_mtime), int(st.st_size)))


def put_sig(source_folder: str, path: str, w: int, h: int, sig: tuple[int, int]) -> None:
    """:func:`put` with a caller-supplied (mtime, size) — no stat."""
    if w <= 0 or h <= 0 or sig is None:
        return
    key = _npath(path)
    entry = [int(w), int(h), int(sig[0]), int(sig[1])]
    with _lock:
        d = _ensure_loaded(source_folder)
        if d.get(key) == entry:
            return
        d[key] = entry
        _dirty.add(source_folder)


def flush(source_folder: str | None = None) -> None:
    """Write buffered indexes to disk atomically. Flushes one folder, or all
    dirty folders when called with no argument (e.g. on app exit)."""
    with _lock:
        targets = ([source_folder] if source_folder is not None
                   else list(_dirty))
        pending = []
        for src in targets:
            if src not in _dirty:
                continue
            pending.append((src, dict(_folders.get(src, {}))))
            _dirty.discard(src)
    # Serialize/write outside the lock — JSON encode + disk IO shouldn't block
    # the fast get()/put() calls coming off the layout loop.
    for src, data in pending:
        try:
            p = _index_path(src)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, p)
            for legacy in _LEGACY_INDEX_NAMES:
                try:
                    (p.parent / legacy).unlink()
                except OSError:
                    pass
        except OSError:
            pass
