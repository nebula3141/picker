import math
import os
import time
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QMessageBox, QApplication, QSizePolicy, QMenu, QStackedWidget,
    QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QPointF, QPoint, QObject, QRunnable, QThreadPool,
    pyqtSignal, pyqtSlot, QTimer, QPropertyAnimation, QEasingCurve, QVariantAnimation
)
from PyQt6.QtGui import (
    QPixmap, QImageReader, QKeyEvent, QWheelEvent, QImage, QColor, QFont,
    QPainter, QPen, QBrush, QMouseEvent, QTransform
)

try:
    import numpy as np
    _HAVE_NUMPY = True
except Exception:
    _HAVE_NUMPY = False

from .image_manager import ImageManager, STATUS_UNREVIEWED
from .gallery_view import cache_file as gallery_cache_file, THUMB_MAX_DIM
from . import external
from . import exif as exif_mod
from . import raw_loader
from . import settings as settings_mod
from . import conflict_dialog
from . import theme as theme_mod
from . import edits as edits_mod
from . import save_dialog as save_dialog_mod
from . import log
from .icon import menu_icon

# Cache settings-derived values on slideshow init; refreshed only when reloading image.
# Avoids re-parsing settings.json on every nav.


def _pixmap_bytes(pm) -> int:
    if pm is None or pm.isNull():
        return 0
    return pm.width() * pm.height() * pm.depth() // 8


# ── Smart decode scaling ───────────────────────────────────────────────────────
# The resolution_pct knob exists to keep memory/decode time sane on huge files
# (60MP RAW). Blindly applying it to a 100×100 thumbnail just throws away detail
# for no gain. So we (1) never downscale an image whose long edge already fits the
# display, and (2) never scale a large image *below* the display floor — the
# requested pct is treated as a hint, clamped to keep the result crisp on screen.

_MIN_DECODE_EDGE: int | None = None


def _min_decode_edge() -> int:
    """Longest edge (px) we'll ever decode down to — the primary screen's long
    side in physical pixels, so a fit-to-window view stays pixel-sharp. Cached."""
    global _MIN_DECODE_EDGE
    if _MIN_DECODE_EDGE is None:
        edge = 1920
        try:
            scr = QApplication.primaryScreen()
            if scr is not None:
                g = scr.geometry()
                dpr = scr.devicePixelRatio() or 1.0
                edge = int(round(max(g.width(), g.height()) * dpr))
        except Exception:
            pass
        _MIN_DECODE_EDGE = max(1280, edge)
    return _MIN_DECODE_EDGE


def _smart_scaled_size(src_w: int, src_h: int, pct: int) -> tuple[int, int] | None:
    """Decode size for (src_w, src_h) at the requested pct, or None to decode at
    native size. Small images are left alone; large ones never drop below the
    display floor."""
    if pct >= 100 or src_w <= 0 or src_h <= 0:
        return None
    long_edge = max(src_w, src_h)
    floor = _min_decode_edge()
    if long_edge <= floor:
        return None  # already small enough — full native detail
    eff = max(pct / 100.0, floor / long_edge)
    if eff >= 1.0:
        return None
    return max(1, round(src_w * eff)), max(1, round(src_h * eff))


# ── Background full-res loader (QThreadPool) ───────────────────────────────────

class _LoaderSignals(QObject):
    image_ready = pyqtSignal(int, QImage)


class ImageLoadTask(QRunnable):
    def __init__(self, manager: ImageManager, idx: int, signals: _LoaderSignals):
        super().__init__()
        self._manager = manager
        self._idx = idx
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        img = self._load(self._idx)
        # QPixmap can't be constructed off-main-thread; emit QImage instead
        self._signals.image_ready.emit(self._idx, img)

    def _load(self, idx: int) -> QImage:
        if idx < 0 or idx >= len(self._manager.images):
            return QImage()
        rec = self._manager.images[idx]
        # If the user set "Disable resolution limit" in Settings, decode at
        # native size regardless of the per-source pct.
        if bool(settings_mod.get("display_full_resolution")):
            pct = 100
        else:
            pct = self._manager.resolution_pct

        if raw_loader.is_raw(rec.path) and raw_loader.available():
            prefer_thumb = (settings_mod.get("raw_preference") or "embedded") == "embedded"
            img = raw_loader.load_raw(rec.path, prefer_thumb=prefer_thumb)
            if img is not None and not img.isNull():
                ssz = _smart_scaled_size(img.width(), img.height(), pct)
                if ssz is not None:
                    img = img.scaled(ssz[0], ssz[1],
                                     Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                return img

        reader = QImageReader(rec.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            ssz = _smart_scaled_size(size.width(), size.height(), pct)
            if ssz is not None:
                reader.setScaledSize(QSize(ssz[0], ssz[1]))
        img = reader.read()
        if img.isNull():
            img = QImage(400, 300, QImage.Format.Format_RGB32)
            img.fill(QColor(60, 60, 60))
        return img


# ── Image canvas — zoom anchored at cursor, pan via drag ───────────────────────

class ImageCanvas(QWidget):
    BG = QColor(17, 17, 17)
    ZOOM_MIN = 0.05
    ZOOM_MAX = 20.0
    WHEEL_FACTOR = 1.18

    view_changed = pyqtSignal(float, float, float)  # norm_cx, norm_cy, zoom_ratio

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None     # rotated pixmap drawn
        self._base_pixmap: QPixmap | None = None  # original (unrotated)
        self._rotation = 0                      # degrees: 0/90/180/270
        self._zoom = 1.0
        self._fit_zoom = 1.0
        self._offset = QPointF(0, 0)
        self._panning = False
        self._pan_start = QPoint()
        self._pan_offset_start = QPointF()

        # Overlays
        self._exif_lines: list[str] = []
        self._show_exif = False
        self._show_histogram = False
        self._show_peaking = False
        self._histogram: list[list[int]] | None = None     # [r, g, b] each 256
        self._histogram_clipped = False
        self._clip_pulse_phase = 0.0
        self._peaking_overlay: QImage | None = None
        self._double_click_enabled = False
        self._raw_loading = False

        # Crop mode — drag a rect on the canvas; stored in rotated-pixmap pixel coords.
        self._crop_mode = False
        self._crop_rect: QRectF | None = None
        # Interaction state while dragging the crop box:
        #   mode in {None, "new", "move", "resize_<tl|tr|bl|br|t|b|l|r>"}
        self._crop_drag_mode: str | None = None
        self._crop_drag_anchor: QPointF | None = None    # pixmap-space anchor point
        self._crop_rect_at_drag: QRectF | None = None    # rect snapshot at drag start

        # Cache per-image overlay results keyed by (path, mtime) or arbitrary key.
        # LRU-bounded so memory stays flat when browsing many images.
        self._current_key: object | None = None
        self._hist_cache: "OrderedDict[object, tuple[list[list[int]], bool]]" = OrderedDict()
        self._peak_cache: "OrderedDict[object, QImage]" = OrderedDict()
        self._CACHE_MAX = 16

        # Overlay positions (from settings): "tl" / "tr" / "bl" / "br"
        self._exif_pos = settings_mod.get("exif_position") or "tr"
        self._hist_pos = settings_mod.get("histogram_position") or "br"

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(60)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

        # Cross-fade animation state. Progress advances linearly; the drawn
        # opacity is run through an ease-in-out curve so the dissolve feels
        # fluid rather than mechanically linear.
        self._fade_opacity = 1.0
        self._fade_progress = 1.0
        self._fade_curve = QEasingCurve(QEasingCurve.Type.InOutCubic)
        self._old_pixmap: QPixmap | None = None
        self._old_offset = QPointF(0, 0)
        self._old_zoom = 1.0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)  # ~60fps
        self._fade_timer.timeout.connect(self._fade_tick)
        self._animate = bool(settings_mod.get("slideshow_animation"))

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ── Sync API (for loupe sync in compare view) ──────────────────────────────

    _sync_emitting = False

    def _emit_view_changed(self):
        if self._sync_emitting or self._pixmap is None or self._pixmap.isNull():
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return
        cx = (self.width() / 2 - self._offset.x()) / (pw * self._zoom)
        cy = (self.height() / 2 - self._offset.y()) / (ph * self._zoom)
        zoom_ratio = self._zoom / self._fit_zoom if self._fit_zoom > 0 else 1.0
        self.view_changed.emit(cx, cy, zoom_ratio)

    def apply_sync(self, norm_cx: float, norm_cy: float, zoom_ratio: float):
        if self._pixmap is None or self._pixmap.isNull():
            return
        self._sync_emitting = True
        pw, ph = self._pixmap.width(), self._pixmap.height()
        self._zoom = self._fit_zoom * zoom_ratio
        self._offset = QPointF(
            self.width() / 2 - norm_cx * pw * self._zoom,
            self.height() / 2 - norm_cy * ph * self._zoom,
        )
        self.update()
        self._sync_emitting = False

    # ── API ────────────────────────────────────────────────────────────────────

    @property
    def show_peaking(self) -> bool:
        return self._show_peaking

    @property
    def show_exif(self) -> bool:
        return self._show_exif

    @property
    def show_histogram(self) -> bool:
        return self._show_histogram

    def set_overlay_positions(self, exif_pos: str, hist_pos: str):
        self._exif_pos = exif_pos
        self._hist_pos = hist_pos
        self.update()

    def _start_fade(self):
        if self._pixmap and not self._pixmap.isNull():
            self._old_pixmap = self._pixmap
            self._old_offset = QPointF(self._offset)
            self._old_zoom = self._zoom
        self._fade_progress = 0.0
        self._fade_opacity = 0.0
        self._fade_timer.start()

    def _fade_tick(self):
        self._fade_progress = min(1.0, self._fade_progress + 0.07)
        self._fade_opacity = self._fade_curve.valueForProgress(self._fade_progress)
        self.update()
        if self._fade_progress >= 1.0:
            self._fade_timer.stop()
            self._fade_opacity = 1.0
            self._old_pixmap = None

    # ── Crop mode ──────────────────────────────────────────────────────────────

    @property
    def crop_mode(self) -> bool:
        return self._crop_mode

    def enter_crop_mode(self):
        self._crop_mode = True
        self._crop_rect = None
        self._crop_drag_mode = None
        self._crop_drag_anchor = None
        self._crop_rect_at_drag = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def exit_crop_mode(self):
        self._crop_mode = False
        self._crop_rect = None
        self._crop_drag_mode = None
        self._crop_drag_anchor = None
        self._crop_rect_at_drag = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    # Hit-testing ------------------------------------------------------------
    _CROP_HANDLE_PX = 10      # hit tolerance in widget pixels

    def _crop_widget_rect(self) -> QRectF | None:
        if self._crop_rect is None:
            return None
        r = self._crop_rect
        return QRectF(
            self._offset.x() + r.x() * self._zoom,
            self._offset.y() + r.y() * self._zoom,
            r.width() * self._zoom,
            r.height() * self._zoom,
        )

    def _crop_hit(self, pos: QPointF) -> str | None:
        """Return 'tl','tr','bl','br','t','b','l','r','in', or None."""
        wr = self._crop_widget_rect()
        if wr is None or wr.width() < 2 or wr.height() < 2:
            return None
        tol = self._CROP_HANDLE_PX
        x, y = pos.x(), pos.y()
        left, right = wr.x(), wr.x() + wr.width()
        top, bottom = wr.y(), wr.y() + wr.height()
        near_l = abs(x - left) <= tol
        near_r = abs(x - right) <= tol
        near_t = abs(y - top) <= tol
        near_b = abs(y - bottom) <= tol
        in_x = left - tol <= x <= right + tol
        in_y = top - tol <= y <= bottom + tol
        if near_l and near_t and in_x and in_y: return "tl"
        if near_r and near_t and in_x and in_y: return "tr"
        if near_l and near_b and in_x and in_y: return "bl"
        if near_r and near_b and in_x and in_y: return "br"
        if near_t and in_x: return "t"
        if near_b and in_x: return "b"
        if near_l and in_y: return "l"
        if near_r and in_y: return "r"
        if left < x < right and top < y < bottom: return "in"
        return None

    @staticmethod
    def _cursor_for_handle(h: str | None) -> Qt.CursorShape:
        return {
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t":  Qt.CursorShape.SizeVerCursor,
            "b":  Qt.CursorShape.SizeVerCursor,
            "l":  Qt.CursorShape.SizeHorCursor,
            "r":  Qt.CursorShape.SizeHorCursor,
            "in": Qt.CursorShape.SizeAllCursor,
        }.get(h or "", Qt.CursorShape.CrossCursor)

    def crop_norm(self) -> QRectF | None:
        """Current crop rect normalized to [0..1] in post-rotation pixmap space."""
        if self._crop_rect is None or self._pixmap is None or self._pixmap.isNull():
            return None
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return None
        r = self._crop_rect
        return QRectF(r.x() / pw, r.y() / ph, r.width() / pw, r.height() / ph)

    def _widget_to_pixmap(self, p: QPointF) -> QPointF:
        if self._zoom <= 0:
            return QPointF()
        return QPointF(
            (p.x() - self._offset.x()) / self._zoom,
            (p.y() - self._offset.y()) / self._zoom,
        )

    def set_pixmap(self, pm: QPixmap, key: object | None = None, rotation: int = 0):
        if self._animate and self._pixmap and not self._pixmap.isNull() and key is not None:
            self._start_fade()
        self._base_pixmap = pm
        self._rotation = rotation % 360
        self._apply_rotation()
        self._current_key = key
        # Pull from cache when possible, else mark for (re)compute
        self._histogram = None
        self._histogram_clipped = False
        self._peaking_overlay = None
        if key is not None:
            cached_h = self._hist_cache.get(key)
            if cached_h is not None:
                self._hist_cache.move_to_end(key)
                self._histogram, self._histogram_clipped = cached_h
            cached_p = self._peak_cache.get(key)
            if cached_p is not None:
                self._peak_cache.move_to_end(key)
                self._peaking_overlay = cached_p
        self._fit_to_window()
        if self._show_histogram and self._histogram is None:
            self._compute_histogram()
        if self._show_peaking and self._peaking_overlay is None:
            QTimer.singleShot(0, self._recompute_peaking)
        self.update()

    def _recompute_peaking(self):
        if not self._show_peaking:
            return
        self._compute_peaking()
        self.update()

    def rotate_cw(self) -> int:
        return self.rotate_by(90)

    def rotate_by(self, delta: int) -> int:
        """Rotate the on-screen preview by `delta` degrees (any signed multiple
        of 90). Non-destructive — the change lives in self._rotation until saved."""
        if self._base_pixmap is None:
            return self._rotation
        self._rotation = (self._rotation + delta) % 360
        self._apply_rotation()
        self._fit_to_window()
        # Peaking overlay is oriented to the pre-rotation pixmap; re-compute.
        # Histogram is rotation-invariant so the cached one is still correct.
        if self._show_peaking:
            self._peaking_overlay = None
            QTimer.singleShot(0, self._recompute_peaking)
        self.update()
        return self._rotation

    def _apply_rotation(self):
        if self._base_pixmap is None:
            self._pixmap = None
            return
        if self._rotation == 0:
            self._pixmap = self._base_pixmap
        else:
            t = QTransform()
            t.rotate(self._rotation)
            self._pixmap = self._base_pixmap.transformed(t, Qt.TransformationMode.SmoothTransformation)

    def zoom(self, factor: float):
        center = QPointF(self.width() / 2, self.height() / 2)
        self._apply_zoom(factor, center)

    def zoom_reset(self):
        self._fit_to_window()
        self.update()

    def zoom_actual(self):
        """1:1 pixel zoom, anchored at widget center."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        center = QPointF(self.width() / 2, self.height() / 2)
        factor = 1.0 / self._zoom
        self._apply_zoom(factor, center)

    def set_exif(self, lines: list[str]):
        self._exif_lines = lines
        if self._show_exif:
            self.update()

    def toggle_exif(self):
        self._show_exif = not self._show_exif
        self.update()

    def toggle_histogram(self):
        self._show_histogram = not self._show_histogram
        if self._show_histogram and self._histogram is None:
            self._compute_histogram()
        if self._show_histogram:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
        self.update()

    def toggle_peaking(self):
        self._show_peaking = not self._show_peaking
        if self._show_peaking and self._peaking_overlay is None:
            self._compute_peaking()
        self.update()

    # ── Histogram + peaking computation ────────────────────────────────────────

    def _qimage_to_np(self, img: QImage) -> "np.ndarray | None":
        """Return H×W×4 BGRA uint8 view. Format_RGB32 on Qt = BGRA little-endian."""
        if not _HAVE_NUMPY or img.isNull():
            return None
        src = img.convertToFormat(QImage.Format.Format_RGB32)
        bits = src.bits()
        bits.setsize(src.sizeInBytes())
        arr = np.frombuffer(bytes(bits), dtype=np.uint8)
        return arr.reshape(src.height(), src.width(), 4)

    def _compute_histogram(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        src = self._pixmap.toImage()
        if src.width() > 400:
            src = src.scaled(400, 300,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.FastTransformation)
        style = settings_mod.get("histogram_style") or "additive"
        if _HAVE_NUMPY:
            arr = self._qimage_to_np(src)
            if arr is None:
                return
            b = np.bincount(arr[:, :, 0].ravel(), minlength=256)[:256].tolist()
            g = np.bincount(arr[:, :, 1].ravel(), minlength=256)[:256].tolist()
            r = np.bincount(arr[:, :, 2].ravel(), minlength=256)[:256].tolist()
            if style == "luminance":
                lum = ((arr[:, :, 0].astype(np.uint16)
                        + arr[:, :, 1].astype(np.uint16) * 2
                        + arr[:, :, 2].astype(np.uint16)) >> 2).astype(np.uint8)
                l = np.bincount(lum.ravel(), minlength=256)[:256].tolist()
                self._histogram = [l, [0] * 256, [0] * 256]
            else:
                self._histogram = [r, g, b]
            total = src.width() * src.height()
            clipped = sum(r[250:]) + sum(g[250:]) + sum(b[250:])
            self._histogram_clipped = total > 0 and (clipped / (total * 3)) > 0.01
        else:
            # Fallback — pure Python
            src2 = src.convertToFormat(QImage.Format.Format_RGB32)
            r = [0] * 256; g = [0] * 256; b = [0] * 256
            bits = src2.bits()
            bits.setsize(src2.sizeInBytes())
            buf = bytes(bits)
            for i in range(0, len(buf), 4):
                b[buf[i]] += 1
                g[buf[i + 1]] += 1
                r[buf[i + 2]] += 1
            self._histogram = [r, g, b]
            total = src2.width() * src2.height()
            clipped = sum(r[250:]) + sum(g[250:]) + sum(b[250:])
            self._histogram_clipped = total > 0 and (clipped / (total * 3)) > 0.01
        if self._current_key is not None:
            self._hist_cache[self._current_key] = (self._histogram, self._histogram_clipped)
            self._hist_cache.move_to_end(self._current_key)
            while len(self._hist_cache) > self._CACHE_MAX:
                self._hist_cache.popitem(last=False)

    def _compute_peaking(self):
        """Edge detect via neighbor diff. Red overlay where edges strong."""
        if self._pixmap is None or self._pixmap.isNull() or not _HAVE_NUMPY:
            return
        src = self._pixmap.toImage()
        max_dim = 600
        if max(src.width(), src.height()) > max_dim:
            src = src.scaled(max_dim, max_dim,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.FastTransformation)
        arr = self._qimage_to_np(src)
        if arr is None:
            return
        h, w, _ = arr.shape
        # Luminance (BGRA layout): 0.25*B + 0.5*G + 0.25*R ≈ (B + 2G + R) >> 2
        lum = ((arr[:, :, 0].astype(np.uint16)
                + arr[:, :, 1].astype(np.uint16) * 2
                + arr[:, :, 2].astype(np.uint16)) >> 2).astype(np.int16)
        dx = np.zeros_like(lum); dy = np.zeros_like(lum)
        dx[:, :-1] = np.abs(lum[:, 1:] - lum[:, :-1])
        dy[:-1, :] = np.abs(lum[1:, :] - lum[:-1, :])
        thresh = max(5, min(80, int(settings_mod.get("peaking_threshold") or 28)))
        edge = (dx + dy) > thresh
        overlay = np.zeros((h, w, 4), dtype=np.uint8)
        overlay[edge, 2] = 255     # R
        overlay[edge, 3] = 200     # A
        img = QImage(overlay.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32).copy()
        self._peaking_overlay = img
        if self._current_key is not None:
            self._peak_cache[self._current_key] = img
            self._peak_cache.move_to_end(self._current_key)
            while len(self._peak_cache) > self._CACHE_MAX:
                self._peak_cache.popitem(last=False)

    # ── Internals ──────────────────────────────────────────────────────────────

    def _overlay_xy(self, pos: str, box_w: int, box_h: int) -> tuple[int, int]:
        """Map corner code ('tl'/'tr'/'bl'/'br') to top-left (x, y) with 16px margin."""
        m = 16
        if pos == "tl":
            return m, m
        if pos == "tr":
            return self.width() - box_w - m, m
        if pos == "bl":
            return m, self.height() - box_h - m
        return self.width() - box_w - m, self.height() - box_h - m

    def _fit_to_window(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0 or w <= 0 or h <= 0:
            return
        self._fit_zoom = min(w / pw, h / ph)
        self._zoom = self._fit_zoom
        dw = pw * self._zoom
        dh = ph * self._zoom
        self._offset = QPointF((w - dw) / 2, (h - dh) / 2)

    def _apply_zoom(self, factor: float, anchor: QPointF):
        if self._pixmap is None or self._pixmap.isNull():
            return
        target_zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
        if target_zoom == self._zoom:
            return
        self._zoom_anchor = anchor
        self._zoom_start = self._zoom
        self._zoom_target = target_zoom
        self._zoom_progress = 0.0
        if not hasattr(self, "_zoom_timer") or self._zoom_timer is None:
            self._zoom_timer = QTimer(self)
            self._zoom_timer.setInterval(12)
            self._zoom_timer.timeout.connect(self._zoom_step)
        self._zoom_timer.start()

    def _zoom_step(self):
        self._zoom_progress = min(1.0, self._zoom_progress + 0.18)
        t = 1.0 - (1.0 - self._zoom_progress) ** 3
        anchor = self._zoom_anchor
        img_x = (anchor.x() - self._offset.x()) / self._zoom
        img_y = (anchor.y() - self._offset.y()) / self._zoom
        self._zoom = self._zoom_start + (self._zoom_target - self._zoom_start) * t
        self._offset = QPointF(
            anchor.x() - img_x * self._zoom,
            anchor.y() - img_y * self._zoom,
        )
        self.update()
        self._emit_view_changed()
        if self._zoom_progress >= 1.0:
            self._zoom_timer.stop()

    # ── Events ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), theme_mod.c("canvas_bg"))
        if self._pixmap is None or self._pixmap.isNull():
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Cross-fade: draw old pixmap fading out, new fading in
        if self._old_pixmap is not None and self._fade_opacity < 1.0:
            old_pw = self._old_pixmap.width() * self._old_zoom
            old_ph = self._old_pixmap.height() * self._old_zoom
            old_dest = QRectF(self._old_offset.x(), self._old_offset.y(), old_pw, old_ph)
            p.setOpacity(1.0 - self._fade_opacity)
            p.drawPixmap(old_dest, self._old_pixmap, QRectF(self._old_pixmap.rect()))
            p.setOpacity(self._fade_opacity)

        pw = self._pixmap.width() * self._zoom
        ph = self._pixmap.height() * self._zoom
        dest = QRectF(self._offset.x(), self._offset.y(), pw, ph)
        p.drawPixmap(dest, self._pixmap, QRectF(self._pixmap.rect()))
        p.setOpacity(1.0)

        # Focus peaking overlay — drawn over image at same dest rect
        if self._show_peaking and self._peaking_overlay is not None:
            p.drawImage(dest, self._peaking_overlay, QRectF(self._peaking_overlay.rect()))

        # Histogram overlay — bottom-right
        if self._show_histogram and self._histogram is not None:
            self._paint_histogram(p)

        # EXIF overlay — top-right
        if self._show_exif and self._exif_lines:
            self._paint_exif(p)

        # Rotation indicator
        if self._rotation != 0:
            p.setPen(QColor(200, 200, 200, 180))
            f = p.font()
            f.setPointSize(10)
            p.setFont(f)
            p.drawText(QRect(12, self.height() - 30, 120, 20),
                       Qt.AlignmentFlag.AlignLeft,
                       f"Rotated: {self._rotation}°")

        # Crop overlay — dim outside + border + rule-of-thirds
        if self._crop_mode:
            self._paint_crop_overlay(p)

        # RAW decode spinner (bottom-right)
        if self._raw_loading:
            bx = self.width() - 160
            by = self.height() - 40
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 180))
            p.drawRoundedRect(bx, by, 150, 28, 6, 6)
            # Spinner
            sx = bx + 10
            sy = by + 6
            pen = QPen(QColor(80, 80, 80), 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(sx, sy, 16, 16)
            angle = int((self._clip_pulse_phase / (2 * math.pi)) * 360)
            p.setPen(QPen(QColor(42, 130, 218), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(sx, sy, 16, 16, -angle * 16, 90 * 16)
            # Text
            p.setPen(QColor(230, 230, 230))
            f = p.font()
            f.setPointSize(9)
            f.setBold(False)
            p.setFont(f)
            p.drawText(QRect(bx + 34, by, 116, 28),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       "Decoding RAW…")

        p.end()

    def _paint_histogram(self, p: QPainter):
        w = 240
        h = 120
        x, y = self._overlay_xy(self._hist_pos, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(x, y, w, h, 6, 6)

        # Clipping pulse — red glow at right edge
        if self._histogram_clipped:
            pulse = (math.sin(self._clip_pulse_phase) + 1) * 0.5  # 0..1
            alpha = int(120 + pulse * 135)
            p.setBrush(QColor(255, 40, 40, alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(x + w - 10, y + 4, 6, h - 8, 3, 3)
            # Warning text
            p.setPen(QColor(255, 180, 180))
            f = p.font()
            f.setPointSize(8)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRect(x, y - 18, w - 14, 16),
                       Qt.AlignmentFlag.AlignRight,
                       "⚠ CLIPPED")

        r, g, b = self._histogram
        peak = max(max(r), max(g), max(b), 1)
        inner_w = w - 12
        inner_h = h - 12
        ox = x + 6
        oy = y + 6
        bar_w = inner_w / 256.0

        # Draw channels additively
        colors = [
            (QColor(255, 80, 80, 180), r),
            (QColor(80, 255, 80, 180), g),
            (QColor(80, 140, 255, 180), b),
        ]
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for col, chan in colors:
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            for i, v in enumerate(chan):
                bh = int((v / peak) * inner_h)
                if bh > 0:
                    p.drawRect(QRectF(ox + i * bar_w, oy + inner_h - bh,
                                      max(1.0, bar_w), bh))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _paint_crop_overlay(self, p: QPainter):
        W, H = self.width(), self.height()
        r = self._crop_rect
        has_rect = r is not None and r.width() > 1 and r.height() > 1
        if has_rect:
            wx = self._offset.x() + r.x() * self._zoom
            wy = self._offset.y() + r.y() * self._zoom
            ww = r.width() * self._zoom
            wh = r.height() * self._zoom
            wr = QRectF(wx, wy, ww, wh)
            dim = QColor(0, 0, 0, 140)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dim)
            p.drawRect(QRectF(0, 0, W, wr.y()))
            p.drawRect(QRectF(0, wr.bottom(), W, H - wr.bottom()))
            p.drawRect(QRectF(0, wr.y(), wr.x(), wr.height()))
            p.drawRect(QRectF(wr.right(), wr.y(), W - wr.right(), wr.height()))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255), 1.5))
            p.drawRect(wr)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for i in (1, 2):
                gx = wr.x() + wr.width() * i / 3
                p.drawLine(QPointF(gx, wr.y()), QPointF(gx, wr.bottom()))
                gy = wr.y() + wr.height() * i / 3
                p.drawLine(QPointF(wr.x(), gy), QPointF(wr.right(), gy))
            # Handles — 4 corners + 4 edge midpoints
            hs = 7
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(255, 255, 255))
            cx = wr.x() + wr.width() / 2
            cy = wr.y() + wr.height() / 2
            for hx, hy in (
                (wr.x(), wr.y()), (wr.right(), wr.y()),
                (wr.x(), wr.bottom()), (wr.right(), wr.bottom()),
                (cx, wr.y()), (cx, wr.bottom()),
                (wr.x(), cy), (wr.right(), cy),
            ):
                p.drawRect(QRectF(hx - hs / 2, hy - hs / 2, hs, hs))
        # Bottom hint banner
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(QRectF(W / 2 - 260, H - 54, 520, 32), 8, 8)
        p.setPen(QColor(230, 230, 230))
        f = p.font(); f.setPointSize(10); f.setBold(False); p.setFont(f)
        p.drawText(QRectF(W / 2 - 300, H - 54, 600, 32),
                   Qt.AlignmentFlag.AlignCenter,
                   "CROP — drag to draw · drag inside to move · handles to resize · Enter apply · Esc cancel")

    def _paint_exif(self, p: QPainter):
        f = p.font()
        f.setPointSize(10)
        f.setBold(False)
        p.setFont(f)
        metrics = p.fontMetrics()
        padding = 10
        line_h = metrics.height() + 2
        max_w = max((metrics.horizontalAdvance(ln) for ln in self._exif_lines), default=0)
        box_w = max_w + padding * 2
        box_h = line_h * len(self._exif_lines) + padding * 2
        x, y = self._overlay_xy(self._exif_pos, box_w, box_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(x, y, box_w, box_h, 6, 6)
        p.setPen(QColor(235, 235, 235))
        ty = y + padding + metrics.ascent()
        for ln in self._exif_lines:
            p.drawText(x + padding, ty, ln)
            ty += line_h

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = self.WHEEL_FACTOR if delta > 0 else (1 / self.WHEEL_FACTOR)
        self._apply_zoom(factor, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap:
            if self._crop_mode:
                self._begin_crop_drag(event.position())
                return
            self._panning = True
            self._pan_start = event.pos()
            self._pan_offset_start = QPointF(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.zoom_reset()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._crop_mode:
            if self._crop_drag_mode is not None:
                self._update_crop_drag(event.position())
                return
            # Hover — update cursor based on handle under mouse.
            hit = self._crop_hit(event.position())
            self.setCursor(self._cursor_for_handle(hit))
            return
        if self._panning:
            d = event.pos() - self._pan_start
            self._offset = QPointF(
                self._pan_offset_start.x() + d.x(),
                self._pan_offset_start.y() + d.y(),
            )
            self.update()
            self._emit_view_changed()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._crop_mode:
                self._crop_drag_mode = None
                self._crop_drag_anchor = None
                self._crop_rect_at_drag = None
                # Normalize rect (non-negative w/h)
                if self._crop_rect is not None:
                    r = self._crop_rect
                    self._crop_rect = QRectF(r).normalized()
                self.update()
                return
            self._panning = False
            self.setCursor(Qt.CursorShape.CrossCursor)

    # ── Crop drag helpers ──────────────────────────────────────────────────────

    def _begin_crop_drag(self, widget_pos: QPointF):
        hit = self._crop_hit(widget_pos)
        if hit is None:
            # Start a new rect from scratch — anchor is press point in pixmap coords.
            anchor = self._widget_to_pixmap(widget_pos)
            self._crop_drag_mode = "new"
            self._crop_drag_anchor = anchor
            self._crop_rect = QRectF(anchor, anchor)
            self._crop_rect_at_drag = None
        elif hit == "in":
            self._crop_drag_mode = "move"
            self._crop_drag_anchor = self._widget_to_pixmap(widget_pos)
            self._crop_rect_at_drag = QRectF(self._crop_rect) if self._crop_rect else None
        else:
            self._crop_drag_mode = f"resize_{hit}"
            self._crop_drag_anchor = self._widget_to_pixmap(widget_pos)
            self._crop_rect_at_drag = QRectF(self._crop_rect) if self._crop_rect else None
        self.update()

    def _update_crop_drag(self, widget_pos: QPointF):
        if self._pixmap is None or self._crop_drag_mode is None:
            return
        pm_w, pm_h = self._pixmap.width(), self._pixmap.height()
        cur = self._widget_to_pixmap(widget_pos)

        if self._crop_drag_mode == "new":
            ax, ay = self._crop_drag_anchor.x(), self._crop_drag_anchor.y()
            x, y = cur.x(), cur.y()
            rect = QRectF(min(ax, x), min(ay, y), abs(x - ax), abs(y - ay))
        elif self._crop_drag_mode == "move" and self._crop_rect_at_drag is not None:
            dx = cur.x() - self._crop_drag_anchor.x()
            dy = cur.y() - self._crop_drag_anchor.y()
            r = self._crop_rect_at_drag
            nx = max(0.0, min(pm_w - r.width(), r.x() + dx))
            ny = max(0.0, min(pm_h - r.height(), r.y() + dy))
            rect = QRectF(nx, ny, r.width(), r.height())
        elif self._crop_drag_mode.startswith("resize_") and self._crop_rect_at_drag is not None:
            side = self._crop_drag_mode.split("_", 1)[1]
            r = self._crop_rect_at_drag
            left, top = r.x(), r.y()
            right, bottom = r.x() + r.width(), r.y() + r.height()
            cx = max(0.0, min(pm_w, cur.x()))
            cy = max(0.0, min(pm_h, cur.y()))
            if "l" in side: left = cx
            if "r" in side: right = cx
            if "t" in side: top = cy
            if "b" in side: bottom = cy
            rect = QRectF(min(left, right), min(top, bottom),
                          abs(right - left), abs(bottom - top))
        else:
            return

        # Clamp to pixmap bounds.
        x = max(0.0, min(pm_w, rect.x()))
        y = max(0.0, min(pm_h, rect.y()))
        w = min(rect.width(), pm_w - x)
        h = min(rect.height(), pm_h - y)
        self._crop_rect = QRectF(x, y, max(0.0, w), max(0.0, h))
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._pixmap
                and self._double_click_enabled):
            factor = 1.0 / self._zoom
            self._apply_zoom(factor, event.position())

    def set_double_click_enabled(self, enabled: bool):
        self._double_click_enabled = enabled

    def set_raw_loading(self, loading: bool):
        self._raw_loading = loading
        if loading:
            self._pulse_timer.start()
        elif not self._show_histogram:
            self._pulse_timer.stop()
        self.update()

    def _on_pulse_tick(self):
        self._clip_pulse_phase = (self._clip_pulse_phase + 0.35) % (2 * math.pi)
        if (self._show_histogram and self._histogram_clipped) or self._raw_loading:
            self.update()

    def resizeEvent(self, event):
        # Re-fit only if currently at fit zoom
        if abs(self._zoom - self._fit_zoom) < 1e-4:
            self._fit_to_window()
        self.update()


# ── Toast notification ─────────────────────────────────────────────────────────

class ToastWidget(QWidget):
    action_clicked = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(
            "background: rgba(30, 30, 30, 220);"
            "border-radius: 8px;"
        )
        self.hide()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        self._label = QLabel()
        self._label.setStyleSheet("color: #ffffff; font-size: 15px; font-weight: bold; background: transparent;")
        lay.addWidget(self._label)

        self._action_btn = QLabel()
        self._action_btn.setStyleSheet(
            "color: #6aa0ff; font-size: 13px; font-weight: 700;"
            " background: rgba(255,255,255,0.08); border-radius: 4px;"
            " padding: 4px 12px;"
        )
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.hide()
        lay.addWidget(self._action_btn)

        self._action_cb = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

        # windowOpacity only affects top-level windows; this is a child widget,
        # so fade through a graphics effect instead (and it lets us slide too).
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fading_out = False

    def show_message(self, text: str, ms: int = 1800, action: str = "", action_cb=None):
        self._hide_timer.stop()
        self._fade.stop()
        self._slide.stop()
        self._fading_out = False
        self._label.setText(text)
        self._action_cb = action_cb
        if action and action_cb:
            self._action_btn.setText(action)
            self._action_btn.show()
        else:
            self._action_btn.hide()
        self.adjustSize()
        self._reposition()
        rest = self.pos()
        self.show()
        self.raise_()
        # Fade + a gentle 14px rise into place.
        self._opacity.setOpacity(0.0)
        self.move(rest.x(), rest.y() + 14)
        self._fade.setDuration(200)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._slide.setDuration(240)
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(rest)
        self._slide.start()
        self._hide_timer.start(ms)

    def _on_fade_finished(self):
        if self._fading_out:
            self._fading_out = False
            self.hide()

    def mousePressEvent(self, event):
        if self._action_cb and self._action_btn.isVisible():
            btn_geo = self._action_btn.geometry()
            if btn_geo.contains(event.pos()):
                cb = self._action_cb
                self._action_cb = None
                self.hide()
                cb()
                return
        super().mousePressEvent(event)

    def _reposition(self):
        parent = self.parentWidget()
        if parent:
            pw, ph = parent.width(), parent.height()
            self.adjustSize()
            x = (pw - self.width()) // 2
            y = ph - self.height() - 120
            self.move(x, y)

    def _fade_out(self):
        self._fading_out = True
        self._fade.stop()
        self._fade.setDuration(420)
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()


# ── Shortcut panel (right-side overlay) ───────────────────────────────────────

class ShortcutPanel(QWidget):
    """Translucent right-side panel showing keyboard shortcuts. Toggle with ?."""

    WIDTH = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self._sections: list[tuple[str, list[tuple[str, str]]]] = []
        self._visible = False
        self.hide()

    def set_shortcuts(self, sections: list[tuple[str, list[tuple[str, str]]]]):
        self._sections = sections
        self.update()

    def toggle(self):
        self._visible = not self._visible
        self.setVisible(self._visible)
        if self._visible:
            self.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 12, 12, 210))
        p.drawRoundedRect(0, 0, W, H, 14, 14)
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W - 2, H - 2, 14, 14)

        y = 18
        for section_title, pairs in self._sections:
            p.setPen(QColor(120, 165, 255))
            f = p.font()
            f.setPointSize(9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRect(16, y, W - 32, 18), Qt.AlignmentFlag.AlignLeft, section_title)
            y += 22

            f.setBold(False)
            f.setPointSize(9)
            p.setFont(f)
            for key, desc in pairs:
                p.setPen(QColor(220, 220, 220))
                p.setBrush(QColor(255, 255, 255, 18))
                fm = p.fontMetrics()
                kw = fm.horizontalAdvance(key) + 14
                p.drawRoundedRect(20, y, kw, 20, 4, 4)
                p.setPen(QColor(255, 255, 255))
                p.drawText(QRect(20, y, kw, 20), Qt.AlignmentFlag.AlignCenter, key)
                p.setPen(QColor(190, 190, 190))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawText(QRect(20 + kw + 10, y, W - kw - 50, 20),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, desc)
                y += 24
            y += 8

        p.end()


# ── Filmstrip bar ──────────────────────────────────────────────────────────────

class _FilmThumbSignals(QObject):
    ready = pyqtSignal(int, QPixmap)


class _FilmThumbTask(QRunnable):
    """Generates a filmstrip thumbnail on cache miss.

    For images: reads via QImageReader with scaled size so full-res DSLR files
    aren't decoded into memory.

    For videos: shells out to ffmpeg via `video_thumb.extract_thumb` and writes
    a sibling `<key>.v.jpg` cache file so future filmstrip / mosaic / gallery
    visits all hit the same on-disk artifact.
    """
    def __init__(self, idx: int, path: str, cache_file: Path, signals: _FilmThumbSignals):
        super().__init__()
        self._idx = idx
        self._path = path
        self._cache_file = cache_file   # image cache path (.jpg)
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        from .media import is_video
        if is_video(self._path):
            self._run_video()
            return
        self._run_image()

    def _run_image(self):
        img: QImage | None = None
        if self._cache_file.exists():
            img = QImage(str(self._cache_file))
            if img.isNull():
                img = None
        if img is None:
            reader = QImageReader(self._path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                longest = max(size.width(), size.height())
                if longest > THUMB_MAX_DIM:
                    scale = THUMB_MAX_DIM / longest
                    reader.setScaledSize(QSize(
                        max(1, int(size.width() * scale)),
                        max(1, int(size.height() * scale)),
                    ))
            img = reader.read()
            if img is None or img.isNull():
                return
            try:
                self._cache_file.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(self._cache_file), "JPEG", 82)
            except Exception:
                pass
        self._signals.ready.emit(self._idx, QPixmap.fromImage(img))

    def _run_video(self):
        from .video_thumb import extract_thumb, cache_path_for_video
        # cache_file path looks like <source>/.picker_cache/<key>.jpg — use the
        # source folder to compute the dedicated <key>.v.jpg video-thumb path.
        try:
            source_folder = self._cache_file.parent.parent
        except Exception:
            source_folder = self._cache_file.parent
        v_cache = cache_path_for_video(str(source_folder), self._path)

        img: QImage | None = None
        if v_cache.exists():
            img = QImage(str(v_cache))
            if img.isNull():
                img = None
        if img is None:
            img = extract_thumb(self._path, v_cache)
        if img is None or img.isNull():
            # ffmpeg missing or decode failed → emit a dark placeholder so the
            # filmstrip still has a tile to draw the play badge on.
            img = QImage(THUMB_MAX_DIM, int(THUMB_MAX_DIM * 9 / 16),
                         QImage.Format.Format_RGB32)
            img.fill(QColor(28, 28, 28))
        self._signals.ready.emit(self._idx, QPixmap.fromImage(img))


class FilmstripBar(QWidget):
    jumped = pyqtSignal(int)

    HEIGHT = 92
    THUMB_H = 78
    SPACING = 4
    CURRENT_BORDER = 3
    THUMB_CACHE_MAX = 200
    WHEEL_PAN_STEP = 160

    DEST_COLORS = [
        QColor(60, 200, 80),
        QColor(60, 150, 230),
        QColor(230, 170, 40),
    ]

    def __init__(self, manager: ImageManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._current = 0
        self._thumbs: "OrderedDict[int, QPixmap]" = OrderedDict()
        self._rects: list[tuple[int, QRect]] = []
        self._pan = 0                    # extra x offset from manual drag
        self._drag_active = False
        self._drag_start_x = 0
        self._drag_start_pan = 0
        self._drag_moved = False
        self._pending: set[int] = set()
        self._signals = _FilmThumbSignals()
        self._signals.ready.connect(self._on_thumb_ready)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        # Separate single-slot pool for ffmpeg jobs — phone HEVC seeks can take
        # 10–30s; running 2 in parallel on the same disk just makes both slow.
        self._video_pool = QThreadPool()
        self._video_pool.setMaxThreadCount(1)
        self.setFixedHeight(self.HEIGHT)
        self.setAutoFillBackground(True)
        self._apply_bg()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Smooth recenter slide when navigating (eases the pan offset to 0).
        self._animate_strip = bool(settings_mod.get("slideshow_animation"))
        self._pan_anim = QVariantAnimation(self)
        self._pan_anim.setDuration(240)
        self._pan_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pan_anim.valueChanged.connect(self._on_pan_anim)

    def _on_pan_anim(self, value):
        self._pan = int(value)
        self.update()

    def _apply_bg(self):
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("filmstrip_bg"))
        self.setPalette(pal)

    def refresh_theme(self):
        self._apply_bg()
        self.update()

    def set_current(self, idx: int):
        old = self._current
        # Slide the strip: start it where the newly-current thumb sat in the
        # previous layout, then ease that offset back to centered (pan 0).
        start_pan = 0
        if self._animate_strip and old != idx and self._rects:
            center = self.width() // 2
            for ridx, rect in self._rects:
                if ridx == idx:
                    start_pan = rect.center().x() - center
                    break
        self._current = idx
        self._load_visible()
        self._pan_anim.stop()
        if start_pan != 0:
            self._pan = start_pan
            self._pan_anim.setStartValue(start_pan)
            self._pan_anim.setEndValue(0)
            self._pan_anim.start()
        else:
            self._pan = 0
            self.update()

    def _load_visible(self):
        half = 30
        lo = max(0, self._current - half)
        hi = min(len(self._manager.images), self._current + half + 1)
        for i in range(lo, hi):
            self._ensure_thumb(i)

    def _ensure_thumb(self, i: int):
        if i in self._thumbs:
            self._thumbs.move_to_end(i)
            return
        if i in self._pending:
            return
        rec = self._manager.images[i]
        cf = gallery_cache_file(self._manager.source_folder, rec.path)

        # For videos, the on-disk thumb lives at <key>.v.jpg (separate from
        # the image cache namespace). Probe that first so cache hits don't
        # round-trip through ffmpeg.
        from .media import is_video
        if is_video(rec.path):
            from .video_thumb import cache_path_for_video
            from . import log
            v_cache = cache_path_for_video(self._manager.source_folder, rec.path)
            if v_cache.exists():
                img = QImage(str(v_cache))
                if not img.isNull():
                    self._thumbs[i] = QPixmap.fromImage(img)
                    while len(self._thumbs) > self.THUMB_CACHE_MAX:
                        self._thumbs.popitem(last=False)
                    return
                log.warn("filmstrip: cached video thumb is unreadable",
                         cache=str(v_cache))
            # Cache miss for video — worker will run ffmpeg. Use the
            # single-slot video pool so ffmpeg invocations queue instead of
            # competing for the same disk.
            log.info("filmstrip: dispatching ffmpeg for video",
                     idx=i, path=rec.path, target=str(v_cache))
            self._pending.add(i)
            self._video_pool.start(_FilmThumbTask(i, rec.path, cf, self._signals))
            return

        # Image: cache hit — read synchronously (fast).
        if cf.exists():
            img = QImage(str(cf))
            if not img.isNull():
                self._thumbs[i] = QPixmap.fromImage(img)
                while len(self._thumbs) > self.THUMB_CACHE_MAX:
                    self._thumbs.popitem(last=False)
                return
        # Cache miss — dispatch a background worker so the tile loads even when
        # the gallery hasn't scrolled this image into view yet.
        self._pending.add(i)
        self._pool.start(_FilmThumbTask(i, rec.path, cf, self._signals))

    @pyqtSlot(int, QPixmap)
    def _on_thumb_ready(self, idx: int, pm: QPixmap):
        self._pending.discard(idx)
        if pm.isNull():
            return
        self._thumbs[idx] = pm
        while len(self._thumbs) > self.THUMB_CACHE_MAX:
            self._thumbs.popitem(last=False)
        if self.isVisible():
            self.update()

    def _thumb_width(self, idx: int) -> int:
        pm = self._thumbs.get(idx)
        if pm and pm.height() > 0:
            return max(40, int(self.THUMB_H * pm.width() / pm.height()))
        return int(self.THUMB_H * 1.5)   # fallback 3:2

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), theme_mod.c("filmstrip_bg"))

        self._rects = []
        bar_w = self.width()
        bar_h = self.height()
        y = (bar_h - self.THUMB_H) // 2
        cx = bar_w // 2 + self._pan

        # Current tile — centered (+ pan)
        cw = self._thumb_width(self._current)
        cur_rect = QRect(cx - cw // 2, y, cw, self.THUMB_H)
        self._draw_tile(p, self._current, cur_rect, is_current=True)
        self._rects.append((self._current, cur_rect))

        # Right side
        x = cur_rect.right() + self.SPACING
        i = self._current + 1
        while i < len(self._manager.images) and x < bar_w:
            tw = self._thumb_width(i)
            r = QRect(x, y, tw, self.THUMB_H)
            self._draw_tile(p, i, r, is_current=False)
            self._rects.append((i, r))
            x += tw + self.SPACING
            i += 1

        # Left side
        x = cur_rect.left() - self.SPACING
        i = self._current - 1
        while i >= 0 and x > 0:
            tw = self._thumb_width(i)
            r = QRect(x - tw, y, tw, self.THUMB_H)
            self._draw_tile(p, i, r, is_current=False)
            self._rects.append((i, r))
            x -= tw + self.SPACING
            i -= 1

        p.end()

    def _draw_tile(self, p: QPainter, idx: int, rect: QRect, is_current: bool):
        pm = self._thumbs.get(idx)
        if pm and not pm.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(rect, pm)
        else:
            p.fillRect(rect, QColor(30, 30, 30))

        rec = self._manager.images[idx]

        # Video badge — small play triangle in the bottom-left corner.
        from .media import is_video
        if is_video(rec.path):
            self._draw_filmstrip_play_badge(p, rect)

        # Dest badge
        color = self._status_color(rec.status)
        if color:
            pen = QPen(color, 2)
            p.setPen(pen)
            inset = 1
            p.drawRect(rect.adjusted(inset, inset, -inset, -inset))

        # Highlight current
        if is_current:
            pen = QPen(QColor(255, 255, 255), self.CURRENT_BORDER)
            p.setPen(pen)
            inset = self.CURRENT_BORDER // 2
            p.drawRect(rect.adjusted(inset, inset, -inset, -inset))

    def _draw_filmstrip_play_badge(self, p: QPainter, rect: QRect) -> None:
        size = 18
        margin = 4
        cx = rect.left() + margin + size // 2
        cy = rect.bottom() - margin - size // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawEllipse(QRectF(cx - size / 2, cy - size / 2, size, size))
        p.setBrush(QColor(255, 255, 255, 230))
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF as _QPF
        tri = QPolygonF([
            _QPF(cx - size * 0.15, cy - size * 0.22),
            _QPF(cx - size * 0.15, cy + size * 0.22),
            _QPF(cx + size * 0.27, cy),
        ])
        p.drawPolygon(tri)

    def _status_color(self, status: str) -> QColor | None:
        if status.startswith("dest_"):
            try:
                i = int(status.split("_")[1])
                return self.DEST_COLORS[i % len(self.DEST_COLORS)]
            except ValueError:
                return None
        return None

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            pos = event.pos()
            for idx, r in self._rects:
                if r.contains(pos):
                    self._show_context_menu(idx, event.globalPosition().toPoint())
                    return
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._pan_anim.stop()   # user is taking over — cancel any recenter slide
        self._drag_active = True
        self._drag_start_x = event.pos().x()
        self._drag_start_pan = self._pan
        self._drag_moved = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _show_context_menu(self, idx: int, global_pos):
        rec = self._manager.images[idx]
        path = rec.path
        menu = QMenu(self)
        act_jump = menu.addAction("Jump to this image")
        act_jump.triggered.connect(lambda: self.jumped.emit(idx))
        menu.addSeparator()
        act_sys = menu.addAction("Open with System Default")
        act_sys.triggered.connect(lambda: external.open_default(path))
        act_copy = menu.addAction("Copy Path")
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(path))
        menu.addSeparator()
        act_reveal = menu.addAction("Reveal in Explorer")
        act_reveal.triggered.connect(
            lambda: __import__("subprocess").Popen(["explorer", "/select,", os.path.normpath(path)])
        )
        menu.exec(global_pos)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._drag_active:
            # Tooltip on hover
            pos = event.pos()
            for idx, r in self._rects:
                if r.contains(pos):
                    rec = self._manager.images[idx]
                    tip = os.path.basename(rec.path)
                    pm = self._thumbs.get(idx)
                    if pm and pm.height() > 0:
                        tip += f"\n{pm.width()}×{pm.height()}"
                    self.setToolTip(tip)
                    return
            self.setToolTip("")
            return
        dx = event.pos().x() - self._drag_start_x
        if abs(dx) > 4:
            self._drag_moved = True
        self._pan = self._drag_start_pan + dx
        # Scrub mode: while dragging, jump to tile under cursor
        pos = event.pos()
        for idx, r in self._rects:
            if r.contains(pos) and idx != self._current:
                self._current = idx
                self.jumped.emit(idx)
                break
        self._load_pan_range()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_drag = self._drag_moved
        self._drag_active = False
        self._drag_moved = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if was_drag:
            return
        # Treat as click → jump
        pos = event.pos()
        for idx, r in self._rects:
            if r.contains(pos):
                if idx != self._current:
                    self.jumped.emit(idx)
                return

    def _load_pan_range(self):
        """Load thumbs for indices covered by current pan view."""
        bar_w = self.width()
        pan_images = max(8, bar_w // 90)
        half = 14 + abs(self._pan) // 80 + pan_images
        lo = max(0, self._current - half)
        hi = min(len(self._manager.images), self._current + half + 1)
        for i in range(lo, hi):
            self._ensure_thumb(i)

    def wheelEvent(self, event: QWheelEvent):
        # Pan the filmstrip horizontally. Wheel delta > 0 = scroll up = pan right
        # (reveals earlier images). Standard horizontal-list convention.
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        self._pan_anim.stop()   # manual pan overrides any in-flight recenter
        step = self.WHEEL_PAN_STEP if delta > 0 else -self.WHEEL_PAN_STEP
        self._pan += step
        self._load_pan_range()
        self.update()
        event.accept()

    def refresh(self):
        self._load_visible()
        self.update()


# ── Slideshow window ───────────────────────────────────────────────────────────

class SlideshowView(QWidget):
    closed = pyqtSignal(int)
    status_changed = pyqtSignal()
    fullscreen_requested = pyqtSignal()
    title_changed = pyqtSignal()

    PIXMAP_CACHE_MAX = 12
    PIXMAP_CACHE_MAX_BYTES = 512 * 1024 * 1024  # 512 MB

    def __init__(self, manager: ImageManager, start_idx: int, parent=None,
                 filmstrip_hidden: bool = False):
        super().__init__(parent)
        self._manager = manager
        self._idx = start_idx
        self._current_dest = 0
        self._pixmap_cache: "OrderedDict[int, QPixmap]" = OrderedDict()
        self._pixmap_cache_bytes = 0
        self._inflight: set[int] = set()
        self._load_t0: dict[int, float] = {}   # idx → perf_counter at dispatch
        self._info_panel_visible = False
        self._compare_view = None
        self._filmstrip_deferred = filmstrip_hidden
        self._scan_total = len(manager.images)

        self._exif_pos = settings_mod.get("exif_position") or "tr"
        self._hist_pos = settings_mod.get("histogram_position") or "br"
        self._preload_count = max(1, min(5, int(settings_mod.get("preload_count") or 2)))
        self._auto_advance = bool(settings_mod.get("auto_advance_on_send"))
        self._show_filmstrip = bool(settings_mod.get("show_filmstrip"))

        # Apply dynamic tuning to canvas constants
        zf = float(settings_mod.get("zoom_factor") or 1.18)
        ImageCanvas.WHEEL_FACTOR = max(1.05, min(1.5, zf))

        self._loader_signals = _LoaderSignals()
        self._loader_signals.image_ready.connect(self._on_image_ready)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) // 2))

        # Keyboard focus so arrow/ESC reach this widget once embedded.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._current_path = manager.images[start_idx].path if start_idx < len(manager.images) else None

        self._build_ui()
        self._load_image(self._idx)
        self._preload_neighbors()

        QTimer.singleShot(0, self._post_show_settle)
        QTimer.singleShot(80, self._post_show_settle)

        if self._filmstrip_deferred:
            QTimer.singleShot(200, self._kick_background_scan)

    def _post_show_settle(self):
        if hasattr(self, "_canvas"):
            self._canvas.updateGeometry()
            self._canvas.update()
        if hasattr(self, "_toast"):
            self._toast._reposition()
        # Grab keyboard focus so arrow keys work immediately — without this the
        # user has to click the image first. Runs after the central-widget swap
        # and fullscreen toggle have settled (called at 0 ms and 80 ms).
        if self.isVisible():
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_ui(self):
        self.setAutoFillBackground(True)
        self._apply_central_bg()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar — filename + [idx/total], centered
        self._title = QLabel()
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_title_qss()
        layout.addWidget(self._title)

        # Middle row: image canvas + (optional) info panel
        self._middle = QWidget()
        self._middle.setAutoFillBackground(True)
        mid_layout = QHBoxLayout(self._middle)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_layout.setSpacing(0)

        # Stacked viewer: page 0 = still-image canvas, page 1 = video player.
        # Switching pages happens in _load_image() based on file type so the
        # rest of the slideshow chrome (top bar, filmstrip, status, info panel)
        # stays put across image/video transitions.
        self._viewer_stack = QStackedWidget()
        self._viewer_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._canvas = ImageCanvas()
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.set_double_click_enabled(True)
        self._viewer_stack.addWidget(self._canvas)

        from .video_view import VideoView, available as video_available
        self._video_view = VideoView()
        self._video_view.playback_finished.connect(self._on_video_finished)
        self._video_view.error_occurred.connect(self._on_video_error)
        self._viewer_stack.addWidget(self._video_view)
        self._video_available = video_available()

        mid_layout.addWidget(self._viewer_stack, stretch=1)

        from .image_info_panel import ImageInfoPanel
        self._info_panel = ImageInfoPanel()
        self._info_panel.hide()
        mid_layout.addWidget(self._info_panel)

        layout.addWidget(self._middle, stretch=1)

        # Shortcut panel (overlay on right side of viewer)
        self._shortcut_panel = ShortcutPanel(self._middle)
        self._update_shortcut_panel()
        QTimer.singleShot(0, self._reposition_shortcut_panel)

        # Filmstrip bar
        self._filmstrip = FilmstripBar(self._manager)
        self._filmstrip.jumped.connect(self._jump_to)
        if self._filmstrip_deferred:
            self._filmstrip.setVisible(False)
        else:
            self._filmstrip.setVisible(self._show_filmstrip)
        layout.addWidget(self._filmstrip)

        # Scan progress bar (thin, subtle, at bottom)
        from PyQt6.QtWidgets import QProgressBar
        self._scan_progress = QProgressBar()
        self._scan_progress.setMaximumHeight(3)
        self._scan_progress.setTextVisible(False)
        self._scan_progress.setStyleSheet(
            "QProgressBar { background: transparent; border: 0; }"
            "QProgressBar::chunk { background: #2a82da; }"
        )
        self._scan_progress.setRange(0, 0)
        self._scan_progress.setVisible(self._filmstrip_deferred)
        layout.addWidget(self._scan_progress)

        # Toast overlay (parented to self so positioning stays consistent)
        self._toast = ToastWidget(self)

        # Bottom status bar (custom QFrame; replaces QStatusBar so this widget
        # can be embedded in MainWindow's central area instead of being its
        # own QMainWindow). Layout: [stats-left | legend (centered) | stats]
        # so the legend reads as truly centered regardless of stats width.
        self._status_bar = QFrame()
        self._status_bar.setFrameShape(QFrame.Shape.NoFrame)
        sb_layout = QHBoxLayout(self._status_bar)
        sb_layout.setContentsMargins(14, 8, 14, 10)
        sb_layout.setSpacing(0)

        # Invisible spacer label on the left, mirroring stats_label's natural
        # width, so the centered legend isn't pulled off-center by stats.
        self._stats_left_spacer = QLabel()
        self._stats_left_spacer.setStyleSheet("color: transparent;")
        sb_layout.addWidget(self._stats_left_spacer, 0, Qt.AlignmentFlag.AlignLeft)

        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._legend.setWordWrap(True)
        sb_layout.addWidget(self._legend, 1)

        self._stats_label = QLabel()
        self._stats_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sb_layout.addWidget(self._stats_label, 0, Qt.AlignmentFlag.AlignRight)

        self._apply_status_qss()
        layout.addWidget(self._status_bar)

        self._update_hint()
        self._update_status()

    def _apply_central_bg(self):
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("canvas_bg"))
        self.setPalette(pal)

    def _apply_title_qss(self):
        bg = theme_mod.c("hint_bar_bg")
        fg = theme_mod.c("hint_bar_fg")
        self._title.setStyleSheet(
            f"background: {bg}; color: {fg};"
            f" padding: 7px 12px; font-size: 13px; font-weight: 600;"
            f" letter-spacing: 0.3px;"
        )

    def _apply_status_qss(self):
        bg = theme_mod.c("status_bar_bg")
        fg = theme_mod.c("status_bar_fg")
        # Bigger, brighter shortcut bar — reads at a glance during slideshow.
        self._status_bar.setStyleSheet(
            f"QFrame {{ background: {bg}; border-top: 1px solid rgba(255,255,255,0.06); }}"
            f"QLabel {{ color: {fg}; font-size: 14px; }}"
        )

    # ── Background scan callbacks ────────────────────────────────────────────

    def _kick_background_scan(self):
        if hasattr(self._manager, "start_background_scan"):
            self._manager.start_background_scan()

    def update_scan_progress(self, count: int):
        self._scan_total = count
        if hasattr(self, "_scan_progress") and self._scan_progress.isVisible():
            self._scan_progress.setRange(0, 0)

    def on_scan_complete(self):
        if hasattr(self, "_scan_progress"):
            self._scan_progress.setVisible(False)
        old_idx = self._idx
        if self._current_path:
            self._idx = self._manager.current_index_of(self._current_path)
        self._scan_total = len(self._manager.images)

        # Remap pixmap cache: old seed indices → new post-scan indices.
        # Keep the current image pixmap so there's no flash.
        if old_idx != self._idx and old_idx in self._pixmap_cache:
            pm = self._pixmap_cache.pop(old_idx)
            self._pixmap_cache[self._idx] = pm
        # Drop other stale seed entries (their indices shifted)
        stale = [k for k in self._pixmap_cache if k != self._idx]
        for k in stale:
            self._pixmap_cache_bytes -= _pixmap_bytes(self._pixmap_cache.pop(k))
        self._inflight.clear()

        self._filmstrip._thumbs.clear()
        self._filmstrip._pending.clear()
        self._filmstrip._current = self._idx
        self._filmstrip._pan = 0
        if self._filmstrip_deferred and self._show_filmstrip:
            self._filmstrip_deferred = False
            self._reveal_filmstrip()
        self._preload_neighbors()
        self._update_status()
        self.title_changed.emit()

    def _reveal_filmstrip(self):
        bar_w = self.width()
        min_thumb_w = 40 + self._filmstrip.SPACING
        max_tiles = max(60, (bar_w // min_thumb_w) + 20)
        half = max_tiles // 2
        lo = max(0, self._filmstrip._current - half)
        hi = min(len(self._manager.images), self._filmstrip._current + half + 1)
        for i in range(lo, hi):
            self._filmstrip._ensure_thumb(i)
        import time as _time
        self._filmstrip_reveal_start = _time.monotonic()
        self._filmstrip_check_timer = QTimer(self)
        self._filmstrip_check_timer.setInterval(100)
        self._filmstrip_check_timer.timeout.connect(self._check_filmstrip_ready)
        self._filmstrip_check_timer.start()

    def _check_filmstrip_ready(self):
        import time as _time
        elapsed = _time.monotonic() - self._filmstrip_reveal_start
        has_pending = bool(self._filmstrip._pending)
        if has_pending and elapsed < 10.0:
            return
        self._filmstrip_check_timer.stop()
        self._filmstrip_check_timer.deleteLater()
        self._filmstrip.setVisible(True)
        effect = QGraphicsOpacityEffect(self._filmstrip)
        self._filmstrip.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(250)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self._filmstrip.setGraphicsEffect(None))
        anim.start()
        self._filmstrip_anim = anim

    def refresh_theme(self):
        self._apply_central_bg()
        self._apply_title_qss()
        self._apply_status_qss()
        self._filmstrip.refresh_theme()
        self._canvas.update()

    def _shortcut_hint(self) -> str:
        # Each shortcut is rendered as a bold "key chip" + label so the eye
        # picks them out at a glance. Groups separated by a dim middle dot.
        def chip(key: str) -> str:
            return (
                '<span style="background:#2a2a2a; color:#f0f0f0;'
                ' font-weight:700; padding:2px 7px; border-radius:4px;'
                ' border:1px solid #3a3a3a;">' + key + '</span>'
            )

        def pair(key: str, label: str) -> str:
            return f'{chip(key)}&nbsp;<span style="color:#cfcfcf;">{label}</span>'

        sep = '&nbsp;&nbsp;&nbsp;&nbsp;<span style="color:#555;">·</span>&nbsp;&nbsp;&nbsp;&nbsp;'
        gap = '&nbsp;&nbsp;'

        # Adapt the hint to the current item type — image vs video.
        from .media import is_video as _is_video
        cur_is_video = (
            0 <= self._idx < len(self._manager.images)
            and _is_video(self._manager.images[self._idx].path)
        )

        if cur_is_video:
            view = pair("← →", "Nav") + gap + pair("ESC", "Back") + gap + pair("F", "Fullscreen")
            playback = (
                pair("Space", "Play/Pause") + gap
                + pair(", .", "±5s") + gap
                + pair("M", "Mute") + gap
                + pair("I", "Info")
            )
            opn = pair("O", "Open") + gap + pair("E", "Explorer") + gap + pair("Ctrl+O", "Open with…")
            groups = [view, playback, opn]
        else:
            view = pair("← →", "Nav") + gap + pair("ESC", "Back") + gap + pair("F", "Fullscreen")
            edit = pair("R", "Rotate") + gap + pair("Ctrl+S", "Save") + gap + pair("C", "Crop")
            zoom = pair("0", "Fit") + gap + pair("Z", "1:1")
            info = pair("H", "Hist") + gap + pair("P", "Peaking") + gap + pair("I", "Info")
            opn = pair("O", "Open") + gap + pair("E", "Explorer") + gap + pair("Ctrl+O", "Open with…")
            groups = [view, edit, zoom, opn, info]

        if self._manager.has_destinations:
            dests = self._manager.destinations
            n = len(dests)
            if n == 1:
                move_hint = pair("Enter", f"→ {dests[0]['name']}")
            else:
                parts = [pair(str(i + 1), dests[i]["name"]) for i in range(n)]
                active = dests[self._current_dest]["name"]
                move_hint = (
                    pair("Enter", f"→ {active}") + gap
                    + gap.join(parts) + gap
                    + pair("Tab", "cycle")
                )
            groups += [move_hint, pair("Ctrl+Z", "Undo")]

        return sep.join(groups)

    def _update_hint(self):
        self._legend.setText(self._shortcut_hint())

    # ── Image loading ──────────────────────────────────────────────────────────

    def _cache_key(self, idx: int) -> tuple | None:
        if idx < 0 or idx >= len(self._manager.images):
            return None
        rec = self._manager.images[idx]
        try:
            mtime = int(os.path.getmtime(rec.path))
        except OSError:
            mtime = 0
        return (rec.path, mtime)

    def _load_image(self, idx: int):
        if idx < 0 or idx >= len(self._manager.images):
            return
        self._idx = idx
        rec = self._manager.images[idx]
        self._current_path = rec.path
        settings_mod.save_position(self._manager.source_folder, idx)
        self._filmstrip.set_current(idx)

        from .media import is_video
        if is_video(rec.path):
            self._load_video(rec)
            return

        # Came from a video item — make sure playback stops before we paint a still.
        try:
            self._video_view.cleanup()
        except Exception:
            pass
        self._viewer_stack.setCurrentWidget(self._canvas)

        key = self._cache_key(idx)
        rotation = self._manager.rotations.get(rec.filename, 0)

        # Overlay positions read once on slideshow init (settings rarely change mid-session)
        self._canvas.set_overlay_positions(self._exif_pos, self._hist_pos)

        if idx in self._pixmap_cache:
            self._pixmap_cache.move_to_end(idx)
            self._canvas.set_raw_loading(False)
            self._canvas.set_pixmap(self._pixmap_cache[idx], key=key, rotation=rotation)
            self._maybe_show_peaking_toast()
            self._update_status()
            return

        # Progressive: show cached thumbnail first, then load full-res
        cf = gallery_cache_file(self._manager.source_folder, rec.path)
        if cf.exists():
            ph = QPixmap(str(cf))
            if ph.isNull():
                ph = QPixmap(400, 300)
                ph.fill(QColor(20, 20, 20))
        else:
            ph = QPixmap(400, 300)
            ph.fill(QColor(20, 20, 20))
        self._canvas.set_pixmap(ph, key=None, rotation=rotation)
        is_raw = raw_loader.is_raw(rec.path) and raw_loader.available()
        self._canvas.set_raw_loading(is_raw)
        self._update_status()
        self._start_load(idx)

    _VIDEO_SIZE_WARN_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB

    def _load_video(self, rec):
        """Switch the viewer stack to the video player and start playback."""
        self._viewer_stack.setCurrentWidget(self._video_view)
        if not self._video_available:
            self._toast.show_message("Video playback unavailable (PyQt6-Multimedia missing)", ms=2500)
            self._update_status()
            return
        try:
            fsize = os.path.getsize(rec.path)
            if fsize > self._VIDEO_SIZE_WARN_BYTES:
                gb = fsize / (1024 ** 3)
                self._toast.show_message(f"Large video ({gb:.1f} GB) — playback may be slow", ms=3000)
        except OSError:
            pass
        self._video_view.load(rec.path, autoplay=True)
        self._update_status()

    def _start_load(self, idx: int):
        if idx < 0 or idx >= len(self._manager.images):
            return
        if idx in self._pixmap_cache or idx in self._inflight:
            return
        # Don't dispatch the image loader for video files — videos are streamed
        # directly through QMediaPlayer.
        from .media import is_video
        if is_video(self._manager.images[idx].path):
            return
        self._inflight.add(idx)
        self._load_t0[idx] = time.perf_counter()
        task = ImageLoadTask(self._manager, idx, self._loader_signals)
        self._pool.start(task)

    @pyqtSlot()
    def _on_video_finished(self):
        """Called when the current video reaches end-of-file."""
        if self._auto_advance:
            self._go_next()

    @pyqtSlot(str)
    def _on_video_error(self, msg: str):
        self._toast.show_message(f"Playback error: {msg}", ms=3500)

    def _preload(self, idx: int):
        self._start_load(idx)

    @pyqtSlot(int, QImage)
    def _on_image_ready(self, idx: int, img: QImage):
        self._inflight.discard(idx)
        t0 = self._load_t0.pop(idx, None)
        if t0 is not None:
            ms = (time.perf_counter() - t0) * 1000
            rec = self._manager.images[idx] if idx < len(self._manager.images) else None
            log.info("image.decode", file=os.path.basename(rec.path) if rec else "?",
                     idx=idx, current=(idx == self._idx),
                     px=f"{img.width()}x{img.height()}", ms=f"{ms:.1f}")
        pixmap = QPixmap.fromImage(img)
        self._pixmap_cache[idx] = pixmap
        self._pixmap_cache.move_to_end(idx)
        self._pixmap_cache_bytes += _pixmap_bytes(pixmap)
        while (len(self._pixmap_cache) > self.PIXMAP_CACHE_MAX
               or self._pixmap_cache_bytes > self.PIXMAP_CACHE_MAX_BYTES):
            if len(self._pixmap_cache) <= 1:
                break
            _, evicted = self._pixmap_cache.popitem(last=False)
            self._pixmap_cache_bytes -= _pixmap_bytes(evicted)
        if idx == self._idx:
            rec = self._manager.images[idx]
            rotation = self._manager.rotations.get(rec.filename, 0)
            self._canvas.set_raw_loading(False)
            self._canvas.set_pixmap(pixmap, key=self._cache_key(idx), rotation=rotation)
            self._maybe_show_peaking_toast()
        self._update_status()

    def _maybe_show_peaking_toast(self):
        if self._canvas.show_peaking:
            self._toast.show_message("Processing…", ms=1500)

    # ── Navigation & actions ───────────────────────────────────────────────────

    def _preload_neighbors(self):
        for off in range(1, self._preload_count + 1):
            self._preload(self._idx + off)
            self._preload(self._idx - off)

    def _go_next(self):
        if self._idx + 1 < len(self._manager.images):
            self._load_image(self._idx + 1)
            self._preload_neighbors()

    def _go_prev(self):
        if self._idx - 1 >= 0:
            self._load_image(self._idx - 1)
            self._preload_neighbors()

    def _next_unreviewed(self):
        for i in range(self._idx + 1, len(self._manager.images)):
            if self._manager.images[i].status == STATUS_UNREVIEWED:
                self._load_image(i)
                self._preload_neighbors()
                return
        self._go_next()

    def _jump_to(self, idx: int):
        self._load_image(idx)
        self._preload_neighbors()

    def _cycle_dest(self):
        if len(self._manager.destinations) <= 1:
            return
        self._current_dest = (self._current_dest + 1) % len(self._manager.destinations)
        self._update_hint()
        dest_name = self._manager.destinations[self._current_dest]["name"]
        self._toast.show_message(f"Destination: {dest_name}", ms=1000)

    def _send_to(self, dest_idx: int):
        if dest_idx < 0 or dest_idx >= len(self._manager.destinations):
            return
        dest_name = self._manager.destinations[dest_idx]["name"]
        prev_handler = self._manager.conflict_handler
        self._manager.conflict_handler = lambda s, d: conflict_dialog.ask(self, s, d)
        try:
            err = self._manager.send_to(self._idx, dest_idx)
        finally:
            self._manager.conflict_handler = prev_handler
        if err:
            QMessageBox.warning(self, "Error", f"Could not send image:\n{err}")
            return
        rec = self._manager.images[self._idx]
        if rec.status == STATUS_UNREVIEWED:
            # User skipped/cancelled the conflict dialog — nothing happened
            self._toast.show_message("Skipped")
            return
        verb = "Moved" if self._manager.mode == "move" else "Copied"
        self._toast.show_message(
            f"✓  {verb} to  {dest_name}", ms=3000,
            action="Undo", action_cb=self._undo,
        )
        self.status_changed.emit()
        self._filmstrip.refresh()
        self._update_status()
        if self._auto_advance:
            self._next_unreviewed()

    def _undo(self):
        err = self._manager.undo()
        if err:
            self._toast.show_message("Nothing to undo")
            return
        self._toast.show_message("↩  Undone")
        self.status_changed.emit()
        self._filmstrip.refresh()
        self._update_status()

    # ── Edits (crop / save rotation) ───────────────────────────────────────────

    def _commit_crop(self):
        norm = self._canvas.crop_norm()
        if norm is None or norm.width() < 0.01 or norm.height() < 0.01:
            self._toast.show_message("Crop area too small")
            return
        rec = self._manager.images[self._idx]
        rot = self._manager.rotations.get(rec.filename, 0)
        mode = save_dialog_mod.resolve_save_mode(rec.path, self)
        if not mode:
            return
        out, err = edits_mod.apply_crop(rec.path, norm, rot, mode)
        if err or not out:
            QMessageBox.warning(self, "Save failed", err or "unknown error")
            return
        self._canvas.exit_crop_mode()
        self._after_save(out, rec, reset_rotation=True, overwrite=(mode == "overwrite"))

    def _save_rotation(self):
        if self._idx < 0 or self._idx >= len(self._manager.images):
            return
        rec = self._manager.images[self._idx]
        rot = self._manager.rotations.get(rec.filename, 0)
        if rot == 0:
            self._toast.show_message("No rotation to save")
            return
        mode = save_dialog_mod.resolve_save_mode(rec.path, self)
        if not mode:
            return
        out, err = edits_mod.apply_rotation(rec.path, rot, mode)
        if err or not out:
            QMessageBox.warning(self, "Save failed", err or "unknown error")
            return
        self._after_save(out, rec, reset_rotation=True, overwrite=(mode == "overwrite"))

    def _after_save(self, out_path: str, rec, reset_rotation: bool, overwrite: bool):
        name = Path(out_path).name
        self._toast.show_message(f"✓  Saved: {name}", ms=1800)
        if reset_rotation:
            self._manager.rotations.pop(rec.filename, None)
        evicted = self._pixmap_cache.pop(self._idx, None)
        if evicted is not None:
            self._pixmap_cache_bytes -= _pixmap_bytes(evicted)
        self._inflight.discard(self._idx)
        if overwrite:
            self._load_image(self._idx)
        else:
            # New file written — current image path unchanged; just redraw to clear rotation.
            self._load_image(self._idx)
        self.status_changed.emit()

    # ── Status bar ─────────────────────────────────────────────────────────────

    def _update_status(self):
        if not self._manager.images:
            return
        rec = self._manager.images[self._idx]
        total = len(self._manager.images)
        # Refresh shortcut hint — it changes shape when current item is a video.
        self._update_hint()
        # Top bar: filename centered, counter muted. Video items get a tiny
        # "VIDEO" pill so the user knows why the chrome looks different.
        muted = theme_mod.c("muted").name()
        from .media import is_video as _is_video
        title_html = f'{rec.filename}'
        if _is_video(rec.path):
            title_html = (
                '<span style="background:#2a82da; color:#fff; font-size:9px;'
                ' font-weight:700; padding:2px 7px; border-radius:3px;'
                ' letter-spacing:1px;">VIDEO</span>&nbsp;&nbsp;'
                + title_html
            )
        if self._manager.scan_complete:
            counter = f'[{self._idx + 1} / {total}]'
        else:
            counter = f'[{self._idx + 1}]'
        self._title.setText(
            f'{title_html}'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{muted}; font-weight:500;">'
            f'{counter}</span>'
        )
        self.title_changed.emit()
        self._title.setTextFormat(Qt.TextFormat.RichText)
        # Bottom-right stats only when destinations are set.
        if not self._manager.has_destinations:
            self._stats_label.setText("")
        else:
            status_str = self._manager.dest_name_for_status(rec.status)
            s = self._manager.stats()
            self._stats_label.setText(
                f"Status: {status_str}   ·   "
                f"Selected: {s['selected']}   ·   Remaining: {s['unreviewed']}"
            )
        # Mirror stats width into the left spacer so the centered legend stays
        # truly centered regardless of how wide the right-side stats grew.
        self._stats_left_spacer.setFixedWidth(self._stats_label.sizeHint().width())
        # Push image info to side panel (lazy — only when visible).
        if self._info_panel_visible:
            self._info_panel.set_image(rec, self._manager)

    # ── Keyboard ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        has_dest = self._manager.has_destinations

        # Crop mode intercepts Enter/Esc before normal routing.
        if self._canvas.crop_mode:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._commit_crop()
                return
            if key == Qt.Key.Key_Escape:
                self._canvas.exit_crop_mode()
                self._toast.show_message("Crop cancelled", ms=800)
                return

        # ── Video-mode key intercepts ────────────────────────────────────────
        from .media import is_video as _is_video
        cur_is_video = _is_video(self._manager.images[self._idx].path)
        if cur_is_video:
            if not ctrl:
                if key == Qt.Key.Key_Space:
                    self._video_view.toggle_play()
                    return
                if key == Qt.Key.Key_M:
                    self._video_view.toggle_mute()
                    return
                if key == Qt.Key.Key_Comma:
                    self._video_view.step_seconds(-5)
                    self._toast.show_message("−5s", ms=600)
                    return
                if key == Qt.Key.Key_Period:
                    self._video_view.step_seconds(5)
                    self._toast.show_message("+5s", ms=600)
                    return
                if key == Qt.Key.Key_J:
                    self._video_view.step_seconds(-10)
                    self._toast.show_message("−10s", ms=600)
                    return
                if key == Qt.Key.Key_L:
                    self._video_view.step_seconds(10)
                    self._toast.show_message("+10s", ms=600)
                    return
                if key == Qt.Key.Key_K:
                    self._video_view.toggle_play()
                    return
                if key == Qt.Key.Key_BracketLeft:
                    self._video_view.speed_down()
                    self._toast.show_message(f"Speed: {self._video_view.current_speed():g}x", ms=800)
                    return
                if key == Qt.Key.Key_BracketRight:
                    self._video_view.speed_up()
                    self._toast.show_message(f"Speed: {self._video_view.current_speed():g}x", ms=800)
                    return
                if key == Qt.Key.Key_Semicolon:
                    self._video_view.frame_step(False)
                    self._toast.show_message("◀ Frame", ms=600)
                    return
                if key == Qt.Key.Key_Apostrophe:
                    self._video_view.frame_step(True)
                    self._toast.show_message("Frame ▶", ms=600)
                    return
                if key == Qt.Key.Key_Home:
                    self._video_view.skip_to_start()
                    return
                if key == Qt.Key.Key_End:
                    self._video_view.skip_to_end()
                    return
            if ctrl and key == Qt.Key.Key_L:
                self._video_view.toggle_loop()
                loop = self._video_view.is_looping()
                self._toast.show_message(f"Loop: {'ON' if loop else 'OFF'}", ms=800)
                return

        if key == Qt.Key.Key_Escape:
            w = self.window()
            if w and w.isFullScreen():
                self.fullscreen_requested.emit()
                return
            self.closed.emit(self._idx)
            return
        elif key == Qt.Key.Key_Delete and not ctrl:
            self._delete_current()
            return
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_D, Qt.Key.Key_Space):
            self._go_next()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self._go_prev()
        elif key == Qt.Key.Key_Home:
            self._jump_to(0)
        elif key == Qt.Key.Key_End:
            self._jump_to(len(self._manager.images) - 1)
        elif key == Qt.Key.Key_PageDown:
            self._jump_to(min(len(self._manager.images) - 1, self._idx + 10))
        elif key == Qt.Key.Key_PageUp:
            self._jump_to(max(0, self._idx - 10))
        elif key == Qt.Key.Key_F11:
            self.fullscreen_requested.emit()
        elif key == Qt.Key.Key_I and not ctrl:
            self._toggle_info_panel()
        elif key == Qt.Key.Key_Question or (key == Qt.Key.Key_Slash and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._show_cheatsheet()
        elif key == Qt.Key.Key_R and not ctrl:
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            self._rotate_current(270 if shift else 90)
        elif key == Qt.Key.Key_H and not ctrl:
            self._canvas.toggle_histogram()
        elif key == Qt.Key.Key_Z and not ctrl:
            self._canvas.zoom_actual()
            self._toast.show_message("1:1", ms=800)
        elif key == Qt.Key.Key_F and not ctrl:
            self.fullscreen_requested.emit()
        elif key == Qt.Key.Key_P and not ctrl:
            self._canvas.toggle_peaking()
            self._toast.show_message("Focus peaking toggled", ms=800)
        elif key == Qt.Key.Key_C and not ctrl:
            self._canvas.enter_crop_mode()
            self._toast.show_message("Crop mode — drag to select", ms=1200)
        elif ctrl and key == Qt.Key.Key_S:
            self._save_rotation()
        elif ctrl and key == Qt.Key.Key_O:
            self._open_with_menu()
        elif key == Qt.Key.Key_O and not ctrl:
            self._open_external("default")
        elif key == Qt.Key.Key_E and not ctrl:
            self._reveal_in_explorer()
        elif has_dest and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._send_to(self._current_dest)
        elif has_dest and key == Qt.Key.Key_1:
            self._send_to(0)
        elif has_dest and key == Qt.Key.Key_2:
            self._send_to(1)
        elif has_dest and key == Qt.Key.Key_3:
            self._send_to(2)
        elif has_dest and key == Qt.Key.Key_Tab:
            self._cycle_dest()
        elif has_dest and key == Qt.Key.Key_Z and ctrl:
            self._undo()
        elif key == Qt.Key.Key_V and not ctrl:
            if self._compare_view is not None:
                self._exit_compare()
            else:
                self._enter_compare()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._canvas.zoom(1.2)
        elif key == Qt.Key.Key_Minus:
            self._canvas.zoom(1 / 1.2)
        elif key == Qt.Key.Key_0:
            self._canvas.zoom_reset()
        else:
            super().keyPressEvent(event)

    def set_chrome_visible(self, visible: bool):
        """MainWindow calls this when toggling fullscreen so the slideshow's
        own top/bottom bars hide together with the menu/status chrome."""
        self._title.setVisible(visible)
        self._filmstrip.setVisible(self._show_filmstrip)
        self._status_bar.setVisible(visible)
        if not visible and hasattr(self, "_shortcut_panel"):
            self._shortcut_panel._visible = False
            self._shortcut_panel.hide()

    def _toggle_info_panel(self):
        self._info_panel_visible = not self._info_panel_visible
        if self._info_panel_visible:
            rec = self._manager.images[self._idx]
            self._info_panel.set_image(rec, self._manager)
            self._info_panel.show()
        else:
            self._info_panel.hide()

    def _show_cheatsheet(self):
        self._shortcut_panel.toggle()

    def _update_shortcut_panel(self):
        sections = [
            ("NAVIGATE", [
                ("← →", "Previous / Next"),
                ("Home / End", "First / Last"),
                ("PgUp / PgDn", "Jump ±10"),
                ("Space", "Next (image) / Play (video)"),
            ]),
            ("ZOOM", [
                ("Scroll", "Zoom at cursor"),
                ("+ / −", "Zoom in / out"),
                ("0", "Fit to window"),
                ("Z", "1:1 pixel zoom"),
            ]),
            ("EDIT", [
                ("R", "Rotate 90° CW"),
                ("Ctrl+S", "Save rotation"),
                ("C", "Crop mode"),
            ]),
            ("OVERLAYS", [
                ("H", "Histogram"),
                ("P", "Focus peaking"),
                ("I", "Info panel"),
            ]),
            ("OPEN", [
                ("O", "System default"),
                ("Ctrl+O", "Open with…"),
                ("E", "Reveal in Explorer"),
            ]),
            ("COMPARE", [
                ("V", "Side-by-side compare"),
                ("S", "Toggle sync (in compare)"),
            ]),
            ("WINDOW", [
                ("F / F11", "Fullscreen"),
                ("ESC", "Exit fullscreen / Back"),
                ("?", "Toggle this panel"),
            ]),
            ("VIDEO", [
                ("Space / K", "Play / pause"),
                (", / .", "Skip ±5s"),
                ("J / L", "Skip ±10s"),
                ("; / '", "Frame step ◀ / ▶"),
                ("[ / ]", "Speed down / up"),
                ("M", "Mute / unmute"),
                ("Ctrl+L", "Toggle loop"),
            ]),
        ]
        if self._manager.has_destinations:
            dests = self._manager.destinations
            dest_pairs = [(str(i + 1), dests[i]["name"]) for i in range(len(dests))]
            sections.append(("SORT", [
                ("Enter", "Send to active"),
                ("Tab", "Cycle destination"),
                *dest_pairs,
                ("Ctrl+Z", "Undo"),
            ]))
        self._shortcut_panel.set_shortcuts(sections)

    def _reposition_shortcut_panel(self):
        if hasattr(self, "_shortcut_panel"):
            m = self._middle
            self._shortcut_panel.setFixedHeight(m.height() - 20)
            self._shortcut_panel.move(m.width() - ShortcutPanel.WIDTH - 10, 10)

    def _open_with_menu(self):
        menu = QMenu(self)
        act = menu.addAction(menu_icon("photoshop"), "Adobe Photoshop")
        act.triggered.connect(lambda: self._open_external("photoshop"))
        act = menu.addAction(menu_icon("lightroom"), "Adobe Lightroom")
        act.triggered.connect(lambda: self._open_external("lightroom"))
        menu.addSeparator()
        act = menu.addAction(menu_icon("system"), "System Default")
        act.triggered.connect(lambda: self._open_external("default"))
        menu.exec(self.mapToGlobal(self.rect().center()))

    def _open_external(self, which: str):
        if self._idx < 0 or self._idx >= len(self._manager.images):
            return
        path = self._manager.images[self._idx].path
        if which == "photoshop":
            app = external.resolve_or_prompt("photoshop", self)
            if not app:
                return
            err = external.open_with(app, path)
            name = "Photoshop"
        elif which == "lightroom":
            app = external.resolve_or_prompt("lightroom", self)
            if not app:
                return
            err = external.open_with(app, path)
            name = "Lightroom"
        else:
            err = external.open_default(path)
            name = "system default"
        if err:
            self._toast.show_message(f"Error: {err}")
        else:
            self._toast.show_message(f"→ {name}")

    def _reveal_in_explorer(self):
        if self._idx < 0 or self._idx >= len(self._manager.images):
            return
        path = self._manager.images[self._idx].path
        import subprocess
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            self._toast.show_message("Opened in Explorer", ms=1200)
        except Exception as exc:
            self._toast.show_message(f"Error: {exc}", ms=3000)

    # ── Right-click menu (all actions) ───────────────────────────────────────

    def contextMenuEvent(self, event):
        if self._idx < 0 or self._idx >= len(self._manager.images):
            return
        rec = self._manager.images[self._idx]
        path = rec.path
        from .media import is_video
        from . import album_browser_view as abv
        video = is_video(path)

        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        head = menu.addAction(os.path.basename(path))
        head.setEnabled(False)
        menu.addSeparator()

        open_menu = menu.addMenu(menu_icon("photoshop"), "Open With")
        open_menu.setToolTipsVisible(True)
        a = open_menu.addAction(menu_icon("photoshop"), "Adobe Photoshop")
        a.triggered.connect(lambda: self._open_external("photoshop"))
        a = open_menu.addAction(menu_icon("lightroom"), "Adobe Lightroom")
        a.triggered.connect(lambda: self._open_external("lightroom"))
        open_menu.addSeparator()
        a = open_menu.addAction(menu_icon("system"), "System Default")
        a.triggered.connect(lambda: self._open_external("default"))

        menu.addSeparator()

        if not video:
            rot_menu = menu.addMenu(menu_icon("system"), "Rotate")
            a = rot_menu.addAction(menu_icon("system"), "Rotate 90° Clockwise")
            a.setShortcut("R")
            a.triggered.connect(lambda: self._rotate_current(90))
            a = rot_menu.addAction(menu_icon("system"), "Rotate 90° Anticlockwise")
            a.setShortcut("Shift+R")
            a.triggered.connect(lambda: self._rotate_current(270))
            a = rot_menu.addAction(menu_icon("system"), "Rotate 180°")
            a.triggered.connect(lambda: self._rotate_current(180))
            rot_menu.addSeparator()
            a = rot_menu.addAction("Reset Rotation")
            a.triggered.connect(self._reset_rotation)
            act_zoom = menu.addAction(menu_icon("select_all"), "Zoom 1:1")
            act_zoom.triggered.connect(lambda: (self._canvas.zoom_actual(),
                                                self._toast.show_message("1:1", ms=800)))
            act_crop = menu.addAction(menu_icon("move"), "Crop")
            act_crop.triggered.connect(lambda: (self._canvas.enter_crop_mode(),
                                                self._toast.show_message("Crop mode — drag to select", ms=1200)))
            act_hist = menu.addAction(menu_icon("system"), "Toggle Histogram")
            act_hist.triggered.connect(self._canvas.toggle_histogram)
            act_peak = menu.addAction(menu_icon("system"), "Toggle Focus Peaking")
            act_peak.triggered.connect(lambda: (self._canvas.toggle_peaking(),
                                                self._toast.show_message("Focus peaking toggled", ms=800)))
            menu.addSeparator()

        act_cmp = menu.addAction(menu_icon("copy"), "Compare")
        act_cmp.triggered.connect(
            lambda: self._exit_compare() if self._compare_view is not None else self._enter_compare())
        act_info = menu.addAction(menu_icon("copy_path"), "Info Panel")
        act_info.triggered.connect(self._toggle_info_panel)
        act_fs = menu.addAction(menu_icon("select_all"), "Toggle Fullscreen")
        act_fs.triggered.connect(self.fullscreen_requested.emit)

        menu.addSeparator()

        # Send to a configured destination (cull workflow).
        if self._manager.has_destinations:
            send_menu = menu.addMenu(menu_icon("move"), "Send to")
            for i, d in enumerate(self._manager.destinations):
                a = send_menu.addAction(menu_icon("folder"), d.get("name", f"Dest {i+1}"))
                a.triggered.connect(lambda _=False, di=i: self._send_to(di))

        recents = abv.recent_target_folders()

        move_menu = menu.addMenu(menu_icon("move"), "Move to")
        move_menu.setToolTipsVisible(True)
        for folder in recents:
            label = os.path.basename(folder.rstrip(os.sep)) or folder
            a = move_menu.addAction(menu_icon("folder"), label)
            a.setToolTip(folder)
            a.triggered.connect(lambda _=False, f=folder: self._move_or_copy_current(f, move=True, confirm=True))
        if recents:
            move_menu.addSeparator()
        a = move_menu.addAction(menu_icon("reveal"), "Choose Folder…")
        a.triggered.connect(lambda: self._choose_transfer_current(move=True))

        copy_menu = menu.addMenu(menu_icon("copy"), "Copy to")
        copy_menu.setToolTipsVisible(True)
        for folder in recents:
            label = os.path.basename(folder.rstrip(os.sep)) or folder
            a = copy_menu.addAction(menu_icon("folder"), label)
            a.setToolTip(folder)
            a.triggered.connect(lambda _=False, f=folder: self._move_or_copy_current(f, move=False, confirm=True))
        if recents:
            copy_menu.addSeparator()
        a = copy_menu.addAction(menu_icon("reveal"), "Choose Folder…")
        a.triggered.connect(lambda: self._choose_transfer_current(move=False))

        menu.addSeparator()

        act_copy = menu.addAction(menu_icon("copy_path"), "Copy Path")
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(path))
        act_reveal = menu.addAction(menu_icon("reveal"), "Reveal in Explorer")
        act_reveal.triggered.connect(self._reveal_in_explorer)

        menu.addSeparator()
        act_del = menu.addAction(menu_icon("delete"), "Delete (Recycle Bin)")
        act_del.triggered.connect(self._delete_current)

        menu.exec(event.globalPos())

    def _rotate_current(self, delta: int = 90):
        new_rot = self._canvas.rotate_by(delta)
        rec = self._manager.images[self._idx]
        self._manager.rotations[rec.filename] = new_rot
        self._filmstrip.refresh()
        labels = {90: "Rotated 90° clockwise", 270: "Rotated 90° anticlockwise",
                  180: "Rotated 180°", 0: "Rotation reset"}
        self._toast.show_message(labels.get(delta % 360, f"Rotated {delta}°"), ms=800)

    def _reset_rotation(self):
        delta = (360 - self._canvas._rotation) % 360
        if delta == 0:
            self._toast.show_message("Already unrotated", ms=800)
            return
        new_rot = self._canvas.rotate_by(delta)
        rec = self._manager.images[self._idx]
        self._manager.rotations[rec.filename] = new_rot
        self._filmstrip.refresh()
        self._toast.show_message("Rotation reset", ms=800)

    def _choose_transfer_current(self, *, move: bool):
        from PyQt6.QtWidgets import QFileDialog
        from . import album_browser_view as abv
        start = abv.recent_target_folders()
        verb = "Move" if move else "Copy"
        dest = QFileDialog.getExistingDirectory(
            self, f"{verb} image to folder",
            start[0] if start else (self._manager.source_folder or ""))
        if dest:
            self._move_or_copy_current(dest, move=move, confirm=False)

    def _move_or_copy_current(self, dest: str, *, move: bool, confirm: bool):
        if self._idx < 0 or self._idx >= len(self._manager.images):
            return
        from . import album_browser_view as abv
        rec = self._manager.images[self._idx]
        name = os.path.basename(dest.rstrip(os.sep)) or dest
        if confirm:
            verb = "Move" if move else "Copy"
            reply = QMessageBox.question(
                self, f"{verb} File", f"{verb} to “{name}”?\n\n{os.path.basename(rec.path)}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                return
        resolver = abv.make_conflict_resolver(self)
        with log.timed("image.transfer", op="move" if move else "copy", dest=dest):
            ok_sources, errors, cancelled = abv.transfer_files(
                [rec.path], dest, move=move, conflict_cb=resolver)
        abv.push_recent_target(dest)
        abv.invalidate_scan_cache(dest)
        if errors:
            self._toast.show_message(f"Failed: {errors[0]}", ms=3000)
            return
        if cancelled:
            return
        if move and ok_sources:
            self._toast.show_message(f"Moved → {name}", ms=1500)
            self._manager.images.pop(self._idx)
            if not self._manager.images:
                self.closed.emit(0)
                return
            if self._idx >= len(self._manager.images):
                self._idx = len(self._manager.images) - 1
            self._load_image(self._idx)
            self._filmstrip.refresh()
            self.status_changed.emit()
        else:
            self._toast.show_message(f"Copied → {name}", ms=1500)

    # ── Delete to Recycle Bin ────────────────────────────────────────────────

    def _delete_current(self):
        if self._idx < 0 or self._idx >= len(self._manager.images):
            return
        rec = self._manager.images[self._idx]
        reply = QMessageBox.question(
            self, "Delete File",
            f"Move to Recycle Bin?\n\n{os.path.basename(rec.path)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from picker._recycle import send_to_recycle_bin
            if not send_to_recycle_bin(rec.path):
                self._toast.show_message("Failed to delete", ms=2000)
                return
        except Exception as e:
            self._toast.show_message(f"Delete failed: {e}", ms=3000)
            return
        self._toast.show_message("Moved to Recycle Bin", ms=1500)
        self._manager.images.pop(self._idx)
        if not self._manager.images:
            self.closed.emit(0)
            return
        if self._idx >= len(self._manager.images):
            self._idx = len(self._manager.images) - 1
        self._load_image(self._idx)
        self._filmstrip.refresh()
        self.status_changed.emit()

    # ── Compare mode ──────────────────────────────────────────────────────────

    def _enter_compare(self):
        n = len(self._manager.images)
        if n < 2:
            self._toast.show_message("Need 2+ images to compare", ms=2000)
            return
        if hasattr(self, "_compare_view") and self._compare_view is not None:
            return

        idx_a = self._idx
        idx_b = (self._idx + 1) % n

        self._title.hide()
        self._middle.hide()
        self._filmstrip.hide()
        self._status_bar.hide()
        self._shortcut_panel.hide()

        self._compare_view = CompareView(self._manager, idx_a, idx_b, parent=self)
        self._compare_view.closed.connect(self._exit_compare)
        self._compare_view.fullscreen_requested.connect(
            lambda: self.fullscreen_requested.emit()
        )
        self.layout().insertWidget(0, self._compare_view, 1)
        self._compare_view.setFocus()

        # Fluid fade-in so compare mode slides in rather than popping.
        if bool(settings_mod.get("slideshow_animation")):
            eff = QGraphicsOpacityEffect(self._compare_view)
            self._compare_view.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", self._compare_view)
            anim.setDuration(220)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: self._compare_view.setGraphicsEffect(None)
                                  if self._compare_view is not None else None)
            anim.start()
            self._compare_anim = anim  # keep ref

    def _exit_compare(self):
        if not hasattr(self, "_compare_view") or self._compare_view is None:
            return
        self._compare_view.cleanup()
        self.layout().removeWidget(self._compare_view)
        self._compare_view.deleteLater()
        self._compare_view = None

        self._title.show()
        self._middle.show()
        self._filmstrip.setVisible(self._show_filmstrip)
        self._status_bar.show()
        self._canvas.setFocus()
        self.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_toast"):
            self._toast._reposition()
        self._reposition_shortcut_panel()

    def cleanup(self):
        """Called by MainWindow before this view is removed from the stack.
        Embedded widgets don't get closeEvent — do worker pool teardown here."""
        if self._compare_view is not None:
            self._compare_view.cleanup()
            self._compare_view = None
        try:
            self._pool.clear()
            self._pool.waitForDone(200)
        except Exception:
            pass
        # Drain filmstrip thumb pools too (image + ffmpeg pools live on the
        # FilmstripBar; without explicit waitForDone, queued ffmpeg jobs
        # would keep running after the slideshow tore down).
        try:
            if hasattr(self, "_filmstrip"):
                fs = self._filmstrip
                fs._pool.clear(); fs._pool.waitForDone(200)
                fs._video_pool.clear(); fs._video_pool.waitForDone(500)
        except Exception:
            pass
        # Stop the video player so it releases the file handle (Windows holds
        # an exclusive lock on open media files until the source is cleared).
        try:
            if hasattr(self, "_video_view"):
                self._video_view.cleanup()
        except Exception:
            pass


# ── Compare / Loupe-sync view ────────────────────────────────────────────────

class CompareView(QWidget):
    """Side-by-side image comparison with synchronized zoom and pan (loupe sync).

    Shows two images at once. Zoom/pan on either side is mirrored to the other in
    normalized coordinates so the same region stays framed regardless of
    resolution. One side is "active" (accent-outlined); ←/→ steps the active side
    through the library, Tab/click switches sides, X swaps the pair."""

    closed = pyqtSignal()
    fullscreen_requested = pyqtSignal()

    def __init__(self, manager: ImageManager, idx_a: int, idx_b: int, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._idx_a = idx_a
        self._idx_b = idx_b
        self._active = "a"                 # which side ←/→ and zoom keys drive
        self._sync_enabled = True
        self._dims: dict[str, tuple[int, int]] = {}   # side -> (w, h) once loaded
        self._loader_signals = _LoaderSignals()
        self._loader_signals.image_ready.connect(self._on_image_ready)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        self._set_active("a")
        self._load_both()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("canvas_bg"))
        self.setPalette(pal)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header — one cell per side; the active side is accent-highlighted.
        header = QWidget()
        header.setStyleSheet("background:#141414;")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(0)
        self._head_a = QLabel()
        self._head_b = QLabel()
        for lbl in (self._head_a, self._head_b):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setTextFormat(Qt.TextFormat.RichText)
        hlay.addWidget(self._head_a, 1)
        hlay.addWidget(self._head_b, 1)
        outer.addWidget(header)

        # Canvas row — each canvas wrapped in a frame we can outline when active.
        canvas_row = QWidget()
        row_lay = QHBoxLayout(canvas_row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(0)

        self._canvas_a = ImageCanvas()
        self._canvas_a.set_double_click_enabled(False)
        self._canvas_b = ImageCanvas()
        self._canvas_b.set_double_click_enabled(False)

        self._frame_a = self._wrap_canvas(self._canvas_a, "a")
        self._frame_b = self._wrap_canvas(self._canvas_b, "b")
        row_lay.addWidget(self._frame_a, 1)
        row_lay.addWidget(self._frame_b, 1)
        outer.addWidget(canvas_row, 1)

        # Bottom hint bar
        self._bar = QLabel()
        self._bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar.setStyleSheet(
            "background:#141414; color:#9a9a9a; padding:7px; font-size:11px;"
        )
        self._bar.setText(
            "←/→ change active side   ·   Tab / click  switch side   ·   "
            "X  swap   ·   scroll  zoom   ·   drag  pan   ·   "
            "S  sync   ·   0  reset   ·   F  fullscreen   ·   Esc  back"
        )
        outer.addWidget(self._bar)

        self._canvas_a.view_changed.connect(self._sync_from_a)
        self._canvas_b.view_changed.connect(self._sync_from_b)

    def _wrap_canvas(self, canvas: "ImageCanvas", side: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName(f"cmp_frame_{side}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(0)
        lay.addWidget(canvas)
        # Clicking anywhere on a side makes it active (pan still works — we only
        # observe the press, we don't consume it).
        canvas.installEventFilter(self)
        return frame

    def eventFilter(self, obj, event):
        if event.type() == event.Type.MouseButtonPress:
            if obj is self._canvas_a:
                self._set_active("a")
            elif obj is self._canvas_b:
                self._set_active("b")
        return super().eventFilter(obj, event)

    def _set_active(self, side: str):
        self._active = side
        accent = theme_mod.c("accent").name()
        for s, frame in (("a", self._frame_a), ("b", self._frame_b)):
            on = (s == side)
            frame.setStyleSheet(
                f"#cmp_frame_{s} {{ background:#0d0d0d; border:3px solid "
                f"{accent if on else 'transparent'}; border-radius:4px; }}"
            )
        self._update_header()

    # ── Sync ──────────────────────────────────────────────────────────────────
    def _sync_from_a(self, cx, cy, zr):
        if self._sync_enabled:
            self._canvas_b.apply_sync(cx, cy, zr)

    def _sync_from_b(self, cx, cy, zr):
        if self._sync_enabled:
            self._canvas_a.apply_sync(cx, cy, zr)

    # ── Loading / header ───────────────────────────────────────────────────────
    def _load_both(self):
        self._start_load(self._idx_a, "a")
        self._start_load(self._idx_b, "b")
        self._update_header()

    def _head_html(self, side: str, idx: int) -> str:
        imgs = self._manager.images
        n = len(imgs)
        name = os.path.basename(imgs[idx].path) if 0 <= idx < n else "?"
        dims = self._dims.get(side)
        dim_txt = f"{dims[0]}×{dims[1]}" if dims else ""
        active = (side == self._active)
        accent = theme_mod.c("accent").name()
        tag_bg = accent if active else "#333"
        tag = (f"<span style='background:{tag_bg}; color:#fff; padding:1px 7px; "
               f"border-radius:3px; font-weight:700;'>{side.upper()}</span>")
        name_col = "#f0f0f0" if active else "#9a9a9a"
        meta = f"  <span style='color:#6f6f6f;'>{idx+1}/{n}"
        if dim_txt:
            meta += f" · {dim_txt}"
        meta += "</span>"
        return (f"<div style='padding:6px;'>{tag} "
                f"<span style='color:{name_col}; font-weight:600;'>{name}</span>{meta}</div>")

    def _update_header(self):
        self._head_a.setText(self._head_html("a", self._idx_a))
        self._head_b.setText(self._head_html("b", self._idx_b))

    def _start_load(self, idx: int, slot: str):
        if idx < 0 or idx >= len(self._manager.images):
            return
        task = ImageLoadTask(self._manager, idx, self._loader_signals)
        self._pool.start(task)

    @pyqtSlot(int, QImage)
    def _on_image_ready(self, idx: int, img: QImage):
        pm = QPixmap.fromImage(img)
        if idx == self._idx_a:
            self._dims["a"] = (img.width(), img.height())
            self._canvas_a.set_pixmap(pm, key=("cmp_a", idx))
        if idx == self._idx_b:
            self._dims["b"] = (img.width(), img.height())
            self._canvas_b.set_pixmap(pm, key=("cmp_b", idx))
        self._update_header()

    # ── Navigation ─────────────────────────────────────────────────────────────
    def _nav_active(self, delta: int):
        """Step the active side through the library, skipping the other side's
        image so the two panes never show the same frame."""
        n = len(self._manager.images)
        other = self._idx_b if self._active == "a" else self._idx_a
        cur = self._idx_a if self._active == "a" else self._idx_b
        nxt = cur
        for _ in range(n):
            nxt = max(0, min(n - 1, nxt + delta))
            if nxt != other or nxt == cur:
                break
        if nxt == other:           # only happens with n<2
            return
        if self._active == "a":
            self._idx_a = nxt
            self._start_load(nxt, "a")
        else:
            self._idx_b = nxt
            self._start_load(nxt, "b")
        self._update_header()

    def _swap(self):
        self._idx_a, self._idx_b = self._idx_b, self._idx_a
        self._dims.clear()
        self._load_both()

    def event(self, e):
        # Tab/Backtab are swallowed by focus traversal before keyPressEvent, so
        # intercept them here to switch the active side.
        if e.type() == e.Type.KeyPress and e.key() in (
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._set_active("b" if self._active == "a" else "a")
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        key = event.key()
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier

        if key == Qt.Key.Key_Escape:
            w = self.window()
            if w and w.isFullScreen():
                self.fullscreen_requested.emit()
            else:
                self.closed.emit()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self.fullscreen_requested.emit()
        elif key == Qt.Key.Key_S and not ctrl:
            self._sync_enabled = not self._sync_enabled
            self._bar.setText(
                ("Sync ON — pan/zoom mirrored" if self._sync_enabled
                 else "Sync OFF — pan/zoom each side independently")
                + "   ·   press S to toggle"
            )
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Space):
            self._set_active("b" if self._active == "a" else "a")
        elif key == Qt.Key.Key_X:
            self._swap()
        elif key == Qt.Key.Key_Right:
            self._nav_active(1)
        elif key == Qt.Key.Key_Left:
            self._nav_active(-1)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            (self._canvas_a if self._active == "a" else self._canvas_b).zoom(1.2)
        elif key == Qt.Key.Key_Minus:
            (self._canvas_a if self._active == "a" else self._canvas_b).zoom(1 / 1.2)
        elif key == Qt.Key.Key_0:
            self._canvas_a.zoom_reset()
            self._canvas_b.zoom_reset()
        else:
            super().keyPressEvent(event)

    def cleanup(self):
        try:
            self._pool.clear()
            self._pool.waitForDone(200)
        except Exception:
            pass
