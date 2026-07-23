"""Folder-tree browser for a source root.

Picasa-style: at any level the user sees the folders inside the current path
first (each rendered as a folder tile with a cover thumbnail and total image
count) followed by the loose images sitting directly in that folder. Clicking
a folder navigates into it (push to the nav stack); clicking an image opens
the slideshow over the current folder's images.

Scan happens synchronously per level — on huge libraries the caller passes a
progress callback that drives a LoadingScreen so the UI stays informative.
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QSize, QRect, QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
)
from PyQt6.QtGui import (
    QImage, QImageReader, QPixmap, QColor, QPainter, QPen, QBrush, QFont,
    QPolygonF
)
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGridLayout, QSizePolicy, QApplication, QFrame, QMenu, QMessageBox,
    QFileDialog, QProgressBar, QLineEdit
)

from . import theme as theme_mod
from . import settings as settings_mod
from . import log
from .album import Folder, ImageItem, scan_path
from .icon import menu_icon
from .gallery_view import (
    cache_file as thumb_cache_file,
    THUMB_MAX_DIM,
    _HeaderTask,
    _ThumbTask,
    _WorkerSignals,
)


TILE_W = 230
TILE_H = 200
COVER_H = 150
TILE_GAP = 16
COVER_LONGEST = 360

# Module-level cache keyed by absolute folder path. Survives across
# AlbumBrowserView instances (the view is destroyed every time the user
# enters the slideshow), so navigating back doesn't re-walk disk.
_SCAN_CACHE: "OrderedDict[str, tuple[list, list]]" = OrderedDict()
_SCAN_CACHE_MAX = 256


def invalidate_scan_cache(path: str | None = None):
    """Drop one entry (path given) or wipe the whole cache."""
    if path is None:
        _SCAN_CACHE.clear()
    else:
        _SCAN_CACHE.pop(path, None)


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


# ── Background cover loader ────────────────────────────────────────────────────

class _CoverSignals(QObject):
    ready = pyqtSignal(str, QPixmap)


class _CoverTask(QRunnable):
    def __init__(self, source_root: str, cover_path: str, signals: _CoverSignals):
        super().__init__()
        self._source_root = source_root
        self._cover_path = cover_path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        cf = thumb_cache_file(self._source_root, self._cover_path)
        img: QImage | None = None
        if cf.exists():
            r = QImageReader(str(cf))
            r.setAutoTransform(True)
            img = r.read()
            if img is None or img.isNull():
                img = None
        if img is None:
            reader = QImageReader(self._cover_path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                longest = max(size.width(), size.height())
                if longest > COVER_LONGEST:
                    s = COVER_LONGEST / longest
                    reader.setScaledSize(QSize(
                        max(1, int(size.width() * s)),
                        max(1, int(size.height() * s)),
                    ))
            img = reader.read()
            if img is None or img.isNull():
                return
            try:
                cf.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(cf), "JPEG", 82)
            except OSError:
                pass
        self._signals.ready.emit(self._cover_path, QPixmap.fromImage(img))


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
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    @property
    def item(self):
        return self._item

    def set_cover(self, pm: QPixmap):
        self._cover = pm
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._item)


class FolderTile(_BaseTile):
    def __init__(self, folder: Folder, parent=None):
        super().__init__(folder, parent)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        bg = _qc("filmstrip_bg")
        fg = _qc("hint_bar_fg")
        muted = _qc("muted")

        cover_rect = QRect(0, 0, TILE_W, COVER_H)
        p.fillRect(cover_rect, QColor(20, 20, 20))

        if self._cover and not self._cover.isNull():
            _draw_cover_into(p, self._cover, cover_rect)

        # Folder badge — small ribbon top-left so user sees this is a folder
        badge_w, badge_h = 78, 22
        bx, by = 8, 8
        p.setBrush(QColor(0, 0, 0, 170))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bx, by, badge_w, badge_h, 4, 4)
        # Mini folder glyph
        gx, gy = bx + 8, by + 6
        p.setBrush(QColor(240, 200, 100))
        p.setPen(Qt.PenStyle.NoPen)
        # Tab
        p.drawRect(gx, gy, 6, 3)
        p.drawRect(gx, gy + 2, 14, 8)
        # Label
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.setPen(QColor(235, 235, 235))
        p.drawText(QRect(bx + 28, by, badge_w - 32, badge_h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "FOLDER")

        # Caption strip
        cap = QRect(0, COVER_H, TILE_W, TILE_H - COVER_H)
        p.fillRect(cap, bg)

        f = p.font(); f.setPointSize(10); f.setBold(True); p.setFont(f)
        p.setPen(fg)
        metrics = p.fontMetrics()
        elided = metrics.elidedText(self.item.name, Qt.TextElideMode.ElideMiddle, TILE_W - 16)
        p.drawText(QRect(8, COVER_H + 6, TILE_W - 16, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        f.setPointSize(9); f.setBold(False); p.setFont(f)
        p.setPen(muted)
        n = self.item.image_count
        p.drawText(QRect(8, COVER_H + 24, TILE_W - 16, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{n:,} image{'s' if n != 1 else ''}")

        if self._hover:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(80, 140, 220), 2))
            p.drawRect(self.rect().adjusted(1, 1, -1, -1))
        p.end()


class _ImageMosaic(QWidget):
    """Justified mosaic of images. Tiles keep their aspect ratio (no crop) and
    pack tightly into rows — same algorithm as the gallery's `_GalleryCanvas`,
    but driven by a list of `ImageItem` instead of an `ImageManager`."""

    clicked = pyqtSignal(int)            # image index in the list passed in
    removed = pyqtSignal(list)           # paths gone from this folder (move/delete)
    scroll_to = pyqtSignal(int, int)     # (x, y) the parent scroll area should reveal
    load_progress = pyqtSignal(int, int) # (headers_done, total) — drives the load bar
    reload_requested = pyqtSignal()      # folder needs a rescan (e.g. rotate wrote new files)
    selection_changed = pyqtSignal(int)  # number of selected tiles (drives the bulk bar)

    HEADER_DISPATCH_CHUNK = 150          # headers queued per event-loop tick

    ROW_HEIGHT = 200
    ROW_HEIGHT_MIN = 90
    ROW_HEIGHT_MAX = 520
    ZOOM_FACTOR = 1.12
    SPACING = 3
    HOVER_BORDER = 3

    def __init__(self, items: list, source_folder: str, parent=None):
        super().__init__(parent)
        self._items = items
        self._source = source_folder
        self._aspects: dict[int, float] = {}
        self._pixmaps: dict[int, QPixmap] = {}
        # tile cache: list of (idx, QRect)
        self._tiles: list[tuple[int, QRect]] = []
        self._viewport_width = 0
        self._total_height = 0
        self._hover_idx = -1
        self._row_height = self.ROW_HEIGHT

        # Multi-selection (shift = range from anchor, ctrl = toggle).
        self._selected: set[int] = set()
        self._anchor: int = -1
        self._cursor: int = -1          # keyboard focus tile (arrow-key navigation)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._signals = _WorkerSignals()
        self._signals.header_ready.connect(self._on_header_ready)
        self._signals.thumb_ready.connect(self._on_thumb_ready)

        cpu = os.cpu_count() or 4
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, cpu // 2))
        # Dimension (header) reads get their OWN pool. Sharing one pool with the
        # heavy thumbnail decodes meant the lightweight header pass — which the
        # whole justified layout waits on — got stuck behind slow full-image
        # decodes, so the layout (and the loading bar) crawled. Isolating them
        # lets the grid settle fast while thumbnails fill in independently.
        self._header_pool = QThreadPool()
        self._header_pool.setMaxThreadCount(max(2, cpu))

        self._pending_headers: set[int] = set()
        self._pending_thumbs: set[int] = set()

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), _qc("canvas_bg"))
        self.setPalette(pal)

        # Header dimensions drive the justified layout. Dispatching all of them
        # synchronously here blocks the main thread (~0.6 ms each → seconds on a
        # 1700-photo folder), freezing the UI and the loading screen. Instead we
        # seed default aspects so the mosaic paints immediately, then queue the
        # header reads in chunks across event-loop ticks so the canvas shows and
        # the progress bar animates while they stream in.
        for idx in range(len(items)):
            self._aspects[idx] = 1.5
        self._headers_done = 0
        self._header_dispatch_i = 0
        self._stopped = False
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._dispatch_headers_chunk)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_viewport_width(self, width: int):
        if width != self._viewport_width:
            self._viewport_width = width
            self._recompute_layout()

    def _dispatch_headers_chunk(self):
        """Queue the next batch of header reads, then yield to the event loop."""
        if self._stopped:
            return  # view was torn down — stop reading headers for a folder we left
        n = len(self._items)
        end = min(n, self._header_dispatch_i + self.HEADER_DISPATCH_CHUNK)
        for idx in range(self._header_dispatch_i, end):
            self._pending_headers.add(idx)
            self._header_pool.start(_HeaderTask(idx, self._items[idx].path, self._signals))
        self._header_dispatch_i = end
        if end < n:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._dispatch_headers_chunk)

    def cleanup(self):
        self._stopped = True   # halt the chunked header dispatch loop
        try:
            self._header_pool.clear()
            self._header_pool.waitForDone(200)
        except Exception:
            pass
        try:
            self._pool.clear()
            self._pool.waitForDone(200)
        except Exception:
            pass

    def _remove_paths(self, paths) -> None:
        """Remove items by path (move/delete) and notify the parent — no rescan."""
        pathset = {os.path.normcase(os.path.abspath(p)) for p in paths}
        indices = [i for i, it in enumerate(self._items)
                   if os.path.normcase(os.path.abspath(it.path)) in pathset]
        if not indices:
            return
        self.remove_indices(indices)
        self.removed.emit(list(paths))

    def remove_indices(self, indices) -> None:
        """Drop the given item indices in place and relayout. Aspects/pixmaps
        already computed for surviving items are remapped to their new indices
        so nothing re-decodes; pending tasks for old indices are dropped (their
        callbacks no-op via the pending-set guard)."""
        drop = {i for i in indices if 0 <= i < len(self._items)}
        if not drop:
            return
        keep = [i for i in range(len(self._items)) if i not in drop]
        self._items = [self._items[i] for i in keep]
        new_aspects: dict[int, float] = {}
        new_pixmaps: dict[int, QPixmap] = {}
        for new_idx, old_idx in enumerate(keep):
            if old_idx in self._aspects:
                new_aspects[new_idx] = self._aspects[old_idx]
            if old_idx in self._pixmaps:
                new_pixmaps[new_idx] = self._pixmaps[old_idx]
        self._aspects = new_aspects
        self._pixmaps = new_pixmaps
        # Outstanding header/thumb tasks reference old indices — let their
        # callbacks no-op rather than write into a remapped slot. Stop any
        # remaining chunked header dispatch (the surviving items already have
        # their aspects).
        self._pending_headers.clear()
        self._pending_thumbs.clear()
        self._header_dispatch_i = len(self._items)
        self._headers_done = len(self._items)
        self._selected = set()
        self.selection_changed.emit(0)
        self._anchor = -1
        self._cursor = -1
        self._hover_idx = -1
        self._recompute_layout()
        self.update()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _recompute_layout(self):
        """Free-flow mosaic, **centred**: every tile keeps its native aspect at
        the current row height, packs left-to-right, wraps when the next tile
        won't fit. After bucketing tiles into rows the layout offsets each row
        by `(viewport_width - row_width) / 2` so the gap is split evenly on
        both sides instead of pooling on the right.

        Row height is interactive — Ctrl+wheel adjusts `self._row_height`
        within ROW_HEIGHT_MIN..ROW_HEIGHT_MAX and re-runs this layout."""
        width = self._viewport_width
        if width <= 0 or not self._items:
            return
        target_h = self._row_height
        spacing = self.SPACING

        # Phase 1 — bucket into rows with their natural total widths.
        rows: list[list[tuple[int, int]]] = [[]]
        row_w: list[int] = [0]
        for idx in range(len(self._items)):
            asp = self._aspects.get(idx, 1.5)
            if asp <= 0:                # bad header / corrupt file
                asp = 1.5
            tw = max(1, min(int(round(asp * target_h)), width))
            cur_w = row_w[-1]
            sep = spacing if rows[-1] else 0
            if rows[-1] and cur_w + sep + tw > width:
                rows.append([])
                row_w.append(0)
                sep = 0
            rows[-1].append((idx, tw))
            row_w[-1] += sep + tw

        # Phase 2 — centre each row horizontally.
        tiles: list[tuple[int, QRect]] = []
        y = 0
        for row, rw in zip(rows, row_w):
            if not row:
                continue
            x = max(0, (width - rw) // 2)
            for idx, tw in row:
                tiles.append((idx, QRect(x, y, tw, target_h)))
                x += tw + spacing
            y += target_h + spacing
        # The trailing increment overshoots by one `spacing` if any row drew.
        if tiles:
            y -= spacing

        self._tiles = tiles
        self._total_height = max(int(y), target_h)
        self.setMinimumHeight(self._total_height)
        self.setFixedHeight(self._total_height)
        self.update()

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
        new_h = int(round(self._row_height * factor))
        new_h = max(self.ROW_HEIGHT_MIN, min(self.ROW_HEIGHT_MAX, new_h))
        if new_h != self._row_height:
            self._row_height = new_h
            self._recompute_layout()
        event.accept()

    # ── Worker callbacks ─────────────────────────────────────────────────────

    @pyqtSlot(int, int, int)
    def _on_header_ready(self, idx: int, w: int, h: int):
        # Stale callback from a task whose index was removed/remapped — ignore.
        if idx not in self._pending_headers:
            return
        self._pending_headers.discard(idx)
        self._headers_done += 1
        if h > 0:
            self._aspects[idx] = w / h
        # Drive the load bar (coarsely, plus a final exact tick).
        total = len(self._items)
        if self._headers_done >= total or self._headers_done % 25 == 0:
            self.load_progress.emit(self._headers_done, total)
        # Relayout while headers are still arriving (every 50) and once at the end.
        if not self._pending_headers or len(self._pending_headers) % 50 == 0:
            self._recompute_layout()

    @pyqtSlot(int, QPixmap, int, int)
    def _on_thumb_ready(self, idx: int, pm: QPixmap, w: int, h: int):
        # Stale callback from a task whose index was removed/remapped — ignore.
        if idx not in self._pending_thumbs:
            return
        self._pending_thumbs.discard(idx)
        self._pixmaps[idx] = pm
        needs_relayout = False
        if h > 0:
            actual_aspect = w / h
            cached = self._aspects.get(idx, 0)
            # If the header lied (EXIF rotation, RAW handler quirks, etc.)
            # the cached aspect can be wildly off — relayout this tile and
            # neighbours so the pixmap fills its natural rect instead of
            # being stretched into a placeholder one.
            if cached <= 0 or abs(actual_aspect - cached) / max(cached, 0.01) > 0.05:
                self._aspects[idx] = actual_aspect
                needs_relayout = True
        if needs_relayout:
            self._schedule_relayout()
            return
        for tile_idx, rect in self._tiles:
            if tile_idx == idx:
                self.update(rect)
                break

    def _schedule_relayout(self):
        """Debounce relayouts to coalesce bursts of corrected aspects."""
        if getattr(self, "_relayout_timer", None) is None:
            from PyQt6.QtCore import QTimer
            self._relayout_timer = QTimer(self)
            self._relayout_timer.setSingleShot(True)
            self._relayout_timer.setInterval(80)
            self._relayout_timer.timeout.connect(self._recompute_layout)
        self._relayout_timer.start()

    def _ensure_thumb(self, idx: int):
        if idx in self._pixmaps or idx in self._pending_thumbs:
            return
        item = self._items[idx]
        cf = thumb_cache_file(self._source, item.path)
        self._pending_thumbs.add(idx)
        self._pool.start(_ThumbTask(idx, item.path, cf, self._signals))

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        visible = event.rect()
        p.fillRect(visible, _qc("canvas_bg"))

        # Schedule loads for visible + small lookahead so scrolling stays smooth.
        margin = 600
        margin_rect = visible.adjusted(0, -margin, 0, margin)

        from .media import is_video
        for idx, rect in self._tiles:
            if not rect.intersects(visible):
                if rect.intersects(margin_rect):
                    self._ensure_thumb(idx)
                continue
            self._ensure_thumb(idx)
            pm = self._pixmaps.get(idx)
            if pm and not pm.isNull():
                p.drawPixmap(rect, pm)
            else:
                # Skeleton placeholder at the tile's real aspect (set from the
                # header read), so nothing jumps when the thumbnail lands.
                p.fillRect(rect, _qc("tile_placeholder"))

            if is_video(self._items[idx].path):
                _draw_play_badge(p, rect)

            selected = idx in self._selected
            if selected:
                p.fillRect(rect, QColor(80, 140, 220, 70))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(90, 160, 240), self.HOVER_BORDER + 1))
                inset = (self.HOVER_BORDER + 1) // 2
                p.drawRect(rect.adjusted(inset, inset, -inset, -inset))
                self._draw_check_badge(p, rect)
            elif idx == self._hover_idx:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(80, 140, 220), self.HOVER_BORDER))
                inset = self.HOVER_BORDER // 2
                p.drawRect(rect.adjusted(inset, inset, -inset, -inset))

            # Keyboard focus ring — white dashed, drawn over any state above.
            if idx == self._cursor:
                pen = QPen(QColor(255, 255, 255), 2, Qt.PenStyle.DashLine)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(pen)
                p.drawRect(rect.adjusted(2, 2, -2, -2))
        p.end()

    @staticmethod
    def _draw_check_badge(p: QPainter, rect: QRect) -> None:
        """Filled accent disc with white tick, top-left of a selected tile."""
        r = 11
        cx = rect.left() + r + 6
        cy = rect.top() + r + 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(90, 160, 240))
        p.drawEllipse(QPointF(float(cx), float(cy)), r, r)
        pen = QPen(QColor(255, 255, 255), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([
            QPointF(cx - 5, cy + 0),
            QPointF(cx - 1, cy + 4),
            QPointF(cx + 5, cy - 4),
        ]))

    # ── Hover + click ────────────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        new_hover = -1
        for idx, rect in self._tiles:
            if rect.contains(pos):
                new_hover = idx
                if idx < len(self._items):
                    item = self._items[idx]
                    self.setToolTip(os.path.basename(item.path))
                break
        else:
            self.setToolTip("")
        if new_hover != self._hover_idx:
            old = self._hover_idx
            self._hover_idx = new_hover
            for tile_idx, rect in self._tiles:
                if tile_idx == old or tile_idx == new_hover:
                    self.update(rect)

    def leaveEvent(self, event):
        if self._hover_idx >= 0:
            old = self._hover_idx
            self._hover_idx = -1
            for tile_idx, rect in self._tiles:
                if tile_idx == old:
                    self.update(rect)
                    break

    def _idx_at(self, pos) -> int:
        for idx, rect in self._tiles:
            if rect.contains(pos):
                return idx
        return -1

    def _set_selection(self, indices):
        new = {i for i in indices if 0 <= i < len(self._items)}
        if new != self._selected:
            self._selected = new
            self.selection_changed.emit(len(self._selected))
            self.update()

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

        self._cursor = idx
        if shift:
            if self._anchor < 0:
                self._anchor = idx
            lo, hi = sorted((self._anchor, idx))
            sel = set(range(lo, hi + 1))
            if ctrl:
                sel |= self._selected
            self._set_selection(sel)
        elif ctrl:
            sel = set(self._selected)
            sel.symmetric_difference_update({idx})
            self._set_selection(sel)
            self._anchor = idx
        else:
            # Plain click — preserve original behaviour: open the slideshow.
            self._set_selection(set())
            self._anchor = idx
            self.clicked.emit(idx)

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
            self._set_selection(set())
            self._anchor = -1
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected:
            self._delete_paths(sorted(self._selected))
            return

        if n == 0:
            super().keyPressEvent(event)
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

        if target is not None:
            self._move_cursor(target, shift=shift, ctrl=ctrl)
            return

        if key == Qt.Key.Key_Space:
            # Toggle the focused tile in/out of the selection.
            if self._cursor < 0:
                self._cursor = 0
            sel = set(self._selected)
            sel.symmetric_difference_update({self._cursor})
            self._set_selection(sel)
            self._anchor = self._cursor
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._cursor >= 0:
                self.clicked.emit(self._cursor)
            return

        super().keyPressEvent(event)

    def _vertical_idx(self, cur_idx: int, *, down: bool) -> int:
        """Nearest tile one row up/down from `cur_idx`, matched by x-centre.
        Rows share a y (uniform row height) so this lands in the adjacent row."""
        rects = dict(self._tiles)
        cur = rects.get(cur_idx)
        if cur is None:
            return cur_idx
        cx, cy = cur.center().x(), cur.center().y()
        best, best_key = cur_idx, None
        for i, r in self._tiles:
            rcy = r.center().y()
            if down and rcy <= cy:
                continue
            if not down and rcy >= cy:
                continue
            key = (abs(rcy - cy), abs(r.center().x() - cx))
            if best_key is None or key < best_key:
                best_key, best = key, i
        return best

    def _move_cursor(self, target: int, *, shift: bool, ctrl: bool) -> None:
        target = max(0, min(len(self._items) - 1, target))
        if shift:
            if self._anchor < 0:
                self._anchor = self._cursor if self._cursor >= 0 else target
            lo, hi = sorted((self._anchor, target))
            self._set_selection(set(range(lo, hi + 1)))
        elif ctrl:
            pass  # move focus only; selection unchanged
        else:
            self._set_selection({target})
            self._anchor = target
        self._cursor = target
        self.update()
        for i, r in self._tiles:
            if i == target:
                self.scroll_to.emit(r.center().x(), r.center().y())
                break

    # ── Context menu ───────────────────────────────────────────────────────────

    def _target_indices(self, idx: int) -> list[int]:
        """Indices the menu acts on: the selection, or just `idx`."""
        if self._selected:
            return sorted(self._selected)
        return [idx] if 0 <= idx < len(self._items) else []

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

        act_copy = menu.addAction(menu_icon("copy_path"),
                                  "Copy Path" + ("s" if multi else ""))
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText("\n".join(paths)))

        act_reveal = menu.addAction(menu_icon("reveal"), "Reveal in Explorer")
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
        norm = os.path.normcase(os.path.abspath(path))
        for idx, it in enumerate(self._items):
            if os.path.normcase(os.path.abspath(it.path)) != norm:
                continue
            cf = thumb_cache_file(self._source, it.path)
            try:
                if cf and os.path.isfile(cf):
                    os.remove(cf)
            except OSError:
                pass
            self._pixmaps.pop(idx, None)
            self._pending_thumbs.discard(idx)
            self._ensure_thumb(idx)
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
        import subprocess
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            pass


# ── Browser view ───────────────────────────────────────────────────────────────

class AlbumBrowserView(QWidget):
    """Folder-tree browser. ``open_image`` carries (folder_path, idx) of the
    image clicked in the current folder's image list."""

    open_image = pyqtSignal(str, int)
    back_requested = pyqtSignal()
    progress = pyqtSignal(str)  # internal: scanner emits current path

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

        self._signals = _CoverSignals()
        self._signals.ready.connect(self._on_cover_ready)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) // 2))

        self._cover_cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._cover_cache_max = 400

        # Module-level cache (see top of file) so navigating back to a folder
        # stays instant even after the slideshow tore down this view.
        self._scan_cache = _SCAN_CACHE
        self._scan_cache_max = _SCAN_CACHE_MAX
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
        self._scan_and_render()

    def go_back(self):
        if len(self._nav_stack) > 1:
            self._nav_stack.pop()
            self._scan_and_render()
        else:
            self.back_requested.emit()

    # ── UI chrome ─────────────────────────────────────────────────────────────

    def _build_chrome(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top bar
        top = QWidget()
        top.setObjectName("albumTopBar")
        top.setStyleSheet(
            f"#albumTopBar {{ background: {_css_color('hint_bar_bg')}; }}"
        )
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(14, 10, 14, 10)
        top_lay.setSpacing(12)

        self._back_btn = QPushButton("‹  Back")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setToolTip("Back (Esc)")
        self._back_btn.setStyleSheet(
            "QPushButton { background: #1b1b20; color: #e6e6ea;"
            " border: 1px solid #2a2a30; border-radius: 9px;"
            " padding: 7px 16px 7px 12px; font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background: #23232a; border-color: #3b82f6; }"
        )
        self._back_btn.clicked.connect(self.go_back)
        top_lay.addWidget(self._back_btn)

        self._breadcrumb = QLabel()
        self._breadcrumb.setTextFormat(Qt.TextFormat.RichText)
        self._breadcrumb.setStyleSheet(
            f"color: {_css_color('hint_bar_fg')};"
            f" font-size: 13px; font-weight: 600;"
        )
        self._breadcrumb.setOpenExternalLinks(False)
        self._breadcrumb.linkActivated.connect(self._on_breadcrumb_clicked)
        top_lay.addWidget(self._breadcrumb, 1)

        # ── Search: live filename filter for this folder, or a real index-backed
        # search across the whole library (scope toggle).
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter this folder…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(230)
        self._search.setToolTip("Search (Ctrl+F) — Esc clears")
        self._search.setStyleSheet(
            "QLineEdit { background:#131317; color:#e6e6ea; border:1px solid #2a2a30;"
            " border-radius:9px; padding:6px 10px; font-size:12px; }"
            "QLineEdit:focus { border-color:#3b82f6; background:#16161b; }"
        )
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_submit)
        top_lay.addWidget(self._search)

        self._scope_btn = QPushButton("This folder")
        self._scope_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scope_btn.setToolTip("Toggle search scope")
        self._scope_btn.setStyleSheet(
            "QPushButton { background:#1b1b20; color:#cfcfd6; border:1px solid #2a2a30;"
            " border-radius:9px; padding:6px 12px; font-size:12px; }"
            "QPushButton:hover { border-color:#3b82f6; color:#fff; }"
        )
        self._scope_btn.clicked.connect(self._toggle_search_scope)
        top_lay.addWidget(self._scope_btn)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {_css_color('muted')}; font-size: 12px;"
        )
        top_lay.addWidget(self._count_label)

        self._rescan_btn = QPushButton("↻ Rescan")
        self._rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rescan_btn.setToolTip(
            "Re-read this folder from disk.\n"
            "Folders are cached in memory after the first visit so navigation stays instant."
        )
        self._rescan_btn.setStyleSheet(
            "QPushButton { background: #262626; color: #e5e5e5;"
            " border: 1px solid #353535; border-radius: 6px;"
            " padding: 6px 12px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #2f2f2f; border-color: #4a4a4a; }"
        )
        self._rescan_btn.clicked.connect(self._force_rescan)
        top_lay.addWidget(self._rescan_btn)

        outer.addWidget(top)

        # Inline scan-progress strip (visible only while scanning)
        self._scan_strip = QWidget()
        self._scan_strip.setObjectName("scanStrip")
        self._scan_strip.setStyleSheet(
            "#scanStrip { background: #16203a; }"
            "QLabel#scanLbl { color: #cfd8e8; font-size: 11px; padding: 4px 12px; }"
            "QProgressBar#scanBar { background: #0f1729; border: none; }"
            "QProgressBar#scanBar::chunk { background: #3b82f6; }"
        )
        scan_lay = QHBoxLayout(self._scan_strip)
        scan_lay.setContentsMargins(0, 0, 0, 0)
        scan_lay.setSpacing(0)
        self._scan_label = QLabel("")
        self._scan_label.setObjectName("scanLbl")
        scan_lay.addWidget(self._scan_label)
        self._scan_bar = QProgressBar()
        self._scan_bar.setObjectName("scanBar")
        self._scan_bar.setFixedHeight(4)
        self._scan_bar.setTextVisible(False)
        self._scan_bar.hide()
        scan_lay.addWidget(self._scan_bar, 1)
        self._scan_strip.hide()

        # Scrollable grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_css_color('canvas_bg')}; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {_css_color('canvas_bg')}; }}"
        )
        self._scroll = scroll

        self._grid_host = QWidget()
        self._grid_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(20, 20, 20, 20)
        self._grid.setSpacing(TILE_GAP)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        scroll.setWidget(self._grid_host)
        outer.addWidget(scroll, 1)

        # Bulk-action bar — appears only when tiles are selected, so "what can I
        # do with a selection?" is visible instead of hidden behind right-click.
        self._bulk_bar = QWidget()
        self._bulk_bar.setObjectName("bulkBar")
        self._bulk_bar.setStyleSheet(
            "#bulkBar { background:#16161a; border-top:1px solid #26262c; }"
            "QLabel { color:#e6e6ea; font-size:13px; font-weight:600; }"
            "QPushButton { background:#1f1f25; color:#e6e6ea; border:1px solid #2a2a30;"
            " border-radius:8px; padding:7px 14px; font-size:12px; }"
            "QPushButton:hover { border-color:#3b82f6; background:#23232a; }"
            "QPushButton#danger { color:#ef6b6b; border-color:#45272a; }"
            "QPushButton#danger:hover { background:#2a1618; color:#fff; border-color:#ef6b6b; }"
        )
        bb = QHBoxLayout(self._bulk_bar)
        bb.setContentsMargins(16, 8, 16, 8)
        bb.setSpacing(10)
        self._bulk_label = QLabel("0 selected")
        bb.addWidget(self._bulk_label)
        bb.addStretch(1)
        for text, slot, obj in (
            ("Move to…", lambda: self._bulk_transfer(move=True), None),
            ("Copy to…", lambda: self._bulk_transfer(move=False), None),
            ("Delete", self._bulk_delete, "danger"),
            ("Clear", self._bulk_clear, None),
        ):
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            bb.addWidget(b)
        self._bulk_bar.hide()
        outer.addWidget(self._bulk_bar)

        # Loading strip pinned at the BOTTOM, under the grid — unobtrusive while
        # thumbnails/dimensions stream in (shown only while work is pending).
        outer.addWidget(self._scan_strip)

    # ── Scan + render ─────────────────────────────────────────────────────────

    def _scan_and_render(self, force: bool = False):
        path = self.current_path
        self._update_breadcrumb()

        # Cache hit — skip the disk walk entirely.
        if not force and path in self._scan_cache:
            self._folders, self._items = self._scan_cache[path]
            self._scan_cache.move_to_end(path)
            self._was_cached = True
            self._scan_strip.hide()
            with log.timed("album.render", path=path, cached=True,
                           folders=len(self._folders), images=len(self._items)):
                self._render_grid()
            self._update_count()
            log.info("album.open", path=path, cached=True,
                     folders=len(self._folders), images=len(self._items))
            return

        # Show full loading screen for heavy folders (with a Cancel escape hatch).
        from .loading_screen import LoadingScreen, ScanCancelled
        folder_label = os.path.basename(path.rstrip(os.sep)) or path
        loading = LoadingScreen(sub=f"Scanning {folder_label}…", parent=self.window(),
                                cancellable=True)
        loading.show()
        QApplication.processEvents()

        last = [time.monotonic()]
        count = [0]

        def _cb(child_path: str):
            count[0] += 1
            now = time.monotonic()
            if now - last[0] >= 0.04:
                last[0] = now
                loading.set_text(
                    f"Scanning {os.path.basename(child_path) or child_path}…"
                )
                QApplication.processEvents()
                if loading.cancelled:
                    raise ScanCancelled()

        try:
            with log.timed("album.scan", path=path):
                self._folders, self._items = scan_path(path, progress_cb=_cb)
        except ScanCancelled:
            log.info("album.scan cancelled by user", path=path)
            loading.close_smoothly()
            self._folders, self._items = [], []
            self._was_cached = False
            self._scan_strip.hide()
            self._render_grid()
            self._update_count()
            return
        # Store in cache (LRU eviction on overflow).
        self._scan_cache[path] = (self._folders, self._items)
        self._scan_cache.move_to_end(path)
        while len(self._scan_cache) > self._scan_cache_max:
            self._scan_cache.popitem(last=False)
        self._was_cached = False
        self._scan_strip.hide()
        # Keep the loading screen up THROUGH render + first paint. It used to
        # close right after the disk scan, so the user stared at an empty dark
        # canvas while thousands of tiles were built and laid out. Render under
        # the overlay, flush one paint, then fade it out.
        with log.timed("album.render", path=path, cached=False,
                       folders=len(self._folders), images=len(self._items)):
            self._render_grid()
        QApplication.processEvents()
        loading.close_smoothly()
        self._update_count()
        log.info("album.open", path=path, cached=False,
                 folders=len(self._folders), images=len(self._items))

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
        self._filter_text = text
        # Typing always filters the current folder live; library results are only
        # produced on Enter, so drop any stale result set as soon as text changes.
        self._search_results = None
        self._render_grid()
        self._update_count()
        self._kick_mosaic_width()

    def _on_search_submit(self):
        """Enter — run a library-wide index search when that scope is active."""
        text = self._filter_text.strip()
        if not text or self._search_scope != "library":
            return
        try:
            from . import index as index_mod
            rows = index_mod.search(filename_like=text, order_by="date_taken", limit=2000)
            self._search_results = [
                ImageItem(path=r["path"], name=os.path.basename(r["path"]))
                for r in rows if os.path.isfile(r["path"])
            ]
            log.info("library search", q=text, hits=len(self._search_results))
        except Exception as e:
            log.error("library search failed", err=str(e))
            self._search_results = []
        self._render_grid()
        self._update_count()
        self._kick_mosaic_width()

    def _toggle_search_scope(self):
        self._search_scope = "library" if self._search_scope == "folder" else "folder"
        self._scope_btn.setText("Whole library" if self._search_scope == "library"
                                else "This folder")
        self._search.setPlaceholderText(
            "Search the whole library… (Enter)" if self._search_scope == "library"
            else "Filter this folder…")
        self._search_results = None
        if self._is_searching():
            if self._search_scope == "library":
                self._on_search_submit()
            else:
                self._render_grid()
                self._update_count()
                self._kick_mosaic_width()

    def focus_search(self):
        self._search.setFocus()
        self._search.selectAll()

    def clear_search(self) -> bool:
        """Returns True if a search was actually cleared."""
        if not self._is_searching() and self._search_results is None:
            return False
        self._search.clear()          # triggers _on_search_text
        return True

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
        # Drop just the current folder from cache and rescan it.
        self._scan_cache.pop(self.current_path, None)
        self._scan_and_render(force=True)

    def _on_items_removed(self, paths):
        """Files left the current folder (move/delete). Update the model and
        cache in place — the mosaic already dropped its tiles, so we must NOT
        rebuild the grid (that would re-create the mosaic from scratch and
        re-decode every surviving thumbnail)."""
        pathset = {os.path.normcase(os.path.abspath(p)) for p in paths}
        self._items = [it for it in self._items
                       if os.path.normcase(os.path.abspath(it.path)) not in pathset]
        # Keep the in-memory scan cache consistent for back-navigation.
        self._scan_cache[self.current_path] = (self._folders, self._items)
        self._scan_cache.move_to_end(self.current_path)
        self._was_cached = True
        self._update_count()

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
        sep = ' <span style="color:#666">›</span> '
        html_parts = []
        for i, (label, path) in enumerate(segs):
            if i < len(segs) - 1:
                html_parts.append(
                    f'<a href="nav:{i}" style="color:#9bb6e0; text-decoration:none;">{label}</a>'
                )
            else:
                html_parts.append(f'<span>{label}</span>')
        self._breadcrumb.setText(sep.join(html_parts))
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
        if self._is_searching():
            n = len(self._display_items())
            where = "library" if self._search_scope == "library" else "folder"
            self._count_label.setText(f"{n:,} result{'s' if n != 1 else ''} in {where}")
            return
        nf = len(self._folders)
        ni = len(self._items)
        parts = []
        if nf:
            parts.append(f"{nf:,} folder{'s' if nf != 1 else ''}")
        if ni:
            parts.append(f"{ni:,} image{'s' if ni != 1 else ''}")
        text = "  ·  ".join(parts) if parts else "empty"
        if self._was_cached:
            text += "  ·  cached"
        self._count_label.setText(text)

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
                msg = (f"No matches for “{self._filter_text}”.\n\n"
                       "Try a different word, or switch the scope to Whole library.")
            else:
                msg = ("No photos or subfolders here.\n\n"
                       "Drop images or a folder in, or press Esc to go back.")
            empty = QLabel(msg)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {_css_color('muted')}; font-size: 14px; padding: 40px; line-height: 150%;"
            )
            self._grid.addWidget(empty, 0, 0)
            return

        # Section: Folders (hidden while searching — results are images)
        if self._folders and not searching:
            header = self._make_section_header(f"FOLDERS ({len(self._folders)})")
            self._grid.addWidget(header, row, 0, 1, cols)
            row += 1
            for f in self._folders:
                tile = FolderTile(f)
                tile.clicked.connect(self._on_folder_clicked)
                self._tiles.append(tile)
                self._tile_by_cover.setdefault(f.cover_path, tile)
                self._grid.addWidget(tile, row, col)
                col += 1
                if col >= cols:
                    col = 0; row += 1
            if col != 0:
                col = 0; row += 1

        # Section: Images (justified mosaic — variable widths, no crop)
        if items:
            label = (f"RESULTS ({len(items):,})" if searching
                     else f"IMAGES ({len(items):,})")
            header = self._make_section_header(label)
            self._grid.addWidget(header, row, 0, 1, cols)
            row += 1
            self._mosaic = _ImageMosaic(items, self._source, parent=self._grid_host)
            self._mosaic.clicked.connect(self._on_image_clicked)
            self._mosaic.removed.connect(self._on_items_removed)
            self._mosaic.reload_requested.connect(self._force_rescan)
            self._mosaic.selection_changed.connect(self._on_selection_changed)
            self._mosaic.scroll_to.connect(self._scroll_mosaic_to)
            self._mosaic.load_progress.connect(self._set_load_progress)
            self._set_load_progress(0, len(items))
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
        self._scan_label.setText(f"Loading photos   {done} / {total}")
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
        lbl.setStyleSheet(
            "color: #8b9cb4; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1.4px; padding: 14px 4px 4px 4px;"
        )
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
        # Folder grid needs a full re-render only when the column count changes.
        if hasattr(self, "_grid") and self._folders:
            new_cols = self._columns()
            if new_cols != self._last_cols:
                self._render_grid()

    # ── Cover loading ─────────────────────────────────────────────────────────

    def _load_covers(self):
        for tile in self._tiles:
            cover = (tile.item.cover_path
                     if isinstance(tile.item, Folder) else tile.item.path)
            if not cover:
                continue
            if cover in self._cover_cache:
                tile.set_cover(self._cover_cache[cover])
                continue
            self._pool.start(_CoverTask(self._source, cover, self._signals))

    @pyqtSlot(str, QPixmap)
    def _on_cover_ready(self, cover_path: str, pm: QPixmap):
        if pm.isNull():
            return
        self._cover_cache[cover_path] = pm
        self._cover_cache.move_to_end(cover_path)
        while len(self._cover_cache) > self._cover_cache_max:
            self._cover_cache.popitem(last=False)
        # Apply to whichever tile claims this cover (folder cover or image self).
        for tile in self._tiles:
            target = (tile.item.cover_path
                      if isinstance(tile.item, Folder) else tile.item.path)
            if target == cover_path:
                tile.set_cover(pm)

    # ── Click handlers ────────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_folder_clicked(self, folder):
        self._navigate_to(folder.path, push=True)

    def _on_image_clicked(self, idx: int):
        self.open_image.emit(self.current_path, idx)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def cleanup(self):
        if self._mosaic is not None:
            try:
                self._mosaic.cleanup()
            except Exception:
                pass
            self._mosaic = None
        self._pool.clear()
        self._pool.waitForDone(200)
