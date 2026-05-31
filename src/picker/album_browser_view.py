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
    QGridLayout, QSizePolicy, QApplication, QFrame, QMenu
)

from . import theme as theme_mod
from .album import Folder, ImageItem, scan_path
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
                   f"{n} image{'s' if n != 1 else ''}")

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

        self._signals = _WorkerSignals()
        self._signals.header_ready.connect(self._on_header_ready)
        self._signals.thumb_ready.connect(self._on_thumb_ready)

        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) // 2))

        self._pending_headers: set[int] = set()
        self._pending_thumbs: set[int] = set()

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), _qc("canvas_bg"))
        self.setPalette(pal)

        for idx, item in enumerate(items):
            self._aspects[idx] = 1.5
            self._pending_headers.add(idx)
            self._pool.start(_HeaderTask(idx, item.path, self._signals))

    # ── Public API ────────────────────────────────────────────────────────────

    def set_viewport_width(self, width: int):
        if width != self._viewport_width:
            self._viewport_width = width
            self._recompute_layout()

    def cleanup(self):
        try:
            self._pool.clear()
            self._pool.waitForDone(200)
        except Exception:
            pass

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
        self._pending_headers.discard(idx)
        if h > 0:
            self._aspects[idx] = w / h
        # Throttle relayouts to every 50 incoming headers, plus once at the end.
        if not self._pending_headers or len(self._pending_headers) % 50 == 0:
            self._recompute_layout()

    @pyqtSlot(int, QPixmap, int, int)
    def _on_thumb_ready(self, idx: int, pm: QPixmap, w: int, h: int):
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
                p.fillRect(rect, QColor(28, 28, 28))

            if is_video(self._items[idx].path):
                _draw_play_badge(p, rect)

            if idx == self._hover_idx:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(80, 140, 220), self.HOVER_BORDER))
                inset = self.HOVER_BORDER // 2
                p.drawRect(rect.adjusted(inset, inset, -inset, -inset))
        p.end()

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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            pos = event.position().toPoint()
            for idx, rect in self._tiles:
                if rect.contains(pos):
                    self._show_context_menu(idx, event.globalPosition().toPoint())
                    return
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        for idx, rect in self._tiles:
            if rect.contains(pos):
                self.clicked.emit(idx)
                return

    def _show_context_menu(self, idx: int, global_pos):
        if idx < 0 or idx >= len(self._items):
            return
        item = self._items[idx]
        path = item.path
        menu = QMenu(self)

        act_open = menu.addAction("Open in Slideshow")
        act_open.triggered.connect(lambda: self.clicked.emit(idx))
        menu.addSeparator()

        act_sys = menu.addAction("Open with System Default")
        act_sys.triggered.connect(lambda: self._open_default(path))

        act_copy = menu.addAction("Copy Path")
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(path))

        menu.addSeparator()
        act_reveal = menu.addAction("Reveal in Explorer")
        act_reveal.triggered.connect(lambda: self._reveal(path))

        menu.exec(global_pos)

    def _open_default(self, path: str):
        from . import external
        external.open_default(path)

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

        self._back_btn = QPushButton("←")
        self._back_btn.setFixedWidth(40)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setStyleSheet(
            "QPushButton { background: #262626; color: #e5e5e5;"
            " border: 1px solid #353535; border-radius: 6px;"
            " padding: 6px 10px; font-size: 14px; font-weight: 700; }"
            "QPushButton:hover { background: #2f2f2f; border-color: #4a4a4a; }"
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
        )
        scan_lay = QHBoxLayout(self._scan_strip)
        scan_lay.setContentsMargins(0, 0, 0, 0)
        scan_lay.setSpacing(0)
        self._scan_label = QLabel("")
        self._scan_label.setObjectName("scanLbl")
        scan_lay.addWidget(self._scan_label)
        self._scan_strip.hide()
        outer.addWidget(self._scan_strip)

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
            self._render_grid()
            self._update_count()
            return

        # Show full loading screen for heavy folders.
        from .loading_screen import LoadingScreen
        folder_label = os.path.basename(path.rstrip(os.sep)) or path
        loading = LoadingScreen(sub=f"Scanning {folder_label}…", parent=self.window())
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

        self._folders, self._items = scan_path(path, progress_cb=_cb)
        # Store in cache (LRU eviction on overflow).
        self._scan_cache[path] = (self._folders, self._items)
        self._scan_cache.move_to_end(path)
        while len(self._scan_cache) > self._scan_cache_max:
            self._scan_cache.popitem(last=False)
        self._was_cached = False
        loading.close_smoothly()
        self._scan_strip.hide()
        self._render_grid()
        self._update_count()

    def _force_rescan(self):
        # Drop just the current folder from cache and rescan it.
        self._scan_cache.pop(self.current_path, None)
        self._scan_and_render(force=True)

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
        nf = len(self._folders)
        ni = len(self._items)
        parts = []
        if nf:
            parts.append(f"{nf} folder{'s' if nf != 1 else ''}")
        if ni:
            parts.append(f"{ni} image{'s' if ni != 1 else ''}")
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

        cols = self._columns()
        self._last_cols = cols
        row = 0
        col = 0

        # Empty placeholder
        if not self._folders and not self._items:
            empty = QLabel("This folder is empty.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {_css_color('muted')}; font-size: 14px; padding: 40px;"
            )
            self._grid.addWidget(empty, 0, 0)
            return

        # Section: Folders (uniform grid — folders need their name caption)
        if self._folders:
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
        if self._items:
            header = self._make_section_header(f"IMAGES ({len(self._items)})")
            self._grid.addWidget(header, row, 0, 1, cols)
            row += 1
            self._mosaic = _ImageMosaic(self._items, self._source, parent=self._grid_host)
            self._mosaic.clicked.connect(self._on_image_clicked)
            self._grid.addWidget(self._mosaic, row, 0, 1, max(cols, 1))
            # Push the row's column stretch so the mosaic spans full width.
            for c in range(max(cols, 1)):
                self._grid.setColumnStretch(c, 1)
            # Initial layout pass + a deferred one (viewport may not be settled).
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._kick_mosaic_width)
            QTimer.singleShot(80, self._kick_mosaic_width)

        self._load_covers()

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
