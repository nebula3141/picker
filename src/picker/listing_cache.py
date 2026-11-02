"""Persistent folder-listing index — the in-RAM index that survives a restart.

The browser keeps every folder it has listed in memory (``album_browser_view.
_SCAN_CACHE``), which makes navigating back and forth instant. That memory used
to die with the process, so the *first* folder you opened after every launch
paid for a full directory scan, and on a big shoot folder that is the slowest
thing the app does.

This module writes that index to one compact file and loads it back on a
background thread at startup, so opening the folder you were last in is
instant — the grid renders from memory while a single ``stat`` of the directory
confirms nothing changed (and a background relist patches it in if it did; see
``_scan_and_render``'s stale-while-revalidate path).

Format (JSON, one object):
    {"v": 1, "folders": {"<folder>": {"m": <dir mtime ns>,
                                      "i": [[name, mtime, size], ...],
                                      "d": {name: [w, h]}}}}
Names are stored, not full paths, which keeps a 10,000-photo folder around
400 KB. Entries are capped so the file can't grow without bound.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

VERSION = 2   # v2: dims from the EXIF-rotation-aware reader
MAX_FOLDERS = 60
MAX_ITEMS_TOTAL = 200_000

_lock = threading.Lock()
_pending: dict = {}      # folder -> raw record, until first use
_dirty = False
_loaded = False


def _file() -> Path:
    from . import settings as settings_mod
    return settings_mod._config_dir() / "listings.json"


def mark_dirty() -> None:
    global _dirty
    _dirty = True


def load_async() -> None:
    """Populate the in-memory scan cache from disk, off the UI thread."""
    threading.Thread(target=load, daemon=True).start()


def load() -> None:
    """Parse the file into raw records. Pure data — no Qt, no widget modules —
    so this is safe to run on a background thread during startup. Records are
    turned into real listings by :func:`take`, on first use."""
    global _loaded
    if _loaded:
        return
    try:
        p = _file()
        if not p.is_file():
            _loaded = True
            return
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _loaded = True
        return
    with _lock:
        if isinstance(raw, dict) and raw.get("v") == VERSION:
            got = raw.get("folders")
            if isinstance(got, dict):
                _pending.update(got)
        _loaded = True


def has(folder: str) -> bool:
    """Is there a stored listing for ``folder``? Memory only — no disk I/O."""
    with _lock:
        return folder in _pending


def take(folder: str, dir_mtime: int):
    """Materialise the stored listing for ``folder`` if it's still valid.

    Returns ``(folders, items, mtime, dims)`` or None. The caller passes the
    directory's current mtime; a mismatch means something was added, removed or
    renamed, so the stale listing is dropped and a real scan happens."""
    with _lock:
        rec = _pending.pop(folder, None)
    if not isinstance(rec, dict) or rec.get("m", -1) != dir_mtime or dir_mtime == -1:
        return None
    try:
        from .album import Folder, ImageItem
        join = os.path.join
        items = [ImageItem(path=join(folder, name), name=name, mtime=mtime, size=size)
                 for name, mtime, size in (rec.get("i") or ())]
        dims = {join(folder, n): (int(wh[0]), int(wh[1]))
                for n, wh in (rec.get("d") or {}).items() if len(wh) == 2}
        subs = [Folder(path=join(folder, n), name=n, image_count=-1, cover_path="")
                for n in (rec.get("f") or ())]
    except Exception:
        return None
    return subs, items, rec.get("m", -1), dims


def save() -> None:
    """Write the most recently used folders back out (atomic)."""
    global _dirty
    if not _dirty:
        return
    _dirty = False
    try:
        from . import album_browser_view as abv
        with abv._SCAN_LOCK:
            entries = list(abv._SCAN_CACHE.items())
    except Exception:
        return
    out: dict = {}
    budget = MAX_ITEMS_TOTAL
    # _SCAN_CACHE is an LRU: walk newest-first so the folders you actually use
    # survive the cap.
    for folder, value in reversed(entries):
        if len(out) >= MAX_FOLDERS or budget <= 0:
            break
        try:
            folders, items, mtime, dims = value
        except (TypeError, ValueError):
            continue
        if mtime == -1 or not items:
            continue
        # Whole folders only. A cut-down listing stamped with the folder's real
        # mtime would be restored next launch as if complete, and the missing
        # photos would stay hidden until the folder changed.
        if len(items) > budget:
            continue
        budget -= len(items)
        dim_by_name = {}
        for it in items:
            d = (dims or {}).get(it.path)
            if d:
                dim_by_name[it.name] = [int(d[0]), int(d[1])]
        out[folder] = {
            "m": mtime,
            "i": [[it.name, it.mtime, it.size] for it in items],
            "d": dim_by_name,
            "f": [f.name for f in folders],
        }
    try:
        p = _file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"v": VERSION, "folders": out},
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
    except (OSError, ValueError):
        pass


def clear() -> None:
    global _dirty
    _dirty = False
    with _lock:
        _pending.clear()
    try:
        _file().unlink(missing_ok=True)
    except OSError:
        pass
