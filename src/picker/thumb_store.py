"""Process-wide thumbnail memory + one shared, prioritised decode service.

Why this exists
---------------
Every view used to own its thumbnails: the folder mosaic, the gallery, the
viewer's filmstrip and the folder covers each kept a private dict of pixmaps and
spun up their own thread pools. Leaving a folder, typing a search letter, or
closing the viewer threw those pixmaps away, so the same JPEGs were decoded from
disk again and again, and four pools fought over the CPU.

Now there is exactly one of each:

* :class:`ThumbStore` — an LRU of decoded thumbnails **keyed by file path**, so a
  thumb decoded by the mosaic is instantly available to the filmstrip, the
  viewer's progressive placeholder, and the two-pane sort view. Its byte budget
  is a slice of *currently available* RAM (re-checked as it fills), so a big
  machine keeps whole libraries hot while a small one stays lean. Entries carry
  the file's (mtime, size) signature and are dropped if the file changes.

* :class:`ThumbService` — one decode pool for thumbnails, one for dimension
  headers, one single-slot pool for ffmpeg. Requests are **deduplicated**,
  **prioritised** (visible tiles jump the queue) and **cancellable** (a tile
  scrolled far out of view before its turn is simply never decoded). Results
  land in the store first, then a path-keyed signal tells whichever views care.

All UI-thread state (store, pending map) is touched only from the UI thread;
workers see nothing but their own arguments and talk back through signals.
"""
from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import (
    QBuffer, QByteArray, QIODevice, QObject, QRunnable, QSize, QThreadPool, QTimer,
    pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QImage, QImageReader, QPixmap

from . import sysmem

THUMB_MAX_DIM = 360            # cached thumb longest side
CACHE_DIRNAME = ".picker_cache"

_MB = 1024 * 1024


# ── Disk-cache helpers (shared by every thumbnail consumer) ────────────────────

def lower_worker_priority():
    """Drop the calling pool thread below the UI thread's priority so decode
    bursts never steal the cores that paint scroll frames."""
    from PyQt6.QtCore import QThread
    th = QThread.currentThread()
    if th is not None:
        th.setPriority(QThread.Priority.LowestPriority)


_npath_memo: dict[str, str] = {}
_NPATH_MEMO_MAX = 200_000


def npath(path: str) -> str:
    """Normalised cache key for a path.

    Called for every tile on every paint, so it avoids ``abspath`` (which calls
    ``getcwd`` and re-normalises) whenever the path is already absolute — every
    path the scanners produce is — and memoises the rest."""
    key = _npath_memo.get(path)
    if key is None:
        key = (os.path.normcase(path) if os.path.isabs(path)
               else os.path.normcase(os.path.abspath(path)))
        if len(_npath_memo) >= _NPATH_MEMO_MAX:
            _npath_memo.clear()
        _npath_memo[path] = key
    return key


def stat_sig(path: str) -> tuple[int, int] | None:
    """(int mtime, size) — the same fields the disk cache key hashes."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return int(st.st_mtime), int(st.st_size)


def cache_key(path: str, sig: tuple[int, int] | None = None) -> str:
    if sig is None:
        st = os.stat(path)
        sig = (int(st.st_mtime), st.st_size)
    raw = f"{path}|{sig[0]}|{sig[1]}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def cache_file(source_folder: str, path: str, sig: tuple[int, int] | None = None) -> Path:
    return Path(source_folder) / CACHE_DIRNAME / f"{cache_key(path, sig)}.jpg"


def _is_video(path: str) -> bool:
    from .media import is_video
    return is_video(path)


def decode_thumbnail(path: str, source_folder: str,
                     sig: tuple[int, int] | None = None) -> QImage | None:
    """Disk-cache-first thumbnail decode. Runs on a worker thread. Returns a
    placeholder tile (never None) for undecodable images so layouts settle."""
    if sig is None:
        sig = stat_sig(path)
        if sig is None:
            return None
    cf = cache_file(source_folder, path, sig)

    if _is_video(path):
        from .video_thumb import extract_thumb
        v_cache = cf.with_name(cf.stem + ".v.jpg")   # == video_thumb.cache_path_for_video
        img = QImage(str(v_cache)) if v_cache.exists() else QImage()
        if img.isNull():
            img = extract_thumb(path, v_cache) or QImage()
        if img.isNull():
            img = QImage(THUMB_MAX_DIM, int(THUMB_MAX_DIM * 9 / 16), QImage.Format.Format_RGB32)
            img.fill(QColor(28, 28, 28))
            img.setText(PLACEHOLDER_KEY, "1")
        return img

    if cf.exists():
        img = QImage(str(cf))
        if not img.isNull():
            return img

    img: QImage | None = None
    from . import heif_loader
    if heif_loader.is_heif(path) and heif_loader.available():
        img = heif_loader.load_heif(path, max_edge=THUMB_MAX_DIM)
    if img is None or img.isNull():
        # Read the bytes and close the file *before* decoding. Windows won't let
        # anyone delete/rename a file while it's open, so decoding straight from
        # the path held a lock for the whole decode — Explorer (or a move in the
        # Sort view) could fail with "file in use". Now it's held only for the
        # read, and a scaled JPEG decode from memory is no slower.
        buf = None
        try:
            if os.path.getsize(path) <= 256 * _MB:
                with open(path, "rb") as fh:
                    buf = QBuffer()
                    buf.setData(QByteArray(fh.read()))
                buf.open(QIODevice.OpenModeFlag.ReadOnly)
        except OSError:
            buf = None
        reader = QImageReader(buf) if buf is not None else QImageReader(path)
        if buf is not None:
            fmt = os.path.splitext(path)[1].lower().lstrip(".")
            if fmt in ("jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff", "gif", "svg", "svgz"):
                reader.setFormat(b"jpeg" if fmt == "jpg" else b"tiff" if fmt == "tif" else fmt.encode())
            reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        size = reader.size()
        is_svg = os.path.splitext(path)[1].lower() in (".svg", ".svgz")
        if size.isValid() and max(size.width(), size.height()) > 0:
            longest = max(size.width(), size.height())
            # Vectors rasterize UP to thumb size (crisp); rasters only scale down.
            if is_svg or longest > THUMB_MAX_DIM:
                scale = THUMB_MAX_DIM / longest
                reader.setScaledSize(QSize(max(1, round(size.width() * scale)),
                                           max(1, round(size.height() * scale))))
        img = reader.read()
    if img is None or img.isNull():
        return placeholder_image()   # never persisted as if it were the real thumb
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(cf), "JPEG", 82)
    except Exception:
        pass
    return img


PLACEHOLDER_KEY = "picker-placeholder"


def placeholder_image() -> QImage:
    """Grey tile for an image that couldn't be decoded. Tagged, so nothing
    downstream mistakes its 3:2 shape for the photo's real size."""
    img = QImage(THUMB_MAX_DIM, int(THUMB_MAX_DIM * 2 / 3), QImage.Format.Format_RGB32)
    img.fill(QColor(40, 40, 40))
    img.setText(PLACEHOLDER_KEY, "1")
    return img


def read_dimensions(path: str) -> tuple[int, int, bool]:
    """Header-only (w, h, exact). EXIF 90/270 orientation is applied so rotated
    phone shots get the right aspect on the first layout pass."""
    if _is_video(path):
        return 16, 9, False
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    sz = reader.size()
    if sz.isValid() and sz.width() > 0 and sz.height() > 0:
        w, h = sz.width(), sz.height()
        # Qt reports the *stored* size; a 90/270 EXIF rotation swaps it.
        # Every rotating transform has the Rotate90 bit (value 4) set. (PyQt6
        # enums don't convert with int() — that TypeError used to be swallowed
        # here, so every rotated portrait was measured as landscape.)
        try:
            if reader.transformation().value & 4:
                w, h = h, w
        except Exception:
            pass
        return w, h, True
    return 3, 2, False   # e.g. HEIC has no Qt header reader — guess, not exact


# ── RAM store ──────────────────────────────────────────────────────────────────

def _pm_bytes(pm: QPixmap) -> int:
    return max(1, pm.width() * pm.height() * max(1, pm.depth()) // 8)


class ThumbStore:
    """LRU of decoded thumbnails keyed by normalised path. UI thread only."""

    FRACTION = 0.12          # of currently-available RAM
    FLOOR = 96 * _MB
    CEILING = 2048 * _MB

    def __init__(self):
        self._lru: "OrderedDict[str, tuple[tuple[int, int] | None, QPixmap]]" = OrderedDict()
        self._bytes = 0
        self._budget = sysmem.budget(self.FRACTION, self.FLOOR, self.CEILING)
        self._puts = 0
        self._placeholders: set[str] = set()

    def get(self, path: str, sig: tuple[int, int] | None = None) -> QPixmap | None:
        key = npath(path)
        entry = self._lru.get(key)
        if entry is None:
            return None
        if sig is not None and entry[0] is not None and entry[0] != sig:
            self._drop(key)       # file changed on disk since we decoded it
            return None
        self._lru.move_to_end(key)
        return entry[1]

    def contains(self, path: str) -> bool:
        return npath(path) in self._lru

    def is_placeholder(self, path: str) -> bool:
        """True if ``path``'s entry is the grey can't-decode tile — its shape
        says nothing about the photo, so layouts must not learn from it."""
        return npath(path) in self._placeholders

    def put(self, path: str, pm: QPixmap, sig: tuple[int, int] | None = None, *,
            placeholder: bool = False) -> None:
        if pm is None or pm.isNull():
            return
        key = npath(path)
        if placeholder:
            self._placeholders.add(key)
        else:
            self._placeholders.discard(key)
        if key in self._lru:
            self._drop(key)
        self._lru[key] = (sig, pm)
        self._bytes += _pm_bytes(pm)
        self._puts += 1
        if self._puts % 128 == 0:
            # Re-read free RAM now and then: grow when memory frees up, shrink
            # when another app needs it.
            self._budget = sysmem.budget(self.FRACTION, self.FLOOR, self.CEILING)
        while self._bytes > self._budget and len(self._lru) > 1:
            old, (_s, opm) = self._lru.popitem(last=False)
            self._bytes -= _pm_bytes(opm)

    def alias(self, src_path: str, dst_path: str) -> None:
        """Make ``dst_path`` show ``src_path``'s thumb (e.g. right after a copy)."""
        entry = self._lru.get(npath(src_path))
        if entry is not None:
            self.put(dst_path, entry[1], None)

    def invalidate(self, path: str) -> None:
        self._drop(npath(path))

    def _drop(self, key: str) -> None:
        entry = self._lru.pop(key, None)
        if entry is not None:
            self._bytes -= _pm_bytes(entry[1])

    def stats(self) -> dict:
        return {"entries": len(self._lru), "mb": round(self._bytes / _MB, 1),
                "budget_mb": round(self._budget / _MB, 1)}


# ── Workers ────────────────────────────────────────────────────────────────────

class _Signals(QObject):
    thumb = pyqtSignal(str, str, QImage, object)        # key, path, image, sig
    dims = pyqtSignal(str, object)                      # source, [(path,w,h,exact,sig)]


class _ThumbTask(QRunnable):
    def __init__(self, key: str, path: str, source: str, sig, signals: _Signals):
        super().__init__()
        self.key, self.path, self.source, self.sig = key, path, source, sig
        self.signals = signals
        self.setAutoDelete(False)   # the service holds the Python ref (cancellable)

    def run(self):
        lower_worker_priority()
        sig = self.sig or stat_sig(self.path)
        img = None
        try:
            img = decode_thumbnail(self.path, self.source, sig)
        except Exception:
            img = None
        try:
            self.signals.thumb.emit(self.key, self.path,
                                    img if img is not None else QImage(), sig)
        except RuntimeError:
            pass   # app shutting down: the receiver is already gone


class _DimsTask(QRunnable):
    def __init__(self, source: str, paths: list[str], signals: _Signals):
        super().__init__()
        self.source, self.paths, self.signals = source, paths, signals
        self.setAutoDelete(True)

    def run(self):
        lower_worker_priority()
        out = []
        for p in self.paths:
            try:
                w, h, exact = read_dimensions(p)
            except Exception:
                w, h, exact = 3, 2, False
            out.append((p, w, h, exact, stat_sig(p) if exact else None))
        try:
            self.signals.dims.emit(self.source, out)
        except RuntimeError:
            pass   # app shutting down: the receiver is already gone


class ThumbService(QObject):
    """Deduplicated, prioritised, cancellable thumbnail + dimension decoding.

    ``thumb_ready(path)`` fires after the pixmap is already in the store, so a
    slot just calls ``store().get(path)``. ``dims_ready(source, results)`` carries
    header sizes; exact ones are persisted to ``dim_cache`` here, once.
    """

    thumb_ready = pyqtSignal(str)
    dims_ready = pyqtSignal(str, object)

    # Visible tiles use PRI_VISIBLE; look-ahead margin PRI_NEAR; speculative
    # prefetch (hovered folder, next folder) PRI_PREFETCH.
    PRI_VISIBLE = 10
    PRI_NEAR = 5
    PRI_PREFETCH = 0

    DIMS_BATCH = 48

    def __init__(self):
        super().__init__()
        cpu = os.cpu_count() or 4
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(8, cpu - 1)))
        self._video_pool = QThreadPool(self)
        self._video_pool.setMaxThreadCount(1)    # ffmpeg seeks thrash a shared disk
        self._dims_pool = QThreadPool(self)
        self._dims_pool.setMaxThreadCount(max(2, cpu // 2))
        self._signals = _Signals()
        self._signals.thumb.connect(self._on_thumb)
        self._signals.dims.connect(self._on_dims)
        self._pending: dict[str, tuple[_ThumbTask, QThreadPool, int]] = {}
        self._dirty_sources: set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(1500)
        self._flush_timer.timeout.connect(self._flush_dims)

    # ── thumbnails ────────────────────────────────────────────────────────────
    def request(self, path: str, source_folder: str, priority: int = PRI_NEAR,
                sig: tuple[int, int] | None = None) -> bool:
        """Queue a decode unless it's already in RAM. Returns True if the thumb
        is (or will be) coming via ``thumb_ready``; False if already stored."""
        if _STORE.get(path, sig) is not None:
            return False
        key = npath(path)
        pend = self._pending.get(key)
        if pend is not None:
            task, pool, pri = pend
            if priority > pri and pool.tryTake(task):
                pool.start(task, priority)            # bump it up the queue
                self._pending[key] = (task, pool, priority)
            return True
        task = _ThumbTask(key, path, source_folder, sig, self._signals)
        pool = self._video_pool if _is_video(path) else self._pool
        self._pending[key] = (task, pool, priority)
        pool.start(task, priority)
        return True

    def cancel(self, path: str) -> None:
        """Drop a queued (not yet running) decode."""
        key = npath(path)
        pend = self._pending.get(key)
        if pend is not None and pend[1].tryTake(pend[0]):
            self._pending.pop(key, None)

    def is_pending(self, path: str) -> bool:
        return npath(path) in self._pending

    @pyqtSlot(str, str, QImage, object)
    def _on_thumb(self, key: str, path: str, img: QImage, sig):
        self._pending.pop(key, None)
        if img.isNull():
            return
        _STORE.put(path, QPixmap.fromImage(img), sig,
                   placeholder=img.text(PLACEHOLDER_KEY) == "1")
        self.thumb_ready.emit(path)

    # ── dimensions ───────────────────────────────────────────────────────────
    def request_dims(self, source_folder: str, paths: list[str]) -> None:
        for i in range(0, len(paths), self.DIMS_BATCH):
            self._dims_pool.start(_DimsTask(source_folder, paths[i:i + self.DIMS_BATCH],
                                            self._signals))

    @pyqtSlot(str, object)
    def _on_dims(self, source: str, results):
        from . import dim_cache
        for p, w, h, exact, sig in results:
            if exact and sig is not None:
                dim_cache.put_sig(source, p, w, h, sig)
                self._dirty_sources.add(source)
        if self._dirty_sources:
            self._flush_timer.start()
        self.dims_ready.emit(source, results)

    def note_dims(self, source: str) -> None:
        """A view persisted corrected dims via dim_cache.put — flush them soon."""
        self._dirty_sources.add(source)
        self._flush_timer.start()

    def _flush_dims(self):
        from . import dim_cache
        sources, self._dirty_sources = self._dirty_sources, set()
        if not sources:
            return
        import threading
        # JSON encode + atomic write off the UI thread.
        threading.Thread(target=lambda: [dim_cache.flush(s) for s in sources],
                         daemon=True).start()

    def shutdown(self, wait_ms: int = 300) -> None:
        for pool in (self._pool, self._video_pool, self._dims_pool):
            pool.clear()
        self._pending.clear()
        for pool in (self._pool, self._dims_pool):
            pool.waitForDone(wait_ms)


_STORE = ThumbStore()
_SERVICE: ThumbService | None = None


def store() -> ThumbStore:
    return _STORE


def service() -> ThumbService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ThumbService()
    return _SERVICE
