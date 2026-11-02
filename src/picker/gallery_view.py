import bisect
import os
from pathlib import Path
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QSizePolicy, QApplication, QMenu, QMessageBox,
    QLabel
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QObject, pyqtSignal, pyqtSlot, QPoint,
    QRunnable, QThreadPool, QPropertyAnimation, QEasingCurve,
    QAbstractAnimation, QTimer
)
from PyQt6.QtGui import (
    QPixmap, QImageReader, QColor, QPainter, QPen, QImage, QBrush, QWheelEvent
)

from .image_manager import ImageManager, STATUS_UNREVIEWED
from . import external
from . import settings as settings_mod
from . import theme as theme_mod
from . import dim_cache
from . import thumb_store
# Disk-cache helpers now live in thumb_store; re-exported here for callers.
from .thumb_store import (  # noqa: F401
    THUMB_MAX_DIM, CACHE_DIRNAME, cache_key, cache_file, lower_worker_priority, stat_sig,
)


# ── Constants ──────────────────────────────────────────────────────────────────

ROW_HEIGHT_DEFAULT = 170
SPACING = 3
BORDER_WIDTH = 3

DEST_COLORS = [
    QColor(60, 200, 80),
    QColor(60, 150, 230),
    QColor(230, 170, 40),
]


def prune_cache_async(source_folder: str, max_bytes: int) -> None:
    """:func:`prune_cache` on a daemon thread — it stats every cached file, which
    used to run on the UI thread each time a gallery opened."""
    import threading
    threading.Thread(target=prune_cache, args=(source_folder, max_bytes),
                     daemon=True).start()


def prune_cache(source_folder: str, max_bytes: int) -> None:
    """Evict oldest-accessed thumbs from disk cache once total size exceeds cap."""
    cache_dir = Path(source_folder) / CACHE_DIRNAME
    if not cache_dir.is_dir() or max_bytes <= 0:
        return
    try:
        entries = []
        total = 0
        for p in cache_dir.iterdir():
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((st.st_atime, st.st_size, p))
            total += st.st_size
        if total <= max_bytes:
            return
        # Evict oldest-accessed first
        entries.sort(key=lambda e: e[0])
        for _atime, size, p in entries:
            if total <= max_bytes:
                break
            try:
                p.unlink()
                total -= size
            except OSError:
                continue
    except OSError:
        return


# Back-compat aliases for internal use
_cache_key = cache_key
_cache_file = cache_file


# ── Background dimension lookup ────────────────────────────────────────────────
# Thumbnails and header reads go through the shared thumb_store service. What's
# left here is the one gallery-specific step: ImageManager records carry no stat
# info, so validating the dimension cache needs a stat per file — done on a
# worker instead of the UI thread.



def _fill_source(pm, rect):
    """The centred part of ``pm`` with ``rect``'s proportions — drawing it into
    ``rect`` fills the tile without stretching the photo."""
    pw, ph = pm.width(), pm.height()
    if pw <= 0 or ph <= 0 or rect.width() <= 0 or rect.height() <= 0:
        return QRectF(0, 0, pw, ph)
    want = rect.width() / rect.height()
    have = pw / ph
    if have > want:                      # photo wider: trim the sides
        w = ph * want
        return QRectF((pw - w) / 2, 0, w, ph)
    h = pw / want                        # photo taller: trim top and bottom
    return QRectF(0, (ph - h) / 2, pw, h)

class _LookupSignals(QObject):
    done = pyqtSignal(object, object)   # hits {path: (w,h)}, sigs {path: sig}


class _DimLookupTask(QRunnable):
    def __init__(self, source: str, paths: list[str], signals: _LookupSignals):
        super().__init__()
        self._source, self._paths, self._signals = source, paths, signals
        self.setAutoDelete(True)

    def run(self):
        lower_worker_priority()
        sigs = {p: stat_sig(p) for p in self._paths}
        entries = [(p, s[0] if s else None, s[1] if s else 0) for p, s in sigs.items()]
        found = dim_cache.get_many(self._source, entries)
        hits = {e[0]: d for e, d in zip(entries, found) if d is not None}
        try:
            self._signals.done.emit(hits, sigs)
        except RuntimeError:
            pass


# ── Tile data ──────────────────────────────────────────────────────────────────

@dataclass
class Tile:
    idx: int
    rect: QRect
    aspect: float   # w / h


# ── Justified gallery canvas ───────────────────────────────────────────────────

class _GalleryCanvas(QWidget):
    """Paints justified rows of tiles. One widget for all images; only the rows
    in view are painted/hit-tested, and thumbs come from the shared RAM store."""

    clicked = pyqtSignal(int)
    load_progress = pyqtSignal(int, int)   # done, total
    load_done = pyqtSignal()

    ZOOM_MIN = 80
    ZOOM_MAX = 400
    LOOKAHEAD = 700

    def __init__(self, manager: ImageManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._aspects: dict[int, float] = {}
        self._tiles: list[Tile] = []          # index order: _tiles[i].idx == i
        self._row_tops: list[int] = []
        self._row_starts: list[int] = []
        self._total_height = 0
        self._viewport_width = 0
        self._row_height = int(settings_mod.get("thumbnail_row_height") or ROW_HEIGHT_DEFAULT)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._persist_row_height)
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(60)
        self._relayout_timer.timeout.connect(self._recompute_layout)

        self.setAutoFillBackground(True)
        self._apply_theme_bg()
        self.setMouseTracking(True)

        self._store = thumb_store.store()
        self._svc = thumb_store.service()
        self._svc.thumb_ready.connect(self._on_thumb_ready)
        self._svc.dims_ready.connect(self._on_dims_ready)
        self._index_by_path: dict[str, int] = {}
        self._sigs: dict[str, tuple[int, int] | None] = {}
        self._requested: set[str] = set()
        self._last_scan_rect = QRect()
        self._pending_headers: set[str] = set()
        self._unknown: set[int] = set()
        self._total_headers = 0
        self._stopped = False

        self._lookup_signals = _LookupSignals()
        self._lookup_signals.done.connect(self._on_lookup_done)

        self._start_loading_headers()

    # ── Loading orchestration ─────────────────────────────────────────────────

    def _apply_theme_bg(self):
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("bg"))
        self.setPalette(pal)

    def _start_loading_headers(self):
        images = self._manager.images
        self._total_headers = len(images)
        self._index_by_path = {rec.path: i for i, rec in enumerate(images)}
        for i in range(len(images)):
            self._aspects[i] = 3.0 / 2.0
        if not images:
            self.load_progress.emit(0, 0)
            self.load_done.emit()
            return
        # Cache validation needs a stat per file — do it off the UI thread.
        paths = [rec.path for rec in images]
        QThreadPool.globalInstance().start(
            _DimLookupTask(self._manager.source_folder, paths, self._lookup_signals))

    @pyqtSlot(object, object)
    def _on_lookup_done(self, hits: dict, sigs: dict):
        if self._stopped:
            return
        self._sigs = sigs
        known = []
        self._unknown = set()
        for path, idx in self._index_by_path.items():
            dims = hits.get(path)
            if dims is not None and dims[1] > 0:
                self._aspects[idx] = dims[0] / dims[1]
                known.append(self._aspects[idx])
            else:
                self._unknown.add(idx)
        # Unknown sizes get the folder's average aspect and are read only when
        # they approach the viewport (see _schedule_visible) — not all at once.
        if known:
            est = sum(known) / len(known)
            for idx in self._unknown:
                self._aspects[idx] = est
        self._pending_headers = set()
        self.load_progress.emit(self._total_headers, self._total_headers)
        self._recompute_layout()
        self.load_done.emit()

    def _request_visible_dims(self, index_range):
        if not self._unknown:
            return
        images = self._manager.images
        want = []
        for idx in index_range:
            if idx in self._unknown and idx < len(images):
                p = images[idx].path
                if p not in self._pending_headers:
                    self._pending_headers.add(p)
                    want.append(p)
        if want:
            self._svc.request_dims(self._manager.source_folder, want)

    @pyqtSlot(str, object)
    def _on_dims_ready(self, source: str, results):
        if self._stopped or source != self._manager.source_folder or not self._pending_headers:
            return
        changed = False
        for p, w, h, exact, _sig in results:
            if p not in self._pending_headers:
                continue
            self._pending_headers.discard(p)
            idx = self._index_by_path.get(p)
            if idx is None:
                continue
            # One header attempt per tile: an inexact answer (video, HEIC) must
            # not be re-requested on every relayout — the thumbnail settles it.
            self._unknown.discard(idx)
            if exact and h > 0:
                self._aspects[idx] = w / h
                changed = True
        if changed:
            self._relayout_timer.start()

    @pyqtSlot(str)
    def _on_thumb_ready(self, path: str):
        if self._stopped:
            return
        idx = self._index_by_path.get(path)
        if idx is None:
            return
        self._requested.discard(path)
        pm = self._store.get(path)
        if pm is None:
            return
        h = pm.height()
        if h > 0 and not self._store.is_placeholder(path):
            self._unknown.discard(idx)
            self._pending_headers.discard(path)
            actual_aspect = pm.width() / h
            if abs(actual_aspect - self._aspects.get(idx, 0)) > 0.01:
                self._aspects[idx] = actual_aspect
                # Thumb is authoritative (EXIF-aware) — persist the correction.
                sig = self._sigs.get(path)
                if sig is not None:
                    dim_cache.put_sig(self._manager.source_folder, path, pm.width(), h, sig)
                    self._svc.note_dims(self._manager.source_folder)
                self._relayout_timer.start()
                return
        if idx < len(self._tiles):
            self.update(self._tiles[idx].rect)

    def _schedule_visible(self):
        vis = self.visibleRegion().boundingRect()
        if vis.isEmpty() or vis == self._last_scan_rect:
            return
        self._last_scan_rect = vis
        images = self._manager.images
        src = self._manager.source_folder
        wanted: set[str] = set()
        self._request_visible_dims(
            self._index_range(vis.top() - self.LOOKAHEAD * 2, vis.bottom() + self.LOOKAHEAD * 2))
        for idx in self._index_range(vis.top() - self.LOOKAHEAD, vis.bottom() + self.LOOKAHEAD):
            if idx >= len(images):
                break
            path = images[idx].path
            wanted.add(path)
            if self._store.contains(path):
                continue
            pri = (self._svc.PRI_VISIBLE if self._tiles[idx].rect.intersects(vis)
                   else self._svc.PRI_NEAR)
            if self._svc.request(path, src, pri, self._sigs.get(path)):
                self._requested.add(path)
        for p in self._requested - wanted:
            self._svc.cancel(p)
        self._requested &= wanted

    # ── Layout ────────────────────────────────────────────────────────────────

    def _recompute_layout(self):
        width = self._viewport_width
        if width <= 0:
            return

        target_h = self._row_height
        spacing = SPACING
        tiles: list[Tile] = []
        row_tops: list[int] = []
        row_starts: list[int] = []

        row_items: list[tuple[int, float]] = []
        row_aspect_sum = 0.0
        y = 0

        def close_row(fit_to_width: bool):
            nonlocal y
            if not row_items:
                return
            # Guard zero-division when every aspect in a row is 0 (all-broken
            # headers — hypothetical but cheap to defend).
            if fit_to_width and row_aspect_sum > 0:
                actual_h = (width - (len(row_items) - 1) * spacing) / row_aspect_sum
            else:
                actual_h = target_h
            row_tops.append(int(round(y)))
            row_starts.append(row_items[0][0])
            x = 0
            for idx, a in row_items:
                w = a * actual_h
                rect = QRect(int(round(x)), int(round(y)),
                             int(round(w)), int(round(actual_h)))
                tiles.append(Tile(idx=idx, rect=rect, aspect=a))
                x += w + spacing
            y += actual_h + spacing

        for idx in range(len(self._manager.images)):
            aspect = self._aspects.get(idx, 1.5)
            if aspect <= 0:                  # bad header → fall back to 3:2
                aspect = 1.5
            row_items.append((idx, aspect))
            row_aspect_sum += aspect
            required_w = row_aspect_sum * target_h + (len(row_items) - 1) * spacing
            if required_w >= width:
                close_row(fit_to_width=True)
                row_items = []
                row_aspect_sum = 0.0

        # Last partial row
        if row_items:
            close_row(fit_to_width=False)

        self._tiles = tiles
        self._row_tops = row_tops
        self._row_starts = row_starts
        self._last_scan_rect = QRect()
        self._total_height = int(y)
        self.setMinimumHeight(self._total_height)
        self.resize(width, self._total_height)
        self.update()

    def _index_range(self, top: int, bottom: int) -> range:
        """Tile indices in rows intersecting [top, bottom] (rows vary in height,
        so start one row early and let the rect test trim)."""
        if not self._row_tops:
            return range(0)
        r0 = max(0, bisect.bisect_right(self._row_tops, top) - 1)
        r1 = bisect.bisect_right(self._row_tops, bottom)
        if r0 >= r1:
            return range(0)
        start = self._row_starts[r0]
        end = self._row_starts[r1] if r1 < len(self._row_starts) else len(self._tiles)
        return range(start, end)

    def _tile_at(self, pos) -> int:
        for idx in self._index_range(pos.y(), pos.y()):
            if self._tiles[idx].rect.contains(pos):
                return idx
        return -1

    def zoom(self, factor: float, anchor_idx: int | None = None) -> int:
        """Scale row height by factor. Returns anchor tile's new top-y for scroll preservation."""
        new_h = int(round(self._row_height * factor))
        new_h = max(self.ZOOM_MIN, min(self.ZOOM_MAX, new_h))
        if new_h == self._row_height:
            return -1
        self._row_height = new_h
        self._recompute_layout()
        self._save_timer.start()
        if anchor_idx is not None and 0 <= anchor_idx < len(self._tiles):
            return self._tiles[anchor_idx].rect.top()
        return -1

    def row_height(self) -> int:
        return self._row_height

    def _persist_row_height(self):
        try:
            settings_mod.set_value("thumbnail_row_height", int(self._row_height))
        except Exception:
            pass

    def set_viewport_width(self, width: int):
        if width != self._viewport_width:
            self._viewport_width = width
            self._recompute_layout()
        # Empty state needs visible area to paint on
        if len(self._manager.images) == 0 and self.parent() is not None:
            vp = self.parent()
            h = vp.height() if hasattr(vp, 'height') else 400
            self.setMinimumHeight(max(h, 400))
            self.resize(width, max(h, 400))
            self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        visible = event.rect()
        painter.fillRect(visible, theme_mod.c("bg"))

        # Empty state
        if len(self._manager.images) == 0:
            self._paint_empty_state(painter)
            painter.end()
            return

        self._schedule_visible()
        images = self._manager.images
        placeholder = theme_mod.c("tile_placeholder")
        has_dest = self._manager.has_destinations

        for idx in self._index_range(visible.top(), visible.bottom()):
            if idx >= len(images):
                break
            tile = self._tiles[idx]
            if not tile.rect.intersects(visible):
                continue
            rec = images[idx]
            pm = self._store.get(rec.path)
            if pm is not None:
                # The thumbnail is authoritative: if the tile's shape came from
                # a stale or guessed size (e.g. on a revisit, when no thumbnail
                # "arrives" to correct it), take the photo's shape.
                if (pm.height() > 0 and not self._store.is_placeholder(rec.path)
                        and abs(pm.width() / pm.height() - self._aspects.get(idx, 0))
                        > 0.05 * (pm.width() / pm.height())):
                    self._aspects[idx] = pm.width() / pm.height()
                    self._relayout_timer.start()
                painter.drawPixmap(QRectF(tile.rect), pm, _fill_source(pm, tile.rect))
            else:
                painter.fillRect(tile.rect, placeholder)

            # Status overlay only when destinations are set
            if not has_dest:
                continue
            color = self._status_color(rec.status)
            if color:
                pen = QPen(color)
                pen.setWidth(BORDER_WIDTH)
                painter.setPen(pen)
                inset = BORDER_WIDTH // 2
                painter.drawRect(tile.rect.adjusted(inset, inset, -inset, -inset))
                # Corner badge
                badge_size = 14
                br = QRect(tile.rect.right() - badge_size - 4,
                           tile.rect.top() + 4,
                           badge_size, badge_size)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(br)

        painter.end()

    def _paint_empty_state(self, p: QPainter):
        w, h = self.width() or 800, self.height() or 400
        cx, cy = w // 2, h // 2

        # Dashed drop-zone rectangle
        box_w = min(520, w - 80)
        box_h = 260
        bx = cx - box_w // 2
        by = cy - box_h // 2
        pen = QPen(theme_mod.c("empty_dash"), 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(bx, by, box_w, box_h, 14, 14)

        # Icon — folder with down arrow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(59, 130, 246, 180))
        icon_size = 56
        ix = cx - icon_size // 2
        iy = by + 32
        p.drawRoundedRect(ix, iy + 10, icon_size, icon_size - 16, 6, 6)
        p.setBrush(QColor(60, 160, 240))
        p.drawRoundedRect(ix, iy + 6, icon_size // 2, 10, 3, 3)
        # Arrow
        p.setPen(QPen(QColor(255, 255, 255), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        ax = cx
        ay = iy + 22
        p.drawLine(ax, ay, ax, ay + 18)
        p.drawLine(ax, ay + 18, ax - 7, ay + 11)
        p.drawLine(ax, ay + 18, ax + 7, ay + 11)

        # Title
        p.setPen(theme_mod.c("empty_title"))
        f = p.font()
        f.setPointSize(15)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRect(bx, iy + icon_size + 18, box_w, 28),
                   Qt.AlignmentFlag.AlignHCenter, "Drop a folder here")

        # Hint
        f.setPointSize(10)
        f.setBold(False)
        p.setFont(f)
        p.setPen(theme_mod.c("empty_hint"))
        p.drawText(QRect(bx, iy + icon_size + 54, box_w, 22),
                   Qt.AlignmentFlag.AlignHCenter,
                   "…or press Ctrl+O to open a folder")
        p.setPen(theme_mod.c("empty_footer"))
        p.drawText(QRect(bx, iy + icon_size + 80, box_w, 20),
                   Qt.AlignmentFlag.AlignHCenter,
                   "Supported: JPG, PNG, TIFF, WEBP, CR2/CR3, NEF, ARW, DNG, RAF, ORF, RW2")

    def _status_color(self, status: str) -> QColor | None:
        if status.startswith("dest_"):
            try:
                i = int(status.split("_")[1])
                return DEST_COLORS[i % len(DEST_COLORS)]
            except ValueError:
                return None
        return None

    # ── Input ─────────────────────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        idx = self._tile_at(event.position().toPoint())
        if 0 <= idx < len(self._manager.images):
            rec = self._manager.images[idx]
            name = os.path.basename(rec.path)
            pm = self._store.get(rec.path)
            dims = f"\n{pm.width()}×{pm.height()}" if pm is not None else ""
            self.setToolTip(f"{name}{dims}")
            return
        self.setToolTip("")

    def mousePressEvent(self, event):
        idx = self._tile_at(event.position().toPoint())
        if idx < 0:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(idx)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(idx, event.globalPosition().toPoint())

    def _show_context_menu(self, idx: int, global_pos):
        rec = self._manager.images[idx]
        path = rec.path
        menu = QMenu(self)

        act_open = menu.addAction("Open in Slideshow")
        act_open.triggered.connect(lambda: self.clicked.emit(idx))
        menu.addSeparator()

        ps = external.photoshop_path()
        act_ps = menu.addAction("Open with Photoshop")
        act_ps.setEnabled(ps is not None)
        if ps:
            act_ps.triggered.connect(lambda: self._launch(external.open_with, ps, path))

        lr = external.lightroom_path()
        act_lr = menu.addAction("Open with Lightroom")
        act_lr.setEnabled(lr is not None)
        if lr:
            act_lr.triggered.connect(lambda: self._launch(external.open_with, lr, path))

        act_sys = menu.addAction("Open with System Default")
        act_sys.triggered.connect(lambda: self._launch(external.open_default, path))

        menu.addSeparator()
        act_folder = menu.addAction(external.reveal_label())
        act_folder.triggered.connect(lambda: self._reveal(path))

        menu.exec(global_pos)

    def _launch(self, fn, *args):
        err = fn(*args)
        if err:
            QMessageBox.warning(self, "Launch failed", err)

    def _reveal(self, path: str):
        err = external.reveal_in_file_manager(path)
        if err:
            QMessageBox.warning(self, "Error", err)

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh_status(self, idx: int | None = None):
        if idx is None:
            self.update()
        elif 0 <= idx < len(self._tiles):
            self.update(self._tiles[idx].rect)

    def tile_rect(self, idx: int) -> QRect | None:
        return self._tiles[idx].rect if 0 <= idx < len(self._tiles) else None

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


# ── Smooth-scroll scroll area ──────────────────────────────────────────────────

class _SmoothScrollArea(QScrollArea):
    WHEEL_STEP = 240    # pixels per wheel notch
    DURATION = 220      # ms
    ZOOM_FACTOR = 1.12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value")
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(self.DURATION)
        self._target = 0

    def wheelEvent(self, event: QWheelEvent):
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        # Ctrl+wheel = zoom tiles
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            canvas = self.widget()
            if canvas and hasattr(canvas, "zoom"):
                factor = self.ZOOM_FACTOR if delta > 0 else (1 / self.ZOOM_FACTOR)
                # Find tile under cursor (canvas coords = viewport y + scroll value)
                vp_pos = event.position().toPoint()
                canvas_y = vp_pos.y() + bar.value()
                anchor_idx = canvas._tile_at(QPoint(vp_pos.x(), canvas_y))
                anchor_y_before = (canvas._tiles[anchor_idx].rect.top() - bar.value()
                                   if anchor_idx >= 0 else 0)
                new_top = canvas.zoom(factor, anchor_idx if anchor_idx >= 0 else None)
                if new_top >= 0:
                    # Keep anchor tile at same viewport y-offset
                    self._anim.stop()
                    target = max(bar.minimum(), min(bar.maximum(), new_top - anchor_y_before))
                    bar.setValue(target)
                    self._target = target
            event.accept()
            return
        step = self.WHEEL_STEP * (1 if delta > 0 else -1)
        if self._anim.state() != QAbstractAnimation.State.Running:
            self._target = bar.value()
        self._target = max(bar.minimum(), min(bar.maximum(), self._target - step))
        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(self._target)
        self._anim.start()
        event.accept()

    def ensure_visible_rect(self, rect: QRect):
        bar = self.verticalScrollBar()
        vp_h = self.viewport().height()
        if rect.top() < bar.value():
            target = rect.top() - 20
        elif rect.bottom() > bar.value() + vp_h:
            target = rect.bottom() - vp_h + 20
        else:
            return
        target = max(bar.minimum(), min(bar.maximum(), target))
        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(target)
        self._anim.start()
        self._target = target


# ── Loading overlay ────────────────────────────────────────────────────────────

class _LoadingOverlay(QWidget):
    """Centered spinner + progress text shown while headers load."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._total = 0
        self._done = 0
        self._angle = 0

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._spin)

    def start(self, total: int):
        self._total = total
        self._done = 0
        self._angle = 0
        self.show()
        self.raise_()
        self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self.hide()

    def set_progress(self, done: int, total: int):
        self._done = done
        self._total = total
        self.update()

    def _spin(self):
        self._angle = (self._angle + 18) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Dim bg
        p.fillRect(self.rect(), QColor(0, 0, 0, 200))

        cx = self.width() // 2
        cy = self.height() // 2

        # Spinner — arc sweeping
        r = 32
        rect = QRect(cx - r, cy - r - 40, 2 * r, 2 * r)
        p.setPen(QPen(QColor(60, 60, 60), 4))
        p.drawEllipse(rect)
        pen = QPen(QColor(59, 130, 246), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, -self._angle * 16, 90 * 16)

        # Text
        p.setPen(QColor(240, 240, 240))
        f = p.font()
        f.setPointSize(13)
        f.setBold(True)
        p.setFont(f)
        title = "Scanning images…"
        p.drawText(QRect(0, cy + 8, self.width(), 24), Qt.AlignmentFlag.AlignHCenter, title)

        # Progress
        f.setPointSize(10)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(160, 160, 160))
        if self._total > 0:
            sub = f"{self._done} / {self._total}"
        else:
            sub = ""
        p.drawText(QRect(0, cy + 34, self.width(), 20), Qt.AlignmentFlag.AlignHCenter, sub)

        # Bar
        bar_w = 260
        bar_h = 4
        bx = cx - bar_w // 2
        by = cy + 62
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(50, 50, 50))
        p.drawRoundedRect(bx, by, bar_w, bar_h, 2, 2)
        if self._total > 0:
            fill = int(bar_w * self._done / self._total)
            p.setBrush(QColor(59, 130, 246))
            p.drawRoundedRect(bx, by, fill, bar_h, 2, 2)
        p.end()


# ── Public GalleryView ─────────────────────────────────────────────────────────

class GalleryView(QWidget):
    open_slideshow = pyqtSignal(int)

    def __init__(self, manager: ImageManager, parent=None):
        super().__init__(parent)
        self._manager = manager

        self.setAutoFillBackground(True)
        self._apply_bg()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._top_bar = QLabel()
        self._top_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._top_bar.setTextFormat(Qt.TextFormat.RichText)
        self._apply_bar_qss()
        layout.addWidget(self._top_bar)

        self._scroll = _SmoothScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._apply_scroll_qss()
        layout.addWidget(self._scroll, 1)

        self._bottom_bar = QLabel()
        self._bottom_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bottom_bar.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._bottom_bar)
        self._refresh_bars()

        self._canvas = _GalleryCanvas(manager)
        self._canvas.clicked.connect(self.open_slideshow)
        self._scroll.setWidget(self._canvas)

        self._overlay = _LoadingOverlay(self)
        self._overlay.hide()
        self._canvas.load_progress.connect(self._on_load_progress)
        self._canvas.load_done.connect(self._on_load_done)
        # The grid is usable immediately (placeholder aspects, cached dims land
        # in milliseconds) — only put up the dimming overlay if loading is
        # genuinely slow, instead of hiding a working grid on every open.
        self._load_finished = False
        if len(manager.images) > 0:
            QTimer.singleShot(400, self._maybe_show_overlay)

        # setCentralWidget can fire our resizeEvent before the scroll area's
        # viewport has settled to its real width — leaving the canvas laid out
        # against a stale width. Re-pull once the event loop has ticked, plus a
        # backup in case other queued events delay the layout pass.
        QTimer.singleShot(0, self._kick_initial_layout)
        QTimer.singleShot(100, self._kick_initial_layout)

        # Evict any stale / oversized disk cache (non-blocking, just stat+unlink)
        try:
            cap_mb = int(settings_mod.get("thumb_cache_mb") or 1024)
            prune_cache_async(manager.source_folder, cap_mb * 1024 * 1024)
        except Exception:
            pass

    def _apply_bg(self):
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("bg"))
        self.setPalette(pal)

    def _apply_bar_qss(self):
        bg = QColor(theme_mod.c("hint_bar_bg")).name()
        fg = QColor(theme_mod.c("hint_bar_fg")).name()
        muted = QColor(theme_mod.c("muted")).name()
        qss_top = (
            f"background:{bg}; color:{fg};"
            f" padding:7px 14px; font-size:13px; font-weight:600;"
            f" letter-spacing:0.3px;"
        )
        qss_bot = (
            f"background:{bg}; color:{muted};"
            f" padding:5px 14px; font-size:11px;"
            f" letter-spacing:0.3px;"
        )
        self._top_bar.setStyleSheet(qss_top)
        if hasattr(self, "_bottom_bar"):
            self._bottom_bar.setStyleSheet(qss_bot)

    def _refresh_bars(self):
        src = self._manager.source_folder or ""
        name = os.path.basename(src.rstrip("/\\")) or src
        muted = QColor(theme_mod.c("muted")).name()
        self._top_bar.setText(
            f'{name}'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{muted}; font-weight:500;">'
            f'{src}</span>'
        )
        sep = '&nbsp;&nbsp;&nbsp;<span style="color:#555">·</span>&nbsp;&nbsp;&nbsp;'
        hints = [
            "Click = open",
            "Right-click = editor",
            "<b>Ctrl+Scroll</b> = zoom tiles",
            "Drop folder = swap source",
        ]
        self._bottom_bar.setText(sep.join(hints))

    def _apply_scroll_qss(self):
        bg = theme_mod.c("scrollarea_bg")
        self._scroll.setStyleSheet(f"QScrollArea {{ background: {bg}; border: 0; }}")
        self._scroll.viewport().setStyleSheet(f"background: {bg};")

    def refresh_theme(self):
        self._apply_bg()
        self._apply_scroll_qss()
        self._apply_bar_qss()
        self._refresh_bars()
        self._canvas._apply_theme_bg()
        self._canvas.update()

    def minimum_state_repaint(self):
        self._canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        vp_w = self._scroll.viewport().width()
        self._canvas.set_viewport_width(vp_w)
        self._overlay.setGeometry(self.rect())

    def _kick_initial_layout(self):
        vp_w = self._scroll.viewport().width()
        if vp_w > 0:
            self._canvas.set_viewport_width(vp_w)

    @pyqtSlot(int, int)
    def _on_load_progress(self, done: int, total: int):
        self._overlay.set_progress(done, total)

    def _maybe_show_overlay(self):
        try:
            if not self._load_finished:
                self._overlay.start(len(self._manager.images))
                self._overlay.setGeometry(self.rect())
        except RuntimeError:
            pass   # view already torn down

    @pyqtSlot()
    def _on_load_done(self):
        self._load_finished = True
        self._overlay.stop()

    def refresh_cell(self, idx: int):
        self._canvas.refresh_status(idx)

    def refresh_all(self):
        self._canvas.refresh_status()

    def scroll_to(self, idx: int):
        rect = self._canvas.tile_rect(idx)
        if rect:
            self._scroll.ensure_visible_rect(rect)

    def status_text(self) -> str:
        s = self._manager.stats()
        if not self._manager.has_destinations:
            return f"Browse  ·  {s['total']} images  ·  {self._manager.source_folder}"
        return (
            f"Total: {s['total']}  |  "
            f"Selected: {s['selected']}  |  "
            f"Remaining: {s['unreviewed']}"
        )

    def cleanup(self):
        self._canvas.cleanup()
