"""When each photo was taken — the data behind the timeline scrollbar.

Answers immediately and improves in the background:

1. **Instantly** every photo has a time: whatever is already cached, else the
   file's modification time (free — the listing carried it).
2. **In the background**, one query asks the library index for the folder's
   ``date_taken`` values, and photos the index doesn't know get their EXIF
   DateTimeOriginal read from the header (Pillow opens lazily, so this reads a
   few KB per file, not the image). Results arrive in batches so the timeline
   sharpens progressively instead of waiting for the whole folder.

Nothing here ever blocks the UI thread, and a request for another folder
cancels the one in flight.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

# path -> (sig, timestamp). sig = (int mtime, size): a changed file re-reads.
_cache: dict[str, tuple[tuple[int, int] | None, float]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 200_000

_EXIF_EXTS = {".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".webp", ".png",
              ".heic", ".heif", ".hif", ".avif"}
_BATCH = 150


def _parse_exif_dt(s) -> float | None:
    """EXIF 'YYYY:MM:DD HH:MM:SS' (or the index's ISO form) -> local epoch."""
    if not s:
        return None
    s = str(s).strip().replace("T", " ")
    if len(s) >= 19 and s[4] in ":-" and s[7] in ":-":
        try:
            return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]),
                            int(s[11:13]), int(s[14:16]), int(s[17:19])).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    return None


def _read_exif_time(path: str) -> float | None:
    if os.path.splitext(path)[1].lower() not in _EXIF_EXTS:
        return None
    from . import exif as exif_mod
    if not exif_mod._ensure_pil():
        return None
    if os.path.splitext(path)[1].lower() in (".heic", ".heif", ".hif", ".avif"):
        from . import heif_loader
        if not heif_loader._ensure_loaded():
            return None
    try:
        with exif_mod.Image.open(path) as im:       # lazy: header only
            ex = im.getexif()
            dt = ex.get_ifd(0x8769).get(36867) or ex.get(306)   # DateTimeOriginal / DateTime
    except Exception:
        return None
    return _parse_exif_dt(dt)


def _index_times(folder: str) -> dict[str, float]:
    """One indexed query for the folder's known capture dates."""
    out: dict[str, float] = {}
    try:
        from . import index as index_mod
        conn = index_mod.connect()
        try:
            rows = conn.execute(
                "SELECT path, date_taken FROM files WHERE parent = ? AND date_taken IS NOT NULL",
                (os.path.normcase(os.path.abspath(folder)),)).fetchall()
        finally:
            conn.close()
        for r in rows:
            ts = _parse_exif_dt(r["date_taken"])
            if ts is not None:
                out[os.path.normcase(r["path"])] = ts
    except Exception:
        pass
    return out


def times_now(items) -> dict[str, float]:
    """Best answer available right now, without touching the disk."""
    out: dict[str, float] = {}
    with _cache_lock:
        for it in items:
            hit = _cache.get(it.path)
            if hit is not None and hit[0] == it.sig:
                out[it.path] = hit[1]
            elif it.mtime is not None:
                out[it.path] = float(it.mtime)
    return out


class _Signals(QObject):
    batch = pyqtSignal(int, object)      # generation, {path: ts}


class _Task(QRunnable):
    def __init__(self, gen: int, folder: str, items: list, signals: _Signals, is_live):
        super().__init__()
        self._gen, self._folder, self._items = gen, folder, items
        self._signals, self._is_live = signals, is_live
        self.setAutoDelete(True)

    def run(self):
        from .thumb_store import lower_worker_priority
        lower_worker_priority()
        indexed = _index_times(self._folder)
        pending: dict[str, float] = {}
        last_emit = time.monotonic()
        for it in self._items:
            if not self._is_live(self._gen):
                return
            with _cache_lock:
                hit = _cache.get(it.path)
            if hit is not None and hit[0] == it.sig:
                continue                           # already known and current
            ts = indexed.get(os.path.normcase(os.path.abspath(it.path)))
            if ts is None:
                ts = _read_exif_time(it.path)
            if ts is None:
                ts = float(it.mtime) if it.mtime is not None else None
            if ts is None:
                continue
            with _cache_lock:
                if len(_cache) > _CACHE_MAX:
                    _cache.clear()
                _cache[it.path] = (it.sig, ts)
            pending[it.path] = ts
            now = time.monotonic()
            if len(pending) >= _BATCH or (pending and now - last_emit > 0.25):
                self._emit(pending)
                pending, last_emit = {}, now
        if pending:
            self._emit(pending)

    def _emit(self, batch):
        try:
            self._signals.batch.emit(self._gen, dict(batch))
        except RuntimeError:
            pass   # receiver gone (view closed)


class CaptureTimes(QObject):
    """Per-view source of capture times. ``updated`` fires (on the UI thread)
    whenever a background batch sharpened some of them."""

    updated = pyqtSignal()

    _pool: QThreadPool | None = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._times: dict[str, float] = {}
        self._signals = _Signals()
        self._signals.batch.connect(self._on_batch)

    @classmethod
    def _shared_pool(cls) -> QThreadPool:
        if cls._pool is None:
            cls._pool = QThreadPool()
            cls._pool.setMaxThreadCount(1)     # one quiet reader; never competes
        return cls._pool

    def request(self, folder: str, items: list, priority_items: list | None = None):
        """Start answering for ``items``; ``priority_items`` (e.g. the first
        photo of every row — all the timeline labels need) are read first."""
        self._gen += 1
        self._times = times_now(items)
        order = list(priority_items or [])
        seen = {it.path for it in order}
        order += [it for it in items if it.path not in seen]
        self._shared_pool().start(
            _Task(self._gen, folder, order, self._signals, self._is_live))

    def _is_live(self, gen: int) -> bool:
        return gen == self._gen

    def cancel(self):
        self._gen += 1

    def get(self, path: str) -> float | None:
        return self._times.get(path)

    def _on_batch(self, gen: int, batch):
        if gen != self._gen:
            return
        self._times.update(batch)
        self.updated.emit()
