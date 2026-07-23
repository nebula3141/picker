import os
import hashlib
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


# ── Constants ──────────────────────────────────────────────────────────────────

ROW_HEIGHT_DEFAULT = 170
SPACING = 3
THUMB_MAX_DIM = 360            # cached thumb longest side
BORDER_WIDTH = 3
CACHE_DIRNAME = ".picker_cache"
WORKERS = 4

DEST_COLORS = [
    QColor(60, 200, 80),
    QColor(60, 150, 230),
    QColor(230, 170, 40),
]
# ── Disk cache helpers (public — used by filmstrip) ────────────────────────────

def cache_key(path: str) -> str:
    stat = os.stat(path)
    raw = f"{path}|{int(stat.st_mtime)}|{stat.st_size}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def cache_file(source_folder: str, path: str) -> Path:
    return Path(source_folder) / CACHE_DIRNAME / f"{cache_key(path)}.jpg"


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


# ── Worker pool ────────────────────────────────────────────────────────────────

class _WorkerSignals(QObject):
    thumb_ready = pyqtSignal(int, QPixmap, int, int)   # idx, pm, orig_w, orig_h
    header_ready = pyqtSignal(int, int, int)           # idx, orig_w, orig_h


def _is_video_path(path: str) -> bool:
    # Local import — avoids a hard dep cycle with media.py at module load.
    from .media import is_video
    return is_video(path)


class _HeaderTask(QRunnable):
    """Read just media header to get dimensions. Very fast.

    For video files the dimensions come from the previously-cached video thumb
    if present (zero ffprobe cost). If no cached thumb exists we emit a 16:9
    placeholder aspect — the layout will correct itself once the thumb task
    delivers the real frame.
    """
    def __init__(self, idx: int, path: str, signals: _WorkerSignals):
        super().__init__()
        self.idx = idx
        self.path = path
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        if _is_video_path(self.path):
            # Try the on-disk video thumb cache for a free dimension read.
            try:
                from .video_thumb import cache_path_for_video
                # Source folder is unknown here; cache_path_for_video uses the
                # video's own folder convention. Probe a generic location:
                # the gallery cache file lives next to source media.
                from os.path import dirname
                src_folder = dirname(self.path)
                cf = cache_path_for_video(src_folder, self.path)
                if cf.exists():
                    img = QImage(str(cf))
                    if not img.isNull():
                        self.signals.header_ready.emit(
                            self.idx, img.width(), img.height()
                        )
                        return
            except Exception:
                pass
            # Fallback: 16:9 placeholder (most videos).
            self.signals.header_ready.emit(self.idx, 16, 9)
            return

        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            # `setAutoTransform(True)` rotates the decoded image but `size()`
            # still returns pre-rotation dimensions from the header. Swap
            # width/height when EXIF orientation is 90/270 so the layout
            # gets the correct aspect on the FIRST pass — otherwise rotated
            # phone shots flash with the wrong aspect until their thumb loads.
            try:
                from PyQt6.QtGui import QImageIOHandler
                tflag = reader.transformation()
                rot = (
                    QImageIOHandler.Transformation.TransformationRotate90
                    | QImageIOHandler.Transformation.TransformationRotate270
                )
                if int(tflag) & int(rot):
                    sz = QSize(sz.height(), sz.width())
            except Exception:
                pass
            self.signals.header_ready.emit(self.idx, sz.width(), sz.height())
        else:
            self.signals.header_ready.emit(self.idx, 3, 2)


class _ThumbTask(QRunnable):
    def __init__(self, idx: int, path: str, cache_file: Path, signals: _WorkerSignals):
        super().__init__()
        self.idx = idx
        self.path = path
        self.cache_file = cache_file
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        if _is_video_path(self.path):
            self._run_video()
            return
        self._run_image()

    def _run_image(self):
        img: QImage | None = None
        if self.cache_file.exists():
            img = QImage(str(self.cache_file))
            if img.isNull():
                img = None

        if img is None:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                longest = max(size.width(), size.height())
                if longest > THUMB_MAX_DIM:
                    scale = THUMB_MAX_DIM / longest
                    reader.setScaledSize(QSize(
                        max(1, int(size.width() * scale)),
                        max(1, int(size.height() * scale)),
                    ))
            img = reader.read()
            if img is None or img.isNull():
                img = QImage(THUMB_MAX_DIM, int(THUMB_MAX_DIM * 2 / 3),
                             QImage.Format.Format_RGB32)
                img.fill(QColor(40, 40, 40))
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(self.cache_file), "JPEG", 82)
            except Exception:
                pass

        pm = QPixmap.fromImage(img)
        self.signals.thumb_ready.emit(self.idx, pm, img.width(), img.height())

    def _run_video(self):
        # The image cache path passed in will be ".jpg"; the video thumb cache
        # uses ".v.jpg" so the two never clobber each other. Compute the video
        # cache path from the same source folder the image cache was based on.
        from .video_thumb import extract_thumb, cache_path_for_video
        # Reverse-engineer the source folder from the cache path:
        # cache_file = <source>/.picker_cache/<key>.jpg
        try:
            source_folder = self.cache_file.parent.parent
        except Exception:
            source_folder = self.cache_file.parent
        v_cache = cache_path_for_video(str(source_folder), self.path)

        img: QImage | None = None
        if v_cache.exists():
            img = QImage(str(v_cache))
            if img.isNull():
                img = None
        if img is None:
            img = extract_thumb(self.path, v_cache)
        if img is None or img.isNull():
            # Placeholder: dark tile so the layout can settle even if ffmpeg
            # is missing or the file is corrupt.
            img = QImage(THUMB_MAX_DIM, int(THUMB_MAX_DIM * 9 / 16),
                         QImage.Format.Format_RGB32)
            img.fill(QColor(28, 28, 28))

        pm = QPixmap.fromImage(img)
        self.signals.thumb_ready.emit(self.idx, pm, img.width(), img.height())


# ── Tile data ──────────────────────────────────────────────────────────────────

@dataclass
class Tile:
    idx: int
    rect: QRect
    aspect: float   # w / h


# ── Justified gallery canvas ───────────────────────────────────────────────────

class _GalleryCanvas(QWidget):
    """Paints justified rows of tiles. One widget for all 1000 images."""

    clicked = pyqtSignal(int)
    load_progress = pyqtSignal(int, int)   # done, total
    load_done = pyqtSignal()

    ZOOM_MIN = 80
    ZOOM_MAX = 400

    def __init__(self, manager: ImageManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._aspects: dict[int, float] = {}
        self._pixmaps: dict[int, QPixmap] = {}
        self._tiles: list[Tile] = []
        self._total_height = 0
        self._viewport_width = 0
        self._row_height = int(settings_mod.get("thumbnail_row_height") or ROW_HEIGHT_DEFAULT)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._persist_row_height)

        self.setAutoFillBackground(True)
        self._apply_theme_bg()
        self.setMouseTracking(True)

        self._signals = _WorkerSignals()
        self._signals.header_ready.connect(self._on_header_ready)
        self._signals.thumb_ready.connect(self._on_thumb_ready)

        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(WORKERS)

        self._pending_headers = set()
        self._pending_thumbs = set()
        self._total_headers = 0

        self._start_loading_headers()

    # ── Loading orchestration ─────────────────────────────────────────────────

    def _apply_theme_bg(self):
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("bg"))
        self.setPalette(pal)

    def _start_loading_headers(self):
        self._total_headers = len(self._manager.images)
        for idx, rec in enumerate(self._manager.images):
            # Default placeholder aspect until header arrives
            self._aspects[idx] = 3.0 / 2.0
            self._pending_headers.add(idx)
            task = _HeaderTask(idx, rec.path, self._signals)
            self._pool.start(task)
        if self._total_headers == 0:
            self.load_done.emit()

    def _schedule_thumb(self, idx: int):
        if idx in self._pixmaps or idx in self._pending_thumbs:
            return
        rec = self._manager.images[idx]
        cf = _cache_file(self._manager.source_folder, rec.path)
        self._pending_thumbs.add(idx)
        task = _ThumbTask(idx, rec.path, cf, self._signals)
        self._pool.start(task)

    @pyqtSlot(int, int, int)
    def _on_header_ready(self, idx: int, w: int, h: int):
        self._pending_headers.discard(idx)
        done = self._total_headers - len(self._pending_headers)
        self.load_progress.emit(done, self._total_headers)
        if h <= 0:
            if not self._pending_headers:
                self.load_done.emit()
            return
        self._aspects[idx] = w / h
        # Recompute layout once in a while — avoid thrash, do every 50 headers
        if len(self._pending_headers) == 0 or len(self._pending_headers) % 50 == 0:
            self._recompute_layout()
        if not self._pending_headers:
            self.load_done.emit()

    @pyqtSlot(int, QPixmap, int, int)
    def _on_thumb_ready(self, idx: int, pm: QPixmap, w: int, h: int):
        self._pending_thumbs.discard(idx)
        self._pixmaps[idx] = pm
        if h > 0:
            actual_aspect = w / h
            if abs(actual_aspect - self._aspects.get(idx, 0)) > 0.01:
                self._aspects[idx] = actual_aspect
        # Repaint just the tile rect
        for tile in self._tiles:
            if tile.idx == idx:
                self.update(tile.rect)
                break

    # ── Layout ────────────────────────────────────────────────────────────────

    def _recompute_layout(self):
        width = self._viewport_width
        if width <= 0:
            return

        target_h = self._row_height
        spacing = SPACING
        tiles: list[Tile] = []

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
        self._total_height = int(y)
        self.setMinimumHeight(self._total_height)
        self.resize(width, self._total_height)
        self.update()

    def zoom(self, factor: float, anchor_idx: int | None = None) -> int:
        """Scale row height by factor. Returns anchor tile's new top-y for scroll preservation."""
        new_h = int(round(self._row_height * factor))
        new_h = max(self.ZOOM_MIN, min(self.ZOOM_MAX, new_h))
        if new_h == self._row_height:
            return -1
        self._row_height = new_h
        self._recompute_layout()
        self._save_timer.start()
        if anchor_idx is not None:
            for tile in self._tiles:
                if tile.idx == anchor_idx:
                    return tile.rect.top()
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

        # Trigger thumb loading for visible + small margin
        margin = 400
        vis_margin = visible.adjusted(0, -margin, 0, margin)

        for tile in self._tiles:
            if not tile.rect.intersects(visible):
                if tile.rect.intersects(vis_margin):
                    self._schedule_thumb(tile.idx)
                continue

            self._schedule_thumb(tile.idx)
            pm = self._pixmaps.get(tile.idx)
            if pm and not pm.isNull():
                painter.drawPixmap(tile.rect, pm)
            else:
                painter.fillRect(tile.rect, theme_mod.c("tile_placeholder"))

            # Status overlay only when destinations are set
            if not self._manager.has_destinations:
                continue
            rec = self._manager.images[tile.idx]
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
        pos = event.position().toPoint()
        for tile in self._tiles:
            if tile.rect.contains(pos):
                rec = self._manager.images[tile.idx]
                name = os.path.basename(rec.path)
                pm = self._pixmaps.get(tile.idx)
                dims = ""
                if pm and not pm.isNull():
                    dims = f"\n{pm.width()}×{pm.height()}"
                self.setToolTip(f"{name}{dims}")
                return
        self.setToolTip("")

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            for tile in self._tiles:
                if tile.rect.contains(pos):
                    self.clicked.emit(tile.idx)
                    return
        elif event.button() == Qt.MouseButton.RightButton:
            for tile in self._tiles:
                if tile.rect.contains(pos):
                    self._show_context_menu(tile.idx, event.globalPosition().toPoint())
                    return

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
        act_folder = menu.addAction("Reveal in Explorer")
        act_folder.triggered.connect(lambda: self._reveal(path))

        menu.exec(global_pos)

    def _launch(self, fn, *args):
        err = fn(*args)
        if err:
            QMessageBox.warning(self, "Launch failed", err)

    def _reveal(self, path: str):
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh_status(self, idx: int | None = None):
        if idx is None:
            self.update()
        else:
            for tile in self._tiles:
                if tile.idx == idx:
                    self.update(tile.rect)
                    return

    def tile_rect(self, idx: int) -> QRect | None:
        for tile in self._tiles:
            if tile.idx == idx:
                return tile.rect
        return None

    def cleanup(self):
        self._pool.clear()
        self._pool.waitForDone(200)


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
                anchor_idx = -1
                anchor_y_before = 0
                for tile in getattr(canvas, "_tiles", []):
                    if tile.rect.contains(vp_pos.x(), canvas_y):
                        anchor_idx = tile.idx
                        anchor_y_before = tile.rect.top() - bar.value()
                        break
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
        if len(manager.images) > 0:
            self._overlay.start(len(manager.images))

        # setCentralWidget can fire our resizeEvent before the scroll area's
        # viewport has settled to its real width — leaving the canvas laid out
        # against a stale width. Re-pull once the event loop has ticked, plus a
        # backup in case other queued events delay the layout pass.
        QTimer.singleShot(0, self._kick_initial_layout)
        QTimer.singleShot(100, self._kick_initial_layout)

        # Evict any stale / oversized disk cache (non-blocking, just stat+unlink)
        try:
            cap_mb = int(settings_mod.get("thumb_cache_mb") or 1024)
            prune_cache(manager.source_folder, cap_mb * 1024 * 1024)
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

    @pyqtSlot()
    def _on_load_done(self):
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
