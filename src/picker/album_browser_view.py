"""Folder-tree browser for a source root.

Picasa-style: at any level the user sees the folders inside the current path
first (each rendered as a folder tile with a cover thumbnail and total image
count) followed by the loose images sitting directly in that folder. Clicking
a folder navigates into it (push to the nav stack); clicking an image opens
the slideshow over the current folder's images.

Opening a folder is instant regardless of subtree size: a single scandir lists
the subfolders (with placeholder counts) and the folder's own images, then each
subfolder's real recursive count + cover stream in from a background pool and
fill the tiles in place (see _start_folder_stats). Empty subfolders are pruned
once their walk completes; the enriched result is cached for instant re-visits.
"""
from __future__ import annotations

import bisect
import html
import os
import threading
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QSize, QRect, QObject, QRunnable, QThreadPool, QTimer, pyqtSignal, pyqtSlot
)
from PyQt6.QtGui import (
    QImage, QImageReader, QPixmap, QColor, QPainter, QPen, QBrush, QFont,
    QPolygonF
)
from PyQt6.QtCore import QEvent, QPointF, QRectF, QVariantAnimation
from PyQt6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGridLayout, QSizePolicy, QApplication, QFrame, QMenu, QMessageBox,
    QFileDialog, QProgressBar, QLineEdit
)

from . import theme as theme_mod
from . import motion as motion_mod
from . import capture_time
from . import timeline as timeline_mod
from . import settings as settings_mod
from . import instrument as inst
from . import log
from . import album as album_mod
from .album import Folder, ImageItem, scan_path
from . import dim_cache
from . import thumb_store
from .icon import menu_icon
from .thumb_store import (
    cache_file as thumb_cache_file,
    THUMB_MAX_DIM,
    lower_worker_priority,
    npath as _npath,
)


TILE_W = 230
TILE_H = 200
COVER_H = 150
TILE_GAP = 16
COVER_LONGEST = 360

# Module-level listing cache keyed by folder path: (folders, images, dir mtime).
# Survives across AlbumBrowserView instances and is warmed speculatively (the
# subfolders of whatever you're looking at are listed in the background), so
# drilling into a folder usually renders from memory with zero disk access.
# Entries are revalidated against the directory's mtime — which changes whenever
# a file is added, removed or renamed in it — so a stale listing is shown for at
# most one frame and then corrected in place.
_SCAN_CACHE: "OrderedDict[str, tuple[list, list, int]]" = OrderedDict()
_SCAN_CACHE_MAX = 512
_SCAN_LOCK = threading.Lock()


def invalidate_scan_cache(path: str | None = None):
    """Drop one entry (path given) or wipe the whole cache. Recursive counts of
    the folder and its ancestors are dropped too — they include its files."""
    with _SCAN_LOCK:
        if path is None:
            _SCAN_CACHE.clear()
        else:
            _SCAN_CACHE.pop(path, None)
    album_mod.invalidate_folder_stats(path)


def _dir_mtime(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return -1


def _cache_get(path: str):
    with _SCAN_LOCK:
        hit = _SCAN_CACHE.get(path)
        if hit is not None:
            _SCAN_CACHE.move_to_end(path)
            return hit
    # Nothing in memory — the index saved by the previous run may still have it
    # (validated against the directory's mtime). Only stat when a record exists:
    # this runs on the UI thread (e.g. 32x per render for subfolder prefetch),
    # and a stat on a sleeping or network drive can take hundreds of ms.
    from . import listing_cache
    if not listing_cache.has(path):
        return None
    restored = listing_cache.take(path, _dir_mtime(path))
    if restored is not None:
        _cache_put(path, *restored)
        # A folder's mtime doesn't change when a file inside is edited in
        # place, so a listing from the last session is shown at once but
        # re-checked in the background the first time it's opened.
        _UNVERIFIED.add(path)
        return restored
    return None


_UNVERIFIED: set[str] = set()   # restored from disk, not yet re-listed this session


def _cache_put(path: str, folders, items, mtime: int, dims: dict | None = None):
    with _SCAN_LOCK:
        _SCAN_CACHE[path] = (folders, items, mtime, dims or {})
        _SCAN_CACHE.move_to_end(path)
        while len(_SCAN_CACHE) > _SCAN_CACHE_MAX:
            _SCAN_CACHE.popitem(last=False)
    from . import listing_cache
    listing_cache.mark_dirty()   # persisted on exit so the next launch is instant


def _px_bytes(pm: QPixmap) -> int:
    return max(1, pm.width() * pm.height() * max(1, pm.depth()) // 8)


def _remember_dims(item_path: str, w: int, h: int) -> None:
    """A thumbnail proved an item's real size: fold it into the in-memory
    listing so the next visit (and the persisted index) lays it out right
    without waiting for the thumbnail again."""
    folder = os.path.dirname(item_path)
    with _SCAN_LOCK:
        hit = _SCAN_CACHE.get(folder)
        if hit is not None:
            hit[3][item_path] = (w, h)


def _list_folder(path: str, source: str | None = None):
    """scandir listing + dir mtime + known image sizes, for workers.

    The mtime is captured *before* listing so a change racing the scan
    invalidates the entry rather than hiding. Dimension-cache lookups happen
    here too — off the UI thread, and carried with the listing so a revisit
    lays the grid out correctly without touching anything."""
    mtime = _dir_mtime(path)
    folders, items = scan_path(path, deep=False)
    dims: dict = {}
    if source and items:
        try:
            hits = dim_cache.get_many(
                source, [(it.path, it.mtime, it.size) for it in items])
            dims = {it.path: d for it, d in zip(items, hits) if d is not None}
        except Exception:
            dims = {}
    return folders, items, mtime, dims


# ── Move / copy + recent destinations ──────────────────────────────────────────

def recent_target_folders() -> list[str]:
    """Last ≤3 move/copy destinations that still exist on disk."""
    raw = settings_mod.get("recent_target_folders") or []
    out: list[str] = []
    seen: set[str] = set()
    for f in raw:
        if not isinstance(f, str) or not os.path.isdir(f):
            continue
        key = os.path.normcase(os.path.abspath(f))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
        if len(out) >= 3:
            break
    return out


def push_recent_target(folder: str) -> None:
    """Promote `folder` to the front of the recent-destinations list (cap 3)."""
    folder = os.path.abspath(folder)
    key = os.path.normcase(folder)
    cur = settings_mod.get("recent_target_folders") or []
    cur = [f for f in cur if isinstance(f, str)
           and os.path.normcase(os.path.abspath(f)) != key]
    cur.insert(0, folder)
    settings_mod.set_value("recent_target_folders", cur[:3])


def _unique_dest(dest: str) -> str:
    """If `dest` exists, append ' (n)' before the extension until free."""
    if not os.path.exists(dest):
        return dest
    root, ext = os.path.splitext(dest)
    n = 1
    while True:
        cand = f"{root} ({n}){ext}"
        if not os.path.exists(cand):
            return cand
        n += 1


def make_conflict_resolver(parent):
    """Build a per-conflict resolver honouring the `conflict_default` setting.
    'ask' shows the side-by-side ConflictDialog; otherwise the fixed policy is
    applied to every clash. Returns a callable (src, dest) -> choice string of
    'replace' | 'rename' | 'skip' | 'cancel'."""
    policy = settings_mod.get("conflict_default") or "ask"

    def resolve(src: str, dest: str) -> str:
        if policy in ("rename", "replace", "skip"):
            return policy
        from . import conflict_dialog
        return conflict_dialog.ask(parent, src, dest)

    return resolve


def transfer_files(paths: list[str], dest_dir: str, *, move: bool,
                   conflict_cb=None) -> tuple[list[str], list[str], bool]:
    """Move/copy each path into dest_dir.

    On a name clash, `conflict_cb(src, dest)` decides: 'replace' | 'rename'
    | 'skip' | 'cancel' ('cancel' aborts the rest of the batch). With no
    callback the legacy behaviour (auto-rename) is used.

    Returns (ok_sources, errors, cancelled) — ok_sources are the source paths
    that were successfully transferred, so callers can update their model
    without rescanning.
    """
    import shutil
    ok: list[str] = []
    errors: list[str] = []
    for src in paths:
        base = os.path.basename(src)
        try:
            dst = os.path.join(dest_dir, base)
            if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
                continue  # same location — skip silently
            if os.path.exists(dst):
                choice = conflict_cb(src, dst) if conflict_cb else "rename"
                if choice == "cancel":
                    return ok, errors, True
                if choice == "skip":
                    continue
                if choice == "rename":
                    dst = _unique_dest(dst)
                elif choice == "replace":
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                else:
                    dst = _unique_dest(dst)
            try:
                if move:
                    shutil.move(src, dst)
                else:
                    shutil.copy2(src, dst)
            except PermissionError:
                # WinError 5 (Access Denied) — most often the read-only
                # attribute. Clear it on src (and on dst if we're replacing)
                # and retry once before giving up.
                _clear_readonly(src)
                if os.path.exists(dst):
                    _clear_readonly(dst)
                if move:
                    shutil.move(src, dst)
                else:
                    shutil.copy2(src, dst)
            ok.append(src)
        except PermissionError:
            errors.append(f"{base}: access denied — file is read-only or open "
                          f"in another program (WinError 5)")
        except Exception as e:
            errors.append(f"{base}: {e}")
    return ok, errors, False


def _clear_readonly(path: str) -> None:
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


def transfer_one(src: str, dest_dir: str, *, move: bool) -> tuple[str | None, str | None]:
    """Move/copy a single file into dest_dir, non-interactively (name clashes are
    resolved by keeping both — the ` (n)` rename). Returns (final_dest_path, error).

    Used by the viewer's one-key Quick-Folder sends, where the exact written path
    is needed so the action can be undone. Handles the read-only/WinError-5 retry
    the same way as `transfer_files`."""
    import shutil
    base = os.path.basename(src)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        dst = os.path.join(dest_dir, base)
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            return None, "source and destination are the same folder"
        if os.path.exists(dst):
            dst = _unique_dest(dst)
        try:
            shutil.move(src, dst) if move else shutil.copy2(src, dst)
        except PermissionError:
            _clear_readonly(src)
            if os.path.exists(dst):
                _clear_readonly(dst)
            shutil.move(src, dst) if move else shutil.copy2(src, dst)
        return dst, None
    except PermissionError:
        return None, f"{base}: access denied — read-only or open in another program"
    except Exception as e:
        return None, f"{base}: {e}"


def _draw_play_badge(p: QPainter, rect: QRect, *, size: int = 38) -> None:
    """Centered translucent disc with a white play triangle. Marks video tiles."""
    cx = rect.center().x()
    cy = rect.center().y()
    r = size // 2
    # Disc
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawEllipse(QPointF(float(cx), float(cy)), r, r)
    p.setBrush(QColor(255, 255, 255, 230))
    # Triangle, slightly nudged right so it looks optically centered
    tri = QPolygonF([
        QPointF(cx - r * 0.30, cy - r * 0.45),
        QPointF(cx - r * 0.30, cy + r * 0.45),
        QPointF(cx + r * 0.55, cy),
    ])
    p.drawPolygon(tri)


def _css_color(key: str) -> str:
    val = theme_mod.c(key)
    return val if isinstance(val, str) else val.name()


def _qc(key: str) -> QColor:
    val = theme_mod.c(key)
    return val if isinstance(val, QColor) else QColor(val)


# ── Background folder listing + speculative prefetch ──────────────────────────
# Covers and image thumbnails go through the shared thumb_store service (one
# RAM cache + one prioritised pool for the whole app). Folder *listings* run on
# this small pool: the folder you clicked (never on the UI thread, so a slow or
# network drive can't freeze the window) and, at lower priority, the subfolders
# you're likely to open next.

class _ListSignals(QObject):
    listed = pyqtSignal(int, str, object)    # (gen, path, (folders, items, mtime))


class _ListTask(QRunnable):
    def __init__(self, gen: int, path: str, signals: _ListSignals | None,
                 source: str | None = None):
        super().__init__()
        self._gen, self._path, self._signals = gen, path, signals
        self._source = source or path
        self.setAutoDelete(True)

    def run(self):
        lower_worker_priority()
        try:
            result = _list_folder(self._path, self._source)
        except Exception:
            result = ([], [], -1, {})
        _cache_put(self._path, *result)
        if self._signals is not None:
            try:
                self._signals.listed.emit(self._gen, self._path, result)
            except RuntimeError:
                pass   # view already destroyed


class _PrefetchTask(QRunnable):
    """List a folder into the scan cache (if not already there) and parse the
    source's dimension index, so opening it later costs nothing."""
    def __init__(self, paths: list[str], source: str):
        super().__init__()
        self._paths, self._source = paths, source
        self.setAutoDelete(True)

    def run(self):
        lower_worker_priority()
        try:
            dim_cache.preload(self._source)
        except Exception:
            pass
        for p in self._paths:
            if _cache_get(p) is not None:
                continue
            try:
                _cache_put(p, *_list_folder(p, self._source))
            except Exception:
                continue


_LIST_POOL: QThreadPool | None = None


def _list_pool() -> QThreadPool:
    global _LIST_POOL
    if _LIST_POOL is None:
        _LIST_POOL = QThreadPool()
        _LIST_POOL.setMaxThreadCount(2)
    return _LIST_POOL


# ── Background folder-stat streamer ────────────────────────────────────────────
# scan_path(deep=False) opens a folder instantly with placeholder counts; this
# fills in each subfolder's real recursive count + cover off the UI thread and
# streams results back as they land. The walks are CPU/IO-bound and independent,
# so several workers pull from one shared, ordered queue — using the machine's
# cores while still resolving the top (visible) folders first. A generation token
# lets the view discard results from a walk it has already navigated away from.

class _FolderStatSignals(QObject):
    ready = pyqtSignal(int, str, int, str)   # (gen, folder_path, count, cover)
    done = pyqtSignal(int)                    # (gen,)


class _FolderStatWork:
    """Thread-safe, in-order work queue shared by the folder-stat workers."""
    def __init__(self, gen: int, paths: list[str], workers: int):
        self.gen = gen
        self.cancelled = False
        self._paths = paths
        self._i = 0
        self._active = workers          # workers still running
        self._lock = threading.Lock()

    def next(self) -> str | None:
        with self._lock:
            if self.cancelled or self._i >= len(self._paths):
                return None
            p = self._paths[self._i]
            self._i += 1
            return p

    def finish_one(self) -> bool:
        """Mark one worker done; returns True for the last one to leave."""
        with self._lock:
            self._active -= 1
            return self._active == 0


class _FolderStatTask(QRunnable):
    def __init__(self, work: _FolderStatWork, signals: _FolderStatSignals):
        super().__init__()
        self._work = work
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        lower_worker_priority()
        from . import album as _album
        work = self._work
        while True:
            path = work.next()
            if path is None:
                break
            try:
                count, cover = _album.folder_stat(path, cancelled=lambda: work.cancelled)
            except Exception:
                count, cover = 0, ""
            if work.cancelled:
                break
            self._signals.ready.emit(work.gen, path, count, cover)
        # Only the last worker to finish emits done (once).
        if work.finish_one() and not work.cancelled:
            self._signals.done.emit(work.gen)


# ── Tile widgets ───────────────────────────────────────────────────────────────

def _draw_cover_into(p: QPainter, pm: QPixmap, target: QRect):
    """Object-fit: cover. Scale-by-expanding then center-clip."""
    scaled = pm.scaled(
        target.width(), target.height(),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    sx = max(0, (scaled.width() - target.width()) // 2)
    sy = max(0, (scaled.height() - target.height()) // 2)
    p.drawPixmap(target, scaled, QRect(sx, sy, target.width(), target.height()))


class _BaseTile(QWidget):
    clicked = pyqtSignal(object)   # Folder or ImageItem

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._item = item
        self._cover: QPixmap | None = None
        self._hover = False
        self._focused = False
        # Keyboard-accessible: tiles take focus so arrows move between them and
        # Enter/Space opens — no mouse required.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._nav_cb = None    # browser sets this to handle arrow navigation
        self._hover_cb = None  # browser sets this for hover-intent prefetch
        self._scaled_cover: QPixmap | None = None
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    @property
    def item(self):
        return self._item

    def set_cover(self, pm: QPixmap):
        self._cover = pm
        self._scaled_cover = None   # re-crop lazily at paint time, once
        self.update()

    def enterEvent(self, event):
        self._hover = True
        if self._hover_cb is not None:
            self._hover_cb(self)
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def focusInEvent(self, event):
        self._focused = True
        self.update()

    def focusOutEvent(self, event):
        self._focused = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit(self._item)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._item)
            return
        if self._nav_cb is not None and key in (
                Qt.Key.Key_Left, Qt.Key.Key_Right,
                Qt.Key.Key_Up, Qt.Key.Key_Down):
            if self._nav_cb(self, key):
                return
        # Escape (and anything else) bubbles up so the browser can go back.
        event.ignore()


class FolderTile(_BaseTile):
    def __init__(self, folder: Folder, parent=None):
        super().__init__(folder, parent)
        # Count/cover may still be unknown (lazy scan) — kept mutable and filled
        # in by set_stat() once the background walk lands.
        self._count = folder.image_count
        self._cover_path = folder.cover_path

    def cover_path(self) -> str:
        return self._cover_path

    def set_stat(self, count: int, cover_path: str):
        self._count = count
        self._cover_path = cover_path
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        r = self.rect()
        inst.draw_deck(p, r)

        cover_rect = QRect(r.left() + 1, r.top() + 1, r.width() - 2, COVER_H)
        p.fillRect(cover_rect, _qc("well"))

        if self._cover and not self._cover.isNull():
            # Smooth-scaling the cover on every hover repaint was wasted work —
            # crop/scale it once and reuse.
            if self._scaled_cover is None:
                self._scaled_cover = QPixmap(cover_rect.size())
                self._scaled_cover.fill(_qc("well"))
                sp = QPainter(self._scaled_cover)
                _draw_cover_into(sp, self._cover, QRect(0, 0, cover_rect.width(),
                                                          cover_rect.height()))
                sp.end()
            p.drawPixmap(cover_rect.topLeft(), self._scaled_cover)
        else:
            inst.draw_icon(p, "folder",
                           QRectF(cover_rect.center().x() - 15,
                                  cover_rect.center().y() - 15, 30, 30),
                           _qc("rail_light"), 1.6)

        inst.draw_rail(p, r.left() + 1, cover_rect.bottom() + 1, r.right() - 1)

        # Plate legend: name in silkscreen caps, count as a readout value.
        p.setFont(theme_mod.legend_font(9, tracking=1.1))
        p.setPen(_qc("fg"))
        metrics = p.fontMetrics()
        elided = metrics.elidedText(self.item.name,
                                    Qt.TextElideMode.ElideMiddle, TILE_W - 24)
        p.drawText(QRect(10, COVER_H + 9, TILE_W - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        p.setFont(theme_mod.readout_font(10, bold=False))
        n = self._count
        if n < 0:
            p.setPen(_qc("screen_dim"))
            caption = "COUNTING"
        else:
            p.setPen(_qc("muted"))
            caption = f"{n:,} PHOTO{'S' if n != 1 else ''}"
        p.drawText(QRect(10, COVER_H + 26, TILE_W - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, caption)
        # A folder is a folder: silkscreened on the plate, not a floating badge.
        p.setFont(theme_mod.legend_font(7, tracking=1.4))
        p.setPen(_qc("faint"))
        p.drawText(QRect(TILE_W - 74, COVER_H + 26, 64, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "FOLDER")

        if self._focused:
            inst.draw_brackets(p, cover_rect, _qc("focus"), arm=16, width=2, inset=4)
            p.setPen(QPen(_qc("focus"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(1, 1, -2, -2))
        elif self._hover:
            inst.draw_brackets(p, cover_rect, _qc("cap_alt"), arm=13, width=2, inset=4)
        p.end()
class _ImageMosaic(QWidget):
    """Justified mosaic of images. Tiles keep their aspect ratio (no crop) and
    pack into centred rows, driven by a list of ``ImageItem``.

    Built to stay instant at any folder size:

    * Aspect ratios come from the persistent dimension cache in one batch lookup
      that needs **no syscalls** (the listing already carried each file's stat),
      so a folder seen before lays out correctly on the very first paint.
      Unknown sizes are read in batches by the shared service and relaid out on
      a short debounce instead of every N results.
    * Thumbnails come from the process-wide RAM store; misses are requested from
      the shared decode service with visible tiles at the highest priority, and
      queued decodes for tiles that scrolled far away are cancelled.
    * Painting, hover and hit-testing only touch the rows in view (binary search
      over row tops) rather than scanning every tile.
    * ``set_items`` swaps the item list in place (search filtering, live folder
      changes) keeping aspects, selection and cursor by path — no rebuild.

    ``sort_mode`` adapts the input model for the two-pane Sort view: a click
    focuses instead of opening, Space/Enter are emitted for the view to act on,
    and ``set_marked`` flags tiles (e.g. "already in the destination")."""

    clicked = pyqtSignal(int)            # image index in the list passed in
    removed = pyqtSignal(list)           # paths gone from this folder (move/delete)
    scroll_to = pyqtSignal(int, int)     # (x, y) the parent scroll area should reveal
    layout_changed = pyqtSignal()        # rows moved (timeline relabels)
    row_height_changed = pyqtSignal(int)   # zoom by wheel/keys, so the knob follows
    load_progress = pyqtSignal(int, int) # (headers_done, total) — drives the load bar
    reload_requested = pyqtSignal()      # folder needs a rescan (e.g. rotate wrote new files)
    selection_changed = pyqtSignal(int)  # number of selected tiles (drives the bulk bar)
    space_pressed = pyqtSignal()         # sort mode: act on cursor/selection
    enter_pressed = pyqtSignal()         # sort mode: preview cursor tile
    cursor_changed = pyqtSignal(int)     # keyboard/mouse focus tile moved

    ROW_HEIGHT = 200
    ROW_HEIGHT_MIN = 90
    ROW_HEIGHT_MAX = 520
    ZOOM_FACTOR = 1.12
    SPACING = 1                          # hairline between photos
    MAX_GROW = 1.6                       # a row may grow this much to fill the width
    HOVER_BORDER = 3
    LOOKAHEAD = 900                      # px above/below the viewport to pre-decode
    SCALED_BUDGET = 96 * 1024 * 1024     # bytes of tile-sized pixmaps kept ready to blit

    def __init__(self, items: list, source_folder: str, parent=None, *,
                 sort_mode: bool = False, cached_dims: dict | None = None,
                 row_height: int | None = None):
        super().__init__(parent)
        self._items: list = list(items)
        self._source = source_folder
        self._sort_mode = sort_mode
        self._aspects: list[float] = []
        self._exact: list[bool] = []
        self._index_by_path: dict[str, int] = {}
        # Layout: tiles are in index order, so _rects[idx] is that tile's rect.
        self._rects: list[QRect] = []
        self._row_tops: list[int] = []
        self._row_starts: list[int] = []     # first item index of each row
        self._viewport_width = 0
        self._total_height = 0
        self._hover_idx = -1
        self._row_height = max(self.ROW_HEIGHT_MIN, min(self.ROW_HEIGHT_MAX,
                                   int(row_height or self.ROW_HEIGHT)))
        self._marked: set[str] = set()       # lower-cased names flagged with a badge
        self._scaled: "OrderedDict[str, tuple[int, QPixmap]]" = OrderedDict()
        self._scaled_bytes = 0

        # Multi-selection (shift = range from anchor, ctrl = toggle).
        self._selected: set[int] = set()
        self._anchor: int = -1
        self._cursor: int = -1          # keyboard focus tile (arrow-key navigation)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._store = thumb_store.store()
        self._svc = thumb_store.service()
        self._svc.thumb_ready.connect(self._on_thumb_ready)
        self._svc.dims_ready.connect(self._on_dims_ready)
        self._requested: set[str] = set()     # paths this view has queued
        self._last_scan_rect = QRect()
        self._dims_pending: set[str] = set()
        self._unknown: set[int] = set()       # tiles whose real size we don't know yet
        self._estimate = 1.5                  # running average aspect for those
        self._headers_done = 0
        self._stopped = False

        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(60)
        self._relayout_timer.timeout.connect(self._recompute_layout)

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), _qc("canvas_bg"))
        self.setPalette(pal)

        self._resolve_aspects(range(len(self._items)), cached_dims)

    # ── Aspect resolution ─────────────────────────────────────────────────────

    def _reindex(self):
        self._index_by_path = {it.path: i for i, it in enumerate(self._items)}

    def _resolve_aspects(self, indices, cached_dims: dict | None = None):
        """Fill aspects for ``indices`` from the dimension cache.

        **Nothing is read from disk here.** Sizes already known (from a previous
        visit, or resolved by the listing worker and handed over in
        ``cached_dims``) are applied immediately; the rest get the folder's
        running average aspect and are read *only if they come near the
        viewport* — see :meth:`_request_visible_dims`. Reading all 10,000 headers
        up front cost a minute of disk churn on a first visit and starved the
        handful of thumbnails actually on screen."""
        self._reindex()
        n = len(self._items)
        if len(self._aspects) != n:
            self._aspects = (self._aspects + [1.5] * n)[:n]
            self._exact = (self._exact + [False] * n)[:n]
        indices = list(indices)
        cached_dims = dict(cached_dims or {})
        # The listing may predate sizes learned since (an empty {} on a first
        # visit): fill the gaps from the dimension cache instead of guessing.
        missing = [i for i in indices
                   if self._items[i].path not in cached_dims and self._items[i].mtime is not None]
        if missing:
            hits = dim_cache.get_many(
                self._source,
                [(self._items[i].path, self._items[i].mtime, self._items[i].size)
                 for i in missing])
            cached_dims.update({self._items[i].path: d for i, d in zip(missing, hits) if d})
        for i in indices:
            dims = cached_dims.get(self._items[i].path)
            if dims is not None and dims[1] > 0:
                self._aspects[i] = dims[0] / dims[1]
                self._exact[i] = True
            else:
                self._unknown.add(i)
        self._refresh_estimate()
        for i in list(self._unknown):
            if i < n and not self._exact[i]:
                self._aspects[i] = self._estimate
            else:
                self._unknown.discard(i)
        # Nothing long-running to report any more: the grid is usable at once.
        self._headers_done = n
        self.load_progress.emit(n, n)

    def _refresh_estimate(self):
        """Average aspect of the tiles we do know — a much better guess for the
        rest than a fixed 3:2, so the layout barely moves as they resolve."""
        known = [a for a, ex in zip(self._aspects, self._exact) if ex]
        if known:
            self._estimate = sum(known) / len(known)

    def _request_visible_dims(self, index_range):
        """Read headers only for not-yet-known tiles near the viewport."""
        if not self._unknown:
            return
        want = []
        for idx in index_range:
            if idx in self._unknown:
                p = self._items[idx].path
                if p not in self._dims_pending:
                    self._dims_pending.add(p)
                    want.append(p)
        if want:
            self._svc.request_dims(self._source, want)

    @pyqtSlot(str, object)
    def _on_dims_ready(self, source: str, results):
        if self._stopped or source != self._source or not self._dims_pending:
            return
        changed = False
        for p, w, h, exact, _sig in results:
            if p not in self._dims_pending:
                continue
            self._dims_pending.discard(p)
            idx = self._index_by_path.get(p)
            if idx is None:
                continue
            # Either way the header has had its one try: a guess (video, HEIC)
            # must not re-request forever — the thumbnail settles it instead.
            self._unknown.discard(idx)
            if exact and h > 0 and not self._exact[idx]:
                self._aspects[idx] = w / h
                self._exact[idx] = True
                changed = True
        if changed:
            self._refresh_estimate()
            self._relayout_timer.start()      # coalesce bursts

    # ── Public API ────────────────────────────────────────────────────────────

    def set_viewport_width(self, width: int):
        if width != self._viewport_width:
            self._viewport_width = width
            self._recompute_layout()

    def set_row_height(self, h: int):
        h = max(self.ROW_HEIGHT_MIN, min(self.ROW_HEIGHT_MAX, int(h)))
        if h != self._row_height:
            self._row_height = h
            self._recompute_layout()
            self.row_height_changed.emit(h)

    def row_height(self) -> int:
        return self._row_height

    def items(self) -> list:
        return self._items

    def cursor_item(self):
        return self._items[self._cursor] if 0 <= self._cursor < len(self._items) else None

    def set_marked(self, names: set[str]):
        """Flag tiles whose lower-cased filename is in ``names`` (sort view)."""
        if names != self._marked:
            self._marked = set(names)
            self.update()

    def aspect_of(self, path: str) -> float | None:
        idx = self._index_by_path.get(path)
        return self._aspects[idx] if idx is not None and self._exact[idx] else None

    def set_items(self, items: list, seed_aspects: dict | None = None,
                  cached_dims: dict | None = None) -> None:
        """Replace the item list in place, keeping per-path state (aspects,
        selection, cursor) so nothing re-decodes or jumps. ``seed_aspects``
        pre-fills known aspect ratios for new paths (e.g. a just-copied file
        takes its source's shape) so they need no header read."""
        old_items = self._items
        old_aspects = {it.path: (self._aspects[i], self._exact[i])
                       for i, it in enumerate(old_items) if i < len(self._aspects)}
        if seed_aspects:
            for p, a in seed_aspects.items():
                if a and p not in old_aspects:
                    old_aspects[p] = (a, True)
        sel_paths = {old_items[i].path for i in self._selected if i < len(old_items)}
        cur_path = old_items[self._cursor].path if 0 <= self._cursor < len(old_items) else None
        anchor_path = old_items[self._anchor].path if 0 <= self._anchor < len(old_items) else None

        self._items = list(items)
        n = len(self._items)
        self._aspects = [1.5] * n
        self._exact = [False] * n
        self._unknown = set()
        unresolved = []
        for i, it in enumerate(self._items):
            prev = old_aspects.get(it.path)
            if prev is not None and prev[1]:
                self._aspects[i], self._exact[i] = prev
            elif prev is not None:
                self._aspects[i] = prev[0]
                unresolved.append(i)
            else:
                unresolved.append(i)
        self._reindex()
        # Only look up sizes for genuinely new or inexact tiles.
        if unresolved:
            self._resolve_aspects(unresolved, cached_dims)
        else:
            self._headers_done = n
            self.load_progress.emit(n, n)

        new_sel = {self._index_by_path[p] for p in sel_paths if p in self._index_by_path}
        self._anchor = self._index_by_path.get(anchor_path, -1) if anchor_path else -1
        if cur_path is not None and cur_path in self._index_by_path:
            self._cursor = self._index_by_path[cur_path]
        elif self._cursor >= n:
            self._cursor = n - 1
        self._hover_idx = -1
        if new_sel != self._selected:
            self._selected = new_sel
            self.selection_changed.emit(len(new_sel))
        self._last_scan_rect = QRect()
        self._recompute_layout()

    def cleanup(self):
        self._stopped = True
        self._relayout_timer.stop()
        for p in self._requested:
            self._svc.cancel(p)
        self._requested.clear()
        try:
            self._svc.thumb_ready.disconnect(self._on_thumb_ready)
            self._svc.dims_ready.disconnect(self._on_dims_ready)
        except (TypeError, RuntimeError):
            pass

    def _remove_paths(self, paths) -> None:
        """Remove items by path (move/delete) and notify the parent — no rescan."""
        pathset = {_npath(p) for p in paths}
        indices = [i for i, it in enumerate(self._items) if _npath(it.path) in pathset]
        if not indices:
            return
        self.remove_indices(indices)
        self.removed.emit(list(paths))

    def remove_indices(self, indices) -> None:
        """Drop the given item indices in place and relayout."""
        drop = {i for i in indices if 0 <= i < len(self._items)}
        if not drop:
            return
        for i in drop:
            self._store.invalidate(self._items[i].path)
        self.set_items([it for i, it in enumerate(self._items) if i not in drop])
        if self._selected:
            self._selected = set()
            self.selection_changed.emit(0)
        self._anchor = -1

    # ── Layout ────────────────────────────────────────────────────────────────

    def _recompute_layout(self):
        """Justified mosaic, **edge to edge**: tiles pack left-to-right at the
        target row height, then each full row is scaled so it exactly fills the
        width — no slack at the sides, every photo at its own proportions. The
        last row keeps the target height (left-aligned) rather than blowing a
        few photos up to full width; so does a row that would need to grow more
        than MAX_GROW to fill (e.g. one narrow photo before a panorama)."""
        width = self._viewport_width
        n = len(self._items)
        if width <= 0:
            return
        target_h = self._row_height
        spacing = self.SPACING
        rects: list[QRect] = [QRect()] * n
        row_tops: list[int] = []
        row_starts: list[int] = []
        max_h = target_h
        y = 0
        i = 0
        aspects = self._aspects
        while i < n:
            start = i
            nat: list[float] = []          # natural widths at target height
            row_w = 0.0
            while i < n:
                asp = aspects[i] if aspects[i] > 0 else 1.5
                tw = max(1.0, asp * target_h)
                sep = spacing if nat else 0
                if nat and row_w + sep + tw > width:
                    break
                nat.append(tw)
                row_w += sep + tw
                i += 1
            k = len(nat)
            gaps = spacing * (k - 1)
            content = sum(nat)
            last_row = i >= n
            grow = (width - gaps) / content if content > 0 else 1.0
            if last_row and grow > 1.0:
                grow = 1.0                  # don't stretch a short final row
            if grow > self.MAX_GROW:
                grow = 1.0
            h = max(1, int(round(target_h * grow)))
            widths = [max(1, int(nw * grow)) for nw in nat]
            if grow != 1.0:
                # absorb rounding so the row lands exactly on the right edge
                widths[-1] = max(1, widths[-1] + (width - gaps) - sum(widths))
            x = 0                           # rows start at the left edge
            for off, tw in enumerate(widths):
                rects[start + off] = QRect(x, y, tw, h)
                x += tw + spacing
            row_tops.append(y)
            row_starts.append(start)
            max_h = max(max_h, h)
            y += h + spacing
        if n:
            y -= spacing
        self._max_row_h = max_h
        self._rects = rects
        self._row_tops = row_tops
        self._row_starts = row_starts
        self._total_height = max(int(y), target_h)
        self._last_scan_rect = QRect()
        # No need to drop _scaled here: each entry records the size it was
        # scaled to and is re-made on mismatch, so a relayout mid-scroll doesn't
        # throw away blits whose tiles kept their size.
        self.setMinimumHeight(self._total_height)
        self.setFixedHeight(self._total_height)
        self.update()
        self.layout_changed.emit()

    def _row_of(self, idx: int) -> int:
        return max(0, bisect.bisect_right(self._row_starts, idx) - 1)

    def _index_range(self, top: int, bottom: int) -> range:
        """Item indices whose rows intersect [top, bottom]."""
        if not self._row_tops:
            return range(0)
        # Row r spans [top_r, top_r + h - 1]; it intersects when top_r > top - h
        # and top_r <= bottom. Row tops are sorted, so both ends are a bisect.
        r0 = bisect.bisect_right(self._row_tops, top - getattr(self, "_max_row_h", self._row_height))
        r1 = bisect.bisect_right(self._row_tops, bottom)
        if r0 >= r1:
            return range(0)
        start = self._row_starts[r0]
        end = self._row_starts[r1] if r1 < len(self._row_starts) else len(self._items)
        return range(start, end)

    # ── Zoom (Ctrl + scroll) ─────────────────────────────────────────────────

    def wheelEvent(self, event):
        # Ctrl+wheel zooms the row height; bare wheel falls through to the
        # parent QScrollArea so vertical scrolling still works.
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            event.ignore()
            return
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        factor = self.ZOOM_FACTOR if delta > 0 else (1.0 / self.ZOOM_FACTOR)
        self.set_row_height(int(round(self._row_height * factor)))
        event.accept()

    # ── Thumbnails ────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def _on_thumb_ready(self, path: str):
        if self._stopped:
            return
        idx = self._index_by_path.get(path)
        if idx is None:
            return
        self._requested.discard(path)
        pm = self._store.get(path, self._items[idx].sig)
        if pm is None or idx >= len(self._rects):
            return
        if not self._reconcile(idx, pm):
            self.update(self._rects[idx])

    def _reconcile(self, idx: int, pm: QPixmap) -> bool:
        """Make tile ``idx`` take its thumbnail's shape if they disagree.

        The decoded thumbnail is authoritative — EXIF rotation, RAW and HEIC
        quirks are already applied to it — so it overrides any header read,
        estimate, or stale cached size. Checked on arrival *and* at paint time,
        because on a revisit the thumbnail is already in memory and never
        "arrives". Returns True if a relayout was scheduled."""
        h = pm.height()
        if h <= 0 or self._store.is_placeholder(self._items[idx].path):
            return False
        actual = pm.width() / h
        cached = self._aspects[idx]
        if cached > 0 and abs(actual - cached) / cached <= 0.05:
            if not self._exact[idx]:
                self._exact[idx] = True
                self._unknown.discard(idx)
            return False
        self._aspects[idx] = actual
        self._exact[idx] = True
        self._unknown.discard(idx)
        self._dims_pending.discard(self._items[idx].path)
        item = self._items[idx]
        # thumbs are scaled copies: persist the ratio at the header's scale
        w_full, h_full = round(actual * 1000), 1000
        if item.sig is not None:
            dim_cache.put_sig(self._source, item.path, w_full, h_full, item.sig)
            self._svc.note_dims(self._source)
        _remember_dims(item.path, w_full, h_full)
        self._relayout_timer.start()
        return True

    def set_scrubbing(self, on: bool):
        """While the timeline is being dragged the view flies past rows it will
        never stop on: paint what's in memory, request nothing, and catch up
        the moment the pointer rests or lets go."""
        self._scrubbing = bool(on)
        if not on:
            self._last_scan_rect = QRect()
            self.update()

    def _schedule_visible(self):
        """Request thumbs for the viewport (high priority) + look-ahead (lower),
        and cancel queued decodes that fell out of range. Runs only when the
        visible rect actually changed."""
        if getattr(self, "_scrubbing", False):
            return
        vis = self.visibleRegion().boundingRect()
        if vis.isEmpty() or vis == self._last_scan_rect:
            return
        self._last_scan_rect = vis
        wanted: set[str] = set()
        store, svc = self._store, self._svc
        items, rects = self._items, self._rects
        la = self.LOOKAHEAD
        band = self._index_range(vis.top() - la * 2, vis.bottom() + la * 2)
        self._request_visible_dims(band)      # headers for what's about to matter
        for idx in self._index_range(vis.top() - la, vis.bottom() + la):
            it = items[idx]
            wanted.add(it.path)
            if store.get(it.path, it.sig) is not None:   # drops a stale entry
                continue
            pri = svc.PRI_VISIBLE if rects[idx].intersects(vis) else svc.PRI_NEAR
            if svc.request(it.path, self._source, pri, it.sig):
                self._requested.add(it.path)
        stale = self._requested - wanted
        for p in stale:
            svc.cancel(p)
        self._requested &= wanted

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        visible = event.rect()
        p.fillRect(visible, _qc("canvas_bg"))
        if not self._rects:
            p.end()
            return
        self._schedule_visible()

        from .media import is_video
        placeholder = _qc("tile_placeholder")
        store = self._store
        for idx in self._index_range(visible.top(), visible.bottom()):
            rect = self._rects[idx]
            if not rect.intersects(visible):
                continue
            item = self._items[idx]
            pm = store.get(item.path, item.sig)
            if pm is not None:
                self._reconcile(idx, pm)     # no-op unless the shapes disagree
                p.drawPixmap(rect.topLeft(), self._scaled_for(item.path, pm, rect))
            else:
                # Skeleton at the tile's real aspect, so nothing jumps on arrival.
                p.fillRect(rect, placeholder)

            if is_video(item.path):
                _draw_play_badge(p, rect)

            if self._marked and item.name.lower() in self._marked:
                self._draw_marked_badge(p, rect)

            # Selection and focus are marked with registration brackets and a
            # hairline, never a colour wash over the photograph.
            selected = idx in self._selected
            if selected:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_qc("focus"), 2))
                p.drawRect(rect.adjusted(1, 1, -1, -1))
                inst.draw_brackets(p, rect, _qc("focus"), arm=14, width=2, inset=4)
                self._draw_check_badge(p, rect)
            elif idx == self._hover_idx:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_qc("cap_alt"), 1))
                p.drawRect(rect.adjusted(0, 0, -1, -1))
                inst.draw_brackets(p, rect, _qc("cap_alt"), arm=11, width=2, inset=4)

            # Keyboard focus — the cursor tile locks like an AF point.
            if idx == self._cursor:
                inst.draw_brackets(p, rect, _qc("focus"), arm=18, width=3, inset=3)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_qc("focus"), 2 if self._sort_mode else 1))
                p.drawRect(rect.adjusted(1, 1, -2, -2))
        p.end()

    def _scaled_for(self, path: str, pm: QPixmap, rect: QRect) -> QPixmap:
        """Tile-sized copy of a thumbnail, cached.

        Scaled to *fill* the tile keeping the photo's proportions and centre-
        cropped — never stretched. When the tile already has the photo's shape
        (the normal case) the crop is zero; while a size is still an estimate
        the photo is trimmed a little rather than squashed.

        Keyed on the pixmap's identity as well as the tile size, so a rotated
        or re-decoded thumbnail is re-scaled instead of serving the old copy.
        Bounded by pixel bytes, not entry count — big tiles are big."""
        want = rect.size()
        key = pm.cacheKey()
        hit = self._scaled.get(path)
        if hit is None or hit[0] != key or hit[1].size() != want:
            full = pm.scaled(want, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            if full.size() != want:
                x = max(0, (full.width() - want.width()) // 2)
                y = max(0, (full.height() - want.height()) // 2)
                full = full.copy(x, y, want.width(), want.height())
            if hit is not None:
                self._scaled_bytes -= _px_bytes(hit[1])
            hit = (key, full)
            self._scaled[path] = hit
            self._scaled_bytes += _px_bytes(full)
            while self._scaled_bytes > self.SCALED_BUDGET and len(self._scaled) > 1:
                _p, (_k, old) = self._scaled.popitem(last=False)
                self._scaled_bytes -= _px_bytes(old)
        else:
            self._scaled.move_to_end(path)
        return hit[1]

    @staticmethod
    def _draw_marked_badge(p: QPainter, rect: QRect) -> None:
        """A filed photo carries a silkscreened plate in the corner — it reads
        at a glance without putting colour across the image."""
        w, h = 30, 16
        plate = QRect(rect.right() - w - 5, rect.top() + 5, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc("cap"))
        p.drawRoundedRect(plate, 2, 2)
        inst.draw_icon(p, "check",
                       QRectF(plate.left() + 3, plate.top() + 2, 12, 12),
                       _qc("legend"), 2.0)
        p.setFont(theme_mod.legend_font(7, tracking=0.6))
        p.setPen(_qc("legend"))
        p.drawText(QRect(plate.left() + 15, plate.top(), 14, h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "IN")

    @staticmethod
    def _draw_check_badge(p: QPainter, rect: QRect) -> None:
        """Selected: a lit square plate, top-left."""
        side = 17
        plate = QRect(rect.left() + 5, rect.top() + 5, side, side)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc("accent"))
        p.drawRoundedRect(plate, 2, 2)
        inst.draw_icon(p, "check", QRectF(plate.adjusted(3, 3, -3, -3)),
                       QColor(11, 13, 16), 2.2)

    # ── Hover + click ────────────────────────────────────────────────────────

    def _update_idx(self, idx: int):
        if 0 <= idx < len(self._rects):
            self.update(self._rects[idx])

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        new_hover = self._idx_at(pos)
        if new_hover != self._hover_idx:
            old = self._hover_idx
            self._hover_idx = new_hover
            self.setToolTip(os.path.basename(self._items[new_hover].path)
                            if new_hover >= 0 else "")
            self._update_idx(old)
            self._update_idx(new_hover)

    def leaveEvent(self, event):
        if self._hover_idx >= 0:
            old = self._hover_idx
            self._hover_idx = -1
            self._update_idx(old)

    def _idx_at(self, pos) -> int:
        if not self._row_tops:
            return -1
        r = bisect.bisect_right(self._row_tops, pos.y()) - 1
        if r < 0:
            return -1
        start = self._row_starts[r]
        end = self._row_starts[r + 1] if r + 1 < len(self._row_starts) else len(self._items)
        for idx in range(start, end):
            if self._rects[idx].contains(pos):
                return idx
        return -1

    def _set_selection(self, indices):
        new = {i for i in indices if 0 <= i < len(self._items)}
        if new != self._selected:
            self._selected = new
            self.selection_changed.emit(len(self._selected))
            self.update()

    def set_cursor(self, idx: int, *, reveal: bool = True):
        n = len(self._items)
        if n == 0:
            self._cursor = -1
            return
        idx = max(0, min(n - 1, idx))
        old = self._cursor
        self._cursor = idx
        self._update_idx(old)
        self._update_idx(idx)
        if reveal and idx < len(self._rects):
            r = self._rects[idx]
            self.scroll_to.emit(r.center().x(), r.center().y())
        if old != idx:
            self.cursor_changed.emit(idx)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        idx = self._idx_at(pos)
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if event.button() == Qt.MouseButton.RightButton:
            self.setFocus()
            # Right-click outside the current selection retargets to that tile.
            if idx >= 0 and idx not in self._selected:
                self._set_selection({idx})
                self._anchor = idx
            self._show_context_menu(idx, event.globalPosition().toPoint())
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()

        if idx < 0:
            if not (ctrl or shift):
                self._set_selection(set())
                self._anchor = -1
            return

        if shift:
            self.set_cursor(idx, reveal=False)
            if self._anchor < 0:
                self._anchor = idx
            lo, hi = sorted((self._anchor, idx))
            sel = set(range(lo, hi + 1))
            if ctrl:
                sel |= self._selected
            self._set_selection(sel)
        elif ctrl:
            self.set_cursor(idx, reveal=False)
            sel = set(self._selected)
            sel.symmetric_difference_update({idx})
            self._set_selection(sel)
            self._anchor = idx
        elif self._sort_mode:
            # Sort view: a click just focuses the tile (Space acts on it).
            self._set_selection(set())
            self._anchor = idx
            self.set_cursor(idx, reveal=False)
        else:
            # Plain click — open the slideshow.
            self._cursor = idx
            self._set_selection(set())
            self._anchor = idx
            self.clicked.emit(idx)

    def mouseDoubleClickEvent(self, event):
        if self._sort_mode and event.button() == Qt.MouseButton.LeftButton:
            idx = self._idx_at(event.position().toPoint())
            if idx >= 0:
                self.set_cursor(idx, reveal=False)
                self.enter_pressed.emit()
            return
        super().mouseDoubleClickEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self._sort_mode and self._cursor < 0 and self._items:
            self.set_cursor(0, reveal=False)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        n = len(self._items)

        if ctrl and key == Qt.Key.Key_A:
            self._set_selection(set(range(n)))
            self._anchor = 0
            self._cursor = max(self._cursor, 0)
            return
        if key == Qt.Key.Key_Escape:
            if self._selected:
                # First Escape just clears a multi-selection.
                self._set_selection(set())
                self._anchor = -1
                return
            # Nothing selected → let Escape bubble up so it goes back a level.
            event.ignore()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected:
            self._delete_paths(sorted(self._selected))
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._sort_mode \
                and 0 <= self._cursor < n:
            self._delete_paths([self._cursor])
            return

        if n == 0:
            event.ignore() if self._sort_mode else super().keyPressEvent(event)
            return

        if self._sort_mode and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            f = self.ZOOM_FACTOR if key != Qt.Key.Key_Minus else 1 / self.ZOOM_FACTOR
            self.set_row_height(int(round(self._row_height * f)))
            if 0 <= self._cursor < len(self._rects):
                r = self._rects[self._cursor]
                self.scroll_to.emit(r.center().x(), r.center().y())
            return

        # ── Arrow / Home / End navigation with selection ───────────────────────
        cur = self._cursor if self._cursor >= 0 else (
            min(self._selected) if self._selected else 0)
        target = None
        if key == Qt.Key.Key_Right:
            target = min(n - 1, cur + 1)
        elif key == Qt.Key.Key_Left:
            target = max(0, cur - 1)
        elif key == Qt.Key.Key_Down:
            target = self._vertical_idx(cur, down=True)
        elif key == Qt.Key.Key_Up:
            target = self._vertical_idx(cur, down=False)
        elif key == Qt.Key.Key_Home:
            target = 0
        elif key == Qt.Key.Key_End:
            target = n - 1
        elif key == Qt.Key.Key_PageDown:
            target = self._page_idx(cur, down=True)
        elif key == Qt.Key.Key_PageUp:
            target = self._page_idx(cur, down=False)

        if target is not None:
            self._move_cursor(target, shift=shift, ctrl=ctrl)
            return

        if key == Qt.Key.Key_Space:
            if self._sort_mode:
                if self._cursor < 0:
                    self.set_cursor(0)
                self.space_pressed.emit()
                return
            # Toggle the focused tile in/out of the selection.
            if self._cursor < 0:
                self._cursor = 0
            sel = set(self._selected)
            sel.symmetric_difference_update({self._cursor})
            self._set_selection(sel)
            self._anchor = self._cursor
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._sort_mode:
                if self._cursor >= 0:
                    self.enter_pressed.emit()
                return
            if self._cursor >= 0:
                self.clicked.emit(self._cursor)
            return

        if self._sort_mode:
            event.ignore()      # let the sort view see Tab / Ctrl+Z / etc.
            return
        super().keyPressEvent(event)

    def _vertical_idx(self, cur_idx: int, *, down: bool) -> int:
        """Nearest tile one row up/down from `cur_idx`, matched by x-centre."""
        if not (0 <= cur_idx < len(self._rects)) or not self._row_starts:
            return cur_idx
        r = self._row_of(cur_idx) + (1 if down else -1)
        if r < 0 or r >= len(self._row_starts):
            return cur_idx
        cx = self._rects[cur_idx].center().x()
        start = self._row_starts[r]
        end = self._row_starts[r + 1] if r + 1 < len(self._row_starts) else len(self._items)
        return min(range(start, end), key=lambda i: abs(self._rects[i].center().x() - cx))

    def _page_idx(self, cur_idx: int, *, down: bool) -> int:
        rows = max(1, (self.visibleRegion().boundingRect().height() or 600)
                   // (self._row_height + self.SPACING))
        idx = cur_idx
        for _ in range(rows):
            nxt = self._vertical_idx(idx, down=down)
            if nxt == idx:
                break
            idx = nxt
        return idx

    def _move_cursor(self, target: int, *, shift: bool, ctrl: bool) -> None:
        target = max(0, min(len(self._items) - 1, target))
        if shift:
            if self._anchor < 0:
                self._anchor = self._cursor if self._cursor >= 0 else target
            lo, hi = sorted((self._anchor, target))
            self._set_selection(set(range(lo, hi + 1)))
        elif ctrl:
            pass  # move focus only; selection unchanged
        elif self._sort_mode:
            self._set_selection(set())    # cursor-driven: Space acts on the focus tile
            self._anchor = target
        else:
            self._set_selection({target})
            self._anchor = target
        self.set_cursor(target)

    # ── Context menu ───────────────────────────────────────────────────────────

    def _target_indices(self, idx: int) -> list[int]:
        """Indices the menu acts on: the selection, or just `idx`."""
        if self._selected:
            return sorted(self._selected)
        return [idx] if 0 <= idx < len(self._items) else []

    def _copy_image_to_clipboard(self, path: str):
        """Load ``path`` and put it on the clipboard for pasting elsewhere."""
        img = None
        try:
            from . import raw_loader
            if raw_loader.is_raw(path) and raw_loader.available():
                img = raw_loader.load_raw(path, prefer_thumb=False)
        except Exception:
            img = None
        if img is None or img.isNull():
            from . import heif_loader
            if heif_loader.is_heif(path) and heif_loader.available():
                img = heif_loader.load_heif(path)
        if img is None or img.isNull():
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            img = reader.read()
        if img is None or img.isNull():
            return
        QApplication.clipboard().setImage(img)

    def _show_context_menu(self, idx: int, global_pos):
        targets = self._target_indices(idx)
        if not targets:
            return
        paths = [self._items[i].path for i in targets]
        n = len(paths)
        multi = n > 1
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        if multi:
            head = menu.addAction(f"{n} selected")
            head.setEnabled(False)
            menu.addSeparator()

        from . import external

        if self._sort_mode:
            act_prev = menu.addAction(menu_icon("zoom"), "Preview  (Enter)")
            act_prev.triggered.connect(lambda: (self.set_cursor(targets[0], reveal=False),
                                                self.enter_pressed.emit()))
        else:
            act_open = menu.addAction(menu_icon("slideshow"), "Open in Slideshow")
            act_open.triggered.connect(lambda: self.clicked.emit(targets[0]))
        menu.addSeparator()

        open_menu = menu.addMenu(menu_icon("photoshop"), "Open With" + (f" ({n})" if multi else ""))
        open_menu.setToolTipsVisible(True)
        act_ps = open_menu.addAction(menu_icon("photoshop"), "Adobe Photoshop")
        act_ps.triggered.connect(lambda: self._open_editor("photoshop", list(paths)))
        act_lr = open_menu.addAction(menu_icon("lightroom"), "Adobe Lightroom")
        act_lr.triggered.connect(lambda: self._open_editor("lightroom", list(paths)))
        open_menu.addSeparator()
        act_sys = open_menu.addAction(menu_icon("system"), "System Default")
        act_sys.triggered.connect(lambda: self._open_many(external.open_default, None, paths))

        rot_menu = menu.addMenu(menu_icon("system"), "Rotate" + (f" ({n})" if multi else ""))
        rot_menu.setToolTipsVisible(True)
        act_rcw = rot_menu.addAction(menu_icon("system"), "Rotate 90° Clockwise")
        act_rcw.triggered.connect(lambda: self._rotate_paths(list(paths), 90))
        act_rccw = rot_menu.addAction(menu_icon("system"), "Rotate 90° Anticlockwise")
        act_rccw.triggered.connect(lambda: self._rotate_paths(list(paths), 270))
        act_r180 = rot_menu.addAction(menu_icon("system"), "Rotate 180°")
        act_r180.triggered.connect(lambda: self._rotate_paths(list(paths), 180))

        menu.addSeparator()

        recents = recent_target_folders()
        suffix = f" ({n})" if multi else ""

        move_menu = menu.addMenu(menu_icon("move"), "Move to" + suffix)
        move_menu.setToolTipsVisible(True)
        for folder in recents:
            label = os.path.basename(folder.rstrip(os.sep)) or folder
            act = move_menu.addAction(menu_icon("folder"), label)
            act.setToolTip(folder)
            act.triggered.connect(
                lambda _=False, f=folder, pp=list(paths): self._move_to(pp, f, confirm=True))
        if recents:
            move_menu.addSeparator()
        act_move_choose = move_menu.addAction(menu_icon("reveal"), "Choose Folder…")
        act_move_choose.triggered.connect(lambda: self._choose_and_transfer(list(paths), move=True))

        copy_menu = menu.addMenu(menu_icon("copy"), "Copy to" + suffix)
        copy_menu.setToolTipsVisible(True)
        for folder in recents:
            label = os.path.basename(folder.rstrip(os.sep)) or folder
            act = copy_menu.addAction(menu_icon("folder"), label)
            act.setToolTip(folder)
            act.triggered.connect(
                lambda _=False, f=folder, pp=list(paths): self._copy_to(pp, f, confirm=True))
        if recents:
            copy_menu.addSeparator()
        act_copy_choose = copy_menu.addAction(menu_icon("reveal"), "Choose Folder…")
        act_copy_choose.triggered.connect(lambda: self._choose_and_transfer(list(paths), move=False))

        menu.addSeparator()

        if not multi:
            from .media import is_video as _is_video
            if not _is_video(paths[0]):
                act_copy_img = menu.addAction(menu_icon("copy"), "Copy Image")
                act_copy_img.setToolTip("Copy the picture to the clipboard "
                                        "(paste into a document, chat, etc.)")
                act_copy_img.triggered.connect(
                    lambda _=False, p=paths[0]: self._copy_image_to_clipboard(p))

        act_copy = menu.addAction(menu_icon("copy_path"),
                                  "Copy Path" + ("s" if multi else ""))
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText("\n".join(paths)))

        from . import external as _ext
        act_reveal = menu.addAction(menu_icon("reveal"), _ext.reveal_label())
        act_reveal.triggered.connect(lambda: self._reveal(paths[0]))

        menu.addSeparator()

        if multi:
            act_all = menu.addAction(menu_icon("select_all"), "Select All")
            act_all.triggered.connect(
                lambda: self._set_selection(set(range(len(self._items)))))
            act_none = menu.addAction(menu_icon("clear"), "Clear Selection")
            act_none.triggered.connect(lambda: self._set_selection(set()))
        else:
            act_all = menu.addAction(menu_icon("select_all"), "Select All")
            act_all.triggered.connect(
                lambda: self._set_selection(set(range(len(self._items)))))

        menu.addSeparator()
        act_del = menu.addAction(menu_icon("delete"),
                                 "Delete" + (f" {n} Items" if multi else "") + " (Recycle Bin)")
        act_del.triggered.connect(lambda: self._delete_paths(targets))

        menu.exec(global_pos)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _open_editor(self, which, paths):
        from . import external
        app = external.resolve_or_prompt(which, self)
        if not app:
            return
        self._open_many(external.open_with, app, paths)

    def _rotate_paths(self, paths, degrees):
        """Rotate the selected files on disk (mosaic has no live preview, so the
        rotation is written immediately, honouring the edit-save-mode setting)."""
        from . import edits as edits_mod
        from . import save_dialog as save_dialog_mod
        if not paths:
            return
        mode = save_dialog_mod.resolve_save_mode(paths[0], self)
        if mode is None:
            return  # user cancelled the save-mode prompt
        done = 0
        errors: list[str] = []
        made_new = False
        with log.timed("album.rotate", n=len(paths), deg=degrees, mode=mode):
            for path in paths:
                out, err = edits_mod.apply_rotation(path, degrees, mode)
                if err:
                    errors.append(f"{os.path.basename(path)}: {err}")
                    continue
                done += 1
                same = out and (os.path.normcase(os.path.abspath(out))
                                == os.path.normcase(os.path.abspath(path)))
                if same:
                    self._refresh_thumb_for_path(path)
                else:
                    made_new = True
        if errors:
            QMessageBox.warning(
                self, "Rotate",
                f"{done} rotated, {len(errors)} failed:\n" + "\n".join(errors[:8]))
        if made_new:
            # Save-as-new (or RAW, which always writes a sibling JPEG) created
            # files the current grid doesn't know about — rescan to show them.
            self.reload_requested.emit()

    def _refresh_thumb_for_path(self, path):
        """Re-decode a single tile's thumbnail after its file changed on disk."""
        norm = _npath(path)
        for idx, it in enumerate(self._items):
            if _npath(it.path) != norm:
                continue
            # The disk cache keys on mtime+size, so the rewritten file misses it
            # automatically; only the RAM copy (keyed by path) must be dropped.
            self._store.invalidate(it.path)
            # The listing's stat is stale now — refresh it so the new thumb and
            # dims validate against the rewritten file.
            try:
                st = os.stat(it.path)
                self._items[idx] = ImageItem(path=it.path, name=it.name,
                                             mtime=st.st_mtime, size=st.st_size)
            except OSError:
                pass
            self._exact[idx] = False
            self._resolve_aspects([idx])
            self._last_scan_rect = QRect()
            self.update()
            return

    def _open_many(self, fn, app, paths):
        errs = []
        for path in paths:
            err = fn(app, path) if app is not None else fn(path)
            if err:
                errs.append(err)
        if errs:
            QMessageBox.warning(self, "Launch failed", "\n".join(errs[:8]))

    def _choose_and_transfer(self, paths, *, move: bool):
        start = recent_target_folders()
        verb = "Move" if move else "Copy"
        dest = QFileDialog.getExistingDirectory(
            self, f"{verb} {len(paths)} item(s) to folder",
            start[0] if start else (self._source or ""))
        if not dest:
            return
        self._do_transfer(paths, dest, move=move)

    def _move_to(self, paths, folder, *, confirm: bool):
        if confirm:
            name = os.path.basename(folder.rstrip(os.sep)) or folder
            ok = QMessageBox.question(
                self, "Move files",
                f"Move {len(paths)} item(s) to “{name}”?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ok != QMessageBox.StandardButton.Yes:
                return
        self._do_transfer(paths, folder, move=True)

    def _copy_to(self, paths, folder, *, confirm: bool):
        if confirm:
            name = os.path.basename(folder.rstrip(os.sep)) or folder
            ok = QMessageBox.question(
                self, "Copy files",
                f"Copy {len(paths)} item(s) to “{name}”?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ok != QMessageBox.StandardButton.Yes:
                return
        self._do_transfer(paths, folder, move=False)

    def _do_transfer(self, paths, dest, *, move: bool):
        resolver = make_conflict_resolver(self)
        with log.timed("album.transfer", op="move" if move else "copy",
                       n=len(paths), dest=dest):
            ok_sources, errors, _cancelled = transfer_files(
                paths, dest, move=move, conflict_cb=resolver)
        log.info("album.transfer done", op="move" if move else "copy",
                 ok=len(ok_sources), failed=len(errors))
        push_recent_target(dest)
        invalidate_scan_cache(dest)
        if errors:
            QMessageBox.warning(
                self, "Some files failed",
                f"{len(ok_sources)} ok, {len(errors)} failed:\n" + "\n".join(errors[:8]))
        # A move empties the source — drop those tiles incrementally (no rescan).
        if move and ok_sources:
            self._remove_paths(ok_sources)

    def _delete_paths(self, indices):
        paths = [self._items[i].path for i in indices if 0 <= i < len(self._items)]
        if not paths:
            return
        n = len(paths)
        ok = QMessageBox.question(
            self, "Delete files",
            f"Send {n} item{'s' if n != 1 else ''} to the Recycle Bin?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        from . import _recycle
        failed = []
        deleted = []
        with log.timed("album.delete", n=len(paths)):
            for path in paths:
                try:
                    if _recycle.send_to_recycle_bin(path):
                        deleted.append(path)
                    else:
                        failed.append(os.path.basename(path))
                except Exception as e:
                    failed.append(f"{os.path.basename(path)}: {e}")
        if failed:
            QMessageBox.warning(self, "Some files failed",
                                "\n".join(failed[:8]))
        if deleted:
            self._remove_paths(deleted)

    def _reveal(self, path: str):
        from . import external
        external.reveal_in_file_manager(path)


# ── Browser view ───────────────────────────────────────────────────────────────

class AlbumBrowserView(QWidget):
    """Folder-tree browser. ``open_image`` carries (folder_path, file_path) of
    the image clicked — by path, so filtered views and library-wide search
    results open exactly the photo that was clicked."""

    open_image = pyqtSignal(str, str)
    sort_requested = pyqtSignal(str)   # Sort (Multi Sort) with this folder as the source
    back_requested = pyqtSignal()
    progress = pyqtSignal(str)  # internal: scanner emits current path

    tile_size_changed = pyqtSignal(int)     # Ctrl+wheel/+- zoom -> window's size slider
    location_changed = pyqtSignal(str)      # the folder now shown -> status bar

    def __init__(self, source_folder: str, parent=None):
        super().__init__(parent)
        self._source = source_folder
        self._nav_stack: list[str] = []   # paths from root → current
        self._folders: list[Folder] = []
        self._items: list[ImageItem] = []
        self._filter_text = ""                       # live search text
        self._search_scope = "folder"                # "folder" | "library"
        self._search_results: list[ImageItem] | None = None
        self._tiles: list[QWidget] = []
        self._tile_by_cover: dict[str, QWidget] = {}
        self._folder_tile_by_path: dict[str, "FolderTile"] = {}
        self._focus_pending = False   # focus first tile after a navigation render
        self._images_header: QLabel | None = None

        # Covers come from the shared thumbnail service + RAM store.
        self._svc = thumb_store.service()
        self._svc.thumb_ready.connect(self._on_cover_ready)

        # Async folder listing (never blocks the UI thread) + generation token.
        self._list_signals = _ListSignals()
        self._list_signals.listed.connect(self._on_listed)
        self._list_gen = 0
        self._list_wait_timer = QTimer(self)
        self._list_wait_timer.setSingleShot(True)
        self._list_wait_timer.setInterval(150)
        self._list_wait_timer.timeout.connect(self._show_listing_strip)

        # Hover-intent prefetch: resting on a folder tile warms its listing and
        # its first screen of thumbnails before the click.
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(120)
        self._hover_timer.timeout.connect(self._prefetch_hovered)
        self._hovered_folder: str | None = None

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(70)
        self._search_timer.timeout.connect(self._apply_search_filter)

        # Lazy folder-stat streaming (deep counts/covers filled in after the
        # instant first paint). Its own single-thread pool so the sequential
        # walk never competes with cover decodes; a generation token discards
        # results from a folder we've already left.
        self._stat_signals = _FolderStatSignals()
        self._stat_signals.ready.connect(self._on_folder_stat)
        self._stat_signals.done.connect(self._on_folder_stats_done)
        # Several walker threads across the machine's cores. Capped: the walks are
        # disk-bound, so past a handful more threads just thrash the drive; leave
        # a core for the UI + cover decodes.
        self._stat_workers = max(1, min(4, (os.cpu_count() or 4) // 2))
        self._stat_pool = QThreadPool()
        self._stat_pool.setMaxThreadCount(self._stat_workers)
        self._stat_gen = 0
        self._stat_work: "_FolderStatWork | None" = None

        self._was_cached = False  # for the "cached" status pill

        self._mosaic: _ImageMosaic | None = None
        self._last_cols = 0   # avoid re-rendering folder grid when cols unchanged

        # Scan progress throttle so we don't spam processEvents.
        self._scan_label: QLabel | None = None
        self._last_progress = 0.0

        self._build_chrome()
        self._navigate_to(self._source, push=False)

    # ── Navigation API ────────────────────────────────────────────────────────

    @property
    def current_path(self) -> str:
        return self._nav_stack[-1] if self._nav_stack else self._source

    def _navigate_to(self, path: str, push: bool = True):
        if push:
            self._nav_stack.append(path)
        elif not self._nav_stack:
            self._nav_stack.append(path)
        # else: replace current top (used when called from breadcrumb)
        else:
            self._nav_stack[-1] = path
        self._focus_pending = True
        self._scan_and_render()

    def go_back(self):
        if len(self._nav_stack) > 1:
            self._nav_stack.pop()
            self._focus_pending = True
            self._scan_and_render()
        else:
            self.back_requested.emit()

    # ── UI chrome ─────────────────────────────────────────────────────────────

    def _build_chrome(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Front-panel rail ──────────────────────────────────────────────
        top = QWidget()
        top.setObjectName("albumTopBar")
        theme_mod.bind(top, lambda: f"#albumTopBar {{ background: {theme_mod.hexc('deck')};"
            f" border-bottom: 1px solid {theme_mod.hexc('rail')}; }}")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(8, 5, 8, 5)
        top_lay.setSpacing(6)

        self._back_btn = inst.KeyCap("", variant="dark", icon="back")
        self._back_btn.setFixedWidth(34)
        self._back_btn.setAccessibleName("Back")
        self._back_btn.setToolTip("Back: up one level (Esc)")
        self._back_btn.clicked.connect(self.go_back)
        top_lay.addWidget(self._back_btn)

        self._breadcrumb = QLabel()
        self._breadcrumb.setTextFormat(Qt.TextFormat.RichText)
        self._breadcrumb.setFont(theme_mod.legend_font(9, tracking=1.1))
        self._breadcrumb.setStyleSheet("background: transparent;")
        self._breadcrumb.setOpenExternalLinks(False)
        self._breadcrumb.linkActivated.connect(self._on_breadcrumb_clicked)
        self._breadcrumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # The album name sits at the centre of the bar — positioned over it
        # (see _place_title) rather than laid out, so it centres on the window
        # and not on the gap between unequal button groups.
        self._breadcrumb.setParent(top)
        self._top_bar = top
        top.installEventFilter(self)
        top_lay.addStretch(1)

        # ── Search: live filename filter for this folder, or a real index-backed
        # search across the whole library (scope toggle).
        # Collapsed to a magnifier until you need it; opens in place.
        self._search_btn = inst.KeyCap("", variant="dark", icon="search")
        self._search_btn.setAccessibleName("Search")
        self._search_btn.setToolTip("Search (Ctrl+F)")
        self._search_btn.setFixedWidth(34)
        self._search_btn.clicked.connect(self.focus_search)
        top_lay.addWidget(self._search_btn)

        self._search_box = QWidget()
        sbl = QHBoxLayout(self._search_box)
        sbl.setContentsMargins(0, 0, 0, 0)
        sbl.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter this folder")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(240)
        self._search.setToolTip("Search (Ctrl+F) — Esc clears and closes")
        self._search.setAccessibleName("Search")
        self._search.installEventFilter(self)
        self._search.setFont(theme_mod.legend_font(9, bold=False, tracking=0.8))
        theme_mod.bind(self._search, lambda: f"QLineEdit {{ background: {theme_mod.hexc('chassis')};"
            f" color: {theme_mod.hexc('fg')};"
            f" border: 1px solid {theme_mod.hexc('rail')}; border-radius: 3px;"
            f" padding: 7px 10px; }}"
            f"QLineEdit:focus {{ border: 1px solid {theme_mod.hexc('focus')}; }}")
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_submit)
        self._search_icon = self._search.addAction(
            self._search_glyph(), QLineEdit.ActionPosition.LeadingPosition)
        sbl.addWidget(self._search)

        self._scope = inst.Segmented(
            [("folder", "This folder"), ("library", "Whole library")], "folder",
            name="Search scope")
        self._scope.changed.connect(self._set_search_scope)
        sbl.addWidget(self._scope)
        self._search_box.hide()
        top_lay.addWidget(self._search_box)
        self._search_anim = QVariantAnimation(self)
        self._search_anim.setEasingCurve(motion_mod.ease())
        self._search_anim.valueChanged.connect(
            lambda v: self._search_box.setMaximumWidth(int(v)))
        self._search_anim.finished.connect(
            lambda: self._search_box.setMaximumWidth(16777215))

        # Readout + size live in the footer (bottom right); built below.
        self._count_label = QLabel("")     # kept for callers; the footer shows it
        self._count_label.hide()

        self._rescan_btn = inst.KeyCap("", variant="dark", icon="rescan")
        self._rescan_btn.setToolTip(
            "Re-read this folder from disk.\n"
            "Folders are held in memory after the first visit so navigation stays instant.")
        self._rescan_btn.setAccessibleName("Rescan this folder")
        self._rescan_btn.clicked.connect(self._force_rescan)
        top_lay.addWidget(self._rescan_btn)

        # The one lit key on this screen: start culling.
        self._sort_btn = inst.KeyCap("Sort", variant="commit", icon="sort")
        self._sort_btn.setToolTip(
            "Sort this folder into up to 9 destinations — number keys send the photo.")
        self._sort_btn.clicked.connect(lambda: self.sort_requested.emit(self.current_path))
        top_lay.addWidget(self._sort_btn)

        # Far right, directly above the rail: the timeline switch.
        self._timeline_on = bool(settings_mod.get("timeline_scroll"))
        self._timeline_btn = inst.KeyCap("", variant="dark", icon="timeline")
        self._timeline_btn.setCheckable(True)
        self._timeline_btn.setChecked(self._timeline_on)
        self._timeline_btn.setFixedWidth(34)
        self._timeline_btn.setAccessibleName("Timeline scrollbar")
        self._timeline_btn.setToolTip("Timeline scrollbar: label the scroll track with when photos were taken")
        self._timeline_btn.toggled.connect(self._set_timeline)
        top_lay.addWidget(self._timeline_btn)

        outer.addWidget(top)

        # Inline scan-progress strip (visible only while reading)
        self._scan_strip = QWidget()
        self._scan_strip.setObjectName("scanStrip")
        theme_mod.bind(self._scan_strip, lambda: f"#scanStrip {{ background: {theme_mod.hexc('screen_bg')};"
            f" border-bottom: 1px solid {theme_mod.hexc('screen_edge')}; }}"
            f"QLabel#scanLbl {{ color: {theme_mod.hexc('screen_fg')};"
            f" font-family: '{theme_mod.mono_family()}'; font-size: 10px;"
            f" letter-spacing: 1px; padding: 3px 12px; background: transparent; }}"
            f"QProgressBar#scanBar {{ background: {theme_mod.hexc('screen_bg')}; border: none; }}"
            f"QProgressBar#scanBar::chunk {{ background: {theme_mod.hexc('screen_fg')}; }}")
        scan_lay = QHBoxLayout(self._scan_strip)
        scan_lay.setContentsMargins(0, 0, 0, 0)
        scan_lay.setSpacing(0)
        self._scan_label = QLabel("")
        self._scan_label.setObjectName("scanLbl")
        scan_lay.addWidget(self._scan_label)
        self._scan_bar = QProgressBar()
        self._scan_bar.setObjectName("scanBar")
        self._scan_bar.setFixedHeight(3)
        self._scan_bar.setTextVisible(False)
        self._scan_bar.hide()
        scan_lay.addWidget(self._scan_bar, 1)
        self._scan_strip.hide()

        # Scrollable grid — the well the photographs sit in
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        theme_mod.bind(scroll, lambda: f"QScrollArea {{ background: {theme_mod.hexc('well')}; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {theme_mod.hexc('well')}; }}")
        self._scroll = scroll

        self._grid_host = QWidget()
        self._grid_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(TILE_GAP)
        self._grid.setVerticalSpacing(4)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        scroll.setWidget(self._grid_host)
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        body_lay.addWidget(scroll, 1)
        self._smooth = timeline_mod.SmoothScroll(scroll)
        self._rail = timeline_mod.TimelineRail(scroll, self, self._smooth)
        body_lay.addWidget(self._rail)
        outer.addWidget(body, 1)
        self._times = capture_time.CaptureTimes(self)
        self._times.updated.connect(self._queue_timeline)
        self._times_key = None
        self._timeline_timer = QTimer(self)
        self._timeline_timer.setSingleShot(True)
        self._timeline_timer.setInterval(40)
        self._timeline_timer.timeout.connect(self._sync_timeline)

        # Bulk-action rail — appears only when tiles are selected.
        self._bulk_bar = QWidget()
        self._bulk_bar.setObjectName("bulkBar")
        theme_mod.bind(self._bulk_bar, lambda: f"#bulkBar {{ background: {theme_mod.hexc('deck')};"
            f" border-top: 1px solid {theme_mod.hexc('rail')}; }}")
        bb = QHBoxLayout(self._bulk_bar)
        bb.setContentsMargins(16, 8, 16, 8)
        bb.setSpacing(8)
        self._bulk_label = QLabel("0 selected")
        self._bulk_label.setFont(theme_mod.legend_font(9, tracking=1.4))
        theme_mod.bind(self._bulk_label, lambda: f"color: {theme_mod.hexc('accent_text')}; background: transparent;")
        bb.addWidget(self._bulk_label)
        bb.addStretch(1)
        for text, icon, slot, variant in (
            ("Move to", "sort", lambda: self._bulk_transfer(move=True), "putty"),
            ("Copy to", "folder", lambda: self._bulk_transfer(move=False), "putty"),
            ("Delete", "close", self._bulk_delete, "danger"),
            ("Clear", "", self._bulk_clear, "dark"),
        ):
            b = inst.KeyCap(text, variant=variant, icon=icon)
            b.clicked.connect(slot)
            bb.addWidget(b)
        self._bulk_bar.hide()
        outer.addWidget(self._bulk_bar)

        # The count readout was retired from view (callers still update it);
        # tile size is the window's status-bar slider, driven through
        # set_tile_size / tile_size_changed.
        self._screen = inst.StatusLine()
        self._screen.hide()
        self._tile_size = int(settings_mod.get("thumbnail_row_height") or _ImageMosaic.ROW_HEIGHT)
        self._apply_timeline_visibility()

        # Loading strip pinned at the BOTTOM, under the grid.
        outer.addWidget(self._scan_strip)

    def tile_size(self) -> int:
        return self._tile_size

    def set_tile_size(self, value: int):
        """From the window's size slider."""
        self._tile_size = int(value)
        if self._mosaic is not None:
            self._mosaic.set_row_height(value)
        settings_mod.set_value("thumbnail_row_height", self._tile_size)

    def _on_mosaic_zoom(self, value: int):
        """Ctrl+wheel / +/- zoomed the grid: tell the window's slider (it must
        not echo back) and remember the size for the next folder."""
        self._tile_size = int(value)
        settings_mod.set_value("thumbnail_row_height", self._tile_size)
        self.tile_size_changed.emit(self._tile_size)

    # ── Scan + render ─────────────────────────────────────────────────────────

    def _scan_and_render(self, force: bool = False):
        path = self.current_path
        self._update_breadcrumb()
        self.location_changed.emit(path)
        # Abandon any stat stream / listing still running for the folder we're leaving.
        self._cancel_folder_stats()
        self._list_gen += 1
        self._list_wait_timer.stop()

        hit = None if force else _cache_get(path)
        if hit is not None:
            folders, items, mtime, dims = hit
            with log.timed("album.render", path=path, cached=True,
                           folders=len(folders), images=len(items)):
                self._apply_listing(path, folders, items, mtime, dims, cached=True)
            # Stale-while-revalidate: one stat tells us whether anything was
            # added/removed/renamed since; if so, relist in the background and
            # patch the grid in place.
            if path in _UNVERIFIED or _dir_mtime(path) != mtime:
                _UNVERIFIED.discard(path)
                _list_pool().start(_ListTask(self._list_gen, path, self._list_signals,
                                             self._source), 10)
            return

        if force:
            invalidate_scan_cache(path)
        # Miss: list on a worker so a slow / network drive never freezes the
        # window. The previous grid stays up for a fast listing (no blank flash);
        # a slow one clears it and shows an "Opening…" strip after 150 ms.
        self._list_wait_timer.start()
        _list_pool().start(_ListTask(self._list_gen, path, self._list_signals,
                                     self._source), 10)

    def _show_listing_strip(self):
        name = os.path.basename(self.current_path) or self.current_path
        self._scan_label.setText(f"Opening {name}…")
        self._scan_bar.setRange(0, 0)
        self._scan_bar.show()
        self._scan_strip.show()

    @pyqtSlot(int, str, object)
    def _on_listed(self, gen: int, path: str, result):
        if gen != self._list_gen or path != self.current_path:
            return
        self._list_wait_timer.stop()
        folders, items, mtime, dims = result
        if getattr(self, "_rendered_path", None) == path:
            # Revalidation of a grid rendered from cache.
            def shown(fs):   # folder paths not already known to be empty
                out = []
                for f in fs:
                    st = album_mod.cached_folder_stat(f.path)
                    if st is None or st[0] > 0:
                        out.append(f.path)
                return out
            old_sig = [(it.path, it.mtime, it.size) for it in self._items]
            new_sig = [(it.path, it.mtime, it.size) for it in items]
            same_folders = shown(self._folders) == shown(folders)
            self._listing_mtime = mtime
            if old_sig == new_sig and same_folders:
                self._cache_current(path)
                return
            album_mod.invalidate_folder_stats(path)
            if same_folders and self._mosaic is not None and self._search_results is None:
                self._items = items
                self._dims = dims
                self._mosaic.set_items(self._display_items(), cached_dims=dims)
                self._refresh_images_header()
                self._update_count()
                self._cache_current(path)
                return
        with log.timed("album.render", path=path, cached=False,
                       folders=len(folders), images=len(items)):
            self._apply_listing(path, folders, items, mtime, dims, cached=False)
        log.info("album.open", path=path, cached=False,
                 folders=len(self._folders), images=len(self._items))

    def _apply_listing(self, path: str, folders, items, mtime: int,
                       dims: dict | None = None, *, cached: bool):
        """Render a listing. Recursive counts already computed by *any* earlier
        walk (including a parent's) fill in instantly; known-empty folders are
        dropped; only truly unknown ones stream in from the background."""
        resolved: list[Folder] = []
        for f in folders:
            hit = album_mod.cached_folder_stat(f.path)
            if hit is not None:
                if hit[0] <= 0:
                    continue
                f = Folder(path=f.path, name=f.name, image_count=hit[0], cover_path=hit[1])
            elif f.image_count >= 0:
                # Count from an older walk whose stats were since invalidated.
                f = Folder(path=f.path, name=f.name, image_count=-1, cover_path="")
            resolved.append(f)
        self._folders, self._items = resolved, items
        self._dims = dims or {}
        self._listing_mtime = mtime
        self._rendered_path = path
        self._was_cached = cached
        self._scan_strip.hide()
        self._render_grid()
        self._update_count()
        self._start_folder_stats(path)
        self._prefetch_children()

    def _prefetch_children(self):
        """Speculatively list the first subfolders into the cache (low priority)
        so clicking into one renders from memory."""
        paths = [f.path for f in self._folders[:32] if _cache_get(f.path) is None]
        _list_pool().start(_PrefetchTask(paths, self._source), -5)

    def _on_folder_hover(self, tile):
        self._hovered_folder = tile.item.path
        self._hover_timer.start()

    def _prefetch_hovered(self):
        p = self._hovered_folder
        if not p:
            return
        hit = _cache_get(p)
        if hit is None:
            _list_pool().start(_PrefetchTask([p], self._source), 5)
            return
        # Warm the first screenful of that folder's thumbnails into RAM.
        svc = self._svc
        for it in hit[1][:40]:
            svc.request(it.path, self._source, svc.PRI_PREFETCH, it.sig)

    # ── Lazy folder-stat streaming ──────────────────────────────────────────────

    def _cache_current(self, path: str):
        """Store the current listing (resolved counts included) in the scan cache.
        Placeholder counts are fine to cache now: a cache-hit render re-resolves
        them from the shared stat cache and restarts walkers for the rest."""
        _cache_put(path, list(self._folders), list(self._items),
                   getattr(self, "_listing_mtime", -1), getattr(self, "_dims", None))

    def _start_folder_stats(self, path: str):
        """Walk each placeholder subfolder in the background for its real count +
        cover, feeding results into the tiles as they arrive."""
        self._cache_current(path)
        pending = [f.path for f in self._folders if f.image_count < 0]
        if not pending:
            return
        self._stat_gen += 1
        self._stat_path = path
        self._stat_results: dict[str, tuple[int, str]] = {}
        n = max(1, min(self._stat_workers, len(pending)))
        self._stat_work = _FolderStatWork(self._stat_gen, pending, n)
        for _ in range(n):
            self._stat_pool.start(_FolderStatTask(self._stat_work, self._stat_signals))

    def _cancel_folder_stats(self):
        if self._stat_work is not None:
            self._stat_work.cancelled = True
            self._stat_work = None
        # Bump the generation so any already-queued signals are ignored.
        self._stat_gen += 1

    @pyqtSlot(int, str, int, str)
    def _on_folder_stat(self, gen: int, folder_path: str, count: int, cover: str):
        if gen != self._stat_gen:
            return
        self._stat_results[folder_path] = (count, cover)
        tile = self._folder_tile_by_path.get(folder_path)
        if tile is None:
            return
        if count <= 0:
            # Empty subtree — drop it (matches the old deep-scan behaviour).
            tile.hide()
            return
        tile.set_stat(count, cover)
        if not cover:
            return
        self._tile_by_cover[cover] = tile
        pm = thumb_store.store().get(cover)
        if pm is not None:
            tile.set_cover(pm)
        else:
            self._svc.request(cover, self._source, self._svc.PRI_VISIBLE)

    @pyqtSlot(int)
    def _on_folder_stats_done(self, gen: int):
        if gen != self._stat_gen:
            return
        # Rebuild the folder list with real counts/covers, dropping empties, and
        # cache it so the next visit is instant with correct numbers.
        enriched: list[Folder] = []
        had_empty = False
        for f in self._folders:
            c, cov = self._stat_results.get(f.path, (f.image_count, f.cover_path))
            if c <= 0:
                had_empty = True
                continue
            enriched.append(Folder(path=f.path, name=f.name,
                                   image_count=c, cover_path=cov))
        self._folders = enriched
        self._cache_current(self._stat_path)
        self._stat_work = None
        # Empty tiles were hidden in place, leaving grid gaps — one light
        # re-layout closes them (rare: only when a subfolder had no images).
        if had_empty:
            self._render_grid()   # rebuilds tiles; covers come from the RAM store
        self._update_count()

    # ── Search ────────────────────────────────────────────────────────────────

    def _is_searching(self) -> bool:
        return bool(self._filter_text.strip())

    def _display_items(self) -> list:
        """Items the grid should show: library results, folder filter, or all."""
        if self._search_results is not None:
            return self._search_results
        t = self._filter_text.strip().lower()
        if not t:
            return self._items
        return [it for it in self._items if t in it.name.lower()]

    def _on_search_text(self, text: str):
        was_searching = self._is_searching() or self._search_results is not None
        self._filter_text = text
        # Typing always filters the current folder live; library results are only
        # produced on Enter, so drop any stale result set as soon as text changes.
        self._search_results = None
        if was_searching != self._is_searching():
            # Entering/leaving search mode shows/hides the folder section —
            # that needs a real re-render (thumbs still come from RAM).
            self._search_timer.stop()
            self._render_grid()
            self._update_count()
            self._kick_mosaic_width()
        else:
            self._search_timer.start()   # coalesce fast typing

    def _apply_search_filter(self):
        """Refilter in place: the existing mosaic just swaps its item list."""
        items = self._display_items()
        if self._mosaic is not None and items and self._images_header is not None:
            self._mosaic.set_items(items)
            self._refresh_images_header()
            self._update_count()
            return
        self._render_grid()
        self._update_count()
        self._kick_mosaic_width()

    def _refresh_images_header(self):
        if self._images_header is None:
            return
        n = len(self._display_items())
        self._images_header.setText(f"RESULTS ({n:,})" if self._is_searching()
                                    else f"IMAGES ({n:,})")

    def _on_search_submit(self):
        """Enter — run a library-wide index search when that scope is active."""
        text = self._filter_text.strip()
        if not text or self._search_scope != "library":
            return
        try:
            from . import index as index_mod
            rows = index_mod.search(filename_like=text, order_by="date_taken", limit=2000)
            results = []
            for r in rows:
                try:
                    st = os.stat(r["path"])
                except OSError:
                    continue   # indexed but gone from disk
                results.append(ImageItem(path=r["path"], name=os.path.basename(r["path"]),
                                         mtime=st.st_mtime, size=st.st_size))
            self._search_results = results
            log.info("library search", q=text, hits=len(self._search_results))
        except Exception as e:
            log.error("library search failed", err=str(e))
            self._search_results = []
        self._render_grid()
        self._update_count()
        self._kick_mosaic_width()

    def _toggle_search_scope(self):
        self._set_search_scope("library" if self._search_scope == "folder" else "folder")

    def _set_search_scope(self, scope: str):
        if scope == self._search_scope:
            return
        self._search_scope = scope
        self._scope.set_value(scope)
        library = scope == "library"
        self._search.setPlaceholderText(
            "Search the library, then Enter" if library else "Filter this folder")
        self._search_results = None
        if self._is_searching():
            if self._search_scope == "library":
                self._on_search_submit()
            else:
                self._render_grid()
                self._update_count()
                self._kick_mosaic_width()

    def focus_search(self):
        self._open_search()
        self._search.setFocus()
        self._search.selectAll()

    def _open_search(self):
        if self._search_box.isVisible():
            return
        self._search_btn.hide()
        self._search_box.show()
        full = self._search_box.sizeHint().width()
        ms = motion_mod.dur(motion_mod.SHORT)
        if ms > 0:
            self._search_anim.stop()
            self._search_anim.setDuration(ms)
            self._search_anim.setStartValue(34)
            self._search_anim.setEndValue(full)
            self._search_box.setMaximumWidth(34)
            self._search_anim.start()

    def _close_search(self):
        if not self._search_box.isVisible():
            return
        self._search_anim.stop()
        self._search_box.setMaximumWidth(16777215)
        self._search_box.hide()
        self._search_btn.show()

    def _maybe_close_search(self):
        """Folds back to the magnifier when focus leaves an empty search."""
        fw = QApplication.focusWidget()
        if fw is not None and self._search_box.isAncestorOf(fw):
            return
        if not self._is_searching() and self._search_results is None:
            self._close_search()

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "_search", None) and ev.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._maybe_close_search)
        elif obj is getattr(self, "_top_bar", None) and ev.type() in (
                QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            QTimer.singleShot(0, self._place_title)
        return super().eventFilter(obj, ev)

    def _place_title(self):
        """Centre the album name on the bar; if the buttons leave too little
        room for true centring, sit it in the gap between them instead."""
        bar = getattr(self, "_top_bar", None)
        if bar is None:
            return
        lab = self._breadcrumb
        pad = 12
        left = self._back_btn.geometry().right() + pad
        right_edges = [w.geometry().left() for w in (
            self._search_btn, self._search_box, self._rescan_btn, self._sort_btn,
            self._timeline_btn) if w.isVisible()]
        right = (min(right_edges) if right_edges else bar.width()) - pad
        want = lab.sizeHint().width()
        half = max(0, min(bar.width() // 2 - left, right - bar.width() // 2))
        h = bar.height()
        if want <= 2 * half:
            w = want
            x = (bar.width() - w) // 2
        else:
            w = max(0, right - left)
            x = left
        lab.setGeometry(x, 0, w, h)
        lab.raise_()

    def clear_search(self) -> bool:
        """Esc: clear the search (and fold it away). True if Esc was used."""
        if self._is_searching() or self._search_results is not None:
            self._search.clear()          # triggers _on_search_text
            self._close_search()
            self.focus_content()
            return True
        if self._search_box.isVisible():
            self._close_search()
            self.focus_content()
            return True
        return False

    def _search_glyph(self):
        from PyQt6.QtGui import QIcon, QPixmap
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inst.draw_icon(p, "search", QRectF(6, 6, 20, 20), _qc("muted"), 2.2)
        p.end()
        return QIcon(pm)

    # ── Timeline scrollbar ────────────────────────────────────────────────────

    def _set_timeline(self, on: bool):
        self._timeline_on = bool(on)
        settings_mod.set_value("timeline_scroll", self._timeline_on)
        self._apply_timeline_visibility()

    def _apply_timeline_visibility(self):
        on = self._timeline_on
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if on else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._rail.setVisible(on)
        if on:
            self._times_key = None
            self._queue_timeline()
        else:
            self._times.cancel()

    def _queue_timeline(self):
        if self._timeline_on:
            self._timeline_timer.start()

    def _sync_timeline(self):
        """Hand the rail one mark per mosaic row: where the row is, and when its
        first photo was taken. Capture times are fetched per item list."""
        m = self._mosaic
        if not self._timeline_on:
            return
        if m is None or not m.items() or not m._row_starts:
            self._rail.set_marks([])
            return
        items = m.items()
        key = (id(m), len(items), items[0].path, items[-1].path)
        if key != self._times_key:
            self._times_key = key
            firsts = [items[s] for s in m._row_starts if s < len(items)]
            self._times.request(self.current_path, list(items), firsts)
        base = m.y()
        marks = []
        get = self._times.get
        for top, start in zip(m._row_tops, m._row_starts):
            if start < len(items):
                ts = get(items[start].path)
                if ts is not None:
                    marks.append((base + top, ts))
        self._rail.set_marks(marks)

    # ── Bulk-action bar ───────────────────────────────────────────────────────

    def _on_selection_changed(self, count: int):
        self._bulk_label.setText(f"{count:,} selected")
        self._bulk_bar.setVisible(count > 0)

    def _selected_indices(self) -> list[int]:
        m = getattr(self, "_mosaic", None)
        return sorted(m._selected) if m is not None else []

    def _bulk_transfer(self, *, move: bool):
        m = getattr(self, "_mosaic", None)
        idxs = self._selected_indices()
        if m is None or not idxs:
            return
        paths = [m._items[i].path for i in idxs if 0 <= i < len(m._items)]
        m._choose_and_transfer(paths, move=move)

    def _bulk_delete(self):
        m = getattr(self, "_mosaic", None)
        idxs = self._selected_indices()
        if m is not None and idxs:
            m._delete_paths(idxs)

    def _bulk_clear(self):
        m = getattr(self, "_mosaic", None)
        if m is not None:
            m._set_selection(set())

    def _force_rescan(self):
        # Drop just the current folder (and its recursive counts) and relist.
        invalidate_scan_cache(self.current_path)
        self._scan_and_render(force=True)

    def listing_names(self, folder: str) -> list[str] | None:
        """Filenames of ``folder`` if this view already holds its listing — lets
        the viewer open without listing the folder again."""
        if folder == self.current_path and getattr(self, "_rendered_path", None) == folder:
            return [it.name for it in self._items]
        hit = _cache_get(folder)
        return [it.name for it in hit[1]] if hit is not None else None

    def reload_current(self):
        """Public: re-scan just the current folder in place — used when files
        changed elsewhere (e.g. moved/deleted in the image viewer). Reuses this
        live view instead of rebuilding it, so only the one folder is touched."""
        self._force_rescan()

    def _on_items_removed(self, paths):
        """Files left the current folder (move/delete). Update the model and
        cache in place — the mosaic already dropped its tiles, so we must NOT
        rebuild the grid (that would re-create the mosaic from scratch and
        re-decode every surviving thumbnail)."""
        pathset = {_npath(p) for p in paths}
        self._items = [it for it in self._items if _npath(it.path) not in pathset]
        # Recursive counts of this folder and its ancestors just changed.
        album_mod.invalidate_folder_stats(self.current_path)
        # Keep the in-memory scan cache consistent for back-navigation; the dir
        # mtime changed too, so record the new one (we already applied the diff).
        self._listing_mtime = _dir_mtime(self.current_path)
        self._cache_current(self.current_path)
        self._was_cached = True
        self._refresh_images_header()
        self._update_count()

    def theme_changed(self):
        """Mode switch: repaint the pieces that carry colour in rich text."""
        self._update_breadcrumb()
        self._update_count()
        if getattr(self, "_search_icon", None) is not None:
            self._search_icon.setIcon(self._search_glyph())

    def _update_breadcrumb(self):
        # Source / sub1 / sub2 / current  — segments are clickable links.
        segs: list[tuple[str, str]] = []  # (label, full_path)
        # Source root segment
        root_label = os.path.basename(self._source.rstrip(os.sep)) or self._source
        segs.append((root_label, self._source))
        # Walk relative parts down from root to current_path
        try:
            rel = os.path.relpath(self.current_path, self._source)
        except ValueError:
            rel = ""
        if rel and rel != ".":
            cur = self._source
            for part in rel.split(os.sep):
                cur = os.path.join(cur, part)
                segs.append((part, cur))
        sep = f' <span style="color:{theme_mod.hexc("faint")}">/</span> '
        link = theme_mod.hexc("muted")
        here = theme_mod.hexc("fg")
        html_parts = []
        for i, (label, path) in enumerate(segs):
            if i < len(segs) - 1:
                html_parts.append(
                    f'<a href="nav:{i}" style="color:{link}; text-decoration:none;">'
                    f'{html.escape(label)}</a>')
            else:
                html_parts.append(f'<span style="color:{here}; font-weight:600">{html.escape(label)}</span>')
        self._breadcrumb.setText(sep.join(html_parts))
        QTimer.singleShot(0, self._place_title)
        self._breadcrumb.setProperty("segments", segs)

    def _on_breadcrumb_clicked(self, link: str):
        if not link.startswith("nav:"):
            return
        try:
            idx = int(link.split(":", 1)[1])
        except ValueError:
            return
        segs = self._breadcrumb.property("segments")
        if not segs or idx < 0 or idx >= len(segs):
            return
        target_path = segs[idx][1]
        # Pop nav stack down to target
        # Stack starts at source; treat source as nav_stack[0].
        # Rebuild: keep prefix matching target_path.
        new_stack: list[str] = []
        full = self._source
        new_stack.append(full)
        try:
            rel = os.path.relpath(target_path, self._source)
        except ValueError:
            rel = "."
        if rel and rel != ".":
            for part in rel.split(os.sep):
                full = os.path.join(full, part)
                new_stack.append(full)
        self._nav_stack = new_stack
        self._scan_and_render()

    def _update_count(self):
        """Drive the readout. It reports what is in front of you and nothing
        decorative: counts, and whether they came from memory or the disk."""
        if self._is_searching():
            n = len(self._display_items())
            where = "LIBRARY" if self._search_scope == "library" else "FOLDER"
            self._screen.set_fields([("RESULTS", f"{n:,}")], f"IN {where}")
            return
        nf = len(self._folders)
        ni = len(self._items)
        fields = []
        if nf:
            fields.append(("FOLDERS", f"{nf:,}"))
        fields.append(("PHOTOS", f"{ni:,}" if ni else "—"))
        pending = any(f.image_count < 0 for f in self._folders)
        note = "Counting…" if pending else ("In memory" if self._was_cached else "From disk")
        self._screen.set_fields(fields, note)

    def _render_grid(self):
        # Clear grid + tear down any previous mosaic widget.
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._tiles.clear()
        self._tile_by_cover.clear()
        self._folder_tile_by_path.clear()
        self._images_header = None
        if getattr(self, "_mosaic", None) is not None:
            try:
                self._mosaic.cleanup()
            except Exception:
                pass
            self._mosaic = None
        if hasattr(self, "_bulk_bar"):
            self._bulk_bar.hide()

        cols = self._columns()
        self._last_cols = cols
        row = 0
        col = 0

        items = self._display_items()
        searching = self._is_searching()

        # Empty placeholder
        if not self._folders and not items:
            if searching:
                head, body = ("NO MATCHES",
                              f"Nothing here is called “{self._filter_text}”. "
                              "Try another word, or switch the scope key to WHOLE LIBRARY.")
            else:
                head, body = ("EMPTY FOLDER",
                              "No photos or subfolders here. Drop images or a folder in, "
                              "or press ESC to go up a level.")
            panel = QWidget()
            panel.setObjectName("emptyDeck")
            theme_mod.bind(panel, lambda: inst.deck_qss("emptyDeck"))
            el = QVBoxLayout(panel)
            el.setContentsMargins(26, 24, 26, 24)
            el.setSpacing(8)
            title = QLabel(head)
            title.setFont(theme_mod.legend_font(11, tracking=2.0))
            theme_mod.bind(title, lambda: f"color: {theme_mod.hexc('fg')}; background: transparent;")
            el.addWidget(title)
            sub = QLabel(body)
            sub.setWordWrap(True)
            theme_mod.bind(sub, lambda: f"color: {theme_mod.hexc('muted')}; font-size: 13px; background: transparent;")
            el.addWidget(sub)
            self._grid.addWidget(panel, 0, 0)
            return

        # Section: Folders (hidden while searching — results are images)
        if self._folders and not searching:
            header = self._make_section_header(f"FOLDERS · {len(self._folders)}")
            self._grid.addWidget(header, row, 0, 1, cols)
            row += 1
            for f in self._folders:
                tile = FolderTile(f)
                tile.clicked.connect(self._on_folder_clicked)
                tile._nav_cb = self._folder_nav
                tile._hover_cb = self._on_folder_hover
                self._tiles.append(tile)
                self._folder_tile_by_path[f.path] = tile
                if f.cover_path:
                    self._tile_by_cover.setdefault(f.cover_path, tile)
                self._grid.addWidget(tile, row, col)
                col += 1
                if col >= cols:
                    col = 0; row += 1
            if col != 0:
                col = 0; row += 1

        # Section: Images (justified mosaic — variable widths, no crop)
        if items:
            label = (f"RESULTS · {len(items):,}" if searching
                     else f"PHOTOS · {len(items):,}")
            header = self._make_section_header(label)
            self._images_header = header
            # A folder of only photos needs no "PHOTOS" heading — the photos
            # start at the top edge. It stays for mixed folders and results.
            header.setVisible(bool(searching or (self._folders and not searching)))
            self._grid.addWidget(header, row, 0, 1, cols)
            row += 1
            self._mosaic = _ImageMosaic(items, self._source, parent=self._grid_host,
                                        cached_dims=getattr(self, "_dims", None),
                                        row_height=self._tile_size)
            self._mosaic.row_height_changed.connect(self._on_mosaic_zoom)
            self._mosaic.layout_changed.connect(self._queue_timeline)
            self._rail.set_scrub_target(self._mosaic.set_scrubbing)
            self._mosaic.clicked.connect(self._on_image_clicked)
            self._mosaic.removed.connect(self._on_items_removed)
            self._mosaic.reload_requested.connect(self._force_rescan)
            self._mosaic.selection_changed.connect(self._on_selection_changed)
            self._mosaic.scroll_to.connect(self._scroll_mosaic_to)
            self._mosaic.load_progress.connect(self._set_load_progress)
            self._set_load_progress(self._mosaic._headers_done, len(items))
            self._grid.addWidget(self._mosaic, row, 0, 1, max(cols, 1))
            # Push the row's column stretch so the mosaic spans full width.
            for c in range(max(cols, 1)):
                self._grid.setColumnStretch(c, 1)
            # Lay out immediately so the mosaic already has tiles + a real
            # height on its very first paint — otherwise it's a zero-height
            # blank until the deferred timer fires (the gap users notice).
            self._kick_mosaic_width()
            # Deferred passes too: on a fresh view the viewport width may not
            # be settled yet, so re-run once the event loop / resize lands.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._kick_mosaic_width)
            QTimer.singleShot(80, self._kick_mosaic_width)

        self._load_covers()

        # After a navigation (not a resize re-render), drop keyboard focus on the
        # first tile so arrows/Enter work immediately without a click.
        if getattr(self, "_focus_pending", False):
            self._focus_pending = False
            if self._tiles:
                self._tiles[0].setFocus(Qt.FocusReason.OtherFocusReason)
            elif self._mosaic is not None:
                self._mosaic.setFocus(Qt.FocusReason.OtherFocusReason)

    def focus_content(self):
        """Put keyboard focus on the first folder tile (or the image grid) so
        arrows/Enter work without a click — used when this view is re-shown."""
        if self._tiles:
            self._tiles[0].setFocus(Qt.FocusReason.OtherFocusReason)
        elif self._mosaic is not None:
            self._mosaic.setFocus(Qt.FocusReason.OtherFocusReason)

    def _folder_nav(self, tile, key) -> bool:
        """Arrow-key movement between folder tiles (grid-aware). Returns True if
        it handled the key. Down past the last folder row drops into the image
        mosaic below."""
        tiles = self._tiles
        try:
            cur = tiles.index(tile)
        except ValueError:
            return False
        cols = max(1, self._last_cols)
        if key == Qt.Key.Key_Left:
            nxt = cur - 1
        elif key == Qt.Key.Key_Right:
            nxt = cur + 1
        elif key == Qt.Key.Key_Down:
            nxt = cur + cols
        else:  # Up
            nxt = cur - cols
        if 0 <= nxt < len(tiles):
            tiles[nxt].setFocus(Qt.FocusReason.OtherFocusReason)
            try:
                self._scroll.ensureWidgetVisible(tiles[nxt], 0, 40)
            except (RuntimeError, AttributeError):
                pass
            return True
        if key == Qt.Key.Key_Down and self._mosaic is not None:
            self._mosaic.setFocus(Qt.FocusReason.OtherFocusReason)
            return True
        return False

    # Below this many photos, headers finish near-instantly — skip the bar
    # so it doesn't flash for small folders.
    _LOAD_BAR_MIN = 80

    def _set_load_progress(self, done: int, total: int):
        """Determinate 'Loading photos X / N' strip, fed by the mosaic as it
        reads headers. Hides itself once every photo is processed."""
        if total < self._LOAD_BAR_MIN or done >= total:
            self._scan_bar.hide()
            self._scan_strip.hide()
            return
        self._scan_label.setText(f"Reading {done} / {total}")
        self._scan_bar.setRange(0, total)
        self._scan_bar.setValue(done)
        self._scan_bar.show()
        self._scan_strip.show()

    def _scroll_mosaic_to(self, x: int, y: int):
        """Keep the keyboard-focused mosaic tile within the viewport. The tile
        point arrives in mosaic-local coords; map it into the scroll widget
        (the mosaic sits below the folder grid + section header) first."""
        from PyQt6.QtCore import QPoint
        try:
            pt = self._mosaic.mapTo(self._grid_host, QPoint(int(x), int(y)))
            self._scroll.ensureVisible(pt.x(), pt.y(), 0, 120)
        except (RuntimeError, AttributeError):
            pass

    def _kick_mosaic_width(self):
        if getattr(self, "_mosaic", None) is None:
            return
        try:
            vp_w = self._scroll.viewport().width() - 40  # account for grid margins
            self._mosaic.set_viewport_width(max(0, vp_w))
        except RuntimeError:
            # widget already gone — ignore
            pass

    def _make_section_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(theme_mod.legend_font(9, tracking=1.8))
        theme_mod.bind(lbl, lambda: f"color: {theme_mod.hexc('canvas_fg')}; background: transparent;"
            f" padding: 8px 8px 4px 8px;")
        return lbl

    def _columns(self) -> int:
        avail = max(self.width(), TILE_W + 40)
        cols = max(1, (avail - 40 + TILE_GAP) // (TILE_W + TILE_GAP))
        return int(cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Cheap path: just push the new viewport width into the mosaic so its
        # rows re-justify without rebuilding aspect/thumb caches.
        if self._mosaic is not None:
            self._kick_mosaic_width()
        # Column count changed → re-flow the existing folder tiles in the layout
        # (no widget rebuild, and the image mosaic keeps its state).
        if hasattr(self, "_grid") and self._folders:
            new_cols = self._columns()
            if new_cols != self._last_cols:
                self._reflow_grid(new_cols)

    def _reflow_grid(self, cols: int):
        """Re-place the grid's existing widgets for a new column count."""
        if not self._tiles:
            self._render_grid()
            return
        widgets = []
        while self._grid.count():
            it = self._grid.takeAt(0)
            if it.widget() is not None:
                widgets.append(it.widget())
        tile_set = set(map(id, self._tiles))
        self._last_cols = cols
        row = col = 0
        for w in widgets:
            if id(w) in tile_set:
                self._grid.addWidget(w, row, col)
                col += 1
                if col >= cols:
                    col = 0
                    row += 1
                continue
            if col != 0:
                col = 0
                row += 1
            self._grid.addWidget(w, row, 0, 1, cols)   # section header / mosaic / empty label
            row += 1
        for c in range(64):
            self._grid.setColumnStretch(c, 1 if (self._mosaic is not None and c < cols) else 0)
        self._kick_mosaic_width()

    # ── Cover loading ─────────────────────────────────────────────────────────

    def _load_covers(self):
        store, svc = thumb_store.store(), self._svc
        for tile in self._tiles:
            cover = (tile.cover_path()
                     if isinstance(tile, FolderTile) else tile.item.path)
            if not cover:
                continue
            pm = store.get(cover)
            if pm is not None:
                tile.set_cover(pm)
            else:
                svc.request(cover, self._source, svc.PRI_VISIBLE)

    @pyqtSlot(str)
    def _on_cover_ready(self, cover_path: str):
        tile = self._tile_by_cover.get(cover_path)
        if tile is None:
            return
        pm = thumb_store.store().get(cover_path)
        if pm is not None:
            try:
                tile.set_cover(pm)
            except RuntimeError:
                pass   # tile deleted by a re-render

    # ── Click handlers ────────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_folder_clicked(self, folder):
        self._navigate_to(folder.path, push=True)

    def _on_image_clicked(self, idx: int):
        if self._mosaic is None:
            return
        items = self._mosaic.items()
        if 0 <= idx < len(items):
            path = items[idx].path
            self.open_image.emit(os.path.dirname(path), path)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def cleanup(self):
        if self._mosaic is not None:
            try:
                self._mosaic.cleanup()
            except Exception:
                pass
            self._mosaic = None
        self._list_gen += 1
        self._hover_timer.stop()
        self._search_timer.stop()
        self._list_wait_timer.stop()
        try:
            self._svc.thumb_ready.disconnect(self._on_cover_ready)
        except (TypeError, RuntimeError):
            pass
        self._cancel_folder_stats()
        self._stat_pool.clear()
        self._stat_pool.waitForDone(200)
